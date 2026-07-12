"""协议客户端事件泵（D1 单核化 PR-3）——attach 模式共用的 /ws 客户端（CLI 用，PR-4 TUI 复用）。

对着 gateway/protocol.py 的冻结面工作：连 gateway 的 /ws（`?sid=` 续会话 +
`Authorization: Bearer` 鉴权，与 REST 同源 #121）、发一条带 `rid` 的 agent 消息、把**本回合**
的协议事件按序回调端侧渲染器，直到收尾事件（agent_done/agent_error/agent_cancelled）。

- **rid 过滤**：只处理回带本回合 rid 的事件——init/历史回放/task_update/notice 等无 rid 的
  一律跳过，共享会话上也不会串台（这正是 PR-1 埋 rid 的用途）。
- **confirm 往返在泵内**：收到 agent_confirm → 调端侧 `confirm(text)` → 回 agent_confirm_response；
  端侧只管"怎么问人"，不碰协议。
- 端侧渲染回调全部可选、逐个 best-effort——渲染炸了不炸回合。
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import uuid
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional

from src.gateway import protocol as P


@dataclass
class TurnOutcome:
    """一个回合的收尾：status ∈ done|error|cancelled；reply=最终回复（多段 emit 以空行拼接）。"""
    status: str
    reply: str = ""
    error: str = ""

    @property
    def ok(self) -> bool:
        return self.status == "done"


def _ws_url(base_url: str, sid: Optional[str]) -> str:
    u = (base_url or "").strip().rstrip("/")
    if u.startswith("http://"):
        u = "ws://" + u[len("http://"):]
    elif u.startswith("https://"):
        u = "wss://" + u[len("https://"):]
    elif not u.startswith(("ws://", "wss://")):
        u = "ws://" + u
    return f"{u}/ws?sid={sid}" if sid else f"{u}/ws"


def _safe(cb: Optional[Callable], *args) -> None:
    """端侧渲染回调 best-effort：渲染炸了不炸回合。"""
    if cb is None:
        return
    try:
        cb(*args)
    except Exception:  # noqa: BLE001
        pass


def local_ref_to_data_url(ref: str, *, is_audio: bool = False) -> str:
    """把本地路径附件转成 serve 端可收的 data URL（服务端 sanitize 只收 data:/http(s)）。

    data:/http(s) 引用原样透传；attach 端（CLI/TUI）发附件前都过这一道。
    """
    from src.llm.content import audio_block, image_block
    if is_audio:
        blk = audio_block(ref)["input_audio"]
        return f"data:audio/{blk['format']};base64,{blk['data']}"
    return image_block(ref)["image_url"]["url"]


async def delete_session(base_url: str, sid: str, *, token: Optional[str] = None,
                         timeout: float = 5.0) -> bool:
    """删 serve 侧一个会话（REST，best-effort）——attach 端"全新开始"用（如 CLI 无 -c 时）。"""
    import aiohttp
    url = (base_url or "").strip().rstrip("/")
    if url.startswith(("ws://", "wss://")):
        url = ("http://" if url.startswith("ws://") else "https://") + url.split("://", 1)[1]
    elif not url.startswith(("http://", "https://")):
        url = "http://" + url
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    try:
        async with aiohttp.ClientSession() as s:
            async with s.delete(f"{url}/api/agent/sessions/{sid}", headers=headers,
                                timeout=aiohttp.ClientTimeout(total=timeout)) as r:
                return r.status < 400
    except Exception:  # noqa: BLE001 —— 删不掉不拦回合（顶多带上旧历史）
        return False


class ProtocolClient:
    """一条 /ws 连接上的协议客户端。async with 用法；连接期读掉 init（存 server_version）。"""

    def __init__(self, base_url: str, *, sid: Optional[str] = None,
                 token: Optional[str] = None, connect_timeout: float = 5.0):
        self._url = _ws_url(base_url, sid)
        self._token = (token or "").strip()
        self._connect_timeout = connect_timeout
        self._session = None
        self._ws = None
        self.server_version: Optional[int] = None    # init 的 v；与 PROTOCOL_VERSION 不同时端侧可警示

    async def __aenter__(self) -> "ProtocolClient":
        import aiohttp
        headers = {"Authorization": f"Bearer {self._token}"} if self._token else {}
        self._session = aiohttp.ClientSession()
        try:
            self._ws = await asyncio.wait_for(
                self._session.ws_connect(self._url, headers=headers),
                timeout=self._connect_timeout)
            # 服务端连上即发 init（带协议版本 v）；读到收下，读不到也不算失败（老服务端）
            msg = await asyncio.wait_for(self._ws.receive(), timeout=self._connect_timeout)
            evt = self._decode(msg)
            if evt is not None and evt.get("type") == P.INIT:
                self.server_version = evt.get("v")
        except BaseException:
            await self._session.close()
            raise
        return self

    async def __aexit__(self, *exc) -> None:
        if self._ws is not None:
            with contextlib.suppress(Exception):
                await self._ws.close()
        if self._session is not None:
            with contextlib.suppress(Exception):
                await self._session.close()

    @staticmethod
    def _decode(msg) -> Optional[Dict[str, Any]]:
        import aiohttp
        if msg.type != aiohttp.WSMsgType.TEXT:
            return None
        try:
            data = json.loads(msg.data)
        except ValueError:
            return None
        return data if isinstance(data, dict) else None

    async def cancel(self) -> None:
        """发「停止」（agent_cancel）；服务端会以 agent_cancelled 收尾在跑的回合。"""
        if self._ws is not None:
            with contextlib.suppress(Exception):
                await self._ws.send_json({"type": P.AGENT_CANCEL})

    @staticmethod
    async def _dispatch_confirm(evt: Dict[str, Any],
                                confirm: Optional[Callable[..., Any]]) -> bool:
        """把一条 agent_confirm 事件转给端侧 confirm，返回 ok（缺省/异常一律拒，安全优先）。

        污点 fail-closed 有两处易错点，都在这里钉死：
        1. tainted 走**结构化字段**。老 serve 不发它、或显式发 ``null`` → 都按"**可能有污点**"处理
           （``bool(evt.get("tainted", True))`` 只挡缺键，挡不住显式 null → bool(None)=False 会 fail-open）。
        2. 老 confirm 回调不收 tainted 关键字会抛 TypeError → 兼容重试一次；**重试再抛也要 fail-closed**，
           绝不能让异常冒出接收循环把整个回合带崩（外层 except 是 try 的兄弟，兜不住 handler 内的抛出）。
        """
        if confirm is None:
            return False
        text = str(evt.get("text", ""))
        raw = evt.get("tainted", True)
        tainted = True if raw is None else bool(raw)
        try:
            return bool(await confirm(text, tainted=tainted))
        except TypeError:
            try:
                return bool(await confirm(text))
            except Exception:  # noqa: BLE001 —— 兼容重试仍失败：拒，别把接收循环带崩
                return False
        except Exception:  # noqa: BLE001
            return False

    async def run_turn(self, prompt: str, *, mode: str = "plan",
                       images: Optional[list] = None, audio: Optional[list] = None,
                       on_say: Optional[Callable[[str], None]] = None,
                       on_stream: Optional[Callable[[str], None]] = None,
                       on_emit: Optional[Callable[[str], None]] = None,
                       on_plan: Optional[Callable[[list], None]] = None,
                       on_reasoning: Optional[Callable[[str], None]] = None,
                       confirm: Optional[Callable[[str], Any]] = None,
                       idle_timeout: float = 600.0) -> TurnOutcome:
        """跑一个回合到收尾。confirm 为 async(text)->bool；缺省一律拒绝（与 headless 同：安全优先）。

        on_reasoning 非空才向 serve 订阅思维链（want_reasoning——不用不发，省流量）。
        idle_timeout：两个事件之间的最长安静时间（防服务端挂死泵永等）；超时按 error 收尾。
        """
        assert self._ws is not None, "未连接（用 async with ProtocolClient(...)）"
        rid = uuid.uuid4().hex[:12]
        req: Dict[str, Any] = {"type": P.AGENT, "text": prompt, "mode": mode, "rid": rid}
        if images:
            req["images"] = images
        if audio:
            req["audio"] = audio
        if on_reasoning is not None:
            req["want_reasoning"] = True
        await self._ws.send_json(req)

        emits: list = []
        while True:
            try:
                msg = await asyncio.wait_for(self._ws.receive(), timeout=idle_timeout)
            except asyncio.TimeoutError:
                return TurnOutcome("error", reply="\n\n".join(emits),
                                   error=f"等服务端事件超时（{idle_timeout:.0f}s 无动静）")
            evt = self._decode(msg)
            if evt is None:
                import aiohttp
                if msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.CLOSE,
                                aiohttp.WSMsgType.ERROR):
                    return TurnOutcome("error", reply="\n\n".join(emits), error="连接断开")
                continue
            if evt.get("rid") != rid:            # 非本回合事件（历史回放/广播等）一律跳过
                continue
            etype = evt.get("type")
            if etype == P.AGENT_SAY:
                _safe(on_say, evt.get("text", ""))
            elif etype == P.AGENT_STREAM:
                _safe(on_stream, evt.get("text", ""))
            elif etype == P.AGENT_REASONING:
                _safe(on_reasoning, evt.get("text", ""))
            elif etype == P.AGENT_EMIT:
                emits.append(str(evt.get("text", "")))
                _safe(on_emit, evt.get("text", ""))
            elif etype == P.AGENT_PLAN:
                _safe(on_plan, evt.get("items") or [])
            elif etype == P.AGENT_CONFIRM:       # 确认往返：问端侧，应答回传（缺省拒绝，安全优先）
                ok = await self._dispatch_confirm(evt, confirm)
                await self._ws.send_json(
                    {"type": P.AGENT_CONFIRM_RESPONSE, "id": evt.get("id"), "ok": ok})
            elif etype == P.AGENT_DONE:
                return TurnOutcome("done", reply="\n\n".join(emits))
            elif etype == P.AGENT_ERROR:
                return TurnOutcome("error", reply="\n\n".join(emits),
                                   error=str(evt.get("text", "")))
            elif etype == P.AGENT_CANCELLED:
                return TurnOutcome("cancelled", reply="\n\n".join(emits))
            # 其余带 rid 的未知事件：宽进跳过（前向兼容）
