"""Web 层共享状态。

从早期 2000+ 行的 server.py 抽出，使「全局状态 / WebSocket 连接 / 日志」
成为可独立导入、可测试的模块；后续按域拆分的各 router 都从这里取
`state` / `manager` / `add_log`，而不是依赖 server.py 里的模块级全局。
"""

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Set

from fastapi import WebSocket


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
        # 旧流水线字段（running/goal/tasks/agents/progress/iterations/tokens_used/auto_approve）
        # 已随路线 A execution/security 路由退役删除（b4 PR-B2）——它们此前经 to_dict 泄漏进
        # /ws init 握手，前端从不读。
        self.workdir = str(Path.cwd())  # 默认工作目录：进程当前目录（总是存在）。
        # 旧默认 ~/personal_project 在多数环境不存在，会让 terminal/文件等端点失败。
        self.model = "mimo-v2.6-pro"  # 默认模型
        self.logs: List[Dict[str, Any]] = []

    def to_dict(self) -> Dict[str, Any]:
        """状态快照——/ws init 握手与 GET /api/status 共用（旧流水线字段已退役，前端不读）。"""
        from src.gateway.workspace_scope import workspace_scope_snapshot

        return {
            "workdir": self.workdir,
            "model": self.model,
            "logs": self.logs[-100:],  # 只返回最近100条日志
            **workspace_scope_snapshot(self.workdir),
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
