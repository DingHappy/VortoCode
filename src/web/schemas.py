"""Web 层请求模型与配置常量（从 server.py 抽出）。"""
from datetime import datetime
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

# 可用模型列表
AVAILABLE_MODELS = [
    {"id": "mimo-v2.5", "name": "MiMo v2.5", "provider": "Xiaomi", "description": "小米 AI 模型"},
    {"id": "gpt-4o", "name": "GPT-4o", "provider": "OpenAI", "description": "OpenAI 最强模型"},
    {"id": "gpt-4o-mini", "name": "GPT-4o Mini", "provider": "OpenAI", "description": "轻量版 GPT-4o"},
    {"id": "claude-3.5-sonnet", "name": "Claude 3.5 Sonnet", "provider": "Anthropic", "description": "Claude 最新模型"},
    {"id": "deepseek-chat", "name": "DeepSeek Chat", "provider": "DeepSeek", "description": "深度求索模型"},
]

# 请求模型
class GoalRequest(BaseModel):
    goal: str

class WorkdirRequest(BaseModel):
    workdir: str

class ModelRequest(BaseModel):
    model: str

class TaskUpdate(BaseModel):
    task_id: str
    status: str
    output: Optional[str] = None

class CreateAgentRequest(BaseModel):
    name: str
    role: str = "custom"
    description: str = ""
    system_prompt: str = ""
    capabilities: List[str] = []
    tools: List[str] = []
    model: str = "mimo-v2.5"

class RunAgentRequest(BaseModel):
    task: str

class ExecuteSkillRequest(BaseModel):
    skill_name: str
    arguments: Dict[str, Any] = {}

class CreateSkillRequest(BaseModel):
    name: str
    description: str
    content: str
    category: str = "custom"
    capabilities: List[str] = []
    tools: List[str] = []

# 代码补全 API
class CompletionRequest(BaseModel):
    file: str
    line: int
    column: int
    content: str
    language: str = "python"

# 错误分析 API
class ErrorAnalysisRequest(BaseModel):
    error_message: str
    traceback: str = ""
    code: str = ""

# 项目管理 API
class AddProjectRequest(BaseModel):
    name: str
    path: str
    description: str = ""
    tech_stack: List[str] = []

class CreateAdvancedAgentRequest(BaseModel):
    name: str
    role: str
    description: str = ""
    avatar: str = "🤖"
    color: str = "#6366f1"
    capabilities: List[str] = []
    tools: List[str] = []
    system_prompt: str = ""
    model: str = "mimo-v2.5"

# 工作区管理 API
class CreateWorkspaceRequest(BaseModel):
    name: str
    goal: str = ""
    project_id: str = None

class ExecuteWorkspaceRequest(BaseModel):
    task: str

# 代码编辑 API
class EditRequest(BaseModel):
    file: str
    line: int
    end_line: int
    content: str

# 沙箱 API
class SandboxExecuteRequest(BaseModel):
    code: str
    language: str = "python"
    timeout: int = 60

# 浏览器 API
class BrowserNavigateRequest(BaseModel):
    url: str
    browser_name: str = "default"

# 终端执行 API
class TerminalRequest(BaseModel):
    command: str
    workdir: str = ""

# 对话历史 API
class ChatMessage(BaseModel):
    role: str  # user, assistant, system
    content: str
    timestamp: datetime = Field(default_factory=datetime.now)

# 测试生成 API
class TestGenerateRequest(BaseModel):
    code: str
    language: str = "python"
    test_type: str = "unit"

# 文档生成 API
class DocGenerateRequest(BaseModel):
    code: str
    language: str = "python"
    format: str = "markdown"
