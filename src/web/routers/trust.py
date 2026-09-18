"""授权档位的读写接口——Desktop/Web 的"这个工作区要我问得多勤"开关。

档位语义见 `src/agents/trust.py`，落盘见 `src/gateway/trust_setting.py`。这里只做三件事：
如实报出**当前档位与该会话的上限**（上限由能力档案决定，端不能越过）、写入新档位、记审计。

改档位**不重建会话**：确认门读的是"当前档位"的读取函数（gate 的 trust_level 可调用），
所以正在跑的对话下一次判定就按新档位走，历史不丢。
"""
from __future__ import annotations

import os

from src.web.deps import *  # noqa: F401,F403

router = APIRouter()


def _status() -> dict:
    from src.agents.trust import ceiling, resolve
    from src.gateway.trust_setting import available_levels, get_trust_level
    from src.gateway.workspace_scope import current_workspace_scope

    repo_root = os.getcwd()
    profile = _capability_profile()
    stored = get_trust_level(repo_root)
    return {
        "level": stored,
        "effective": resolve(stored, profile),   # 被能力档案夹过之后**真正生效**的那一档
        "ceiling": ceiling(profile),
        "levels": list(available_levels()),
        "capability_profile": profile,
        "workspace_scope": current_workspace_scope(),
    }


def _capability_profile() -> str:
    """与 realtime 装配会话时同一套判定——两处不一致会让界面报出一个假的上限。"""
    from src.gateway.workspace_scope import GENERAL, current_workspace_scope

    desktop_trusted = (os.getenv("VORTOCODE_DESKTOP_SIDECAR") == "1"
                       and current_workspace_scope() != GENERAL)
    return "local" if desktop_trusted else "external"


@router.get("/api/trust")
async def get_trust():
    return _status()


@router.put("/api/trust")
async def put_trust(body: dict):
    from src.gateway.audit import record_event_audit
    from src.gateway.trust_setting import available_levels, set_trust_level

    level = (body or {}).get("level")
    if not isinstance(level, str) or level not in available_levels():
        raise HTTPException(status_code=400,
                            detail=f"level 必须是 {list(available_levels())} 之一")
    stored = set_trust_level(os.getcwd(), level)
    status = _status()
    record_event_audit(
        os.getcwd(), session="runtime", mode="settings", event="trust_level_changed",
        data={"level": stored, "effective": status["effective"],
              "ceiling": status["ceiling"]},
    )
    return status
