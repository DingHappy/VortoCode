"""钉钉 Stream 模式适配器——出站 WebSocket 长连（**零入站暴露**，无需公网 IP/域名）。

协议（open-dingtalk 协议文档）：① POST /v1.0/gateway/connections/open（clientId/secret + 订阅）
拿 {endpoint, ticket} → ② 连 wss `{endpoint}/connect?ticket=`；③ 收 JSON 帧（headers.topic + data
字符串）；SYSTEM/ping 回 pong（带 opaque）、SYSTEM/disconnect 重连；机器人消息 topic
`/v1.0/im/bot/messages/get` 要回 ACK（echo messageId），data 里有 senderStaffId/sessionWebhook/
text.content；④ 回复走消息里的 sessionWebhook（POST text）。

两处 v1 取舍（钉钉与 Telegram 的能力差异）：
- **文本式确认**：钉钉简单机器人无内联按钮（互动卡片需卡片平台+回调注册），故确认发「回复 y/n」，
  适配器把主人的 y/n 回复**翻译成 bridge 的 callback 事件**（bridge 逻辑不变）。
- **不支持编辑消息**（edits_supported=False）：进度只能发新消息，bridge 据此放慢节流。

transport 可注入（connect_fn/reply_fn）：真实层走 aiohttp（默认）；测试注入假的即可不触网跑全逻辑。
真实 transport（_default_connect/_default_reply）需你的钉钉企业内机器人凭证做真机验证。
"""

from __future__ import annotations

import asyncio
import json
from typing import AsyncIterator, Awaitable, Callable, Optional

from .channel import ChannelAdapter, ChannelEvent

BOT_TOPIC = "/v1.0/im/bot/messages/get"
_APPROVE = {"y", "yes", "批准", "同意", "确认", "ok", "好"}
_DENY = {"n", "no", "拒绝", "否", "取消", "不"}

# connect_fn: async () -> ws-like（有 async recv()->str|None / async send(str) / async close()）
ConnectFn = Callable[[], Awaitable[object]]
# reply_fn: async (sessionWebhook, payload_dict) -> None
ReplyFn = Callable[[str, dict], Awaitable[None]]


class _Disconnect(Exception):
    pass


class DingTalkAdapter(ChannelAdapter):
    edits_supported = False        # 钉钉不支持编辑历史消息 → 进度发新消息（bridge 放慢节流）

    def __init__(self, client_id: str, client_secret: str, owner_id: str, *,
                 connect_fn: Optional[ConnectFn] = None, reply_fn: Optional[ReplyFn] = None):
        self._cid = client_id
        self._secret = client_secret
        self.owner_id = str(owner_id)
        self._connect_fn = connect_fn or self._default_connect
        self._reply_fn = reply_fn or self._default_reply
        self._session = None
        self._webhook: Optional[str] = None       # 最近一条主人消息的 sessionWebhook（回复目标）
        self._awaiting_confirm: Optional[str] = None   # 文本式确认：待回 y/n 的 callback_id

    # ------------------------------------------------------------ ChannelAdapter
    async def poll(self) -> AsyncIterator[ChannelEvent]:
        backoff = 1.0
        while True:
            try:
                ws = await self._connect_fn()
            except Exception:  # noqa: BLE001 —— 建连失败退避重试
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30.0)
                continue
            backoff = 1.0
            try:
                while True:
                    raw = await ws.recv()
                    if raw is None:               # 连接关闭
                        break
                    for ev in await self._handle_frame(ws, raw):
                        yield ev
            except _Disconnect:
                pass
            except Exception:  # noqa: BLE001 —— 收帧异常：断开重连
                pass
            finally:
                try:
                    await ws.close()
                except Exception:  # noqa: BLE001
                    pass
            await asyncio.sleep(1.0)              # 重连间隔

    async def _handle_frame(self, ws, raw: str) -> list:
        try:
            frame = json.loads(raw)
        except Exception:  # noqa: BLE001
            return []
        headers = frame.get("headers") or {}
        topic = headers.get("topic")
        mid = headers.get("messageId")
        if (frame.get("type") == "SYSTEM") or topic in ("ping", "disconnect"):
            if topic == "ping":                   # 心跳：回 pong（带回 opaque）
                data = _loads(frame.get("data"))
                await ws.send(_resp(mid, {"opaque": data.get("opaque")}))
            elif topic == "disconnect":           # 服务端要求断开 → 重连
                raise _Disconnect()
            return []
        if topic == BOT_TOPIC:
            await ws.send(_resp(mid, {"response": None}))   # ACK（回 echo messageId）
            data = _loads(frame.get("data"))
            sender = str(data.get("senderStaffId", ""))
            wh = data.get("sessionWebhook")
            if wh:
                self._webhook = wh                # 更新回复目标
            text = ((data.get("text") or {}).get("content") or "").strip()
            ev = self._to_event(sender, text, is_group=_is_group(data),
                                mentioned=bool(data.get("isInAtList")))
            return [ev] if ev is not None else []
        return []

    def _to_event(self, sender: str, text: str, *, is_group: bool = False,
                  mentioned: bool = False) -> Optional[ChannelEvent]:
        if not text:
            return None
        # 文本式确认：pending 且是主人回的 y/n → 翻成 callback（bridge 据此解开确认 Future）。
        # **群聊标记必须一起带过去**：钉钉的"按钮"其实是一条普通群消息，若这里把 is_group 抹平，
        # 群里任何人一句 "y" 就能替主人批准——群提及门必须照样管得住这条伪 callback。
        if self._awaiting_confirm and sender == self.owner_id:
            low = text.strip().lower()
            if low in _APPROVE or low in _DENY:
                cid = self._awaiting_confirm
                self._awaiting_confirm = None
                return ChannelEvent(kind="callback", sender_id=sender, callback_id=cid,
                                    approved=low in _APPROVE, is_group=is_group,
                                    mentioned=mentioned)
        return ChannelEvent(kind="message", sender_id=sender, text=text, is_group=is_group,
                            mentioned=mentioned)

    async def send_text(self, text: str) -> str:
        if self._webhook:
            await self._reply_fn(self._webhook, {"msgtype": "text", "text": {"content": _clip(text)}})
        return ""                                 # 钉钉无 message_id 供编辑

    async def edit_text(self, message_id: str, text: str) -> None:
        await self.send_text(text)                # 不支持编辑 → 发新消息（bridge 已放慢节流）

    async def send_confirm(self, text: str, callback_id: str) -> None:
        self._awaiting_confirm = callback_id
        await self.send_text(text + "\n\n请回复 **y**（批准）或 **n**（拒绝）。")

    async def ack_callback(self, event: ChannelEvent) -> None:
        return                                    # 文本式确认无需回执

    async def close(self) -> None:
        if self._session is not None:
            try:
                await self._session.close()
            except Exception:  # noqa: BLE001
                pass
            self._session = None

    # ------------------------------------------------------------ 真实 transport（需钉钉 app 验证）
    async def _default_connect(self):
        import aiohttp
        if self._session is None:
            self._session = aiohttp.ClientSession()
        async with self._session.post(
                "https://api.dingtalk.com/v1.0/gateway/connections/open",
                json={"clientId": self._cid, "clientSecret": self._secret,
                      "subscriptions": [{"type": "CALLBACK", "topic": BOT_TOPIC}],
                      "ua": "vortocode-im/1.0"}) as resp:
            data = await resp.json()
        endpoint = str(data["endpoint"]).rstrip("/")
        ticket = data["ticket"]
        base = endpoint if endpoint.endswith("/connect") else endpoint + "/connect"
        ws = await self._session.ws_connect(f"{base}?ticket={ticket}")
        return _AioWS(ws)

    async def _default_reply(self, webhook: str, payload: dict) -> None:
        import aiohttp
        if self._session is None:
            self._session = aiohttp.ClientSession()
        async with self._session.post(webhook, json=payload) as resp:
            await resp.read()


class _AioWS:
    """把 aiohttp WebSocket 包成 recv()/send()/close() 极简接口（bridge/adapter 只用这三个）。"""
    def __init__(self, ws):
        self._ws = ws

    async def recv(self) -> Optional[str]:
        import aiohttp
        msg = await self._ws.receive()
        if msg.type == aiohttp.WSMsgType.TEXT:
            return msg.data
        if msg.type in (aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
            return None
        return ""                                 # 忽略其它帧型

    async def send(self, s: str) -> None:
        await self._ws.send_str(s)

    async def close(self) -> None:
        await self._ws.close()


def _resp(message_id, data: dict) -> str:
    return json.dumps({"code": 200,
                       "headers": {"messageId": message_id, "contentType": "application/json"},
                       "message": "OK", "data": json.dumps(data, ensure_ascii=False)})


def _is_group(data: dict) -> bool:
    """钉钉回调的会话类型：conversationType "1"=单聊、"2"=群聊。

    缺字段按单聊（老回调/测试构造的最小帧）；**其它未知取值一律按群**（从严：要求显式 @）。
    群里是否 @ 到机器人看 `isInAtList`，不去解析正文里的 "@名字"（改个昵称就绕过了）。
    """
    conv = str(data.get("conversationType") or "").strip()
    return conv not in ("", "1")


def _loads(s) -> dict:
    if isinstance(s, dict):
        return s
    try:
        return json.loads(s) if s else {}
    except Exception:  # noqa: BLE001
        return {}


def _clip(text: str, limit: int = 4000) -> str:
    text = text or "（空）"
    return text if len(text) <= limit else text[: limit - 20] + "\n…（已截断）"
