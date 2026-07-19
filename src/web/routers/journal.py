"""REST surface for deterministic, repository-local daily journals."""
import os

from src.web.deps import *  # noqa: F401,F403

router = APIRouter()


@router.get("/api/journal")
async def get_journal(date: str = ""):
    from src.gateway.journal import get_daily_journal

    try:
        return get_daily_journal(os.getcwd(), date)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@router.get("/api/journal/days")
async def list_journal_days():
    from src.gateway.journal import JournalStore

    return {"days": JournalStore(os.getcwd()).list_days()}


@router.get("/api/journal/weekly")
async def get_weekly_journal(end: str = "", days: int = 7):
    from src.gateway.journal import build_weekly_journal

    try:
        return build_weekly_journal(os.getcwd(), end, days)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@router.get("/api/journal/continuation")
async def get_journal_continuation(date: str):
    from src.gateway.journal import build_journal_continuation

    try:
        return build_journal_continuation(os.getcwd(), date)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@router.post("/api/journal/snapshot")
async def snapshot_journal(body: dict):
    from src.gateway.journal import snapshot_daily_journal

    try:
        return snapshot_daily_journal(os.getcwd(), str((body or {}).get("date") or ""))
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except OSError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@router.post("/api/journal/notes")
async def add_note(body: dict):
    from src.gateway.journal import add_journal_note

    payload = body or {}
    try:
        return add_journal_note(
            os.getcwd(), str(payload.get("text") or ""), str(payload.get("date") or ""),
        )
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except OSError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
