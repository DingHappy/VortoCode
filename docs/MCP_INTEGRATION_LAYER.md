# 工具集成层设计 (MCP Protocol)

## 概述

工具集成层实现了 Model Context Protocol (MCP) 支持，使 vortocode 能够动态发现、连接和使用外部工具，极大地扩展了系统的能力边界。

## MCP 协议简介

MCP (Model Context Protocol) 是一个开放标准，用于连接 AI 工具与外部数据源和工具。它提供了一种标准化的方式让 AI 代理访问文件系统、数据库、API 等外部资源。

### 核心概念

- **MCP Server**: 提供工具和资源的服务端
- **MCP Client**: 连接和使用 MCP Server 的客户端
- **Tools**: MCP Server 提供的可执行功能
- **Resources**: MCP Server 提供的数据资源

## 架构设计

```
┌─────────────────────────────────────────────────────────────┐
│                    VortoCode Agent                       │
└───────────────────────────┬─────────────────────────────────┘
                            │
┌───────────────────────────▼─────────────────────────────────┐
│                   MCP Integration Layer                      │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐      │
│  │   Discovery  │  │   Registry   │  │   Executor   │      │
│  │   Service    │  │   Service    │  │   Service    │      │
│  └──────────────┘  └──────────────┘  └──────────────┘      │
└───────┬──────────────────┬──────────────────┬───────────────┘
        │                  │                  │
┌───────▼──────────┐ ┌─────▼─────────┐ ┌─────▼────────────┐
│  File System     │ │   Git         │ │   Browser        │
│  MCP Server      │ │   MCP Server  │ │   MCP Server     │
└──────────────────┘ └───────────────┘ └──────────────────┘
        │                  │                  │
┌───────▼──────────┐ ┌─────▼─────────┐ ┌─────▼────────────┐
│  Database        │ │   API         │ │   Custom         │
│  MCP Server      │ │   MCP Server  │ │   MCP Server     │
└──────────────────┘ └───────────────┘ └──────────────────┘
```

## 核心组件

### 1. MCPClient (MCP 客户端)

负责与 MCP Server 建立连接和通信。

```python
import asyncio
from typing import Dict, List, Optional, Any
from pydantic import BaseModel
import json

class MCPServerConfig(BaseModel):
    """MCP 服务器配置"""
    name: str
    command: str
    args: List[str] = []
    env: Dict[str, str] = {}
    transport: str = "stdio"  # stdio, http, sse, ws

class MCPToolDefinition(BaseModel):
    """MCP 工具定义"""
    name: str
    description: str
    input_schema: Dict[str, Any]
    server_name: str

class MCPResource(BaseModel):
    """MCP 资源"""
    uri: str
    name: str
    description: str
    mime_type: str

class MCPClient:
    """MCP 客户端"""
    
    def __init__(self, config: MCPServerConfig):
        self.config = config
        self.process: Optional[asyncio.subprocess.Process] = None
        self.tools: List[MCPToolDefinition] = []
        self.resources: List[MCPResource] = []
        self._connected = False
    
    async def connect(self):
        """连接到 MCP 服务器"""
        if self.config.transport == "stdio":
            await self._connect_stdio()
        elif self.config.transport == "http":
            await self._connect_http()
        else:
            raise ValueError(f"不支持的传输方式: {self.config.transport}")
        
        # 获取服务器能力
        await self._discover_capabilities()
        self._connected = True
    
    async def _connect_stdio(self):
        """通过 stdio 连接"""
        self.process = await asyncio.create_subprocess_exec(
            self.config.command,
            *self.config.args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=self.config.env
        )
    
    async def _connect_http(self):
        """通过 HTTP 连接"""
        # HTTP 连接实现
        pass
    
    async def _discover_capabilities(self):
        """发现服务器能力"""
        # 获取工具列表
        tools_response = await self._send_request("tools/list", {})
        self.tools = [
            MCPToolDefinition(**tool, server_name=self.config.name)
            for tool in tools_response.get("tools", [])
        ]
        
        # 获取资源列表
        resources_response = await self._send_request("resources/list", {})
        self.resources = [
            MCPResource(**resource)
            for resource in resources_response.get("resources", [])
        ]
    
    async def execute_tool(
        self, 
        tool_name: str, 
        arguments: Dict[str, Any]
    ) -> Any:
        """执行工具"""
        if not self._connected:
            raise ConnectionError("未连接到 MCP 服务器")
        
        response = await self._send_request(
            "tools/call",
            {
                "name": tool_name,
                "arguments": arguments
            }
        )
        
        return response.get("result")
    
    async def read_resource(self, uri: str) -> str:
        """读取资源"""
        if not self._connected:
            raise ConnectionError("未连接到 MCP 服务器")
        
        response = await self._send_request(
            "resources/read",
            {"uri": uri}
        )
        
        return response.get("contents", [{}])[0].get("text", "")
    
    async def _send_request(
        self, 
        method: str, 
        params: Dict
    ) -> Dict:
        """发送请求到 MCP 服务器"""
        request = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": method,
            "params": params
        }
        
        # 发送请求
        request_json = json.dumps(request) + "\n"
        self.process.stdin.write(request_json.encode())
        await self.process.stdin.drain()
        
        # 读取响应
        response_line = await self.process.stdout.readline()
        response = json.loads(response_line.decode())
        
        if "error" in response:
            raise MCPError(response["error"])
        
        return response.get("result", {})
    
    async def disconnect(self):
        """断开连接"""
        if self.process:
            self.process.terminate()
            await self.process.wait()
        self._connected = False


class MCPError(Exception):
    """MCP 错误"""
    def __init__(self, error: Dict):
        self.code = error.get("code")
        self.message = error.get("message")
        super().__init__(self.message)
```

### 2. MCPRegistry (MCP 注册表)

管理所有已注册的 MCP 服务器和工具。

```python
class MCPRegistry:
    """MCP 注册表"""
    
    def __init__(self):
        self.servers: Dict[str, MCPClient] = {}
        self.tools: Dict[str, MCPToolDefinition] = {}
        self.resources: Dict[str, MCPResource] = {}
    
    async def register_server(self, config: MCPServerConfig):
        """注册 MCP 服务器"""
        client = MCPClient(config)
        await client.connect()
        self.servers[config.name] = client
        
        # 注册服务器提供的工具
        for tool in client.tools:
            tool_key = f"{config.name}:{tool.name}"
            self.tools[tool_key] = tool
        
        # 注册服务器提供的资源
        for resource in client.resources:
            resource_key = f"{config.name}:{resource.uri}"
            self.resources[resource_key] = resource
        
        print(f"已注册 MCP 服务器: {config.name}")
        print(f"  - 工具: {len(client.tools)} 个")
        print(f"  - 资源: {len(client.resources)} 个")
    
    async def unregister_server(self, server_name: str):
        """注销 MCP 服务器"""
        if server_name in self.servers:
            client = self.servers[server_name]
            await client.disconnect()
            
            # 移除相关工具和资源
            self.tools = {
                k: v for k, v in self.tools.items() 
                if not k.startswith(f"{server_name}:")
            }
            self.resources = {
                k: v for k, v in self.resources.items() 
                if not k.startswith(f"{server_name}:")
            }
            
            del self.servers[server_name]
    
    def get_tool(self, tool_name: str) -> Optional[MCPToolDefinition]:
        """获取工具定义"""
        return self.tools.get(tool_name)
    
    def find_tools(
        self, 
        capabilities: List[str]
    ) -> List[MCPToolDefinition]:
        """根据能力查找工具"""
        matching_tools = []
        for tool in self.tools.values():
            if self._matches_capabilities(tool, capabilities):
                matching_tools.append(tool)
        return matching_tools
    
    def _matches_capabilities(
        self, 
        tool: MCPToolDefinition, 
        capabilities: List[str]
    ) -> bool:
        """检查工具是否匹配能力需求"""
        tool_desc = tool.description.lower()
        for cap in capabilities:
            if cap.lower() in tool_desc:
                return True
        return False
    
    async def execute_tool(
        self, 
        tool_name: str, 
        arguments: Dict[str, Any]
    ) -> Any:
        """执行工具"""
        tool = self.tools.get(tool_name)
        if not tool:
            raise ToolNotFoundError(tool_name)
        
        server = self.servers.get(tool.server_name)
        if not server:
            raise ServerNotFoundError(tool.server_name)
        
        return await server.execute_tool(tool.name, arguments)
```

### 3. ToolPermissionManager (工具权限管理器)

管理工具的访问权限。

```python
from enum import Enum

class PermissionLevel(Enum):
    ALLOW = "allow"
    DENY = "deny"
    ASK = "ask"

class ToolPermission(BaseModel):
    """工具权限配置"""
    tool_pattern: str  # 工具名模式，支持通配符
    permission: PermissionLevel
    conditions: Dict[str, Any] = {}  # 额外条件

class ToolPermissionManager:
    """工具权限管理器"""
    
    def __init__(self):
        self.permissions: List[ToolPermission] = []
        self.permission_cache: Dict[str, PermissionLevel] = {}
    
    def add_permission(self, permission: ToolPermission):
        """添加权限规则"""
        self.permissions.append(permission)
        self.permission_cache.clear()  # 清除缓存
    
    def check_permission(
        self, 
        tool_name: str, 
        arguments: Dict[str, Any] = None
    ) -> PermissionLevel:
        """检查工具权限"""
        # 检查缓存
        cache_key = f"{tool_name}:{json.dumps(arguments or {}, sort_keys=True)}"
        if cache_key in self.permission_cache:
            return self.permission_cache[cache_key]
        
        # 按顺序检查权限规则
        for permission in self.permissions:
            if self._matches_pattern(tool_name, permission.tool_pattern):
                # 检查额外条件
                if self._check_conditions(permission.conditions, arguments):
                    self.permission_cache[cache_key] = permission.permission
                    return permission.permission
        
        # 默认需要询问
        self.permission_cache[cache_key] = PermissionLevel.ASK
        return PermissionLevel.ASK
    
    def _matches_pattern(self, tool_name: str, pattern: str) -> bool:
        """检查工具名是否匹配模式"""
        # 支持通配符匹配
        import fnmatch
        return fnmatch.fnmatch(tool_name, pattern)
    
    def _check_conditions(
        self, 
        conditions: Dict[str, Any], 
        arguments: Dict[str, Any]
    ) -> bool:
        """检查额外条件"""
        if not conditions:
            return True
        
        for key, value in conditions.items():
            if key not in arguments or arguments[key] != value:
                return False
        
        return True
```

### 4. ToolExecutor (工具执行器)

在隔离环境中执行工具。

```python
import docker
from typing import Any, Dict

class ToolExecutor:
    """工具执行器"""
    
    def __init__(self, use_sandbox: bool = True):
        self.use_sandbox = use_sandbox
        self.docker_client = docker.from_env() if use_sandbox else None
    
    async def execute(
        self, 
        tool: MCPToolDefinition, 
        arguments: Dict[str, Any],
        timeout: int = 60
    ) -> Any:
        """执行工具"""
        if self.use_sandbox:
            return await self._execute_in_sandbox(tool, arguments, timeout)
        else:
            return await self._execute_directly(tool, arguments, timeout)
    
    async def _execute_in_sandbox(
        self, 
        tool: MCPToolDefinition, 
        arguments: Dict[str, Any],
        timeout: int
    ) -> Any:
        """在沙箱中执行"""
        # 创建临时容器
        container = self.docker_client.containers.run(
            "vortocode-sandbox",
            command=self._build_command(tool, arguments),
            detach=True,
            network_disabled=True,
            mem_limit="512m",
            cpu_quota=50000
        )
        
        try:
            # 等待执行完成
            result = container.wait(timeout=timeout)
            logs = container.logs().decode()
            
            if result["StatusCode"] == 0:
                return {"success": True, "output": logs}
            else:
                return {"success": False, "error": logs}
        finally:
            container.remove(force=True)
    
    async def _execute_directly(
        self, 
        tool: MCPToolDefinition, 
        arguments: Dict[str, Any],
        timeout: int
    ) -> Any:
        """直接执行"""
        # 通过 MCP 客户端执行
        # 这里需要调用 MCP 服务器的执行方法
        pass
    
    def _build_command(
        self, 
        tool: MCPToolDefinition, 
        arguments: Dict[str, Any]
    ) -> str:
        """构建执行命令"""
        # 根据工具类型构建命令
        return f"execute-tool --name {tool.name} --args '{json.dumps(arguments)}'"
```

### 5. MCPIntegrationLayer (MCP 集成层)

整合所有组件，提供统一的工具集成接口。

```python
class MCPIntegrationLayer:
    """MCP 集成层"""
    
    def __init__(self, config_path: str = None):
        self.registry = MCPRegistry()
        self.permission_manager = ToolPermissionManager()
        self.tool_executor = ToolExecutor()
        self.config_path = config_path
        
        if config_path:
            self._load_config(config_path)
    
    def _load_config(self, config_path: str):
        """加载配置"""
        import yaml
        with open(config_path, 'r') as f:
            config = yaml.safe_load(f)
        
        # 加载服务器配置
        for server_config in config.get("servers", []):
            self.registry.register_server(MCPServerConfig(**server_config))
        
        # 加载权限配置
        for perm_config in config.get("permissions", []):
            self.permission_manager.add_permission(
                ToolPermission(**perm_config)
            )
    
    async def discover_servers(self):
        """自动发现 MCP 服务器"""
        # 扫描配置文件
        config_files = [
            ".mcp.json",
            ".vortocode/mcp.json",
            "~/.config/vortocode/mcp.json"
        ]
        
        for config_file in config_files:
            if os.path.exists(config_file):
                self._load_config(config_file)
    
    async def execute_tool(
        self, 
        tool_name: str, 
        arguments: Dict[str, Any],
        agent_id: str = None
    ) -> Any:
        """执行工具"""
        # 1. 权限检查
        permission = self.permission_manager.check_permission(
            tool_name, arguments
        )
        
        if permission == PermissionLevel.DENY:
            raise PermissionDeniedError(tool_name)
        
        if permission == PermissionLevel.ASK:
            # 需要用户确认
            confirmed = await self._ask_user_permission(
                tool_name, arguments
            )
            if not confirmed:
                raise PermissionDeniedError(tool_name)
        
        # 2. 执行工具
        try:
            result = await self.registry.execute_tool(tool_name, arguments)
            
            # 3. 记录执行日志
            await self._log_execution(tool_name, arguments, result, agent_id)
            
            return result
        except Exception as e:
            # 4. 记录错误
            await self._log_error(tool_name, arguments, e, agent_id)
            raise
    
    async def _ask_user_permission(
        self, 
        tool_name: str, 
        arguments: Dict[str, Any]
    ) -> bool:
        """询问用户权限"""
        # 通过 Web 控制台或 CLI 询问用户
        # ...
        return True
    
    async def _log_execution(
        self, 
        tool_name: str, 
        arguments: Dict[str, Any],
        result: Any,
        agent_id: str
    ):
        """记录执行日志"""
        log_entry = {
            "timestamp": datetime.now().isoformat(),
            "tool": tool_name,
            "arguments": arguments,
            "result_summary": str(result)[:200],
            "agent_id": agent_id
        }
        # 写入日志文件或数据库
        pass
    
    async def _log_error(
        self, 
        tool_name: str, 
        arguments: Dict[str, Any],
        error: Exception,
        agent_id: str
    ):
        """记录错误日志"""
        log_entry = {
            "timestamp": datetime.now().isoformat(),
            "tool": tool_name,
            "arguments": arguments,
            "error": str(error),
            "agent_id": agent_id
        }
        # 写入错误日志
        pass
```

## 预置 MCP 服务器

### 1. 文件系统服务器

```json
{
  "name": "filesystem",
  "command": "npx",
  "args": ["-y", "@modelcontextprotocol/server-filesystem", "/path/to/workspace"],
  "transport": "stdio"
}
```

提供工具：
- `read_file`: 读取文件
- `write_file`: 写入文件
- `list_directory`: 列出目录
- `search_files`: 搜索文件
- `get_file_info`: 获取文件信息

### 2. Git 服务器

```json
{
  "name": "git",
  "command": "npx",
  "args": ["-y", "@modelcontextprotocol/server-git"],
  "transport": "stdio"
}
```

提供工具：
- `git_status`: 获取 Git 状态
- `git_diff`: 获取差异
- `git_commit`: 提交更改
- `git_branch`: 分支操作
- `git_log`: 查看日志

### 3. 浏览器服务器

```json
{
  "name": "playwright",
  "command": "npx",
  "args": ["-y", "@playwright/mcp@latest"],
  "transport": "stdio"
}
```

提供工具：
- `navigate`: 导航到 URL
- `screenshot`: 截图
- `click`: 点击元素
- `fill`: 填写表单
- `evaluate`: 执行 JavaScript

### 4. 数据库服务器

```json
{
  "name": "database",
  "command": "npx",
  "args": ["-y", "@modelcontextprotocol/server-postgres"],
  "env": {
    "DATABASE_URL": "postgresql://user:pass@localhost/db"
  },
  "transport": "stdio"
}
```

提供工具：
- `query`: 执行 SQL 查询
- `list_tables`: 列出表
- `describe_table`: 描述表结构
- `explain_query`: 解释查询计划

## 配置文件格式

### .mcp.json

```json
{
  "servers": [
    {
      "name": "filesystem",
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-filesystem", "."],
      "transport": "stdio"
    },
    {
      "name": "git",
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-git"],
      "transport": "stdio"
    }
  ],
  "permissions": [
    {
      "tool_pattern": "filesystem:read_file",
      "permission": "allow"
    },
    {
      "tool_pattern": "filesystem:write_file",
      "permission": "ask"
    },
    {
      "tool_pattern": "git:*",
      "permission": "allow"
    }
  ]
}
```

## 使用示例

```python
# 初始化集成层
mcp = MCPIntegrationLayer(".mcp.json")

# 发现服务器
await mcp.discover_servers()

# 执行工具
result = await mcp.execute_tool(
    "filesystem:read_file",
    {"path": "src/main.py"},
    agent_id="developer-1"
)

print(f"文件内容: {result}")

# 执行 Git 操作
status = await mcp.execute_tool(
    "git:git_status",
    {},
    agent_id="developer-1"
)

print(f"Git 状态: {status}")
```

## 安全考虑

### 1. 权限控制

- 默认拒绝所有工具访问
- 需要显式授权才能使用
- 支持细粒度的权限控制

### 2. 沙箱隔离

- 工具在隔离环境中执行
- 限制资源使用（CPU、内存、网络）
- 防止恶意代码执行

### 3. 审计日志

- 记录所有工具调用
- 包含调用者、参数、结果
- 支持日志分析和审计

### 4. 输入验证

- 验证工具参数格式
- 防止注入攻击
- 限制输入大小
