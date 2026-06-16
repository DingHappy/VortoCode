"""MCP客户端实现"""

import asyncio
import json
import logging
import subprocess
import sys
from abc import ABC, abstractmethod
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Union
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class MCPTransport(str, Enum):
    """MCP传输类型"""
    STDIO = "stdio"
    HTTP = "http"
    SSE = "sse"


class MCPTool(BaseModel):
    """MCP工具定义"""
    name: str
    description: str = ""
    input_schema: Dict[str, Any] = Field(default_factory=dict)
    server_name: str = ""
    server_url: str = ""


class MCPToolCall(BaseModel):
    """MCP工具调用"""
    tool_name: str
    arguments: Dict[str, Any] = Field(default_factory=dict)
    request_id: str = ""


class MCPToolResult(BaseModel):
    """MCP工具调用结果"""
    success: bool
    output: Any = None
    error: Optional[str] = None
    tool_name: str = ""
    execution_time: float = 0.0


class MCPClient(ABC):
    """MCP客户端基类"""
    
    def __init__(self, server_name: str, transport: MCPTransport):
        self.server_name = server_name
        self.transport = transport
        self.tools: List[MCPTool] = []
        self.connected = False
    
    @abstractmethod
    async def connect(self) -> bool:
        """连接到MCP服务器"""
        pass
    
    @abstractmethod
    async def disconnect(self) -> None:
        """断开连接"""
        pass
    
    @abstractmethod
    async def list_tools(self) -> List[MCPTool]:
        """列出可用工具"""
        pass
    
    @abstractmethod
    async def call_tool(self, tool_call: MCPToolCall) -> MCPToolResult:
        """调用工具"""
        pass
    
    async def refresh_tools(self) -> List[MCPTool]:
        """刷新工具列表"""
        if self.connected:
            self.tools = await self.list_tools()
        return self.tools


class StdioMCPClient(MCPClient):
    """基于stdio的MCP客户端"""
    
    def __init__(
        self,
        server_name: str,
        command: str,
        args: List[str] = None,
        env: Dict[str, str] = None,
        cwd: Optional[str] = None
    ):
        super().__init__(server_name, MCPTransport.STDIO)
        self.command = command
        self.args = args or []
        self.env = env or {}
        self.cwd = cwd
        self.process: Optional[subprocess.Popen] = None
        self.request_id = 0
    
    async def connect(self) -> bool:
        """启动MCP服务器进程"""
        try:
            import os
            env = {**os.environ, **self.env}
            
            self.process = subprocess.Popen(
                [self.command] + self.args,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=env,
                cwd=self.cwd,
                text=True,
                bufsize=1
            )
            
            # 发送初始化请求
            init_request = {
                "jsonrpc": "2.0",
                "id": self._next_id(),
                "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {
                        "name": "auto-dev-crew",
                        "version": "0.1.0"
                    }
                }
            }
            
            await self._send_request(init_request)
            response = await self._read_response()
            
            if response and "result" in response:
                self.connected = True
                logger.info(f"Connected to MCP server: {self.server_name}")
                return True
            else:
                logger.error(f"Failed to initialize MCP server: {self.server_name}")
                return False
                
        except Exception as e:
            logger.error(f"Failed to connect to MCP server {self.server_name}: {e}")
            return False
    
    async def disconnect(self) -> None:
        """断开连接并关闭进程"""
        if self.process:
            try:
                # 发送关闭通知
                shutdown_request = {
                    "jsonrpc": "2.0",
                    "method": "shutdown"
                }
                await self._send_request(shutdown_request)
                
                self.process.terminate()
                self.process.wait(timeout=5)
            except Exception as e:
                logger.warning(f"Error during disconnect: {e}")
            finally:
                self.process = None
                self.connected = False
    
    async def list_tools(self) -> List[MCPTool]:
        """列出可用工具"""
        if not self.connected:
            return []
        
        request = {
            "jsonrpc": "2.0",
            "id": self._next_id(),
            "method": "tools/list"
        }
        
        await self._send_request(request)
        response = await self._read_response()
        
        tools = []
        if response and "result" in response:
            for tool_data in response["result"].get("tools", []):
                tool = MCPTool(
                    name=tool_data.get("name", ""),
                    description=tool_data.get("description", ""),
                    input_schema=tool_data.get("inputSchema", {}),
                    server_name=self.server_name
                )
                tools.append(tool)
        
        return tools
    
    async def call_tool(self, tool_call: MCPToolCall) -> MCPToolResult:
        """调用工具"""
        import time
        start_time = time.time()
        
        if not self.connected:
            return MCPToolResult(
                success=False,
                error="Not connected to MCP server",
                tool_name=tool_call.tool_name
            )
        
        request = {
            "jsonrpc": "2.0",
            "id": self._next_id(),
            "method": "tools/call",
            "params": {
                "name": tool_call.tool_name,
                "arguments": tool_call.arguments
            }
        }
        
        try:
            await self._send_request(request)
            response = await self._read_response()
            
            execution_time = time.time() - start_time
            
            if response and "result" in response:
                result_data = response["result"]
                return MCPToolResult(
                    success=True,
                    output=result_data.get("content", []),
                    tool_name=tool_call.tool_name,
                    execution_time=execution_time
                )
            elif response and "error" in response:
                return MCPToolResult(
                    success=False,
                    error=response["error"].get("message", "Unknown error"),
                    tool_name=tool_call.tool_name,
                    execution_time=execution_time
                )
            else:
                return MCPToolResult(
                    success=False,
                    error="Invalid response from MCP server",
                    tool_name=tool_call.tool_name,
                    execution_time=execution_time
                )
                
        except Exception as e:
            execution_time = time.time() - start_time
            return MCPToolResult(
                success=False,
                error=str(e),
                tool_name=tool_call.tool_name,
                execution_time=execution_time
            )
    
    def _next_id(self) -> int:
        """生成下一个请求ID"""
        self.request_id += 1
        return self.request_id
    
    async def _send_request(self, request: Dict[str, Any]) -> None:
        """发送JSON-RPC请求"""
        if not self.process or not self.process.stdin:
            raise RuntimeError("Process not running")
        
        request_str = json.dumps(request) + "\n"
        self.process.stdin.write(request_str)
        self.process.stdin.flush()
    
    async def _read_response(self) -> Optional[Dict[str, Any]]:
        """读取JSON-RPC响应"""
        if not self.process or not self.process.stdout:
            return None
        
        try:
            line = self.process.stdout.readline()
            if line:
                return json.loads(line.strip())
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse JSON response: {e}")
        except Exception as e:
            logger.error(f"Failed to read response: {e}")
        
        return None


class HTTPMCPClient(MCPClient):
    """基于HTTP的MCP客户端"""
    
    def __init__(
        self,
        server_name: str,
        base_url: str,
        headers: Dict[str, str] = None,
        timeout: float = 30.0
    ):
        super().__init__(server_name, MCPTransport.HTTP)
        self.base_url = base_url.rstrip("/")
        self.headers = headers or {}
        self.timeout = timeout
        self.session = None
    
    async def connect(self) -> bool:
        """连接到HTTP MCP服务器"""
        try:
            import aiohttp
            self.session = aiohttp.ClientSession(
                headers=self.headers,
                timeout=aiohttp.ClientTimeout(total=self.timeout)
            )
            
            # 测试连接
            async with self.session.get(f"{self.base_url}/health") as response:
                if response.status == 200:
                    self.connected = True
                    logger.info(f"Connected to HTTP MCP server: {self.server_name}")
                    return True
                else:
                    logger.error(f"Failed to connect to HTTP MCP server: {response.status}")
                    return False
                    
        except Exception as e:
            logger.error(f"Failed to connect to HTTP MCP server {self.server_name}: {e}")
            return False
    
    async def disconnect(self) -> None:
        """断开连接"""
        if self.session:
            await self.session.close()
            self.session = None
        self.connected = False
    
    async def list_tools(self) -> List[MCPTool]:
        """列出可用工具"""
        if not self.connected or not self.session:
            return []
        
        try:
            async with self.session.get(f"{self.base_url}/tools") as response:
                if response.status == 200:
                    data = await response.json()
                    tools = []
                    for tool_data in data.get("tools", []):
                        tool = MCPTool(
                            name=tool_data.get("name", ""),
                            description=tool_data.get("description", ""),
                            input_schema=tool_data.get("inputSchema", {}),
                            server_name=self.server_name,
                            server_url=self.base_url
                        )
                        tools.append(tool)
                    return tools
        except Exception as e:
            logger.error(f"Failed to list tools: {e}")
        
        return []
    
    async def call_tool(self, tool_call: MCPToolCall) -> MCPToolResult:
        """调用工具"""
        import time
        start_time = time.time()
        
        if not self.connected or not self.session:
            return MCPToolResult(
                success=False,
                error="Not connected to MCP server",
                tool_name=tool_call.tool_name
            )
        
        try:
            payload = {
                "name": tool_call.tool_name,
                "arguments": tool_call.arguments
            }
            
            async with self.session.post(
                f"{self.base_url}/tools/call",
                json=payload
            ) as response:
                execution_time = time.time() - start_time
                
                if response.status == 200:
                    result_data = await response.json()
                    return MCPToolResult(
                        success=True,
                        output=result_data.get("content", []),
                        tool_name=tool_call.tool_name,
                        execution_time=execution_time
                    )
                else:
                    error_data = await response.json()
                    return MCPToolResult(
                        success=False,
                        error=error_data.get("error", "Unknown error"),
                        tool_name=tool_call.tool_name,
                        execution_time=execution_time
                    )
                    
        except Exception as e:
            execution_time = time.time() - start_time
            return MCPToolResult(
                success=False,
                error=str(e),
                tool_name=tool_call.tool_name,
                execution_time=execution_time
            )


def create_mcp_client(
    server_name: str,
    transport: MCPTransport,
    **kwargs
) -> MCPClient:
    """创建MCP客户端工厂函数"""
    if transport == MCPTransport.STDIO:
        return StdioMCPClient(server_name, **kwargs)
    elif transport == MCPTransport.HTTP:
        return HTTPMCPClient(server_name, **kwargs)
    else:
        raise ValueError(f"Unsupported transport: {transport}")
