"""工具注册表"""

import logging
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Set
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class ToolPermission(str, Enum):
    """工具权限级别"""
    READ = "read"          # 只读操作
    WRITE = "write"        # 写操作
    EXECUTE = "execute"    # 执行命令
    ADMIN = "admin"        # 管理员权限


class Tool(BaseModel):
    """工具定义"""
    name: str
    description: str = ""
    category: str = "general"
    permissions: List[ToolPermission] = Field(default_factory=lambda: [ToolPermission.READ])
    input_schema: Dict[str, Any] = Field(default_factory=dict)
    output_schema: Dict[str, Any] = Field(default_factory=dict)
    server_name: str = ""  # MCP服务器名称
    server_url: str = ""   # MCP服务器URL
    enabled: bool = True
    version: str = "1.0.0"
    tags: List[str] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)


class ToolRegistry:
    """工具注册表"""
    
    def __init__(self):
        self.tools: Dict[str, Tool] = {}
        self.categories: Dict[str, List[str]] = {}  # category -> tool names
        self.servers: Dict[str, List[str]] = {}  # server_name -> tool names
    
    def register(self, tool: Tool) -> bool:
        """注册工具"""
        if tool.name in self.tools:
            logger.warning(f"Tool {tool.name} already registered, overwriting")
        
        self.tools[tool.name] = tool
        
        # 更新分类索引
        if tool.category not in self.categories:
            self.categories[tool.category] = []
        if tool.name not in self.categories[tool.category]:
            self.categories[tool.category].append(tool.name)
        
        # 更新服务器索引
        if tool.server_name:
            if tool.server_name not in self.servers:
                self.servers[tool.server_name] = []
            if tool.name not in self.servers[tool.server_name]:
                self.servers[tool.server_name].append(tool.name)
        
        logger.info(f"Registered tool: {tool.name}")
        return True
    
    def unregister(self, tool_name: str) -> bool:
        """注销工具"""
        if tool_name not in self.tools:
            logger.warning(f"Tool {tool_name} not found")
            return False
        
        tool = self.tools[tool_name]
        
        # 从分类索引中移除
        if tool.category in self.categories:
            if tool_name in self.categories[tool.category]:
                self.categories[tool.category].remove(tool_name)
            if not self.categories[tool.category]:
                del self.categories[tool.category]
        
        # 从服务器索引中移除
        if tool.server_name and tool.server_name in self.servers:
            if tool_name in self.servers[tool.server_name]:
                self.servers[tool.server_name].remove(tool_name)
            if not self.servers[tool.server_name]:
                del self.servers[tool.server_name]
        
        del self.tools[tool_name]
        logger.info(f"Unregistered tool: {tool_name}")
        return True
    
    def get(self, tool_name: str) -> Optional[Tool]:
        """获取工具"""
        return self.tools.get(tool_name)
    
    def list_tools(
        self,
        category: Optional[str] = None,
        server_name: Optional[str] = None,
        permissions: Optional[List[ToolPermission]] = None,
        enabled_only: bool = True
    ) -> List[Tool]:
        """列出工具"""
        tools = list(self.tools.values())
        
        if enabled_only:
            tools = [t for t in tools if t.enabled]
        
        if category:
            tools = [t for t in tools if t.category == category]
        
        if server_name:
            tools = [t for t in tools if t.server_name == server_name]
        
        if permissions:
            tools = [
                t for t in tools 
                if any(p in t.permissions for p in permissions)
            ]
        
        return tools
    
    def list_categories(self) -> List[str]:
        """列出所有分类"""
        return list(self.categories.keys())
    
    def list_servers(self) -> List[str]:
        """列出所有服务器"""
        return list(self.servers.keys())
    
    def get_tools_by_category(self, category: str) -> List[Tool]:
        """获取分类下的工具"""
        tool_names = self.categories.get(category, [])
        return [self.tools[name] for name in tool_names if name in self.tools]
    
    def get_tools_by_server(self, server_name: str) -> List[Tool]:
        """获取服务器提供的工具"""
        tool_names = self.servers.get(server_name, [])
        return [self.tools[name] for name in tool_names if name in self.tools]
    
    def search_tools(self, query: str) -> List[Tool]:
        """搜索工具"""
        query_lower = query.lower()
        results = []
        
        for tool in self.tools.values():
            if (query_lower in tool.name.lower() or 
                query_lower in tool.description.lower() or
                any(query_lower in tag.lower() for tag in tool.tags)):
                results.append(tool)
        
        return results
    
    def enable_tool(self, tool_name: str) -> bool:
        """启用工具"""
        if tool_name in self.tools:
            self.tools[tool_name].enabled = True
            return True
        return False
    
    def disable_tool(self, tool_name: str) -> bool:
        """禁用工具"""
        if tool_name in self.tools:
            self.tools[tool_name].enabled = False
            return True
        return False
    
    def update_tool(self, tool_name: str, updates: Dict[str, Any]) -> bool:
        """更新工具信息"""
        if tool_name not in self.tools:
            return False
        
        tool = self.tools[tool_name]
        for key, value in updates.items():
            if hasattr(tool, key):
                setattr(tool, key, value)
        
        return True
    
    def clear(self) -> None:
        """清空注册表"""
        self.tools.clear()
        self.categories.clear()
        self.servers.clear()
    
    def export_tools(self, file_path: str) -> bool:
        """导出工具列表到文件"""
        try:
            import json
            data = {
                "tools": [tool.model_dump() for tool in self.tools.values()],
                "categories": self.categories,
                "servers": self.servers
            }
            
            with open(file_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            
            return True
        except Exception as e:
            logger.error(f"Failed to export tools: {e}")
            return False
    
    def import_tools(self, file_path: str) -> bool:
        """从文件导入工具列表"""
        try:
            import json
            
            with open(file_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            for tool_data in data.get("tools", []):
                tool = Tool(**tool_data)
                self.register(tool)
            
            return True
        except Exception as e:
            logger.error(f"Failed to import tools: {e}")
            return False


class DynamicToolRegistry(ToolRegistry):
    """动态工具注册表，支持从MCP服务器动态加载工具"""
    
    def __init__(self):
        super().__init__()
        self.mcp_clients: Dict[str, Any] = {}  # server_name -> MCPClient
    
    async def register_mcp_server(self, client: Any) -> bool:
        """注册MCP服务器并加载其工具"""
        try:
            if not await client.connect():
                return False
            
            self.mcp_clients[client.server_name] = client
            
            # 加载服务器提供的工具
            tools = await client.list_tools()
            for mcp_tool in tools:
                tool = Tool(
                    name=mcp_tool.name,
                    description=mcp_tool.description,
                    category="mcp",
                    permissions=[ToolPermission.EXECUTE],
                    input_schema=mcp_tool.input_schema,
                    server_name=mcp_tool.server_name,
                    server_url=mcp_tool.server_url,
                    tags=["mcp", "dynamic"]
                )
                self.register(tool)
            
            logger.info(f"Registered MCP server {client.server_name} with {len(tools)} tools")
            return True
            
        except Exception as e:
            logger.error(f"Failed to register MCP server: {e}")
            return False
    
    async def unregister_mcp_server(self, server_name: str) -> bool:
        """注销MCP服务器及其工具"""
        if server_name not in self.mcp_clients:
            return False
        
        client = self.mcp_clients[server_name]
        
        # 移除服务器提供的工具
        tools_to_remove = self.get_tools_by_server(server_name)
        for tool in tools_to_remove:
            self.unregister(tool.name)
        
        # 断开连接
        await client.disconnect()
        del self.mcp_clients[server_name]
        
        logger.info(f"Unregistered MCP server: {server_name}")
        return True
    
    async def refresh_server_tools(self, server_name: str) -> bool:
        """刷新服务器工具"""
        if server_name not in self.mcp_clients:
            return False
        
        client = self.mcp_clients[server_name]
        
        # 移除旧工具
        old_tools = self.get_tools_by_server(server_name)
        for tool in old_tools:
            self.unregister(tool.name)
        
        # 加载新工具
        tools = await client.list_tools()
        for mcp_tool in tools:
            tool = Tool(
                name=mcp_tool.name,
                description=mcp_tool.description,
                category="mcp",
                permissions=[ToolPermission.EXECUTE],
                input_schema=mcp_tool.input_schema,
                server_name=mcp_tool.server_name,
                server_url=mcp_tool.server_url,
                tags=["mcp", "dynamic"]
            )
            self.register(tool)
        
        logger.info(f"Refreshed tools for server {server_name}: {len(tools)} tools")
        return True
    
    async def refresh_all_servers(self) -> Dict[str, bool]:
        """刷新所有服务器工具"""
        results = {}
        for server_name in self.mcp_clients:
            results[server_name] = await self.refresh_server_tools(server_name)
        return results
    
    async def call_tool(self, tool_name: str, arguments: Dict[str, Any]) -> Any:
        """调用工具"""
        tool = self.get(tool_name)
        if not tool:
            raise ValueError(f"Tool {tool_name} not found")
        
        if not tool.server_name:
            raise ValueError(f"Tool {tool_name} is not from an MCP server")
        
        client = self.mcp_clients.get(tool.server_name)
        if not client:
            raise ValueError(f"MCP server {tool.server_name} not connected")
        
        from .mcp_client import MCPToolCall
        tool_call = MCPToolCall(
            tool_name=tool_name,
            arguments=arguments
        )
        
        result = await client.call_tool(tool_call)
        return result
    
    async def disconnect_all(self) -> None:
        """断开所有MCP服务器"""
        for server_name, client in self.mcp_clients.items():
            try:
                await client.disconnect()
            except Exception as e:
                logger.warning(f"Error disconnecting server {server_name}: {e}")
        
        self.mcp_clients.clear()
        self.clear()
