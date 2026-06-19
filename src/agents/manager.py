"""高级 Agent 管理系统"""

import logging
import uuid
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class AgentStatus(str, Enum):
    """Agent 状态"""
    IDLE = "idle"
    WORKING = "working"
    THINKING = "thinking"
    ERROR = "error"
    DISABLED = "disabled"


class AgentCapability(str, Enum):
    """Agent 能力"""
    # 代码能力
    CODE_GENERATION = "code_generation"
    CODE_REVIEW = "code_review"
    CODE_REFACTORING = "code_refactoring"
    CODE_DEBUGGING = "code_debugging"
    
    # 测试能力
    UNIT_TESTING = "unit_testing"
    INTEGRATION_TESTING = "integration_testing"
    E2E_TESTING = "e2e_testing"
    
    # 设计能力
    ARCHITECTURE_DESIGN = "architecture_design"
    UI_DESIGN = "ui_design"
    DATABASE_DESIGN = "database_design"
    API_DESIGN = "api_design"
    
    # 分析能力
    REQUIREMENTS_ANALYSIS = "requirements_analysis"
    BUG_ANALYSIS = "bug_analysis"
    PERFORMANCE_ANALYSIS = "performance_analysis"
    SECURITY_AUDIT = "security_audit"
    
    # 其他能力
    DOCUMENTATION = "documentation"
    DEPLOYMENT = "deployment"
    TESTING = "testing"


class AgentConfig(BaseModel):
    """Agent 配置"""
    id: str = Field(default_factory=lambda: str(uuid.uuid4())[:8])
    name: str
    role: str
    description: str = ""
    avatar: str = "🤖"
    color: str = "#6366f1"
    
    # 能力配置
    capabilities: List[AgentCapability] = Field(default_factory=list)
    tools: List[str] = Field(default_factory=list)
    
    # 模型配置
    model: str = "mimo-v2.5"
    temperature: float = 0.7
    max_tokens: int = 4096
    
    # 行为配置
    system_prompt: str = ""
    max_iterations: int = 10
    timeout: int = 300
    
    # 元数据
    created_at: datetime = Field(default_factory=datetime.now)
    is_active: bool = True
    is_template: bool = False
    tags: List[str] = Field(default_factory=list)


class AgentPerformance(BaseModel):
    """Agent 性能统计"""
    total_tasks: int = 0
    successful_tasks: int = 0
    failed_tasks: int = 0
    total_tokens: int = 0
    total_duration: float = 0.0
    avg_task_duration: float = 0.0
    success_rate: float = 0.0
    last_active: Optional[datetime] = None


class AgentInstance:
    """Agent 实例"""
    
    def __init__(self, config: AgentConfig):
        self.config = config
        self.status = AgentStatus.IDLE
        self.current_task: Optional[str] = None
        self.progress: int = 0
        self.performance = AgentPerformance()
        self.history: List[Dict[str, Any]] = []
        self.context: Dict[str, Any] = {}
    
    def update_status(self, status: AgentStatus, task: str = None):
        """更新状态"""
        self.status = status
        self.current_task = task
        
        if status == AgentStatus.WORKING:
            self.performance.last_active = datetime.now()
    
    def update_progress(self, progress: int):
        """更新进度"""
        self.progress = min(100, max(0, progress))
    
    def record_task(self, success: bool, tokens: int = 0, duration: float = 0.0):
        """记录任务"""
        self.performance.total_tasks += 1
        self.performance.total_tokens += tokens
        self.performance.total_duration += duration
        
        if success:
            self.performance.successful_tasks += 1
        else:
            self.performance.failed_tasks += 1
        
        # 更新平均值
        if self.performance.total_tasks > 0:
            self.performance.avg_task_duration = (
                self.performance.total_duration / self.performance.total_tasks
            )
            self.performance.success_rate = (
                self.performance.successful_tasks / self.performance.total_tasks
            )
        
        # 记录历史
        self.history.append({
            "timestamp": datetime.now().isoformat(),
            "success": success,
            "tokens": tokens,
            "duration": duration
        })
        
        # 限制历史记录
        if len(self.history) > 100:
            self.history = self.history[-50:]
    
    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        return {
            "id": self.config.id,
            "name": self.config.name,
            "role": self.config.role,
            "description": self.config.description,
            "avatar": self.config.avatar,
            "color": self.config.color,
            "status": self.status.value,
            "current_task": self.current_task,
            "progress": self.progress,
            "capabilities": [c.value for c in self.config.capabilities],
            "tools": self.config.tools,
            "model": self.config.model,
            "is_active": self.config.is_active,
            "performance": {
                "total_tasks": self.performance.total_tasks,
                "success_rate": self.performance.success_rate,
                "avg_duration": self.performance.avg_task_duration,
                "total_tokens": self.performance.total_tokens
            },
            "tags": self.config.tags
        }


class AgentTemplate:
    """Agent 模板"""
    
    TEMPLATES = [
        {
            "name": "全栈开发工程师",
            "role": "developer",
            "description": "能够处理前端和后端开发任务",
            "avatar": "💻",
            "color": "#8b5cf6",
            "capabilities": [
                AgentCapability.CODE_GENERATION,
                AgentCapability.CODE_REVIEW,
                AgentCapability.UNIT_TESTING
            ],
            "tools": ["read_file", "write_file", "execute_command"],
            "system_prompt": "你是一个全栈开发工程师，擅长前后端开发。"
        },
        {
            "name": "前端专家",
            "role": "frontend",
            "description": "专注于前端开发和 UI/UX",
            "avatar": "🎨",
            "color": "#3b82f6",
            "capabilities": [
                AgentCapability.CODE_GENERATION,
                AgentCapability.UI_DESIGN
            ],
            "tools": ["read_file", "write_file"],
            "system_prompt": "你是一个前端专家，擅长 React、Vue 等框架。"
        },
        {
            "name": "后端专家",
            "role": "backend",
            "description": "专注于后端架构和 API 设计",
            "avatar": "⚙️",
            "color": "#10b981",
            "capabilities": [
                AgentCapability.CODE_GENERATION,
                AgentCapability.ARCHITECTURE_DESIGN,
                AgentCapability.API_DESIGN
            ],
            "tools": ["read_file", "write_file", "execute_command"],
            "system_prompt": "你是一个后端专家，擅长 API 设计和数据库。"
        },
        {
            "name": "测试工程师",
            "role": "tester",
            "description": "负责测试策略和自动化测试",
            "avatar": "🧪",
            "color": "#f59e0b",
            "capabilities": [
                AgentCapability.UNIT_TESTING,
                AgentCapability.INTEGRATION_TESTING,
                AgentCapability.E2E_TESTING
            ],
            "tools": ["read_file", "execute_command"],
            "system_prompt": "你是一个测试工程师，擅长自动化测试。"
        },
        {
            "name": "安全审计员",
            "role": "security",
            "description": "专注于代码安全和漏洞检测",
            "avatar": "🛡️",
            "color": "#ef4444",
            "capabilities": [
                AgentCapability.SECURITY_AUDIT,
                AgentCapability.CODE_REVIEW
            ],
            "tools": ["read_file"],
            "system_prompt": "你是一个安全审计员，擅长发现安全漏洞。"
        },
        {
            "name": "文档工程师",
            "role": "docs",
            "description": "负责技术文档和 API 文档",
            "avatar": "📚",
            "color": "#6366f1",
            "capabilities": [
                AgentCapability.DOCUMENTATION,
                AgentCapability.API_DESIGN
            ],
            "tools": ["read_file", "write_file"],
            "system_prompt": "你是一个文档工程师，擅长编写技术文档。"
        },
        {
            "name": "DevOps 工程师",
            "role": "devops",
            "description": "负责部署、运维和监控",
            "avatar": "🚀",
            "color": "#0ea5e9",
            "capabilities": [
                AgentCapability.DEPLOYMENT,
                AgentCapability.DATABASE_DESIGN
            ],
            "tools": ["read_file", "write_file", "execute_command"],
            "system_prompt": "你是一个 DevOps 工程师，擅长部署和运维。"
        },
        {
            "name": "性能优化专家",
            "role": "performance",
            "description": "专注于性能分析和优化",
            "avatar": "⚡",
            "color": "#f97316",
            "capabilities": [
                AgentCapability.PERFORMANCE_ANALYSIS,
                AgentCapability.CODE_REFACTORING
            ],
            "tools": ["read_file", "execute_command"],
            "system_prompt": "你是一个性能优化专家，擅长分析和优化性能。"
        }
    ]
    
    @classmethod
    def get_all(cls) -> List[Dict[str, Any]]:
        """获取所有模板"""
        return cls.TEMPLATES
    
    @classmethod
    def get_by_name(cls, name: str) -> Optional[Dict[str, Any]]:
        """根据名称获取模板"""
        for template in cls.TEMPLATES:
            if template["name"] == name:
                return template
        return None
    
    @classmethod
    def create_from_template(cls, name: str) -> Optional[AgentConfig]:
        """从模板创建配置"""
        template = cls.get_by_name(name)
        if not template:
            return None
        
        return AgentConfig(
            name=template["name"],
            role=template["role"],
            description=template["description"],
            avatar=template["avatar"],
            color=template["color"],
            capabilities=template["capabilities"],
            tools=template["tools"],
            system_prompt=template["system_prompt"]
        )


class AgentManager:
    """Agent 管理器"""
    
    def __init__(self, persist_path: Optional[str] = None):
        self.persist_path = persist_path
        self.agents: Dict[str, AgentInstance] = {}
        loaded = False
        if persist_path:
            from pathlib import Path
            if Path(persist_path).exists():
                self._load()
                loaded = True
        if not loaded:
            self._init_default_agents()
            self._save()

    def _save(self) -> None:
        """持久化 agent 配置到 JSON（失败不影响功能；运行期统计不落盘）。"""
        if not self.persist_path:
            return
        import json
        from pathlib import Path
        try:
            p = Path(self.persist_path)
            p.parent.mkdir(parents=True, exist_ok=True)
            data = {aid: inst.config.model_dump(mode="json") for aid, inst in self.agents.items()}
            p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        except Exception:
            pass

    def _load(self) -> None:
        import json
        from pathlib import Path
        try:
            data = json.loads(Path(self.persist_path).read_text(encoding="utf-8"))
            self.agents = {aid: AgentInstance(AgentConfig(**cfg)) for aid, cfg in data.items()}
        except Exception:
            self._init_default_agents()
    
    def _init_default_agents(self):
        """初始化默认 Agent"""
        default_roles = [
            ("product", "需求分析师", "📋", "#22c55e"),
            ("architect", "架构师", "🏗️", "#3b82f6"),
            ("developer", "开发工程师", "💻", "#8b5cf6"),
            ("tester", "测试工程师", "🧪", "#f59e0b"),
            ("reviewer", "代码审查员", "🔍", "#ef4444")
        ]
        
        for role, name, avatar, color in default_roles:
            config = AgentConfig(
                name=name,
                role=role,
                avatar=avatar,
                color=color,
                is_active=True
            )
            self.agents[config.id] = AgentInstance(config)
    
    def create_agent(
        self,
        name: str,
        role: str,
        description: str = "",
        avatar: str = "🤖",
        color: str = "#6366f1",
        capabilities: List[AgentCapability] = None,
        tools: List[str] = None,
        system_prompt: str = "",
        model: str = "mimo-v2.5"
    ) -> AgentInstance:
        """创建 Agent"""
        config = AgentConfig(
            name=name,
            role=role,
            description=description,
            avatar=avatar,
            color=color,
            capabilities=capabilities or [],
            tools=tools or [],
            system_prompt=system_prompt,
            model=model
        )
        
        agent = AgentInstance(config)
        self.agents[config.id] = agent
        self._save()
        logger.info(f"Created agent: {name} ({config.id})")
        return agent
    
    def create_from_template(self, template_name: str) -> Optional[AgentInstance]:
        """从模板创建 Agent"""
        config = AgentTemplate.create_from_template(template_name)
        if not config:
            return None
        
        agent = AgentInstance(config)
        self.agents[config.id] = agent
        self._save()
        logger.info(f"Created agent from template: {template_name}")
        return agent
    
    def get_agent(self, agent_id: str) -> Optional[AgentInstance]:
        """获取 Agent"""
        return self.agents.get(agent_id)
    
    def list_agents(self, role: str = None, active_only: bool = False) -> List[AgentInstance]:
        """列出 Agent"""
        agents = list(self.agents.values())
        
        if role:
            agents = [a for a in agents if a.config.role == role]
        
        if active_only:
            agents = [a for a in agents if a.config.is_active]
        
        return agents
    
    def update_agent_status(
        self, 
        agent_id: str, 
        status: AgentStatus, 
        task: str = None
    ):
        """更新 Agent 状态"""
        agent = self.agents.get(agent_id)
        if agent:
            agent.update_status(status, task)
    
    def update_agent_progress(self, agent_id: str, progress: int):
        """更新 Agent 进度"""
        agent = self.agents.get(agent_id)
        if agent:
            agent.update_progress(progress)
    
    def record_task_completion(
        self, 
        agent_id: str, 
        success: bool, 
        tokens: int = 0, 
        duration: float = 0.0
    ):
        """记录任务完成"""
        agent = self.agents.get(agent_id)
        if agent:
            agent.record_task(success, tokens, duration)
    
    def delete_agent(self, agent_id: str) -> bool:
        """删除 Agent"""
        if agent_id in self.agents:
            del self.agents[agent_id]
            self._save()
            return True
        return False

    def toggle_agent(self, agent_id: str) -> bool:
        """切换 Agent 状态"""
        agent = self.agents.get(agent_id)
        if agent:
            agent.config.is_active = not agent.config.is_active
            self._save()
            return True
        return False

    def set_active(self, agent_id: str, active: bool) -> bool:
        """显式设置启用/停用。"""
        agent = self.agents.get(agent_id)
        if agent:
            agent.config.is_active = active
            self._save()
            return True
        return False
    
    def get_agent_by_role(self, role: str) -> Optional[AgentInstance]:
        """根据角色获取 Agent"""
        for agent in self.agents.values():
            if agent.config.role == role and agent.config.is_active:
                return agent
        return None
    
    def get_available_agents(self) -> List[AgentInstance]:
        """获取可用的 Agent"""
        return [
            a for a in self.agents.values()
            if a.config.is_active and a.status == AgentStatus.IDLE
        ]
    
    def get_busy_agents(self) -> List[AgentInstance]:
        """获取忙碌的 Agent"""
        return [
            a for a in self.agents.values()
            if a.status == AgentStatus.WORKING
        ]
    
    def get_performance_stats(self) -> Dict[str, Any]:
        """获取性能统计"""
        agents = list(self.agents.values())
        
        return {
            "total_agents": len(agents),
            "active_agents": len([a for a in agents if a.config.is_active]),
            "working_agents": len([a for a in agents if a.status == AgentStatus.WORKING]),
            "total_tasks": sum(a.performance.total_tasks for a in agents),
            "avg_success_rate": (
                sum(a.performance.success_rate for a in agents) / len(agents)
                if agents else 0
            ),
            "total_tokens": sum(a.performance.total_tokens for a in agents)
        }
    
    def get_templates(self) -> List[Dict[str, Any]]:
        """获取所有模板"""
        return AgentTemplate.get_all()
