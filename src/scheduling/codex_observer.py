"""Codex 观察员：跑在用户的 Mac 上（Desktop 的本机引擎里），看 Codex 在做什么，报给常驻助理。

Codex CLI（0.16x）的本机后台服务在 ``~/.codex/app-server-control/app-server-control.sock``
提供 app-server 协议（Unix socket 上的 WebSocket + JSON-RPC）。终端里的 Codex 界面就连在它上面，
所以这里看到的就是用户正在跑的那几个会话的实时状态。

只读：列出已加载的会话、读最近一轮的摘要，压成一份精简进度 POST 给常驻助理
（``VORTOCODE_CODEX_REPORT_URL`` + ``VORTOCODE_CODEX_REPORT_TOKEN``，由 Desktop 启动本机引擎时注入）。
不发完整对话和完整 diff：只有任务、最后几句说明、计划、改动的文件名、正在等批准的命令。
另带一个数：这台 Mac 的键盘鼠标多久没动了——助理据此判断你在不在电脑前，在的话例行进度不推手机。
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import socket
import time
import uuid
from copy import deepcopy
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, List, Optional

from src.agents.backends.codex import CodexBackend, codex_execution_state
from src.agents.backends.control import control_session
from src.agents.backends.execution import (
    ExecutionProgress, ExecutionRef, ExecutionSnapshot, process_instance,
)
from src.agents.backends.observation_order import ObservationCursor, ObservationOrder

logger = logging.getLogger(__name__)

INTERVAL_SECONDS = 20
HEARTBEAT_SECONDS = 600          # 没变化也每 10 分钟报一次，让助理知道观察员还在
ACTIVE_HEARTBEAT_SECONDS = 60    # 有会话在跑或在等人时每分钟报一次（「等批准超过 2 分钟」靠它判断）
PRESENT_IDLE_SECONDS = 300      # 键盘鼠标这么久内动过 = 主人在 Mac 前（与常驻助理同一条线）
_MAX_THREADS = 12
_TEXT = {"request": 400, "reply": 900, "plan": 700, "command": 240, "error": 300}


def socket_path() -> Path:
    return Path(os.path.expanduser("~/.codex/app-server-control/app-server-control.sock"))


def report_target() -> Optional[tuple]:
    url = os.getenv("VORTOCODE_CODEX_REPORT_URL", "").strip().rstrip("/")
    token = os.getenv("VORTOCODE_CODEX_REPORT_TOKEN", "").strip()
    return (url, token) if url and token else None


def observer_enabled() -> bool:
    return report_target() is not None


@dataclass(frozen=True)
class ObserverHealth:
    run_id: str = ""
    phase: str = "not_started"
    updated_at: float | None = None
    last_observed_at: float | None = None
    last_acknowledged_at: float | None = None
    transport_mode: str = "unknown"
    pending: bool = False
    error_code: str = ""


class ObserverMonitor:
    """One process-local loop observation; contains no target, token or content."""
    def __init__(self):
        self.health = ObserverHealth()

    def start(self) -> str:
        run_id = uuid.uuid4().hex
        self.health = ObserverHealth(run_id=run_id, phase="starting", updated_at=time.time())
        return run_id

    def update(self, run_id: str, phase: str, *, publisher: ReportPublisher | None = None,
               observed: bool = False, acknowledged: bool = False, error_code: str = "") -> None:
        if run_id != self.health.run_id:
            return  # An old loop's cleanup cannot stop a replacement loop.
        now = time.time()
        self.health = replace(self.health, phase=phase, updated_at=now,
            last_observed_at=now if observed else self.health.last_observed_at,
            last_acknowledged_at=now if acknowledged else self.health.last_acknowledged_at,
            transport_mode=(publisher.mode or "unknown") if publisher is not None else self.health.transport_mode,
            pending=publisher.pending is not None if publisher is not None else self.health.pending,
            error_code=error_code)

    def snapshot(self, *, configured: bool) -> dict:
        health = self.health if configured else ObserverHealth(phase="disabled")
        return {"schema_version": 1, "configured": configured, **asdict(health)}


_MONITOR = ObserverMonitor()


def observer_status() -> dict:
    return _MONITOR.snapshot(configured=observer_enabled())


def user_idle_seconds() -> Optional[float]:
    """这台 Mac 的键盘鼠标多久没动了（秒）。只有这一个数，不看你在做什么；拿不到就 None（助理照常推）。"""
    import sys
    if sys.platform != "darwin":
        return None
    try:
        import ctypes
        import ctypes.util
        lib = ctypes.cdll.LoadLibrary(ctypes.util.find_library("ApplicationServices")
                                      or "/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices")
        since_last = lib.CGEventSourceSecondsSinceLastEventType
        since_last.restype = ctypes.c_double
        since_last.argtypes = [ctypes.c_int32, ctypes.c_uint32]
        # kCGEventSourceStateCombinedSessionState=0，kCGAnyInputEventType=~0
        value = float(since_last(0, 0xFFFFFFFF))
    except Exception:  # noqa: BLE001 —— 拿不到就当不知道
        return None
    return value if 0 <= value < 10 ** 7 else None


def _clip(text: Any, limit: int, *, tail: bool = False) -> str:
    value = " ".join(str(text or "").split()) if not tail else str(text or "").strip()
    if len(value) <= limit:
        return value
    return ("…" + value[-limit:]) if tail else (value[:limit] + "…")


def _home_short(path: str) -> str:
    home = os.path.expanduser("~")
    return "~" + path[len(home):] if path and path.startswith(home) else path


def _user_text(item: dict) -> str:
    parts = [part.get("text", "") for part in item.get("content") or [] if isinstance(part, dict)]
    return " ".join(part for part in parts if part)


# 用命令写文件（cat > x、tee x、sed -i、cp/mv/touch）：Codex 的命令条目不分读写（都标 unknown），
# 补丁方式的改动才进 fileChange。不认这些，助理会把「写了文件」误判成「0 个文件、在说谎」（真机 2026-10-20）。
_PATH = r"(['\"]?)((?:[~./]|[A-Za-z0-9_-]+/)?[A-Za-z0-9_./~-]*?(?:/[A-Za-z0-9_.-]+|\.[A-Za-z0-9]{1,8}))\1(?=[\s;|&)'\"]|$)"
_REDIRECT = re.compile(r"(?:^|\s)\d?>>?\s*" + _PATH)
_WRITE_TARGET = [
    re.compile(r"(?:^|[\s;|&(])tee\s+(?:-a\s+)?" + _PATH),
    re.compile(r"(?:^|[\s;|&(])touch\s+" + _PATH),
    re.compile(r"(?:^|[\s;|&(])(?:cp|mv|install)\s+(?:-\S+\s+)*\S+\s+" + _PATH),
    re.compile(r"(?:^|[\s;|&(])sed\s+-i\S*\s+(?:''\s+)?(?:-e\s+)?(?:'[^']*'|\"[^\"]*\"|\S+)\s+" + _PATH),
]
_WRITE_HINT = re.compile(r"\b(?:write_text|write_bytes|fs\.writeFile(?:Sync)?)\(|\bopen\([^)]*,\s*['\"][wa]b?['\"]|\bgit\s+commit\b")
_READ_ONLY = {"read", "search", "listFiles"}


def shell_writes(command: str, actions: Optional[list] = None) -> tuple:
    """一条命令像不像写了文件：(是否, 写到的路径)。

    Codex 已经认出是读 / 搜 / 列目录的命令不算；其余按重定向、tee、touch、cp/mv、sed -i 找目标，
    目标必须像个文件（带扩展名或带 /），免得把搜索词里的 ``->``、``=>`` 当成写文件。
    脚本里写文件（write_text、open(..., 'w')）认得出「可能写了」但拿不到路径。尽力而为，不当证据用。
    """
    kinds = {str((action or {}).get("type")) for action in actions or [] if isinstance(action, dict)}
    if kinds and kinds <= _READ_ONLY:
        return False, []
    text = str(command or "")
    paths: List[str] = []
    for pattern in (_REDIRECT, *_WRITE_TARGET):
        for match in pattern.finditer(text):
            target = match.group(2)
            if target.startswith("/dev/") or target in paths:
                continue
            paths.append(target)
    return bool(paths or _WRITE_HINT.search(text)), paths


def summarize_turn(turn: Optional[dict]) -> Optional[dict]:
    """一轮 → 精简进度：用户的要求、Codex 最后说的话、计划、跑过的命令、改动的文件、在等的命令。"""
    if not isinstance(turn, dict):
        return None
    items = [item for item in turn.get("items") or [] if isinstance(item, dict)]
    request = next((_user_text(item) for item in items if item.get("type") == "userMessage"), "")
    replies = [item.get("text", "") for item in items if item.get("type") == "agentMessage" and item.get("text")]
    plans = [item.get("text", "") for item in items if item.get("type") == "plan" and item.get("text")]
    commands = [item for item in items if item.get("type") == "commandExecution"]
    files: List[str] = []
    for item in items:
        if item.get("type") == "fileChange":
            for change in item.get("changes") or []:
                path = _home_short(str((change or {}).get("path") or ""))
                if path and path not in files:
                    files.append(path)
    writes, write_paths = 0, []
    for item in commands:
        wrote, paths = shell_writes(item.get("command") or "", item.get("commandActions"))
        writes += wrote
        for path in paths:
            short = _clip(_home_short(path), 160)
            if short not in write_paths and short not in files:
                write_paths.append(short)
    running = next((item for item in reversed(commands) if item.get("status") == "inProgress"), None)
    error = turn.get("error") if isinstance(turn.get("error"), dict) else None
    return {
        "id": str(turn.get("id") or ""),
        "status": str(turn.get("status") or ""),
        "started_at": turn.get("startedAt"),
        "completed_at": turn.get("completedAt"),
        "duration_ms": turn.get("durationMs"),
        "request": _clip(request, _TEXT["request"]),
        "reply": _clip(replies[-1] if replies else "", _TEXT["reply"], tail=True),
        "plan": _clip(plans[-1] if plans else "", _TEXT["plan"], tail=True),
        "commands": len(commands),
        "last_command": _clip((commands[-1] or {}).get("command") if commands else "", _TEXT["command"]),
        "running_command": _clip((running or {}).get("command"), _TEXT["command"]),
        "files": files[:20],
        "file_count": len(files),
        "shell_writes": writes,
        # 重定向出来的日志（> build.log）排后面：先报真正的产出（文档、代码）
        "shell_write_paths": sorted(write_paths, key=lambda path: path.endswith((".log", ".tmp")) or "/tmp/" in path)[:10],
        "error": _clip((error or {}).get("message"), _TEXT["error"]),
    }


def summarize_thread(meta: dict, turn: Optional[dict], *, observed_at: float | None = None) -> dict:
    status = meta.get("status") if isinstance(meta.get("status"), dict) else {}
    thread = {
        "id": str(meta.get("id") or ""),
        "name": _clip(meta.get("name") or meta.get("preview") or "（未命名会话）", 60),
        "cwd": _home_short(str(meta.get("cwd") or "")),
        "status": str(status.get("type") or "unknown"),
        "flags": [str(flag) for flag in status.get("activeFlags") or []],
        "updated_at": meta.get("updatedAt"),
        "turn": summarize_turn(turn),
    }
    recent = thread["turn"] or {}
    report = None
    if thread["id"] and recent.get("id"):
        report = ExecutionSnapshot(
            ExecutionRef("codex", process_instance("codex.observer"), recent["id"],
                         session_id=thread["id"], turn_id=recent["id"]),
            codex_execution_state(thread["status"], recent["status"], thread["flags"]),
            time.time() if observed_at is None else observed_at,
            native_state=f"{thread['status']}:{recent['status']}",
            progress=ExecutionProgress(recent.get("plan") or recent.get("request") or ""),
            features=frozenset({"execution.observe"}))
    thread["execution"] = report.to_dict() if report else None
    return thread


def digest(threads: List[dict]) -> str:
    """判断「有没有变化」用：去掉一直在走的耗时，免得每轮都当成变化。"""
    stable = []
    for thread in threads:
        turn = dict(thread.get("turn") or {})
        turn.pop("duration_ms", None)
        item = {**thread, "turn": turn}
        if isinstance(thread.get("execution"), dict):
            item["execution"] = {key: value for key, value in thread["execution"].items() if key != "observed_at"}
        stable.append(item)
    return hashlib.sha256(json.dumps(stable, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


class CodexClient:
    """连 Codex 本机后台服务的最小 JSON-RPC 客户端。"""

    def __init__(self, path: Optional[Path] = None):
        self.path = path or socket_path()
        self._ws = None
        self._next = 0

    async def __aenter__(self) -> "CodexClient":
        from websockets.asyncio.client import unix_connect
        self._ws = await unix_connect(os.path.realpath(self.path), uri="ws://localhost/",
                                      max_size=16 * 1024 * 1024, open_timeout=10)
        await self.call("initialize", {"clientInfo": {"name": "vortocode-observer", "version": "1"}})
        await self._ws.send(json.dumps({"jsonrpc": "2.0", "method": "initialized"}))
        return self

    async def __aexit__(self, *_exc) -> None:
        if self._ws is not None:
            await self._ws.close()

    async def call(self, method: str, params: dict, timeout: float = 20) -> Any:
        self._next += 1
        rid = self._next
        await self._ws.send(json.dumps({"jsonrpc": "2.0", "id": rid, "method": method, "params": params}))
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(f"Codex 没有回应 {method}")
            message = json.loads(await asyncio.wait_for(self._ws.recv(), remaining))
            if message.get("id") != rid:
                continue                      # 通知和别的请求：观察员不处理
            if "error" in message:
                raise RuntimeError(f"Codex 拒绝 {method}：{_clip((message['error'] or {}).get('message'), 200)}")
            return message.get("result")


async def snapshot(client: CodexClient) -> List[dict]:
    """已加载（终端或 Codex 应用里开着）的会话，每个附最近一轮的精简进度。"""
    loaded = (await client.call("thread/loaded/list", {}) or {}).get("data") or []
    threads = []
    for thread_id in loaded[:_MAX_THREADS]:
        meta = (await client.call("thread/read", {"threadId": thread_id}) or {}).get("thread") or {}
        if not meta or meta.get("id") != thread_id:
            continue
        # 最近一轮读完整条目（要从里面取计划、命令和改动的文件）；只取需要的字段，走的是本机 socket
        try:
            page = await client.call("thread/turns/list", {"threadId": thread_id, "limit": 1,
                                                         "itemsView": "full", "sortDirection": "desc"})
            turns = (page or {}).get("data") or []
        except RuntimeError:   # 临时会话等不支持按轮读：只报状态，别让一个会话拖垮整轮快照
            turns = []
        threads.append(summarize_thread(meta, turns[0] if turns else None))
    threads.sort(key=lambda item: item.get("updated_at") or 0, reverse=True)
    return threads


def _report_payload(threads: List[dict]) -> dict:
    payload = {"threads": deepcopy(threads), "observed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
               "host": socket.gethostname()[:60]}
    idle = user_idle_seconds()
    if idle is not None:
        payload["user_idle_seconds"] = round(idle)
        # 声明：主人到 Mac 前 / 离开的那一刻会补报一次。常驻助理据此可以信任「上一份报告时在 = 现在还在」，
        # 不必要求报告本身是 5 分钟内的（Codex 空闲时心跳 10 分钟才一次）。
        payload["presence_reports"] = True
    return payload


async def post_report(threads: List[dict], *, http=None) -> bool:
    """Legacy transport retained for older callers and resident services."""
    target = report_target()
    if target is None:
        return False
    url, token = target
    payload = _report_payload(threads)
    if http is not None:
        return bool(await http(f"{url}/api/codex/report", payload, token))
    import aiohttp
    timeout = aiohttp.ClientTimeout(total=15)
    # trust_env=False：常驻电脑在局域网，别经代理
    async with aiohttp.ClientSession(timeout=timeout, trust_env=False) as session:
        async with session.post(f"{url}/api/codex/report", json=payload,
                                headers={"Authorization": f"Bearer {token}"}) as response:
            return response.status < 300


class ResidentHTTPError(RuntimeError):
    def __init__(self, status: int):
        self.status = status
        super().__init__(f"常驻助理返回 {status}")


async def resident_call(method: str, path: str, payload: Optional[dict] = None) -> dict:
    """调常驻助理的 Codex 接口（取待执行的动作、认领、报结果）。"""
    target = report_target()
    if target is None:
        raise RuntimeError("没有常驻助理的地址")
    url, token = target
    import aiohttp
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15), trust_env=False) as session:
        async with session.request(method, f"{url}{path}", json=payload,
                                   headers={"Authorization": f"Bearer {token}"}) as response:
            if response.status >= 300:
                raise ResidentHTTPError(response.status)
            return await response.json()


class ReportPublisher:
    """One producer loop; causal reports use the existing status/report routes.

    Keep a pending request unchanged until acknowledged. Losing the cursor fences
    this publisher; it must never silently read the new cursor and steal it back.
    """
    def __init__(self, *, call=None, generation_id: str | None = None):
        self.call = call or resident_call
        self.generation_id = generation_id or uuid.uuid4().hex
        self.producer_id = f"codex.observer:{socket.gethostname()[:60]}"
        self.mode: str | None = None
        self.cursor: ObservationCursor | None = None
        self.sequence = 0
        self.pending: dict | None = None
        self.superseded = False
        self._publishing = False

    async def initialize(self) -> None:
        if self.mode is not None:
            return
        status = await self.call("GET", "/api/codex/status")
        if not isinstance(status, dict):
            raise ValueError("进度状态回执无效")
        if "report_protocol" not in status:
            self.mode = "legacy"
            return
        if type(status["report_protocol"]) is not int or status["report_protocol"] != 1:
            raise ValueError("不支持常驻助理的上报顺序协议")
        if "report_cursor" not in status:
            raise ValueError("上报顺序协议缺少当前游标")
        cursor = status["report_cursor"]
        self.cursor = ObservationCursor.from_dict(cursor) if cursor is not None else None
        self.mode = "ordered"

    async def _send_pending(self) -> bool:
        assert self.pending is not None
        try:
            response = await self.call("POST", "/api/codex/report", deepcopy(self.pending))
        except ResidentHTTPError as error:
            if error.status == 409:
                self.superseded = True
                logger.info("Codex 观察员上报游标已被接替，停止旧来源上报")
                return False
            raise
        expected = ObservationOrder.from_dict(self.pending["report_order"]).cursor
        if (not isinstance(response, dict) or response.get("ok") is not True
                or ObservationCursor.from_dict(response.get("report_cursor")) != expected):
            raise ValueError("进度上报回执无法核对，保留原报告等待重传")
        self.cursor, self.sequence, self.pending = expected, expected.sequence, None
        return True

    async def publish(self, threads: List[dict]) -> bool:
        if self.superseded:
            return False
        if self._publishing:
            raise RuntimeError("同一个观察员不能并发上报")
        self._publishing = True
        try:
            await self.initialize()
            payload = _report_payload(threads)
            if self.mode == "legacy":
                response = await self.call("POST", "/api/codex/report", payload)
                return isinstance(response, dict) and response.get("ok") is True
            if self.pending is not None:
                prior = {key: value for key, value in self.pending.items() if key != "report_order"}
                if not await self._send_pending():
                    return False
                if prior == payload:
                    return True
            order = ObservationOrder(ObservationCursor(self.producer_id, self.generation_id, self.sequence + 1), self.cursor)
            self.pending = {**payload, "report_order": order.to_dict()}
            return await self._send_pending()
        finally:
            self._publishing = False


async def execute_action(client: CodexClient, action: dict, threads: List[dict]) -> tuple:
    """把一条已批准的动作交给 Codex。返回 (成功?, 失败原因)。"""
    thread = next((item for item in threads if item.get("id") == action.get("thread_id")), None)
    if thread is None:
        return False, "这个 Codex 会话已经关了"
    turn = thread.get("turn") or {}
    running = thread.get("status") == "active" and turn.get("status") == "inProgress" and turn.get("id")
    try:
        kind = action.get("kind")
        if kind not in {"message", "interrupt"}:
            return False, "不支持这项 Codex 操作"
        if action.get("kind") == "interrupt":
            if not running:
                return False, "它已经停了"
            if action.get("turn_id") and turn.get("id") != action.get("turn_id"):
                return False, "要叫停的那一轮已经结束了，没动新的这一轮"
        text = str(action.get("text") or "").strip()
        if kind == "message" and not text:
            return False, "要发的话是空的"
        # Re-read state after approval/claim. Automated suggestions stay bound to
        # their source turn; explicit user messages retain their original scope.
        expected = None
        if kind == "interrupt" or not action.get("session"):
            expected = str(action.get("turn_id") or turn.get("id") or "") or None
        await control_session(CodexBackend(client), str(thread["id"]), action=kind,
                              text=text, expected_turn_id=expected)
        return True, ""
    except Exception as error:  # noqa: BLE001 —— Codex 拒绝或超时：如实报回去
        return False, str(error)[:200]


# 已经交给 Codex、结果却没报上去的回执（常驻助理连不上）：下一轮先补报。只补报结果，绝不重发指令；
# 观察员进程重启就丢了，常驻助理那边超时后会标成「结果无法确认」。
_UNREPORTED: dict = {}


async def _report_result(resident, action_id: str, result: dict) -> None:
    try:
        await resident("POST", f"/api/codex/actions/{action_id}/result", result)
    except ResidentHTTPError as error:
        if error.status in (404, 409):        # 对方明确拒收（已有定论或已清理）：不再补报
            _UNREPORTED.pop(action_id, None)
            return
        _UNREPORTED[action_id] = result
        raise
    except Exception:  # noqa: BLE001 —— 网络断了：留着下一轮补报
        _UNREPORTED[action_id] = result
        raise
    _UNREPORTED.pop(action_id, None)


async def run_approved_actions(client: CodexClient, threads: List[dict], *, resident=resident_call) -> int:
    """取你批准过的动作 → 先认领（防止重复发）→ 交给 Codex → 报结果。返回处理了几条。"""
    for action_id, result in list(_UNREPORTED.items()):
        await _report_result(resident, action_id, result)
    data = await resident("GET", "/api/codex/actions?status=approved")
    done = 0
    for action in (data or {}).get("actions") or []:
        action_id = str(action.get("id") or "")
        if not action_id.startswith("cdx-"):
            continue
        try:
            await resident("POST", f"/api/codex/actions/{action_id}/claim")
        except Exception:  # noqa: BLE001 —— 别处已认领或状态变了：跳过
            continue
        ok, error = await execute_action(client, action, threads)
        await _report_result(resident, action_id, {"ok": ok, "error": error})
        done += 1
    return done


def owner_present(idle: Optional[float]) -> Optional[bool]:
    return None if idle is None else idle < PRESENT_IDLE_SECONDS


def should_report(*, current: str, last_digest: str, now: float, last_sent: float, busy: bool,
                  present: Optional[bool], last_present: Optional[bool]) -> bool:
    """Codex 有变化、心跳到点，或主人刚到 Mac 前 / 刚离开，就该报。"""
    due = HEARTBEAT_SECONDS if not busy else ACTIVE_HEARTBEAT_SECONDS
    return current != last_digest or now - last_sent >= due or present != last_present


async def observe_loop(stop: asyncio.Event, *, interval: float = INTERVAL_SECONDS,
                       monitor: ObserverMonitor | None = None) -> None:
    """每 interval 秒看一次 Codex；有变化、心跳到点或你刚到/刚离开 Mac，就报给常驻助理。Codex 没在跑就安静等着。"""
    last_digest, last_sent = "", 0.0
    last_present: Optional[bool] = None
    publisher = ReportPublisher()
    monitor = _MONITOR if monitor is None else monitor
    run_id = monitor.start()
    try:
        while not stop.is_set():
            stage = "codex"
            try:
                if socket_path().exists():
                    monitor.update(run_id, "connecting_codex", publisher=publisher)
                    async with CodexClient() as client:
                        # Read the predecessor before collecting a new native snapshot.
                        stage = "resident"
                        monitor.update(run_id, "connecting_resident", publisher=publisher)
                        await publisher.initialize()
                        stage = "codex"
                        monitor.update(run_id, "observing", publisher=publisher)
                        threads = await snapshot(client)
                        monitor.update(run_id, "observing", publisher=publisher, observed=True)
                        busy = any(thread["status"] == "active" for thread in threads)
                        now = time.monotonic()
                        current = digest(threads)
                        present = owner_present(user_idle_seconds())
                        if should_report(current=current, last_digest=last_digest, now=now, last_sent=last_sent,
                                         busy=busy, present=present, last_present=last_present):
                            stage = "resident"
                            monitor.update(run_id, "reporting", publisher=publisher)
                            if await publisher.publish(threads):
                                last_digest, last_sent, last_present = current, now, present
                                monitor.update(run_id, "ready", publisher=publisher, acknowledged=True)
                            else:
                                monitor.update(run_id, "superseded" if publisher.superseded else "retrying",
                                    publisher=publisher, error_code="source_replaced" if publisher.superseded else "invalid_receipt")
                        else:
                            monitor.update(run_id, "superseded" if publisher.superseded else "ready", publisher=publisher,
                                error_code="source_replaced" if publisher.superseded else "")
                        stage = "actions"
                        await run_approved_actions(client, threads)
                else:
                    monitor.update(run_id, "waiting_codex", publisher=publisher)
            except asyncio.CancelledError:
                raise
            except Exception as error:  # noqa: BLE001 —— 下轮重试；诊断不返回异常正文
                code = ("auth_failed" if isinstance(error, ResidentHTTPError) and error.status in {401, 403}
                        else "protocol_error" if stage == "resident" and isinstance(error, ValueError)
                        else {"codex": "codex_unavailable", "resident": "resident_unreachable", "actions": "actions_unavailable"}[stage])
                monitor.update(run_id, "superseded" if publisher.superseded else "retrying", publisher=publisher,
                               error_code="source_replaced" if publisher.superseded else code)
                logger.info("Codex 观察员这一轮没完成：%s", type(error).__name__)
            try:
                await asyncio.wait_for(stop.wait(), timeout=interval)
            except asyncio.TimeoutError:
                pass
    finally:
        monitor.update(run_id, "stopped", publisher=publisher)
