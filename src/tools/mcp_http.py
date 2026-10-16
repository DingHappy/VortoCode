"""Explicit, bounded 2026-07-28 Streamable HTTP tool adapter.

Older session-era transports are not silently negotiated or retried.
"""
import asyncio
import base64
import json
import logging
import re
import time

from .mcp_client import MCPClient, MCPTool, MCPToolCall, MCPToolResult, MCPTransport
from src.utils.async_ops import timeout_scope

logger = logging.getLogger(__name__)
VERSION = "2026-07-28"
MAX_BYTES = 1024 * 1024
MAX_TOOLS = 256
MAX_PAGES = 32


def header_value(value: str) -> str:
    if (value == value.strip() and all(32 <= ord(c) <= 126 or c == '\t' for c in value)
            and not (value.startswith('=?base64?') and value.endswith('?='))):
        return value
    return '=?base64?' + base64.b64encode(value.encode()).decode() + '?='


def header_paths(schema: dict) -> list:
    """Validate all annotations, including annotations in unreachable schemas."""
    found = []
    names = set()
    nodes = 0

    def visit(node, path=(), reachable=True, depth=0):
        nonlocal nodes
        nodes += 1
        if nodes > 2048 or depth > 32:
            raise ValueError('MCP schema inspection budget exhausted')
        if isinstance(node, dict):
            if 'x-mcp-header' in node:
                name = node['x-mcp-header']
                typ = node.get('type')
                if (not path or not reachable or not isinstance(name, str)
                        or len(name) > 64 or not re.fullmatch(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+", name)
                        or name.lower() in names or typ not in ('string', 'integer', 'boolean')):
                    raise ValueError('Invalid x-mcp-header annotation')
                names.add(name.lower())
                found.append((path, name, typ))
            for key, child in node.items():
                if key == 'properties' and isinstance(child, dict):
                    for prop, definition in child.items():
                        visit(definition, path + (prop,), reachable, depth + 1)
                elif isinstance(child, (dict, list)):
                    visit(child, path, False, depth + 1)
        elif isinstance(node, list):
            for child in node:
                visit(child, path, False, depth + 1)
    visit(schema)
    return found


class StreamableHTTPMCPClient(MCPClient):
    def __init__(self, server_name, base_url, headers=None, timeout=30.0,
                 protocol_version=VERSION):
        super().__init__(server_name, MCPTransport.HTTP)
        if protocol_version not in (VERSION, "2025-03-26", "2025-06-18", "2025-11-25"):
            raise ValueError('Unsupported Streamable HTTP version')
        if not 0 < timeout <= 120:
            raise ValueError('MCP timeout must be within (0,120] seconds')
        self.protocol_version = protocol_version
        self.session_id = None
        self.server_capabilities = {}
        self._virtual_methods = {}
        self.base_url = base_url  # Exact endpoint; do not append legacy paths.
        self.headers = headers or {}
        self.timeout = timeout
        self.session = None
        self._request_id = 0

    async def connect(self):
        import aiohttp
        if self.session is None:
            self.session = aiohttp.ClientSession(
                headers=self.headers, trust_env=False,
                timeout=aiohttp.ClientTimeout(total=self.timeout))
        self.connected = True
        if self.protocol_version != VERSION:
            try:
                result = await self._rpc('initialize', {
                    'protocolVersion': self.protocol_version, 'capabilities': {},
                    'clientInfo': {'name': 'vortocode', 'version': '0.1.0'},
                })
                version = result.get('protocolVersion')
                if version not in ('2025-03-26', '2025-06-18', '2025-11-25'):
                    raise ValueError('Unsupported negotiated MCP version')
                self.protocol_version = version
                capabilities = result.get('capabilities')
                if not isinstance(capabilities, dict):
                    raise ValueError('Invalid MCP server capabilities')
                self.server_capabilities = capabilities
                await self._notify('notifications/initialized')
            except BaseException:
                await self.disconnect()
                raise
        return True  # Catalogue discovery below establishes actual reachability.

    async def disconnect(self):
        self.connected = False
        if self.session is not None and self.session_id is not None:
            try:
                async with timeout_scope(2):
                    async with self.session.delete(self.base_url, headers=self._session_headers(),
                                                   allow_redirects=False):
                        pass
            except Exception:
                logger.warning('MCP remote session cleanup not confirmed')
        self.session_id = None
        session, self.session = self.session, None
        if session is not None:
            await session.close()

    def _response(self, message, request_id):
        if not isinstance(message, dict) or message.get('jsonrpc') != '2.0':
            raise ValueError('Invalid MCP JSON-RPC response')
        if 'method' in message:
            if 'id' in message or message['method'] not in ('notifications/progress', 'notifications/message'):
                raise ValueError('Unsupported MCP server message')
            return None
        if type(message.get('id')) is not int or message['id'] != request_id:
            raise ValueError('MCP response identity mismatch')
        if ('result' in message) == ('error' in message):
            raise ValueError('Invalid MCP response result/error')
        if 'error' in message:
            error = message['error']
            raise RuntimeError('MCP RPC error: ' + str(error.get('message', 'unknown') if isinstance(error, dict) else error))
        result = message['result']
        if not isinstance(result, dict) or 'inputRequests' in result:
            raise ValueError('Unsupported MCP result or input request')
        return result

    def _session_headers(self):
        headers = {'MCP-Protocol-Version': self.protocol_version}
        if self.session_id is not None:
            headers['MCP-Session-Id'] = self.session_id
        return headers

    async def _notify(self, method, params=None):
        payload = {'jsonrpc': '2.0', 'method': method}
        if params is not None:
            payload['params'] = params
        async with self.session.post(self.base_url, json=payload,
                                     headers={'Accept': 'application/json, text/event-stream',
                                              **self._session_headers()}, allow_redirects=False) as response:
            if response.status != 202:
                raise RuntimeError('MCP notification not accepted')

    async def _rpc(self, method, params=None, extra_headers=None):
        request_id = self._request_id + 1
        try:
            return await self._rpc_impl(method, params, extra_headers)
        except asyncio.CancelledError:
            if self.protocol_version != VERSION and self.session is not None and method != 'initialize':
                try:
                    async with timeout_scope(1):
                        await self._notify('notifications/cancelled', {'requestId': request_id})
                except (Exception, asyncio.CancelledError):
                    logger.warning('MCP cancellation notification not confirmed')
            raise

    async def _rpc_impl(self, method, params=None, extra_headers=None):
        if not self.connected or self.session is None:
            raise RuntimeError('Not connected to MCP server')
        self._request_id += 1
        request_id = self._request_id
        params = {**(params or {})}
        if self.protocol_version == VERSION:
            params['_meta'] = {
            'io.modelcontextprotocol/protocolVersion': VERSION,
            'io.modelcontextprotocol/clientInfo': {'name': 'vortocode', 'version': '0.1.0'},
            'io.modelcontextprotocol/clientCapabilities': {},
            }
        headers = {'Accept': 'application/json, text/event-stream',
                   **self._session_headers(), 'Mcp-Method': method, **(extra_headers or {})}
        if 'name' in params or 'uri' in params:
            headers['Mcp-Name'] = header_value(params.get('name', params.get('uri')))
        payload = {'jsonrpc': '2.0', 'id': request_id, 'method': method, 'params': params}
        # No redirects, reconnection, automatic retry, or protocol fallback.
        async with self.session.post(self.base_url, json=payload, headers=headers,
                                     allow_redirects=False) as response:
            if method == 'initialize' and response.status == 200:
                identity = response.headers.get('MCP-Session-Id')
                if identity is not None:
                    if len(identity) > 256 or not identity or any(not 33 <= ord(c) <= 126 for c in identity):
                        raise ValueError('Invalid MCP session identity')
                    self.session_id = identity
            if response.status == 404 and self.session_id is not None:
                self.connected = False  # Explicit reconnect required; never replay a tool call.
            if response.status != 200:
                raise RuntimeError(f'MCP HTTP {response.status}; request not retried')
            mime = response.content_type
            if mime not in ('application/json', 'text/event-stream'):
                raise ValueError('Unsupported MCP response content type')
            total = events = 0
            buffer = b''
            data = []
            async for chunk in response.content.iter_chunked(65536):
                total += len(chunk)
                if total > MAX_BYTES:
                    raise ValueError('MCP response byte budget exhausted')
                buffer += chunk
                if mime == 'application/json':
                    continue
                while True:
                    match = re.search(b'\r\n|\r|\n', buffer)
                    if match is None or (match.group() == b'\r' and match.end() == len(buffer)):
                        break
                    line, buffer = buffer[:match.start()], buffer[match.end():]
                    if not line:
                        if data:
                            events += 1
                            if events > 256:
                                raise ValueError('MCP SSE event budget exhausted')
                            result = self._response(json.loads(b'\n'.join(data)), request_id)
                            data = []
                            if result is not None:
                                return result
                    elif line.startswith(b'data:'):
                        value = line[5:]
                        data.append(value[1:] if value.startswith(b' ') else value)
            if mime == 'text/event-stream' and buffer == b'\r' and data:
                result = self._response(json.loads(b'\n'.join(data)), request_id)
                if result is not None:
                    return result
            if mime == 'application/json':
                result = self._response(json.loads(buffer), request_id)
                if result is not None:
                    return result
            raise ValueError('MCP response ended without a final result')

    def _feature_tools(self, tools):
        methods = {}
        extras = []
        definitions = [
            ('resources', 'mcp_resources_list', 'resources/list', {'cursor': {'type': 'string'}}, []),
            ('resources', 'mcp_resource_read', 'resources/read', {'uri': {'type': 'string'}}, ['uri']),
            ('prompts', 'mcp_prompts_list', 'prompts/list', {'cursor': {'type': 'string'}}, []),
            ('prompts', 'mcp_prompt_get', 'prompts/get', {'name': {'type': 'string'}, 'arguments': {'type': 'object', 'additionalProperties': {'type': 'string'}}}, ['name']),
        ]
        names = {t.name for t in tools}
        for capability, name, method, props, required in definitions:
            if isinstance(self.server_capabilities.get(capability), dict):
                if name in names or len(tools) + len(extras) >= MAX_TOOLS:
                    raise ValueError('MCP feature identity collision or catalogue budget exhausted')
                extras.append(MCPTool(name=name, description=f'[MCP {method}] Explicit request; no automatic subscription',
                                      input_schema={'type': 'object', 'properties': props, 'required': required, 'additionalProperties': False},
                                      server_name=self.server_name, server_url=self.base_url))
                methods[name] = method
        self._virtual_methods = methods
        return tools + extras

    async def list_tools(self):
        if not self.connected or self.session is None:
            raise RuntimeError('Not connected to MCP server')
        # Legacy initialization is authoritative: unadvertised tools/list need not exist.
        # Modern revisions retain their original per-request discovery contract.
        if self.protocol_version != VERSION and 'tools' not in self.server_capabilities:
            self.tools = self._feature_tools([])
            return self.tools
        tools, seen, cursors = [], set(), set()
        cursor = None
        async with timeout_scope(self.timeout):
            for _ in range(MAX_PAGES):
                result = await self._rpc('tools/list', {'cursor': cursor} if cursor is not None else {})
                entries = result.get('tools')
                if not isinstance(entries, list):
                    raise ValueError('Invalid MCP catalogue')
                for item in entries:
                    if not isinstance(item, dict) or not isinstance(item.get('name'), str):
                        raise ValueError('Invalid MCP tool')
                    if item['name'] in seen or len(seen) >= MAX_TOOLS:
                        raise ValueError('Duplicate tool or MCP catalogue budget exhausted')
                    seen.add(item['name'])
                    schema = item.get('inputSchema')
                    if not isinstance(schema, dict):
                        raise ValueError('Invalid MCP input schema')
                    try:
                        header_paths(schema)
                    except ValueError as e:
                        logger.warning('Excluded MCP tool %s: %s', item['name'], e)
                        continue
                    annotations = item.get('annotations')
                    tools.append(MCPTool(name=item['name'], description=item.get('description', ''),
                                         input_schema=schema, server_name=self.server_name,
                                         server_url=self.base_url,
                                         annotations=annotations if isinstance(annotations, dict) else {}))
                cursor = result.get('nextCursor')
                if cursor is None:
                    self.tools = self._feature_tools(tools)
                    return self.tools
                if not isinstance(cursor, str) or not cursor or len(cursor) > 4096 or cursor in cursors:
                    raise ValueError('Invalid or repeated MCP catalogue cursor')
                cursors.add(cursor)
            raise ValueError('MCP catalogue page budget exhausted')

    async def call_tool(self, tool_call: MCPToolCall):
        start = time.monotonic()
        try:
            tool = next((t for t in self.tools if t.name == tool_call.tool_name), None)
            if tool is None:
                raise ValueError('MCP tool not in discovered catalogue')
            headers = {}
            for path, name, typ in header_paths(tool.input_schema):
                value = tool_call.arguments
                for part in path:
                    if not isinstance(value, dict) or part not in value:
                        break
                    value = value[part]
                else:
                    if not ((typ == 'string' and type(value) is str)
                            or (typ == 'boolean' and type(value) is bool)
                            or (typ == 'integer' and type(value) is int and abs(value) <= 2**53 - 1)):
                        raise ValueError('Invalid mirrored MCP argument')
                    encoded = header_value(str(value).lower() if typ == 'boolean' else str(value))
                    if len(encoded) > 8192:
                        raise ValueError('MCP parameter header budget exhausted')
                    headers['Mcp-Param-' + name] = encoded
            method = self._virtual_methods.get(tool_call.tool_name)
            if method is not None:
                params = dict(tool_call.arguments)
                required = 'uri' if method == 'resources/read' else 'name' if method == 'prompts/get' else None
                if required and (not isinstance(params.get(required), str) or not params[required] or len(params[required]) > 4096):
                    raise ValueError('Invalid MCP feature request')
                if 'arguments' in params and (not isinstance(params['arguments'], dict) or any(not isinstance(v, str) for v in params['arguments'].values())):
                    raise ValueError('MCP prompt arguments must be strings')
                result = await self._rpc(method, params)
                field = {'resources/list': 'resources', 'resources/read': 'contents', 'prompts/list': 'prompts', 'prompts/get': 'messages'}[method]
                if not isinstance(result.get(field), list):
                    raise ValueError('Invalid MCP feature result')
                return MCPToolResult(success=True, output=result, tool_name=tool_call.tool_name,
                                     execution_time=time.monotonic()-start)
            result = await self._rpc('tools/call', {'name': tool_call.tool_name,
                                                  'arguments': tool_call.arguments}, headers)
            from .mcp_client import parse_tool_result
            return parse_tool_result(result, tool_name=tool_call.tool_name,
                                     execution_time=time.monotonic()-start)
        except Exception as e:
            return MCPToolResult(success=False, error=str(e), tool_name=tool_call.tool_name,
                                 execution_time=time.monotonic()-start)
