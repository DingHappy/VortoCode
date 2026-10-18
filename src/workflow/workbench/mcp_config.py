"""Desktop 的 MCP 服务管理：看清每个服务**实际会不会启动**，增删启停免网络 HTTP 服务，测试连接。

状态用的是连接时同一套筛选（``src/agents/mcp_tools``），页面上看到的就是会发生的：
- ``available``：显式 ``credentialed: false`` 的 HTTP 服务，external 会话会连接它；
- ``connector``：带凭据名字的 HTTPS 连接器，只在常驻助理的通用范围、授权后连接；
- ``blocked``：不会启动，``reason`` 说为什么（stdio 会在宿主机执行命令，被安全策略禁用；
  HTTP 但没标 ``credentialed: false``、带 headers、URL 里像有凭据……）；
- ``disabled``：配置里关着。

只能**新增**免凭据的 HTTP 服务。编辑方式与 cron 编辑面同一纪律：逐行改、不碰主人的注释和别的条目，
改完用真解析回验，对不上就不写盘。
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List

import yaml

MAX_BYTES = 256 * 1024
_NAME = re.compile(r"[A-Za-z0-9_-]{1,56}")


class McpConfigError(ValueError):
    """配置不合法、找不到条目或回验失败；文件未改动。"""


def config_path(root: str) -> Path:
    return Path(root) / "config" / "mcp.yaml"


def _read(root: str) -> str:
    path = config_path(root)
    if not path.is_file():
        return ""
    if path.stat().st_size > MAX_BYTES:
        raise McpConfigError("config/mcp.yaml 超过 256KB，请手动整理")
    return path.read_text(encoding="utf-8")


def _parse(text: str) -> List[Dict[str, Any]]:
    try:
        data = yaml.safe_load(text) if text.strip() else {}
    except yaml.YAMLError as error:
        raise McpConfigError("config/mcp.yaml 不是合法的 YAML，请先手动修好") from error
    servers = (data or {}).get("servers") if isinstance(data, dict) else None
    if servers is None:
        return []
    if not isinstance(servers, list):
        raise McpConfigError("config/mcp.yaml 的 servers 不是列表")
    return [server for server in servers if isinstance(server, dict)]


def _status(server: Dict[str, Any]) -> tuple[str, str]:
    from src.agents.mcp_tools import _credential_free_http_servers, _credential_free_http_url
    if not server.get("enabled", True):
        return "disabled", "配置里关着"
    transport = str(server.get("transport") or "stdio").strip().lower()
    if transport != "http":
        return "blocked", "stdio 服务会在这台电脑上执行命令，被安全策略禁用，不会启动"
    if server.get("credential"):
        url = str(server.get("url") or "")
        if server.get("headers") or not url.startswith("https://") or not _credential_free_http_url(url):
            return "blocked", "连接器必须是 HTTPS、不带 headers、URL 里不能有凭据"
        return "connector", f"凭据「{server['credential']}」：只在常驻助理里、授权后连接"
    if _credential_free_http_servers({"servers": [server]}):
        return "available", "external 会话会连接它；工具调用仍逐次确认"
    if server.get("headers"):
        return "blocked", "带 headers（等于把凭据写进了文件），不会连接"
    if server.get("credentialed") is not False:
        return "blocked", "没有显式标 credentialed: false，按带凭据处理，不会连接"
    return "blocked", "URL 不合法或像带了凭据，不会连接"


def list_servers(root: str) -> Dict[str, Any]:
    text = _read(root)
    items = []
    for server in _parse(text):
        status, reason = _status(server)
        items.append({
            "name": str(server.get("name") or ""),
            "transport": str(server.get("transport") or "stdio").lower(),
            "target": str(server.get("url") or " ".join([str(server.get("command") or "")]
                                                         + [str(a) for a in server.get("args") or []])).strip(),
            "enabled": bool(server.get("enabled", True)),
            "status": status, "reason": reason,
        })
    return {"path": "config/mcp.yaml", "exists": bool(text), "servers": items}


def _write_verified(root: str, new_text: str, check) -> None:
    servers = _parse(new_text)
    if not check(servers):
        raise McpConfigError("改动后的配置回验没通过，文件未改动；请手动编辑 config/mcp.yaml")
    path = config_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".yaml.tmp")
    tmp.write_text(new_text, encoding="utf-8")
    tmp.replace(path)


def _names(servers: List[Dict[str, Any]]) -> List[str]:
    return [str(server.get("name") or "") for server in servers]


def add_http_server(root: str, name: str, url: str) -> Dict[str, Any]:
    from src.agents.mcp_tools import _credential_free_http_url
    name = str(name or "").strip()
    url = str(url or "").strip()
    if not _NAME.fullmatch(name) or "__" in name:
        raise McpConfigError("名称只能用字母、数字、- 和 _，最长 56 个字符，不能含连续两个下划线")
    if not _credential_free_http_url(url):
        raise McpConfigError("URL 需要是 http(s) 地址，且不能带账号、令牌或片段（#）")
    text = _read(root)
    before = _parse(text)
    if name in _names(before):
        raise McpConfigError(f"已经有叫「{name}」的服务")
    block = (f"  - name: {yaml.safe_dump(name).strip().splitlines()[0]}\n"
             f"    transport: http\n"
             f"    url: {yaml.safe_dump(url, width=10_000).strip().splitlines()[0]}\n"
             f"    protocol: streamable_http\n"
             # 现有服务大多还是会话式握手（官方 SDK 1.22 就是）；客户端默认的无状态 2026-07-28
             # 会被它们回 400。写明 2025-06-18，服务端支持更新的版本时握手会协商上去。
             f"    protocol_version: \"2025-06-18\"\n"
             f"    credentialed: false\n"
             f"    enabled: true\n")
    lines = text.splitlines(keepends=True)
    head = next((i for i, line in enumerate(lines) if re.match(r"^servers\s*:", line)), None)
    if not text:
        new_text = "# MCP 服务（Desktop 插件页可管理）\nservers:\n" + block
    elif head is None:
        new_text = text + ("" if text.endswith("\n") else "\n") + "servers:\n" + block
    else:
        if re.match(r"^servers\s*:\s*\[\s*\]\s*$", lines[head]):
            lines[head] = "servers:\n"
        end = len(lines)
        for i in range(head + 1, len(lines)):                     # 段止于下一个顶格键
            if lines[i].strip() and not lines[i][:1].isspace() and not lines[i].lstrip().startswith("#"):
                end = i
                break
        while end > head + 1 and not lines[end - 1].strip():        # 插在段内最后一个非空行之后
            end -= 1
        if end > 0 and not lines[end - 1].endswith("\n"):
            lines[end - 1] += "\n"
        lines.insert(end, block)
        new_text = "".join(lines)
    _write_verified(root, new_text, lambda after: _names(after) == _names(before) + [name])
    return next(item for item in list_servers(root)["servers"] if item["name"] == name)


def _block(lines: List[str], name: str) -> tuple[int, int, str]:
    head = re.compile(r"^(\s*)-\s+name\s*:\s*['\"]?" + re.escape(name) + r"['\"]?\s*(#.*)?$")
    start = next((i for i, line in enumerate(lines) if head.match(line.rstrip("\n"))), None)
    if start is None:
        raise McpConfigError(f"在 config/mcp.yaml 里找不到「{name}」（可能写成了行内格式），请手动编辑")
    indent = head.match(lines[start].rstrip("\n")).group(1)
    stop = len(lines)
    for i in range(start + 1, len(lines)):
        line = lines[i].rstrip("\n")
        if not line.strip():
            continue
        if re.match(r"^" + re.escape(indent) + r"-\s", line) or not line[:1].isspace():
            stop = i
            break
    while stop > start + 1 and not lines[stop - 1].strip():
        stop -= 1
    return start, stop, indent


def set_enabled(root: str, name: str, enabled: bool) -> Dict[str, Any]:
    text = _read(root)
    before = _parse(text)
    if name not in _names(before):
        raise McpConfigError(f"没有叫「{name}」的服务")
    lines = text.splitlines(keepends=True)
    start, stop, indent = _block(lines, name)
    literal = "true" if enabled else "false"
    hit = next((i for i in range(start + 1, stop) if re.match(r"^\s*enabled\s*:", lines[i])), None)
    if hit is None:
        lines.insert(start + 1, f"{indent}  enabled: {literal}\n")
    else:
        lead = re.match(r"^(\s*)", lines[hit]).group(1)
        comment = re.search(r"\s+#.*$", lines[hit].rstrip("\n"))
        lines[hit] = f"{lead}enabled: {literal}{comment.group(0) if comment else ''}\n"

    def check(after):
        changed = [s for s in after if s.get("name") == name]
        return (_names(after) == _names(before) and len(changed) == 1
                and bool(changed[0].get("enabled", True)) is enabled
                and all(a == b for a, b in zip(after, before) if a.get("name") != name))
    _write_verified(root, "".join(lines), check)
    return next(item for item in list_servers(root)["servers"] if item["name"] == name)


def remove_server(root: str, name: str) -> None:
    text = _read(root)
    before = _parse(text)
    if name not in _names(before):
        raise McpConfigError(f"没有叫「{name}」的服务")
    lines = text.splitlines(keepends=True)
    start, stop, _indent = _block(lines, name)
    del lines[start:stop]
    _write_verified(root, "".join(lines),
                    lambda after: after == [server for server in before if server.get("name") != name])


async def test_server(root: str, name: str) -> Dict[str, Any]:
    """只连这一个可用的 HTTP 服务、读工具目录、断开。不调用任何工具，不经过模型。"""
    from src.agents.mcp_tools import _credential_free_http_servers
    from src.tools.manager import ToolManager
    server = next((s for s in _parse(_read(root)) if s.get("name") == name), None)
    if server is None:
        raise McpConfigError(f"没有叫「{name}」的服务")
    allowed = _credential_free_http_servers({"servers": [server]})
    if not allowed:
        status, reason = _status(server)
        raise McpConfigError(f"「{name}」不会被连接：{reason}")
    manager = ToolManager()
    manager.config = {"servers": allowed}
    try:
        await manager.initialize()
        prefix = f"mcp__{name}__"
        tools = sorted(tool.name[len(prefix):] if tool.name.startswith(prefix) else tool.name
                       for tool in manager.list_tools())
        connected = name in manager.mcp_clients
    finally:
        await manager.shutdown()
    if not connected:
        return {"ok": False, "detail": "连不上：检查地址、服务是否在跑、协议是否为 streamable HTTP", "tools": []}
    return {"ok": True, "detail": f"已连上，提供 {len(tools)} 个工具", "tools": tools[:50]}
