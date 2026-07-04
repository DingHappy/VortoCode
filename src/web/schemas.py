"""Web 层请求模型（从 server.py 抽出）。"""
from typing import Any, Dict, List
from pydantic import BaseModel

# 请求模型
class CreateAgentRequest(BaseModel):
    name: str
    role: str = "custom"
    description: str = ""
    system_prompt: str = ""
    capabilities: List[str] = []
    tools: List[str] = []
    model: str = "mimo-v2.5"

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
