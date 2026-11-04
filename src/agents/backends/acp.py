"""ACP（Agent Client Protocol）通用客户端（ADR-014 S2，任务上下文的外部框架成员）。

一个适配器覆盖所有说 ACP 的框架：DeepSeek Harness（``dsh --profile acp``）、OpenCode（``opencode acp``），
以及 Gemini CLI、Qwen Code 等。协议是 stdio 上逐行的 JSON-RPC 2.0：

    initialize → session/new → session/prompt（期间框架发 session/update 通知、向我们请求权限 / 读写文件）

我们这边的规则（不靠框架自觉）：

- **权限**：框架每次要执行命令、改文件、联网，都发 ``session/request_permission``；交给调用方注入的
  ``decide`` 判定。放行只选 ``allow_once``，从不选 ``allow_always``——不让框架记住授权、绕过下一次判定。
  判定出错按拒绝处理。
- **文件**：只有调用方给了 ``read_root`` / ``write_root`` 才向框架声明读 / 写能力；路径必须落在根内。
  写文件另外再过一次 ``decide``（框架不一定先请求权限）。终端能力不声明。
- **污点**：看到 ``fetch`` / ``search`` / ``execute`` / ``other`` 或不认识的工具类别就记为可能联网——命令
  也能出网，宁可多标。只有 read / edit / delete / move / think / switch_mode 视为本地。
- **结束**：超时先发 ``session/cancel``，给框架几秒收尾，然后结束整个进程组。子进程环境由调用方给
  （通常是 ``external_workers.worker_env()``，已剥离操作密钥与 GitHub 令牌）。

本模块只说协议，不读任务台账、不建调度器、不授予权限；不得导入 workflow / gateway / scheduling / web / im。
"""
from __future__ import annotations

import asyncio
import inspect
import json
import os
import signal
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional, Union

PROTOCOL_VERSION = 1
LOCAL_KINDS = frozenset({"read", "edit", "delete", "move", "think", "switch_mode"})
_LINE_LIMIT = 8 * 1024 * 1024
_STDERR_TAIL = 4000
_MAX_TEXT = 200_000
_MAX_TOOL_CALLS = 200
_CANCEL_GRACE = 5.0

# 已知框架的启动命令。可执行文件找不到时由调用方报「未安装」。
COMMANDS: Dict[str, List[str]] = {
    "deepseek-harness": ["dsh", "--profile", "acp"],
    "opencode": ["opencode", "acp"],
}


def framework_env(name: Optional[str]) -> Dict[str, str]:
    """框架子进程的环境：剥离操作密钥与 GitHub 令牌（同 Codex / Claude Code 执行者）。

    常驻模式（设了 ``VORTOCODE_WORKER_HOME``）下，``name`` 框架的 HOME 与 XDG 目录指到专用目录，登录与配置
    不碰个人账号；本机模式不改。"""
    from src.agents.external_workers import worker_env, worker_home
    env = worker_env()
    home = worker_home()
    if home is not None and name:
        base = home / name
        for sub in ("", ".config", ".local/share", ".local/state", ".cache"):
            (base / sub).mkdir(mode=0o700, parents=True, exist_ok=True)
        env.update(HOME=str(base), XDG_CONFIG_HOME=str(base / ".config"), XDG_DATA_HOME=str(base / ".local/share"),
                   XDG_STATE_HOME=str(base / ".local/state"), XDG_CACHE_HOME=str(base / ".cache"))
    return env


class AcpError(RuntimeError):
    """框架没按协议走（版本不符、请求报错、进程提前退出）。"""


@dataclass
class PermissionAsk:
    """框架提出的一次权限请求，或我们替写文件合成的请求。"""
    title: str
    kind: str
    raw_input: Any = None
    locations: List[str] = field(default_factory=list)


Decide = Callable[[PermissionAsk], Union[bool, Awaitable[bool]]]


@dataclass
class AcpResult:
    text: str = ""
    stop_reason: str = ""
    error: str = ""
    networked: bool = False
    tool_calls: List[Dict[str, str]] = field(default_factory=list)
    permissions: List[Dict[str, Any]] = field(default_factory=list)
    followed_up: bool = False


def _deny(_ask: PermissionAsk) -> bool:
    return False


class AcpSession:
    """跑一次 ACP 会话：起进程、发一条提示、收齐结果、关进程。一个实例只用一次。"""

    def __init__(self, argv: List[str], *, cwd: str, env: Optional[Dict[str, str]] = None,
                 decide: Decide = _deny, read_root: Optional[str] = None, write_root: Optional[str] = None):
        if not argv:
            raise ValueError("缺少启动命令")
        self.argv = list(argv)
        self.cwd = str(Path(cwd).resolve())
        self.env = env if env is not None else framework_env(None)
        self.decide = decide
        self.read_root = str(Path(read_root).resolve()) if read_root else None
        self.write_root = str(Path(write_root).resolve()) if write_root else None
        self.result = AcpResult()
        self._proc: Optional[asyncio.subprocess.Process] = None
        self._pending: Dict[int, asyncio.Future] = {}
        self._next_id = 0
        self._session_id = ""
        self._stderr = b""
        self._calls: Dict[str, Dict[str, str]] = {}
        self._tasks: List[asyncio.Task] = []
        self._write_lock = asyncio.Lock()

    # ── 对外 ──────────────────────────────────────────────────────────
    async def run(self, prompt: str, *, timeout: float, followup: Optional[str] = None) -> AcpResult:
        """``followup``：第一条提示做完却没交回任何文字时，在同一个会话里再发这一条（只发一次，共用时限）。
        真机 2026-11-04：OpenCode 某个动作被拒后直接结束本轮，什么都没交回。"""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        try:
            await self._start()
            await asyncio.wait_for(self._handshake(), timeout=min(timeout, 60))
            try:
                for index, text in enumerate([prompt, followup]):
                    if index and (not text or self.result.text.strip() or self.result.stop_reason != "end_turn"):
                        break
                    remaining = deadline - loop.time()
                    if remaining <= 0:
                        raise asyncio.TimeoutError
                    response = await asyncio.wait_for(self._request("session/prompt", {
                        "sessionId": self._session_id, "prompt": [{"type": "text", "text": text}]}), timeout=remaining)
                    self.result.stop_reason = str((response or {}).get("stopReason") or "")
                    self.result.followed_up = bool(index)
            except asyncio.TimeoutError:
                self.result.error = "超过单轮时限"
                await self._cancel_turn()
        except asyncio.TimeoutError:
            self.result.error = "框架启动超时"
        except AcpError as error:
            self.result.error = str(error)[:500]
        except FileNotFoundError:
            self.result.error = f"没有找到 {self.argv[0]}：常驻电脑上还没装这个框架"
        finally:
            await self._close()
        self.result.tool_calls = list(self._calls.values())[-_MAX_TOOL_CALLS:]
        return self.result

    # ── 进程 ──────────────────────────────────────────────────────────
    async def _start(self) -> None:
        self._proc = await asyncio.create_subprocess_exec(
            *self.argv, cwd=self.cwd, env=self.env, limit=_LINE_LIMIT, start_new_session=True,
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        self._tasks = [asyncio.create_task(self._read_stdout()), asyncio.create_task(self._read_stderr())]

    async def _close(self) -> None:
        proc = self._proc
        if proc is not None and proc.returncode is None:
            try:
                if proc.stdin is not None:
                    proc.stdin.close()
                await asyncio.wait_for(proc.wait(), timeout=2)
            except (asyncio.TimeoutError, OSError, RuntimeError):
                self._kill_group(signal.SIGTERM)
                try:
                    await asyncio.wait_for(proc.wait(), timeout=3)
                except asyncio.TimeoutError:
                    self._kill_group(signal.SIGKILL)
                    await proc.wait()
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._fail_pending(AcpError("框架进程已结束"))

    def _kill_group(self, sig: int) -> None:
        if self._proc is None or self._proc.returncode is not None:
            return
        try:
            os.killpg(self._proc.pid, sig)
        except (ProcessLookupError, PermissionError):
            pass

    async def _cancel_turn(self) -> None:
        if self._session_id:
            try:
                await self._send({"jsonrpc": "2.0", "method": "session/cancel", "params": {"sessionId": self._session_id}})
            except (OSError, RuntimeError):
                return
        # 给框架一点时间收尾（它会以 stopReason=cancelled 回应），之后由 _close 结束进程组
        await asyncio.sleep(_CANCEL_GRACE)

    def _stderr_tail(self) -> str:
        return self._stderr.decode("utf-8", "replace").strip()[-300:]

    def _fail_pending(self, error: Exception) -> None:
        for future in self._pending.values():
            if not future.done():
                future.set_exception(error)
        self._pending.clear()

    # ── 收发 ──────────────────────────────────────────────────────────
    async def _send(self, message: Dict[str, Any]) -> None:
        proc = self._proc
        if proc is None or proc.stdin is None:
            raise AcpError("框架进程没起来")
        data = (json.dumps(message, ensure_ascii=False) + "\n").encode("utf-8")
        async with self._write_lock:
            proc.stdin.write(data)
            await proc.stdin.drain()

    async def _request(self, method: str, params: Dict[str, Any]) -> Any:
        self._next_id += 1
        rid = self._next_id
        future: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending[rid] = future
        await self._send({"jsonrpc": "2.0", "id": rid, "method": method, "params": params})
        return await future

    async def _read_stderr(self) -> None:
        assert self._proc is not None and self._proc.stderr is not None
        while True:
            chunk = await self._proc.stderr.read(4096)
            if not chunk:
                return
            self._stderr = (self._stderr + chunk)[-_STDERR_TAIL:]

    async def _read_stdout(self) -> None:
        assert self._proc is not None and self._proc.stdout is not None
        try:
            while True:
                line = await self._proc.stdout.readline()
                if not line:
                    break
                try:
                    message = json.loads(line)
                except ValueError:
                    continue                      # 框架往 stdout 打了日志：跳过，不当协议
                if isinstance(message, dict):
                    await self._dispatch(message)
        except (ValueError, asyncio.LimitOverrunError):
            pass
        tail = self._stderr_tail()
        self._fail_pending(AcpError("框架进程提前退出" + (f"：{tail}" if tail else "")))

    async def _dispatch(self, message: Dict[str, Any]) -> None:
        if "method" not in message:
            rid = message.get("id")
            future = self._pending.pop(rid, None) if isinstance(rid, int) else None
            if future is None or future.done():
                return
            if "error" in message:
                error = message["error"] if isinstance(message["error"], dict) else {}
                future.set_exception(AcpError(f"框架报错：{str(error.get('message') or error)[:300]}"))
            else:
                future.set_result(message.get("result"))
            return
        method, params = str(message["method"]), message.get("params") or {}
        if "id" not in message:
            if method == "session/update":
                self._on_update(params.get("update") or {})
            return
        # 框架向我们发的请求：放到单独任务里处理，判定慢也不堵住读循环
        self._tasks.append(asyncio.create_task(self._answer(message["id"], method, params)))

    async def _answer(self, rid: Any, method: str, params: Dict[str, Any]) -> None:
        try:
            if method == "session/request_permission":
                result: Any = await self._permission(params)
            elif method == "fs/read_text_file":
                result = self._read_file(params)
            elif method == "fs/write_text_file":
                await self._write_file(params)
                result = None
            else:
                await self._send({"jsonrpc": "2.0", "id": rid, "error": {"code": -32601, "message": f"不支持 {method}"}})
                return
            await self._send({"jsonrpc": "2.0", "id": rid, "result": result})
        except _Refused as refused:
            await self._send({"jsonrpc": "2.0", "id": rid, "error": {"code": -32000, "message": str(refused)}})
        except (OSError, RuntimeError):
            return

    # ── 握手与通知 ─────────────────────────────────────────────────────
    async def _handshake(self) -> None:
        init = await self._request("initialize", {
            "protocolVersion": PROTOCOL_VERSION,
            "clientCapabilities": {"fs": {"readTextFile": self.read_root is not None,
                                          "writeTextFile": self.write_root is not None},
                                   "terminal": False}})
        version = (init or {}).get("protocolVersion")
        if version != PROTOCOL_VERSION:
            raise AcpError(f"框架的 ACP 版本是 {version}，这里只支持 {PROTOCOL_VERSION}")
        session = await self._request("session/new", {"cwd": self.cwd, "mcpServers": []})
        self._session_id = str((session or {}).get("sessionId") or "")
        if not self._session_id:
            raise AcpError("框架没有返回会话 ID")

    def _on_update(self, update: Dict[str, Any]) -> None:
        kind = update.get("sessionUpdate")
        if kind == "agent_message_chunk":
            content = update.get("content") or {}
            if content.get("type") == "text" and len(self.result.text) < _MAX_TEXT:
                self.result.text += str(content.get("text") or "")
        elif kind in ("tool_call", "tool_call_update"):
            cid = str(update.get("toolCallId") or "")
            entry = self._calls.setdefault(cid, {"id": cid, "title": "", "kind": "", "status": ""})
            for key in ("title", "kind", "status"):
                if update.get(key):
                    entry[key] = str(update[key])[:200]
            if entry["kind"] and entry["kind"] not in LOCAL_KINDS:
                self.result.networked = True
            if len(self._calls) > _MAX_TOOL_CALLS * 2:
                for old in list(self._calls)[:_MAX_TOOL_CALLS]:
                    del self._calls[old]

    # ── 权限与文件 ─────────────────────────────────────────────────────
    async def _judge(self, ask: PermissionAsk) -> bool:
        try:
            verdict = self.decide(ask)
            if inspect.isawaitable(verdict):
                verdict = await verdict
            allowed = verdict is True
        except Exception:  # noqa: BLE001 —— 判定出错按拒绝
            allowed = False
        self.result.permissions.append({"title": ask.title[:200], "kind": ask.kind, "allowed": allowed})
        return allowed

    async def _permission(self, params: Dict[str, Any]) -> Dict[str, Any]:
        call = params.get("toolCall") or {}
        kind = str(call.get("kind") or "other")
        if kind not in LOCAL_KINDS:
            self.result.networked = True          # 要了权限就算可能出网，不管最后放没放
        locations = [str(item.get("path")) for item in call.get("locations") or [] if isinstance(item, dict) and item.get("path")]
        ask = PermissionAsk(title=str(call.get("title") or kind), kind=kind, raw_input=call.get("rawInput"), locations=locations)
        allowed = await self._judge(ask)
        options = [item for item in params.get("options") or [] if isinstance(item, dict)]
        wanted = "allow_once" if allowed else "reject_once"
        chosen = next((item for item in options if item.get("kind") == wanted), None)
        if chosen is None:
            return {"outcome": {"outcome": "cancelled"}}
        return {"outcome": {"outcome": "selected", "optionId": chosen.get("optionId")}}

    @staticmethod
    def _inside(root: Optional[str], path: Any) -> Optional[Path]:
        from src.utils.paths import resolve_within
        return resolve_within(root, path) if root else None

    def _read_file(self, params: Dict[str, Any]) -> Dict[str, Any]:
        target = self._inside(self.read_root, params.get("path"))
        if target is None:
            raise _Refused("只能读工作目录里的文件")
        text = target.read_text(encoding="utf-8", errors="replace")
        line, limit = params.get("line"), params.get("limit")
        if isinstance(line, int) or isinstance(limit, int):
            lines = text.splitlines(keepends=True)
            start = max(int(line or 1) - 1, 0)
            text = "".join(lines[start:start + int(limit)] if isinstance(limit, int) else lines[start:])
        return {"content": text}

    async def _write_file(self, params: Dict[str, Any]) -> None:
        target = self._inside(self.write_root, params.get("path"))
        if target is None:
            raise _Refused("只能写工作目录里的文件")
        if not await self._judge(PermissionAsk(title=f"写入 {target}", kind="edit", locations=[str(target)])):
            raise _Refused("写文件没有获准")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(str(params.get("content") or ""), encoding="utf-8")


class _Refused(Exception):
    pass
