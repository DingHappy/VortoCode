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
import time
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
        self._webhook: Optional[str] = None       # 回复目标：最近一条**过了入站闸**的消息的
                                                  # sessionWebhook（只在 commit_reply_target 更新）
        self._awaiting_confirm: Optional[str] = None   # 文本式确认：待回 y/n 的 callback_id
        # 发媒体要主动调 API（sessionWebhook 只吃 text/markdown，发不了图和文件），而主动调用
        # 需要 access_token。钉钉这里有**两套**：新接口（发消息）与老接口（媒体上传）各一个，
        # 有效期都是 7200s。缓存 + 提前 5 分钟刷新——每次现取会被限流。
        self._tok_new: tuple = ("", 0.0)              # (token, 过期时刻 monotonic)
        self._tok_old: tuple = ("", 0.0)

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
            text = ((data.get("text") or {}).get("content") or "").strip()
            ev = self._to_event(sender, text, is_group=_is_group(data),
                                mentioned=bool(data.get("isInAtList")))
            if ev is not None:
                # 回复路由**不在收帧阶段采纳**：sessionWebhook 只随事件申报（reply_to），
                # 过了 bridge 三道闸才由 commit_reply_target 落成回复目标——否则白名单外的
                # 任何一条消息都能把后续回复劫到自己的会话（内容外泄 + 把主人的确认打聋）。
                ev.reply_to = data.get("sessionWebhook") or None
            return [ev] if ev is not None else []
        return []

    def commit_reply_target(self, event: ChannelEvent) -> None:
        if getattr(event, "reply_to", None):      # bridge 保证：只有过了闸的事件走到这里
            self._webhook = event.reply_to

    def _to_event(self, sender: str, text: str, *, is_group: bool = False,
                  mentioned: bool = False) -> Optional[ChannelEvent]:
        if not text:
            return None
        # 文本式确认：pending 且是主人回的 y/n → 翻成 callback（bridge 据此解开确认 Future）。
        # **群聊标记必须一起带过去**：钉钉的"按钮"其实是一条普通群消息，若这里把 is_group 抹平，
        # 群里任何人一句 "y" 就能替主人批准——群提及门必须照样管得住这条伪 callback。
        #
        # 群里还要**先过提及判断再消费** `_awaiting_confirm`（顺序不能反）：没 @ 的那条 y 反正会被
        # bridge 的群提及门丢掉，若在此之前就把待确认态清掉，这次确认就再没有第二次机会——主人明明
        # 回了 y，却只能干等 600s 超时=拒绝，且不知道自己漏了个 @。保留待确认态，主人补一条
        # "@机器人 y" 仍然生效。放行口径没有变宽：没 @ 的 y 依旧不批准任何东西。
        # （钉钉群回调的 text.content 已剔除 "@机器人" 前缀、只余正文，故补 @ 后仍匹配得上 y/n。）
        if self._awaiting_confirm and sender == self.owner_id and (mentioned or not is_group):
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

    # ------------------------------------------------------------ 媒体（图片/文件）
    async def _token(self, *, legacy: bool) -> str:
        """取 access_token，带缓存与提前刷新。legacy=True 是媒体上传用的老接口那套。"""
        import aiohttp
        cached, exp = self._tok_old if legacy else self._tok_new
        if cached and time.monotonic() < exp:
            return cached
        if self._session is None:
            self._session = aiohttp.ClientSession()
        if legacy:
            async with self._session.get("https://oapi.dingtalk.com/gettoken",
                                         params={"appkey": self._cid, "appsecret": self._secret}) as r:
                d = await r.json(content_type=None)
            tok, ttl = d.get("access_token", ""), int(d.get("expires_in") or 7200)
        else:
            async with self._session.post("https://api.dingtalk.com/v1.0/oauth2/accessToken",
                                          json={"appKey": self._cid, "appSecret": self._secret}) as r:
                d = await r.json(content_type=None)
            tok, ttl = d.get("accessToken", ""), int(d.get("expireIn") or 7200)
        if not tok:
            raise RuntimeError(f"取 access_token 失败: {str(d)[:160]}")
        slot = (tok, time.monotonic() + max(60, ttl - 300))   # 提前 5 分钟过期，避开边界
        if legacy:
            self._tok_old = slot
        else:
            self._tok_new = slot
        return tok

    async def _upload_media(self, path: str, kind: str) -> str:
        """上传媒体拿 media_id（只有老接口提供）。kind ∈ image/file。"""
        import os

        import aiohttp
        tok = await self._token(legacy=True)
        form = aiohttp.FormData()
        with open(path, "rb") as fh:
            form.add_field("media", fh.read(), filename=os.path.basename(path),
                           content_type="application/octet-stream")
        async with self._session.post("https://oapi.dingtalk.com/media/upload",
                                      params={"access_token": tok, "type": kind},
                                      data=form) as r:
            d = await r.json(content_type=None)
        mid = d.get("media_id")
        if not mid:
            raise RuntimeError(f"上传媒体失败: {str(d)[:160]}")
        return mid

    async def _send_msg(self, msg_key: str, msg_param: dict) -> None:
        """主动给**已配对 owner** 发一条消息。收件人恒为 owner，不受入站消息影响——
        目标固定是这条通道能作为出站面的前提（对比 web_fetch：URL 由模型决定，那才是真外传）。"""
        tok = await self._token(legacy=False)
        async with self._session.post(
                "https://api.dingtalk.com/v1.0/robot/oToMessages/batchSend",
                json={"robotCode": self._cid, "userIds": [self.owner_id],
                      "msgKey": msg_key, "msgParam": json.dumps(msg_param, ensure_ascii=False)},
                headers={"x-acs-dingtalk-access-token": tok}) as r:
            if r.status != 200:
                raise RuntimeError(f"发消息失败 HTTP {r.status}: {(await r.text())[:160]}")

    async def send_image(self, path: str, caption: str = "") -> bool:
        mid = await self._upload_media(path, "image")
        await self._send_msg("sampleImageMsg", {"photoURL": mid})
        if caption:
            await self.send_text(caption)         # 图片消息体不带说明文字，附注单独发
        return True

    async def send_file(self, path: str, caption: str = "") -> bool:
        import os
        mid = await self._upload_media(path, "file")
        name = os.path.basename(path)
        await self._send_msg("sampleFile", {"mediaId": mid, "fileName": name,
                                            "fileType": (name.rsplit(".", 1) + ["bin"])[1][:8]})
        if caption:
            await self.send_text(caption)
        return True

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
