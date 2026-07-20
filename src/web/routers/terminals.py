"""REST bridge for Desktop's local interactive PTY.

**信任模型（刻意如此，别当成疏漏）：single-token 单用户。**
`TerminalManager` 是进程级单例，终端不归属任何"会话"——凡是过了鉴权的调用方都能驱动
任意 terminal id。这是因为 Web 层压根没有会话身份可归属：鉴权只有一个共享的
`VORTOCODE_API_TOKEN`（不设则完全放行 + 只绑 127.0.0.1），拿到它的人本来就能
`POST /api/terminals` 开自己的 PTY、`POST /api/runs` 跑任意命令。把终端"按会话归属"
到调用方自报的 id 上只会是摆设——伪造它所需的凭据，正是威胁模型里假定攻击者已经有的那一个。

真正承重的是 fail-closed：**所有**终端端点都过 `require_shell()`，
`VORTOCODE_ENABLE_SHELL` 未设即全面 403。契约见 tests/integration/test_security.py。
要支持多用户，得先有真正的按用户鉴权——那时这里的单例和下面的闸都要一并重做。
"""
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
    require_shell()  # 终端输出 = 宿主机命令输出，读侧同样 fail-closed
    _terminal_or_404(terminal_id)
    return get_terminal_manager().read(terminal_id, offset)


@router.post("/api/terminals/{terminal_id}/input")
async def write_terminal(terminal_id: str, body: dict):
    require_shell()
    _terminal_or_404(terminal_id)
    try:
        return get_terminal_manager().write(terminal_id, str((body or {}).get("data") or ""))
    except (RuntimeError, ValueError, OSError) as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@router.post("/api/terminals/{terminal_id}/resize")
async def resize_terminal(terminal_id: str, body: dict):
    require_shell()
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
    require_shell()
    _terminal_or_404(terminal_id)
    return get_terminal_manager().stop(terminal_id)

