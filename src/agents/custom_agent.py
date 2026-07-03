"""自定义 Agent 管理 - 手动创建和管理子 Agent"""

import uuid
import logging
from datetime import datetime
from enum import Enum
from typing import Dict, List, Optional
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class AgentRole(str, Enum):
    """Agent 角色"""
    PRODUCT = "product"
    ARCHITECT = "architect"
    DEVELOPER = "developer"
    TESTER = "tester"
    REVIEWER = "reviewer"
    CUSTOM = "custom"


class AgentCapability(str, Enum):
    """Agent 能力"""
    # 代码相关
    CODE_GENERATION = "code_generation"
    CODE_REVIEW = "code_review"
    CODE_REFACTORING = "code_refactoring"
    
    # 测试相关
    UNIT_TESTING = "unit_testing"
    INTEGRATION_TESTING = "integration_testing"
    E2E_TESTING = "e2e_testing"
    
    # 设计相关
    ARCHITECTURE_DESIGN = "architecture_design"
    UI_DESIGN = "ui_design"
    DATABASE_DESIGN = "database_design"
    
    # 分析相关
    REQUIREMENTS_ANALYSIS = "requirements_analysis"
    BUG_ANALYSIS = "bug_analysis"
    PERFORMANCE_ANALYSIS = "performance_analysis"
    
    # 文档相关
    DOCUMENTATION = "documentation"
    API_DESIGN = "api_design"
    
    # 运维相关
    DEPLOYMENT = "deployment"
    MONITORING = "monitoring"
    SECURITY_AUDIT = "security_audit"


class ToolPermission(BaseModel):
    """工具权限"""
    name: str
    allowed: bool = True
    description: str = ""


class CustomAgentConfig(BaseModel):
    """自定义 Agent 配置"""
    id: str = Field(default_factory=lambda: str(uuid.uuid4())[:8])
    name: str
    role: AgentRole = AgentRole.CUSTOM
    description: str = ""
    system_prompt: str = ""
    capabilities: List[AgentCapability] = Field(default_factory=list)
    tools: List[ToolPermission] = Field(default_factory=list)
    model: str = "mimo-v2.5"
    temperature: float = 0.7
    max_tokens: int = 4096
    created_at: datetime = Field(default_factory=datetime.now)
    is_active: bool = True


class AgentTemplate(BaseModel):
    """Agent 模板"""
    name: str
    role: AgentRole
    description: str
    system_prompt: str
    capabilities: List[AgentCapability]
    tools: List[str]
    icon: str = "fa-robot"


# 预定义的 Agent 模板
AGENT_TEMPLATES: List[AgentTemplate] = [
    AgentTemplate(
        name="全栈开发工程师",
        role=AgentRole.DEVELOPER,
        description="能够处理前端和后端开发任务",
        system_prompt="""你是一个全栈开发工程师，擅长：
- 前端：React, Vue, Angular, HTML/CSS
- 后端：Node.js, Python, Java, Go
- 数据库：MySQL, PostgreSQL, MongoDB
- API 设计：RESTful, GraphQL

请根据需求编写高质量、可维护的代码。""",
        capabilities=[
            AgentCapability.CODE_GENERATION,
            AgentCapability.CODE_REVIEW,
            AgentCapability.UNIT_TESTING
        ],
        tools=["read_file", "write_file", "execute_command"],
        icon="fa-code"
    ),
    AgentTemplate(
        name="前端专家",
        role=AgentRole.DEVELOPER,
        description="专注于前端开发和 UI/UX",
        system_prompt="""你是一个前端专家，擅长：
- 现代前端框架：React, Vue, Svelte
- 状态管理：Redux, Zustand, Pinia
- 样式：Tailwind CSS, Styled Components
- 性能优化和用户体验

请创建美观、响应式、高性能的前端代码。""",
        capabilities=[
            AgentCapability.CODE_GENERATION,
            AgentCapability.UI_DESIGN
        ],
        tools=["read_file", "write_file"],
        icon="fa-palette"
    ),
    AgentTemplate(
        name="后端专家",
        role=AgentRole.DEVELOPER,
        description="专注于后端架构和 API 设计",
        system_prompt="""你是一个后端专家，擅长：
- 服务端框架：Express, FastAPI, Spring Boot
- 数据库设计和优化
- 微服务架构
- 安全性和性能优化

请设计可扩展、安全、高性能的后端系统。""",
        capabilities=[
            AgentCapability.CODE_GENERATION,
            AgentCapability.ARCHITECTURE_DESIGN,
            AgentCapability.DATABASE_DESIGN,
            AgentCapability.API_DESIGN
        ],
        tools=["read_file", "write_file", "execute_command"],
        icon="fa-server"
    ),
    AgentTemplate(
        name="测试工程师",
        role=AgentRole.TESTER,
        description="负责测试策略和自动化测试",
        system_prompt="""你是一个测试工程师，擅长：
- 单元测试：Jest, Pytest, JUnit
- 集成测试：Supertest, TestContainers
- E2E 测试：Playwright, Cypress
- 测试策略和质量保证

请编写全面的测试用例，确保代码质量。""",
        capabilities=[
            AgentCapability.UNIT_TESTING,
            AgentCapability.INTEGRATION_TESTING,
            AgentCapability.E2E_TESTING,
            AgentCapability.BUG_ANALYSIS
        ],
        tools=["read_file", "execute_command"],
        icon="fa-flask"
    ),
    AgentTemplate(
        name="安全审计员",
        role=AgentRole.REVIEWER,
        description="专注于代码安全和漏洞检测",
        system_prompt="""你是一个安全审计员，擅长：
- 代码安全审查
- 漏洞检测和修复
- 安全最佳实践
- 依赖安全分析

请检查代码中的安全隐患并提供修复建议。""",
        capabilities=[
            AgentCapability.SECURITY_AUDIT,
            AgentCapability.CODE_REVIEW
        ],
        tools=["read_file"],
        icon="fa-shield-alt"
    ),
    AgentTemplate(
        name="文档工程师",
        role=AgentRole.CUSTOM,
        description="负责技术文档和 API 文档",
        system_prompt="""你是一个文档工程师，擅长：
- 技术文档编写
- API 文档生成
- 用户指南
- 代码注释

请编写清晰、完整、易懂的技术文档。""",
        capabilities=[
            AgentCapability.DOCUMENTATION,
            AgentCapability.API_DESIGN
        ],
        tools=["read_file", "write_file"],
        icon="fa-book"
    ),
    AgentTemplate(
        name="DevOps 工程师",
        role=AgentRole.CUSTOM,
        description="负责部署、运维和监控",
        system_prompt="""你是一个 DevOps 工程师，擅长：
- Docker 和 Kubernetes
- CI/CD 流水线
- 云服务：AWS, GCP, Azure
- 监控和日志

请设计和实现可靠的部署和运维方案。""",
        capabilities=[
            AgentCapability.DEPLOYMENT,
            AgentCapability.MONITORING
        ],
        tools=["read_file", "write_file", "execute_command"],
        icon="fa-cloud"
    ),
    AgentTemplate(
        name="性能优化专家",
        role=AgentRole.CUSTOM,
        description="专注于性能分析和优化",
        system_prompt="""你是一个性能优化专家，擅长：
- 性能分析和瓶颈定位
- 代码优化
- 数据库查询优化
- 缓存策略

请分析并优化系统性能。""",
        capabilities=[
            AgentCapability.PERFORMANCE_ANALYSIS,
            AgentCapability.CODE_REFACTORING
        ],
        tools=["read_file", "execute_command"],
        icon="fa-tachometer-alt"
    )
]


class CustomAgentManager:
    """自定义 Agent 管理器"""
    
    def __init__(self, persist_path: Optional[str] = None):
        self.persist_path = persist_path
        self.agents: Dict[str, CustomAgentConfig] = {}
        self.templates = AGENT_TEMPLATES
        if persist_path:
            self._load()

    def _save(self) -> None:
        """持久化到 JSON（持久化失败不影响功能）。"""
        if not self.persist_path:
            return
        import json
        from pathlib import Path
        try:
            p = Path(self.persist_path)
            p.parent.mkdir(parents=True, exist_ok=True)
            data = {aid: cfg.model_dump(mode="json") for aid, cfg in self.agents.items()}
            p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        except Exception:
            pass

    def _load(self) -> None:
        import json
        from pathlib import Path
        try:
            p = Path(self.persist_path)
            if not p.exists():
                return
            data = json.loads(p.read_text(encoding="utf-8"))
            self.agents = {aid: CustomAgentConfig(**cfg) for aid, cfg in data.items()}
        except Exception:
            pass
    
    def create_agent(
        self,
        name: str,
        role: AgentRole = AgentRole.CUSTOM,
        description: str = "",
        system_prompt: str = "",
        capabilities: List[AgentCapability] = None,
        tools: List[str] = None,
        model: str = "mimo-v2.5"
    ) -> CustomAgentConfig:
        """创建自定义 Agent"""
        # 创建工具权限
        tool_permissions = []
        if tools:
            for tool_name in tools:
                tool_permissions.append(ToolPermission(
                    name=tool_name,
                    allowed=True
                ))
        
        agent = CustomAgentConfig(
            name=name,
            role=role,
            description=description,
            system_prompt=system_prompt,
            capabilities=capabilities or [],
            tools=tool_permissions,
            model=model
        )
        
        self.agents[agent.id] = agent
        logger.info(f"Created custom agent: {agent.name} ({agent.id})")
        self._save()
        return agent
    
    def create_from_template(self, template_name: str) -> Optional[CustomAgentConfig]:
        """从模板创建 Agent"""
        template = next((t for t in self.templates if t.name == template_name), None)
        if not template:
            return None
        
        return self.create_agent(
            name=template.name,
            role=template.role,
            description=template.description,
            system_prompt=template.system_prompt,
            capabilities=template.capabilities,
            tools=template.tools
        )
    
    def get_agent(self, agent_id: str) -> Optional[CustomAgentConfig]:
        """获取 Agent"""
        return self.agents.get(agent_id)
    
    def list_agents(self) -> List[CustomAgentConfig]:
        """列出所有 Agent"""
        return list(self.agents.values())
    
    def list_templates(self) -> List[AgentTemplate]:
        """列出所有模板"""
        return self.templates
    
    def update_agent(self, agent_id: str, **kwargs) -> Optional[CustomAgentConfig]:
        """更新 Agent"""
        agent = self.agents.get(agent_id)
        if not agent:
            return None
        
        for key, value in kwargs.items():
            if hasattr(agent, key):
                setattr(agent, key, value)
        
        return agent
    
    def delete_agent(self, agent_id: str) -> bool:
        """删除 Agent"""
        if agent_id in self.agents:
            del self.agents[agent_id]
            self._save()
            return True
        return False
    
    def activate_agent(self, agent_id: str) -> bool:
        """激活 Agent"""
        agent = self.agents.get(agent_id)
        if agent:
            agent.is_active = True
            self._save()
            return True
        return False
    
    def deactivate_agent(self, agent_id: str) -> bool:
        """停用 Agent"""
        agent = self.agents.get(agent_id)
        if agent:
            agent.is_active = False
            self._save()
            return True
        return False
    
    def get_active_agents(self) -> List[CustomAgentConfig]:
        """获取活跃的 Agent"""
        return [a for a in self.agents.values() if a.is_active]
    
    def get_agents_by_capability(self, capability: AgentCapability) -> List[CustomAgentConfig]:
        """根据能力获取 Agent"""
        return [
            a for a in self.agents.values()
            if capability in a.capabilities and a.is_active
        ]
    
    def get_agents_by_role(self, role: AgentRole) -> List[CustomAgentConfig]:
        """根据角色获取 Agent"""
        return [
            a for a in self.agents.values()
            if a.role == role and a.is_active
        ]
