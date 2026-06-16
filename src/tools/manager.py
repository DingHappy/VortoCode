"""工具管理器"""

import asyncio
import logging
import yaml
from pathlib import Path
from typing import Any, Dict, List, Optional

from .mcp_client import MCPClient, MCPTransport, create_mcp_client
from .registry import ToolRegistry, Tool, ToolPermission, DynamicToolRegistry
from .permission import ToolPermissionManager, PermissionRule
from .executor import ToolExecutor, AsyncToolExecutor

logger = logging.getLogger(__name__)


class ToolManager:
    """工具管理器"""
    
    def __init__(self, config_path: Optional[str] = None):
        self.config_path = config_path
        self.config: Dict[str, Any] = {}
        
        # 初始化组件
        self.tool_registry = DynamicToolRegistry()
        self.permission_manager = ToolPermissionManager()
        self.tool_executor = AsyncToolExecutor(
            self.tool_registry,
            self.permission_manager
        )
        
        # MCP客户端
        self.mcp_clients: Dict[str, MCPClient] = {}
        
        # 加载配置
        if config_path:
            self.load_config(config_path)
    
    def load_config(self, config_path: str) -> bool:
        """加载配置文件"""
        try:
            with open(config_path, 'r', encoding='utf-8') as f:
                self.config = yaml.safe_load(f)
            
            logger.info(f"Loaded tool config from {config_path}")
            return True
        except Exception as e:
            logger.error(f"Failed to load config: {e}")
            return False
    
    async def initialize(self) -> bool:
        """初始化工具管理器"""
        try:
            # 加载MCP服务器配置
            await self._load_mcp_servers()
            
            # 加载权限规则
            self._load_permission_rules()
            
            logger.info("Tool manager initialized successfully")
            return True
        except Exception as e:
            logger.error(f"Failed to initialize tool manager: {e}")
            return False
    
    async def _load_mcp_servers(self) -> None:
        """加载MCP服务器"""
        servers_config = self.config.get("servers", [])
        
        for server_config in servers_config:
            if not server_config.get("enabled", True):
                continue
            
            try:
                server_name = server_config.get("name")
                transport = MCPTransport(server_config.get("transport", "stdio"))
                
                # 创建客户端
                if transport == MCPTransport.STDIO:
                    client = create_mcp_client(
                        server_name,
                        transport,
                        command=server_config.get("command"),
                        args=server_config.get("args", []),
                        env=server_config.get("env", {})
                    )
                elif transport == MCPTransport.HTTP:
                    client = create_mcp_client(
                        server_name,
                        transport,
                        base_url=server_config.get("url"),
                        headers=server_config.get("headers", {})
                    )
                else:
                    logger.warning(f"Unsupported transport for server {server_name}: {transport}")
                    continue
                
                # 注册服务器
                success = await self.tool_registry.register_mcp_server(client)
                if success:
                    self.mcp_clients[server_name] = client
                    logger.info(f"Loaded MCP server: {server_name}")
                else:
                    logger.warning(f"Failed to load MCP server: {server_name}")
                    
            except Exception as e:
                logger.error(f"Error loading MCP server: {e}")
    
    def _load_permission_rules(self) -> None:
        """加载权限规则"""
        permissions_config = self.config.get("permissions", {})
        rules_config = permissions_config.get("rules", [])
        
        for rule_config in rules_config:
            rule = PermissionRule(**rule_config)
            self.permission_manager.add_rule(rule)
        
        logger.info(f"Loaded {len(rules_config)} permission rules")
    
    async def execute_tool(
        self,
        tool_name: str,
        arguments: Dict[str, Any],
        agent_role: str = "",
        timeout: Optional[float] = None
    ) -> Any:
        """执行工具"""
        if timeout is None:
            timeout = self.config.get("execution", {}).get("default_timeout", 30)
        
        result = await self.tool_executor.execute(
            tool_name,
            arguments,
            agent_role,
            timeout
        )
        
        return result
    
    async def execute_batch(
        self,
        tool_calls: List[Dict[str, Any]],
        agent_role: str = "",
        max_concurrent: Optional[int] = None
    ) -> List[Any]:
        """批量执行工具"""
        if max_concurrent is None:
            max_concurrent = self.config.get("execution", {}).get("max_concurrent", 5)
        
        results = await self.tool_executor.execute_batch(
            tool_calls,
            agent_role,
            max_concurrent
        )
        
        return results
    
    def list_tools(
        self,
        category: Optional[str] = None,
        server_name: Optional[str] = None,
        enabled_only: bool = True
    ) -> List[Tool]:
        """列出工具"""
        return self.tool_registry.list_tools(
            category=category,
            server_name=server_name,
            enabled_only=enabled_only
        )
    
    def get_tool(self, tool_name: str) -> Optional[Tool]:
        """获取工具"""
        return self.tool_registry.get(tool_name)
    
    def search_tools(self, query: str) -> List[Tool]:
        """搜索工具"""
        return self.tool_registry.search_tools(query)
    
    async def refresh_tools(self, server_name: Optional[str] = None) -> Dict[str, bool]:
        """刷新工具"""
        if server_name:
            success = await self.tool_registry.refresh_server_tools(server_name)
            return {server_name: success}
        else:
            return await self.tool_registry.refresh_all_servers()
    
    def get_statistics(self) -> Dict[str, Any]:
        """获取统计信息"""
        return {
            "tools": {
                "total": len(self.tool_registry.tools),
                "categories": len(self.tool_registry.categories),
                "servers": len(self.tool_registry.servers)
            },
            "execution": self.tool_executor.get_statistics(),
            "mcp_servers": {
                "total": len(self.mcp_clients),
                "connected": sum(1 for c in self.mcp_clients.values() if c.connected)
            }
        }
    
    async def shutdown(self) -> None:
        """关闭工具管理器"""
        # 断开所有MCP服务器
        await self.tool_registry.disconnect_all()
        
        # 取消所有执行
        await self.tool_executor.cancel_all()
        
        logger.info("Tool manager shut down")


class ToolManagerFactory:
    """工具管理器工厂"""
    
    @staticmethod
    def create(config_path: Optional[str] = None) -> ToolManager:
        """创建工具管理器"""
        return ToolManager(config_path)
    
    @staticmethod
    def create_default() -> ToolManager:
        """创建默认工具管理器"""
        default_config_path = Path(__file__).parent.parent.parent / "config" / "mcp.yaml"
        if default_config_path.exists():
            return ToolManager(str(default_config_path))
        else:
            return ToolManager()
