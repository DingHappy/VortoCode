"""Web 层共享状态。

从早期 2000+ 行的 server.py 抽出，使「全局状态 / WebSocket 连接 / 日志」
成为可独立导入、可测试的模块；后续按域拆分的各 router 都从这里取
`state` / `manager` / `add_log`，而不是依赖 server.py 里的模块级全局。
"""

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from fastapi import WebSocket

from src.security import PermissionManager, SafetyGuard
from src.context import ProjectContext, GitIntegration
from src.agents import CustomAgentManager, AgentManager
from src.skills import SkillManager
from src.indexing import CodeIndexer
from src.editor import CodeEditor
from src.sandbox import SandboxManager, SandboxExecutor
from src.browser import BrowserManager
from src.projects import ProjectManager, MultiProjectOrchestrator
from src.workspaces import WorkspaceManager

logger = logging.getLogger(__name__)


class ConnectionManager:
    """WebSocket 连接管理器"""

    def __init__(self):
        self.active_connections: Set[WebSocket] = set()

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.add(websocket)
        logger.info(f"WebSocket connected. Total: {len(self.active_connections)}")

    def disconnect(self, websocket: WebSocket):
        self.active_connections.discard(websocket)
        logger.info(f"WebSocket disconnected. Total: {len(self.active_connections)}")

    async def broadcast(self, message: Dict[str, Any]):
        """广播消息给所有连接"""
        message_str = json.dumps(message, ensure_ascii=False)
        disconnected = set()

        for connection in self.active_connections:
            try:
                await connection.send_text(message_str)
            except Exception:
                disconnected.add(connection)

        # 清理断开的连接
        self.active_connections -= disconnected


manager = ConnectionManager()


class AppState:
    def __init__(self):
        self.running = False
        self.goal = ""
        self.workdir = str(Path.cwd())  # 默认工作目录：进程当前目录（总是存在）。
        # 旧默认 ~/personal_project 在多数环境不存在，会让 terminal/文件等端点失败。
        self.model = "mimo-v2.5"  # 默认模型
        self.auto_approve = False  # 自动批准
        self.tasks: List[Dict[str, Any]] = []
        self.agents: Dict[str, Dict[str, Any]] = {}
        self.logs: List[Dict[str, Any]] = []
        self.progress = 0
        self.iterations = 0
        self.tokens_used = 0

        # 项目管理器
        self.project_manager = ProjectManager()
        self.multi_project_orchestrator = MultiProjectOrchestrator(self.project_manager)

        # 工作区管理器
        self.workspace_manager = WorkspaceManager()

        # 权限管理
        self.permission_manager = PermissionManager(self.workdir)
        self.safety_guard = SafetyGuard(self.permission_manager)

        # Git 集成
        self.git = GitIntegration(self.workdir)

        # 项目上下文
        self.project_context: Optional[ProjectContext] = None
        self._load_project_context()

        # 自定义 Agent 管理器（持久化到 .vortocode/，重启不丢）
        self.custom_agent_manager = CustomAgentManager(
            persist_path=str(Path(".vortocode") / "web_custom_agents.json"))

        # 高级 Agent 管理器（持久化）
        self.agent_manager = AgentManager(
            persist_path=str(Path(".vortocode") / "web_advanced_agents.json"))

        # 技能管理器
        self.skill_manager = SkillManager(self.workdir)

        # 代码索引器
        self.code_indexer: Optional[CodeIndexer] = None
        self.code_editor: Optional[CodeEditor] = None

        # 沙箱管理器
        self.sandbox_manager = SandboxManager()
        self.sandbox_executor = SandboxExecutor(self.sandbox_manager)

        # 浏览器管理器
        self.browser_manager = BrowserManager()

    def _load_project_context(self):
        """加载项目上下文"""
        try:
            self.project_context = ProjectContext(self.workdir)
        except Exception as e:
            logger.warning(f"Failed to load project context: {e}")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "running": self.running,
            "goal": self.goal,
            "workdir": self.workdir,
            "model": self.model,
            "auto_approve": self.auto_approve,
            "tasks": self.tasks,
            "agents": self.agents,
            "logs": self.logs[-100:],  # 只返回最近100条日志
            "progress": self.progress,
            "iterations": self.iterations,
            "tokens_used": self.tokens_used,
            "pending_approvals": len(self.permission_manager.get_pending_requests())
        }


state = AppState()


def add_log(level: str, message: str):
    """添加一条日志到全局状态（供各 router 与执行流程共享）"""
    log_entry = {
        "time": datetime.now().strftime("%H:%M:%S"),
        "level": level,
        "message": message,
    }
    state.logs.append(log_entry)

    # 限制日志数量
    if len(state.logs) > 1000:
        state.logs = state.logs[-500:]
