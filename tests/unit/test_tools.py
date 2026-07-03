"""工具集成测试"""

import pytest

from src.tools import (
    MCPTransport, ToolRegistry, Tool, ToolPermission,
    ToolPermissionManager, PermissionRule,
    ToolExecutor, ToolManager
)


class TestToolRegistry:
    """工具注册表测试"""
    
    def test_register_tool(self):
        """测试注册工具"""
        registry = ToolRegistry()
        tool = Tool(
            name="test_tool",
            description="Test tool",
            category="test",
            permissions=[ToolPermission.READ]
        )
        
        result = registry.register(tool)
        assert result is True
        assert "test_tool" in registry.tools
    
    def test_unregister_tool(self):
        """测试注销工具"""
        registry = ToolRegistry()
        tool = Tool(
            name="test_tool",
            description="Test tool",
            category="test",
            permissions=[ToolPermission.READ]
        )
        
        registry.register(tool)
        result = registry.unregister("test_tool")
        assert result is True
        assert "test_tool" not in registry.tools
    
    def test_list_tools(self):
        """测试列出工具"""
        registry = ToolRegistry()
        
        tool1 = Tool(name="tool1", category="cat1")
        tool2 = Tool(name="tool2", category="cat2")
        tool3 = Tool(name="tool3", category="cat1")
        
        registry.register(tool1)
        registry.register(tool2)
        registry.register(tool3)
        
        # 列出所有工具
        all_tools = registry.list_tools()
        assert len(all_tools) == 3
        
        # 按分类列出
        cat1_tools = registry.list_tools(category="cat1")
        assert len(cat1_tools) == 2
        
        cat2_tools = registry.list_tools(category="cat2")
        assert len(cat2_tools) == 1
    
    def test_search_tools(self):
        """测试搜索工具"""
        registry = ToolRegistry()
        
        tool1 = Tool(name="file_reader", description="Read files")
        tool2 = Tool(name="file_writer", description="Write files")
        tool3 = Tool(name="git_commit", description="Git commit")
        
        registry.register(tool1)
        registry.register(tool2)
        registry.register(tool3)
        
        # 搜索文件相关工具
        file_tools = registry.search_tools("file")
        assert len(file_tools) == 2
        
        # 搜索git相关工具
        git_tools = registry.search_tools("git")
        assert len(git_tools) == 1


class TestToolPermissionManager:
    """工具权限管理器测试"""
    
    def test_default_permissions(self):
        """测试默认权限"""
        manager = ToolPermissionManager()
        
        # 检查默认规则
        assert len(manager.rules) > 0
    
    def test_add_rule(self):
        """测试添加规则"""
        manager = ToolPermissionManager()
        
        # 创建工具注册表并注册工具
        registry = ToolRegistry()
        tool = Tool(name="test_tool", description="Test tool")
        registry.register(tool)
        
        rule = PermissionRule(
            id="test_rule",
            name="Test Rule",
            action="allow",
            tool_names=["test_tool"]
        )
        
        manager.add_rule(rule)
        
        # 检查规则是否添加
        rules = manager.get_rules_for_tool("test_tool", registry)
        assert len(rules) > 0
        assert any(r.id == "test_rule" for r in rules)
    
    def test_remove_rule(self):
        """测试移除规则"""
        manager = ToolPermissionManager()
        
        rule = PermissionRule(
            id="test_rule",
            name="Test Rule",
            action="allow",
            tool_names=["test_tool"]
        )
        
        manager.add_rule(rule)
        result = manager.remove_rule("test_rule")
        
        assert result is True
        
        # 检查规则是否移除
        rules = manager.get_rules_for_tool("test_tool")
        assert not any(r.id == "test_rule" for r in rules)


class TestToolExecutor:
    """工具执行器测试"""
    
    @pytest.mark.asyncio
    async def test_execute_tool(self):
        """测试执行工具"""
        registry = ToolRegistry()
        permission_manager = ToolPermissionManager()
        executor = ToolExecutor(registry, permission_manager)
        
        # 注册工具
        tool = Tool(
            name="test_tool",
            description="Test tool",
            category="test",
            permissions=[ToolPermission.EXECUTE]
        )
        registry.register(tool)
        
        # 添加允许规则
        rule = PermissionRule(
            id="test_rule",
            name="Test Rule",
            action="allow",
            tool_names=["test_tool"]
        )
        permission_manager.add_rule(rule)
        
        # 执行工具
        result = await executor.execute("test_tool", {"arg": "value"})
        
        assert result.success is True
        assert result.tool_name == "test_tool"
    
    @pytest.mark.asyncio
    async def test_execute_nonexistent_tool(self):
        """测试执行不存在的工具"""
        registry = ToolRegistry()
        permission_manager = ToolPermissionManager()
        executor = ToolExecutor(registry, permission_manager)
        
        # 执行不存在的工具
        result = await executor.execute("nonexistent_tool", {})
        
        assert result.success is False
        assert "not found" in result.error


class TestToolManager:
    """工具管理器测试"""
    
    def test_create_manager(self):
        """测试创建管理器"""
        manager = ToolManager()
        
        assert manager.tool_registry is not None
        assert manager.permission_manager is not None
        assert manager.tool_executor is not None
    
    def test_list_tools(self):
        """测试列出工具"""
        manager = ToolManager()
        
        # 注册工具
        tool = Tool(name="test_tool", description="Test tool")
        manager.tool_registry.register(tool)
        
        tools = manager.list_tools()
        assert len(tools) == 1
        assert tools[0].name == "test_tool"
    
    def test_search_tools(self):
        """测试搜索工具"""
        manager = ToolManager()
        
        # 注册工具
        tool1 = Tool(name="file_reader", description="Read files")
        tool2 = Tool(name="git_commit", description="Git commit")
        
        manager.tool_registry.register(tool1)
        manager.tool_registry.register(tool2)
        
        # 搜索工具
        results = manager.search_tools("file")
        assert len(results) == 1
        assert results[0].name == "file_reader"
    
    def test_get_statistics(self):
        """测试获取统计信息"""
        manager = ToolManager()
        
        # 注册工具
        tool = Tool(name="test_tool", description="Test tool")
        manager.tool_registry.register(tool)
        
        stats = manager.get_statistics()
        
        assert "tools" in stats
        assert "execution" in stats
        assert stats["tools"]["total"] == 1


class TestMCPClient:
    """MCP客户端测试"""
    
    def test_create_stdio_client(self):
        """测试创建stdio客户端"""
        from src.tools.mcp_client import create_mcp_client
        
        client = create_mcp_client(
            "test_server",
            MCPTransport.STDIO,
            command="echo",
            args=["hello"]
        )
        
        assert client.server_name == "test_server"
        assert client.transport == MCPTransport.STDIO
    
    def test_create_http_client(self):
        """测试创建HTTP客户端"""
        from src.tools.mcp_client import create_mcp_client
        
        client = create_mcp_client(
            "test_server",
            MCPTransport.HTTP,
            base_url="http://localhost:8080"
        )
        
        assert client.server_name == "test_server"
        assert client.transport == MCPTransport.HTTP


if __name__ == "__main__":
    pytest.main([__file__])
