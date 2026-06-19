"""工具权限管理"""

import logging
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Set
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class PermissionAction(str, Enum):
    """权限动作"""
    ALLOW = "allow"
    DENY = "deny"
    ASK = "ask"  # 需要用户确认


class PermissionScope(str, Enum):
    """权限范围"""
    GLOBAL = "global"      # 全局权限
    AGENT = "agent"        # Agent级别
    TOOL = "tool"          # 工具级别
    SERVER = "server"      # 服务器级别


class PermissionRule(BaseModel):
    """权限规则"""
    id: str = ""
    name: str = ""
    description: str = ""
    scope: PermissionScope = PermissionScope.GLOBAL
    action: PermissionAction = PermissionAction.ALLOW
    
    # 匹配条件
    agent_roles: List[str] = Field(default_factory=list)  # 适用于哪些Agent角色
    tool_names: List[str] = Field(default_factory=list)    # 适用于哪些工具
    tool_categories: List[str] = Field(default_factory=list)  # 适用于哪些工具分类
    server_names: List[str] = Field(default_factory=list)  # 适用于哪些服务器
    
    # 权限限制
    max_calls_per_minute: Optional[int] = None
    max_calls_per_hour: Optional[int] = None
    allowed_arguments: Optional[Dict[str, Any]] = None  # 允许的参数
    blocked_arguments: Optional[Dict[str, Any]] = None  # 禁止的参数
    
    # 时间限制
    valid_from: Optional[str] = None  # ISO格式时间
    valid_until: Optional[str] = None
    
    priority: int = 0  # 优先级，数字越大优先级越高
    enabled: bool = True


class PermissionCheckResult(BaseModel):
    """权限检查结果"""
    allowed: bool
    rule_id: Optional[str] = None
    reason: str = ""
    requires_confirmation: bool = False


class ToolPermissionManager:
    """工具权限管理器"""
    
    def __init__(self, config_path: Optional[str] = None):
        self.rules: List[PermissionRule] = []
        self.config_path = config_path
        self.call_counts: Dict[str, Dict[str, int]] = {}  # tool_name -> {minute: count, hour: count}
        
        # 加载默认规则
        self._load_default_rules()
        
        # 加载配置文件
        if config_path and Path(config_path).exists():
            self._load_config(config_path)
    
    def _load_default_rules(self) -> None:
        """加载默认规则"""
        default_rules = [
            # 允许读取操作
            PermissionRule(
                id="default-read",
                name="Default Read Permission",
                description="Allow read operations by default",
                scope=PermissionScope.GLOBAL,
                action=PermissionAction.ALLOW,
                tool_categories=["read", "file_read", "search"],
                priority=0
            ),
            # 需要确认写入操作
            PermissionRule(
                id="default-write",
                name="Default Write Permission",
                description="Ask for confirmation for write operations",
                scope=PermissionScope.GLOBAL,
                action=PermissionAction.ASK,
                tool_categories=["write", "file_write", "edit"],
                priority=0
            ),
            # 需要确认执行操作
            PermissionRule(
                id="default-execute",
                name="Default Execute Permission",
                description="Ask for confirmation for execute operations",
                scope=PermissionScope.GLOBAL,
                action=PermissionAction.ASK,
                tool_categories=["execute", "command", "shell"],
                priority=0
            ),
            # 禁止危险操作
            PermissionRule(
                id="block-dangerous",
                name="Block Dangerous Operations",
                description="Block potentially dangerous operations",
                scope=PermissionScope.GLOBAL,
                action=PermissionAction.DENY,
                tool_names=["rm", "rmdir", "format", "mkfs"],
                priority=100
            ),
        ]
        
        self.rules.extend(default_rules)
    
    def _load_config(self, config_path: str) -> None:
        """加载配置文件"""
        try:
            import yaml
            with open(config_path, 'r', encoding='utf-8') as f:
                config = yaml.safe_load(f)
            
            for rule_data in config.get("rules", []):
                rule = PermissionRule(**rule_data)
                self.rules.append(rule)
            
            logger.info(f"Loaded {len(config.get('rules', []))} permission rules from {config_path}")
        except Exception as e:
            logger.error(f"Failed to load permission config: {e}")
    
    def add_rule(self, rule: PermissionRule) -> None:
        """添加规则"""
        # 移除同ID规则
        self.rules = [r for r in self.rules if r.id != rule.id]
        self.rules.append(rule)
        self.rules.sort(key=lambda r: r.priority, reverse=True)
        logger.info(f"Added permission rule: {rule.id}")
    
    def remove_rule(self, rule_id: str) -> bool:
        """移除规则"""
        original_count = len(self.rules)
        self.rules = [r for r in self.rules if r.id != rule_id]
        if len(self.rules) < original_count:
            logger.info(f"Removed permission rule: {rule_id}")
            return True
        return False
    
    def check_permission(
        self,
        tool_name: str,
        agent_role: str = "",
        arguments: Optional[Dict[str, Any]] = None,
        tool_registry: Optional[Any] = None
    ) -> PermissionCheckResult:
        """检查权限"""
        from .registry import ToolRegistry
        
        # 获取工具信息
        if tool_registry is None:
            tool_registry = ToolRegistry()
        tool = tool_registry.get(tool_name)
        
        if not tool:
            return PermissionCheckResult(
                allowed=False,
                reason=f"Tool {tool_name} not found"
            )
        
        # 检查工具是否启用
        if not tool.enabled:
            return PermissionCheckResult(
                allowed=False,
                reason=f"Tool {tool_name} is disabled"
            )
        
        # 检查调用频率
        if not self._check_rate_limit(tool_name):
            return PermissionCheckResult(
                allowed=False,
                reason=f"Rate limit exceeded for tool {tool_name}"
            )
        
        # 应用规则
        for rule in self.rules:
            if not rule.enabled:
                continue
            
            if self._rule_matches(rule, tool, agent_role, arguments):
                result = PermissionCheckResult(
                    allowed=rule.action == PermissionAction.ALLOW,
                    rule_id=rule.id,
                    reason=f"Matched rule: {rule.name}",
                    requires_confirmation=rule.action == PermissionAction.ASK
                )
                
                # 记录调用
                self._record_call(tool_name)
                
                return result
        
        # 默认拒绝
        return PermissionCheckResult(
            allowed=False,
            reason="No matching permission rule"
        )
    
    def _rule_matches(
        self,
        rule: PermissionRule,
        tool: Any,
        agent_role: str,
        arguments: Optional[Dict[str, Any]]
    ) -> bool:
        """检查规则是否匹配"""
        # 检查Agent角色
        if rule.agent_roles and agent_role not in rule.agent_roles:
            return False
        
        # 检查工具名称
        if rule.tool_names and tool.name not in rule.tool_names:
            return False
        
        # 检查工具分类
        if rule.tool_categories and tool.category not in rule.tool_categories:
            return False
        
        # 检查服务器名称
        if rule.server_names and tool.server_name not in rule.server_names:
            return False
        
        # 检查参数限制
        if arguments:
            if rule.blocked_arguments:
                for key, value in rule.blocked_arguments.items():
                    if key in arguments and arguments[key] == value:
                        return False
            
            if rule.allowed_arguments:
                for key, value in rule.allowed_arguments.items():
                    if key in arguments and arguments[key] != value:
                        return False
        
        return True
    
    def _check_rate_limit(self, tool_name: str) -> bool:
        """检查调用频率限制"""
        if tool_name not in self.call_counts:
            return True
        
        counts = self.call_counts[tool_name]
        
        # 检查每分钟限制
        minute_count = counts.get("minute", 0)
        if minute_count > 60:  # 默认每分钟60次
            return False
        
        # 检查每小时限制
        hour_count = counts.get("hour", 0)
        if hour_count > 1000:  # 默认每小时1000次
            return False
        
        return True
    
    def _record_call(self, tool_name: str) -> None:
        """记录调用"""
        if tool_name not in self.call_counts:
            self.call_counts[tool_name] = {"minute": 0, "hour": 0}
        
        self.call_counts[tool_name]["minute"] += 1
        self.call_counts[tool_name]["hour"] += 1
    
    def reset_rate_limits(self) -> None:
        """重置调用频率计数"""
        self.call_counts.clear()
    
    def get_rules_for_tool(self, tool_name: str, tool_registry: Optional[Any] = None) -> List[PermissionRule]:
        """获取适用于工具的规则"""
        from .registry import ToolRegistry
        
        if tool_registry is None:
            tool_registry = ToolRegistry()
        tool = tool_registry.get(tool_name)
        
        if not tool:
            return []
        
        matching_rules = []
        for rule in self.rules:
            if not rule.enabled:
                continue
            
            if self._rule_matches(rule, tool, "", None):
                matching_rules.append(rule)
        
        return matching_rules
    
    def get_rules_for_agent(self, agent_role: str) -> List[PermissionRule]:
        """获取适用于Agent的规则"""
        matching_rules = []
        for rule in self.rules:
            if not rule.enabled:
                continue
            
            if not rule.agent_roles or agent_role in rule.agent_roles:
                matching_rules.append(rule)
        
        return matching_rules
    
    def export_rules(self, file_path: str) -> bool:
        """导出规则到文件"""
        try:
            import json
            data = {
                "rules": [rule.model_dump() for rule in self.rules]
            }
            
            with open(file_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            
            return True
        except Exception as e:
            logger.error(f"Failed to export rules: {e}")
            return False
    
    def import_rules(self, file_path: str) -> bool:
        """从文件导入规则"""
        try:
            import json
            
            with open(file_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            for rule_data in data.get("rules", []):
                rule = PermissionRule(**rule_data)
                self.add_rule(rule)
            
            return True
        except Exception as e:
            logger.error(f"Failed to import rules: {e}")
            return False


class AgentPermissionManager:
    """Agent权限管理器"""
    
    def __init__(self):
        self.agent_permissions: Dict[str, List[str]] = {}  # agent_role -> [tool_name]
        self.tool_permission_manager = ToolPermissionManager()
    
    def set_agent_permissions(self, agent_role: str, tool_names: List[str]) -> None:
        """设置Agent权限"""
        self.agent_permissions[agent_role] = tool_names
        logger.info(f"Set permissions for agent {agent_role}: {len(tool_names)} tools")
    
    def add_tool_permission(self, agent_role: str, tool_name: str) -> None:
        """添加工具权限"""
        if agent_role not in self.agent_permissions:
            self.agent_permissions[agent_role] = []
        
        if tool_name not in self.agent_permissions[agent_role]:
            self.agent_permissions[agent_role].append(tool_name)
    
    def remove_tool_permission(self, agent_role: str, tool_name: str) -> None:
        """移除工具权限"""
        if agent_role in self.agent_permissions:
            if tool_name in self.agent_permissions[agent_role]:
                self.agent_permissions[agent_role].remove(tool_name)
    
    def check_permission(
        self,
        agent_role: str,
        tool_name: str,
        arguments: Optional[Dict[str, Any]] = None
    ) -> PermissionCheckResult:
        """检查Agent是否有权限使用工具"""
        # 检查Agent级别权限
        if agent_role in self.agent_permissions:
            if tool_name not in self.agent_permissions[agent_role]:
                return PermissionCheckResult(
                    allowed=False,
                    reason=f"Agent {agent_role} does not have permission for tool {tool_name}"
                )
        
        # 检查工具级别权限
        return self.tool_permission_manager.check_permission(
            tool_name, agent_role, arguments
        )
    
    def get_agent_tools(self, agent_role: str) -> List[str]:
        """获取Agent可用的工具列表"""
        return self.agent_permissions.get(agent_role, [])
