"""Desktop decision center and shared audit timeline endpoints."""
import os

from src.web.deps import *  # noqa: F401,F403

router = APIRouter()


def _queue(limit: int = 100, session: str = "") -> list[dict]:
    from src.gateway.decisions import DecisionStore, build_decision_queue
    from src.gateway.goals import GoalLedger
    from src.gateway.runs import RunLedger
    from src.gateway.tasks import TaskLedger
    from src.web.routers.realtime import pending_confirmations

    root = os.getcwd()
    store = DecisionStore(root)
    return build_decision_queue(
        confirmations=pending_confirmations(str(session or "")[:180]),
        goals=GoalLedger(root).list(),
        tasks=TaskLedger(root).list(),
        runs=RunLedger(root).list(),
        dismissed=store.dismissed(),
        limit=limit,
    )


@router.get("/api/decisions")
async def list_decisions(limit: int = 100, session: str = ""):
    return {"decisions": _queue(max(1, min(int(limit), 200)), session)}


@router.get("/api/runtime-inbox")
async def runtime_inbox():
    """One bounded read-only snapshot for Desktop's cross-runtime inbox."""
    from src.gateway.goals import GoalLedger
    from src.gateway.runtime_inbox import build_runtime_inbox
    from src.gateway.tasks import TaskLedger
    from src.gateway.workspace_scope import GENERAL, current_workspace_scope
    from src.web.routers.realtime import _session_dashboard_snapshot

    root = os.getcwd()
    scope = current_workspace_scope()
    return build_runtime_inbox(
        scope=scope,
        sessions=_session_dashboard_snapshot(root),
        decisions=_queue(100),
        goals=[] if scope == GENERAL else GoalLedger(root).list(),
        tasks=[] if scope == GENERAL else TaskLedger(root).list(limit=200),
    )


@router.post("/api/decisions/{decision_id}/dismiss")
async def dismiss_decision(decision_id: str):
    from src.gateway.decisions import DecisionStore

    current = next((item for item in _queue(200) if item["id"] == decision_id), None)
    if current is None:
        raise HTTPException(status_code=404, detail="待决策事项已解决或不存在")
    if not current.get("can_dismiss"):
        raise HTTPException(status_code=409, detail="实时确认不能忽略，请明确允许或拒绝")
    if not DecisionStore(os.getcwd()).dismiss(decision_id):
        raise HTTPException(status_code=409, detail="无法忽略该事项")
    return {"ok": True, "id": decision_id}


@router.get("/api/audit")
async def get_audit(limit: int = 100):
    from src.gateway.audit import list_audit

    return {"entries": list_audit(os.getcwd(), max(1, min(int(limit), 500)))}
