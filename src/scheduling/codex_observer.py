"""Codex 观察员：跑在用户的 Mac 上（Desktop 的本机引擎里），看 Codex 在做什么，报给常驻助理。

Codex CLI（0.16x）的本机后台服务在 ``~/.codex/app-server-control/app-server-control.sock``
提供 app-server 协议（Unix socket 上的 WebSocket + JSON-RPC）。终端里的 Codex 界面就连在它上面，
所以这里看到的就是用户正在跑的那几个会话的实时状态。

只读：列出已加载的会话、读最近一轮的摘要，压成一份精简进度 POST 给常驻助理
（``VORTOCODE_CODEX_REPORT_URL`` + ``VORTOCODE_CODEX_REPORT_TOKEN``，由 Desktop 启动本机引擎时注入）。
不发完整对话和完整 diff：只有任务、最后几句说明、计划、改动的文件名、正在等批准的命令。
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
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, List, Optional

logger = logging.getLogger(__name__)

INTERVAL_SECONDS = 20
HEARTBEAT_SECONDS = 600          # 没变化也每 10 分钟报一次，让助理知道观察员还在
ACTIVE_HEARTBEAT_SECONDS = 60    # 有会话在跑或在等人时每分钟报一次（「等批准超过 2 分钟」靠它判断）
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


def summarize_thread(meta: dict, turn: Optional[dict]) -> dict:
    status = meta.get("status") if isinstance(meta.get("status"), dict) else {}
    return {
        "id": str(meta.get("id") or ""),
        "name": _clip(meta.get("name") or meta.get("preview") or "（未命名会话）", 60),
        "cwd": _home_short(str(meta.get("cwd") or "")),
        "status": str(status.get("type") or "unknown"),
        "flags": [str(flag) for flag in status.get("activeFlags") or []],
        "updated_at": meta.get("updatedAt"),
        "turn": summarize_turn(turn),
    }


def digest(threads: List[dict]) -> str:
    """判断「有没有变化」用：去掉一直在走的耗时，免得每轮都当成变化。"""
    stable = []
    for thread in threads:
        turn = dict(thread.get("turn") or {})
        turn.pop("duration_ms", None)
        stable.append({**thread, "turn": turn})
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
        if not meta:
            continue
        # 最近一轮读完整条目（要从里面取计划、命令和改动的文件）；只取需要的字段，走的是本机 socket
        try:
            page = await client.call("thread/turns/list", {"threadId": thread_id, "limit": 1, "itemsView": "full"})
            turns = (page or {}).get("data") or []
        except RuntimeError:   # 临时会话等不支持按轮读：只报状态，别让一个会话拖垮整轮快照
            turns = []
        threads.append(summarize_thread(meta, turns[0] if turns else None))
    threads.sort(key=lambda item: item.get("updated_at") or 0, reverse=True)
    return threads


async def post_report(threads: List[dict], *, http=None) -> bool:
    target = report_target()
    if target is None:
        return False
    url, token = target
    payload = {"threads": threads, "observed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
               "host": socket.gethostname()[:60]}
    if http is not None:
        return bool(await http(f"{url}/api/codex/report", payload, token))
    import aiohttp
    timeout = aiohttp.ClientTimeout(total=15)
    # trust_env=False：常驻电脑在局域网，别经代理
    async with aiohttp.ClientSession(timeout=timeout, trust_env=False) as session:
        async with session.post(f"{url}/api/codex/report", json=payload,
                                headers={"Authorization": f"Bearer {token}"}) as response:
            return response.status < 300


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
                raise RuntimeError(f"常驻助理返回 {response.status}")
            return await response.json()


async def execute_action(client: CodexClient, action: dict, threads: List[dict]) -> tuple:
    """把一条已批准的动作交给 Codex。返回 (成功?, 失败原因)。"""
    thread = next((item for item in threads if item.get("id") == action.get("thread_id")), None)
    if thread is None:
        return False, "这个 Codex 会话已经关了"
    turn = thread.get("turn") or {}
    running = thread.get("status") == "active" and turn.get("status") == "inProgress" and turn.get("id")
    try:
        if action.get("kind") == "interrupt":
            if not running:
                return False, "它已经停了"
            await client.call("turn/interrupt", {"threadId": thread["id"], "turnId": turn["id"]})
            return True, ""
        text = str(action.get("text") or "").strip()
        if not text:
            return False, "要发的话是空的"
        user_input = [{"type": "text", "text": text, "text_elements": []}]
        if running:   # 正在做：作为补充插进当前这一轮（轮次对不上 Codex 会拒绝，免得插错地方）
            await client.call("turn/steer", {"threadId": thread["id"], "expectedTurnId": turn["id"], "input": user_input})
        else:         # 空闲：开新的一轮
            await client.call("turn/start", {"threadId": thread["id"], "input": user_input})
        return True, ""
    except Exception as error:  # noqa: BLE001 —— Codex 拒绝或超时：如实报回去
        return False, str(error)[:200]


async def run_approved_actions(client: CodexClient, threads: List[dict], *, resident=resident_call) -> int:
    """取你批准过的动作 → 先认领（防止重复发）→ 交给 Codex → 报结果。返回处理了几条。"""
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
        await resident("POST", f"/api/codex/actions/{action_id}/result", {"ok": ok, "error": error})
        done += 1
    return done


async def observe_loop(stop: asyncio.Event, *, interval: float = INTERVAL_SECONDS) -> None:
    """每 interval 秒看一次 Codex；有变化（或心跳到点）就报给常驻助理。Codex 没在跑就安静等着。"""
    last_digest, last_sent = "", 0.0
    while not stop.is_set():
        try:
            if socket_path().exists():
                async with CodexClient() as client:
                    threads = await snapshot(client)
                    busy = any(thread["status"] == "active" for thread in threads)
                    now = time.monotonic()
                    current = digest(threads)
                    due = HEARTBEAT_SECONDS if not busy else ACTIVE_HEARTBEAT_SECONDS
                    if current != last_digest or now - last_sent >= due:
                        if await post_report(threads):
                            last_digest, last_sent = current, now
                    # 你批准过的「替你给 Codex 发话 / 叫停」
                    await run_approved_actions(client, threads)
        except asyncio.CancelledError:
            raise
        except Exception as error:  # noqa: BLE001 —— Codex 或常驻电脑暂时不可达：下一轮再试
            logger.info("Codex 观察员这一轮没完成：%s", type(error).__name__)
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval)
        except asyncio.TimeoutError:
            pass
