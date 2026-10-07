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

from src.web.state import state, manager, add_log  # noqa: F401
from src.web.schemas import *  # noqa: F401,F403
from src.web.auth import require_shell, resolve_within  # noqa: F401

logger = logging.getLogger("src.web")
