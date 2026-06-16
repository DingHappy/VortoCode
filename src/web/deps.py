"""各路由共用的导入集中处，避免每个 router 重复一大段，也隔离循环依赖。"""
import asyncio
import base64
import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, FileResponse

from src.security import PermissionManager, SafetyGuard, RiskLevel
from src.context import ProjectContext, GitIntegration, DiffViewer
from src.agents import CustomAgentManager, AgentRole, CustomAgentCapability, AgentManager
from src.skills import SkillManager
from src.indexing import CodeIndexer, CodeEmbedding
from src.editor import CodeEditor, DiffGenerator
from src.sandbox import SandboxManager, SandboxExecutor, SandboxConfig
from src.browser import BrowserManager, BrowserConfig
from src.projects import ProjectManager, MultiProjectOrchestrator
from src.workspaces import WorkspaceManager

from src.web.state import state, manager, add_log
from src.web.schemas import *  # noqa: F401,F403
from src.web.auth import require_shell, require_browser, validate_navigation_url, resolve_within

logger = logging.getLogger("src.web")
