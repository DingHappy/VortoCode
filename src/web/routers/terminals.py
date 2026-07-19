"""REST bridge for Desktop's local interactive PTY."""
from src.web.deps import *  # noqa: F401,F403

router = APIRouter()
_TERMINAL_MANAGER = None


def get_terminal_manager():
    global _TERMINAL_MANAGER
    if _TERMINAL_MANAGER is None:
        from src.gateway.terminals import TerminalManager

        _TERMINAL_MANAGER = TerminalManager(os.getcwd())
    return _TERMINAL_MANAGER


def _terminal_or_404(terminal_id: str):
    terminal = get_terminal_manager().get(terminal_id)
    if terminal is None:
        raise HTTPException(status_code=404, detail=f"无此终端 {terminal_id}")
    return terminal


@router.get("/api/terminals")
async def list_terminals():
    return {"terminals": get_terminal_manager().list()}


@router.post("/api/terminals")
async def create_terminal(body: dict):
    require_shell()  # PTY = 交互式宿主 shell，默认 fail-closed（无 id 则 input/resize/stop 皆 404）
    payload = body or {}
    try:
        return get_terminal_manager().create(
            cols=payload.get("cols", 100),
            rows=payload.get("rows", 28),
        )
    except (RuntimeError, ValueError) as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@router.get("/api/terminals/{terminal_id}/output")
async def read_terminal(terminal_id: str, offset: int = 0):
    _terminal_or_404(terminal_id)
    return get_terminal_manager().read(terminal_id, offset)


@router.post("/api/terminals/{terminal_id}/input")
async def write_terminal(terminal_id: str, body: dict):
    _terminal_or_404(terminal_id)
    try:
        return get_terminal_manager().write(terminal_id, str((body or {}).get("data") or ""))
    except (RuntimeError, ValueError, OSError) as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@router.post("/api/terminals/{terminal_id}/resize")
async def resize_terminal(terminal_id: str, body: dict):
    payload = body or {}
    _terminal_or_404(terminal_id)
    try:
        return get_terminal_manager().resize(
            terminal_id,
            payload.get("cols", 100),
            payload.get("rows", 28),
        )
    except (RuntimeError, ValueError, OSError) as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@router.post("/api/terminals/{terminal_id}/stop")
async def stop_terminal(terminal_id: str):
    _terminal_or_404(terminal_id)
    return get_terminal_manager().stop(terminal_id)

