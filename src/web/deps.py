"""各路由共用的导入集中处，避免每个 router 重复一大段，也隔离循环依赖。

本模块是**转出中枢**：各 router 用 `from src.web.deps import *` 取这些名字。所以这里的导入
"本地未用"是有意的（供转出），全部带 `# noqa: F401` 让 ruff 别当未用导入删掉。
"""
import asyncio  # noqa: F401
import base64  # noqa: F401
import json  # noqa: F401
import logging
import os  # noqa: F401
from datetime import datetime  # noqa: F401
from pathlib import Path  # noqa: F401
from typing import Any, Dict, List, Optional  # noqa: F401

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect  # noqa: F401
from fastapi.responses import HTMLResponse, FileResponse  # noqa: F401

from src.security import PermissionManager, SafetyGuard, RiskLevel  # noqa: F401
from src.context import ProjectContext, GitIntegration, DiffViewer  # noqa: F401
from src.agents import CustomAgentManager, AgentRole, CustomAgentCapability, AgentManager  # noqa: F401
from src.skills import SkillManager  # noqa: F401
from src.indexing import CodeIndexer, CodeEmbedding  # noqa: F401
from src.editor import CodeEditor, DiffGenerator  # noqa: F401
from src.sandbox import SandboxManager, SandboxExecutor, SandboxConfig  # noqa: F401
from src.browser import BrowserManager, BrowserConfig  # noqa: F401
from src.projects import ProjectManager, MultiProjectOrchestrator  # noqa: F401
from src.workspaces import WorkspaceManager  # noqa: F401

from src.web.state import state, manager, add_log  # noqa: F401
from src.web.schemas import *  # noqa: F401,F403
from src.web.auth import require_shell, require_browser, validate_navigation_url, resolve_within  # noqa: F401

logger = logging.getLogger("src.web")
