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
import logging
import time
from pathlib import Path
from typing import AsyncIterator, Awaitable, Callable, Optional

from .channel import ChannelAdapter, ChannelEvent

_log = logging.getLogger("vortocode.im.dingtalk")

BOT_TOPIC = "/v1.0/im/bot/messages/get"
_APPROVE = {"y", "yes", "批准", "同意", "确认", "ok", "好"}
_DENY = {"n", "no", "拒绝", "否", "取消", "不"}

# connect_fn: async () -> ws-like（有 async recv()->str|None / async send(str) / async close()）
ConnectFn = Callable[[], Awaitable[object]]
# reply_fn: async (sessionWebhook, payload_dict) -> None
ReplyFn = Callable[[str, dict], Awaitable[None]]
# oto_fn: async (msg_key, msg_param_dict) -> None —— 主动推送（oToMessages/batchSend）transport
OtoFn = Callable[[str, dict], Awaitable[None]]


class _Disconnect(Exception):
    pass


class DingTalkAdapter(ChannelAdapter):
    edits_supported = False        # 钉钉不支持编辑历史消息 → 进度发新消息（bridge 放慢节流）

    def __init__(self, client_id: str, client_secret: str, owner_id: str, *,
                 connect_fn: Optional[ConnectFn] = None, reply_fn: Optional[ReplyFn] = None,
                 oto_fn: Optional[OtoFn] = None, card_sender=None,
                 inbox_dir: Optional[str] = None):
        self._cid = client_id
        self._secret = client_secret
        self.owner_id = str(owner_id)
        self._connect_fn = connect_fn or self._default_connect
        self._reply_fn = reply_fn or self._default_reply
        self._oto_fn = oto_fn                     # None = 真实 batchSend（见 _send_msg）
        # 互动卡片确认（配了模板才启用；没配则**建连 payload 与不加本功能时逐字节相同**）。
        # card_sender 可注入，测试不触网。
        from src.im.dingtalk_card import CardSender, card_template_id
        tpl = card_template_id()
        self._card = card_sender if card_sender is not None else (
            CardSender(tpl, self.owner_id, token_fn=self._token,
                       session_fn=self._ensure_session) if tpl else None)
        self._session = None
        self._webhook: Optional[str] = None       # 回复目标：最近一条**过了入站闸**的消息的
                                                  # sessionWebhook（只在 commit_reply_target 更新）
        self._awaiting_confirm: Optional[str] = None   # 文本式确认：待回 y/n 的 callback_id
        # 发媒体要主动调 API（sessionWebhook 只吃 text/markdown，发不了图和文件），而主动调用
        # 需要 access_token。钉钉这里有**两套**：新接口（发消息）与老接口（媒体上传）各一个，
        # 有效期都是 7200s。缓存 + 提前 5 分钟刷新——每次现取会被限流。
        self._tok_new: tuple = ("", 0.0)              # (token, 过期时刻 monotonic)
        self._tok_old: tuple = ("", 0.0)
        # 入站附件的落点：刻意**不在仓库工作区里**——用户发来的文件不该被当成代码改动收进 diff
        self._inbox_dir = str(Path(inbox_dir or Path.home() / ".vortocode" / "im_inbox"))

    # ------------------------------------------------------------ ChannelAdapter
    async def poll(self) -> AsyncIterator[ChannelEvent]:
        backoff = 1.0
        while True:
            try:
                ws = await self._connect_fn()
            except Exception as e:  # noqa: BLE001 —— 建连失败退避重试
                self.note_disconnected(f"建连失败: {type(e).__name__}: {e}")
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30.0)
                continue
            backoff = 1.0
            self.note_connected()
            try:
                while True:
                    raw = await ws.recv()
                    if raw is None:               # 连接关闭
                        break
                    self.note_frame()             # 任何一帧（含 ping）都是"线还活着"的证据
                    for ev in await self._handle_frame(ws, raw):
                        yield ev
            except _Disconnect:
                self.note_disconnected("服务端要求断开（正常轮转）")
            except Exception as e:  # noqa: BLE001 —— 收帧异常：断开重连
                self.note_disconnected(f"收帧异常: {type(e).__name__}: {e}")
            finally:
                self.note_disconnected()
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
        from src.im.dingtalk_card import CARD_CALLBACK_TOPIC, parse_card_callback
        if topic == CARD_CALLBACK_TOPIC:
            await ws.send(_resp(mid, {"response": None}))
            parsed = parse_card_callback(_loads(frame.get("data")))
            if parsed is None:
                return []                      # 认不出（含无发送者）→ 当没看见，绝不当成"批准"
            track, approved, sender = parsed
            # 帧处理阶段**不做任何状态变更**：不清文本待确认态、不刷卡片终态——这帧还没过
            # bridge 的三道入站闸（白名单 / 群提及 / 审批只认主人）。过了闸 bridge 才回调
            # ack_callback，状态在那里才动；否则陌生帧既能吃掉主人的文本 y/n 兜底，
            # 又能把卡片刷成"已批准"的假象。sender 也如实申报，绝不缺省成主人。
            return [ChannelEvent(kind="callback", sender_id=sender, callback_id=track,
                                 approved=approved, ack="card")]
        if topic == BOT_TOPIC:
            await ws.send(_resp(mid, {"response": None}))   # ACK（回 echo messageId）
            data = _loads(frame.get("data"))
            sender = str(data.get("senderStaffId", ""))
            text = ((data.get("text") or {}).get("content") or "").strip()
            # 附件（图片/文件）：钉钉不在帧里带内容，只给 downloadCode，要再换一次临时 URL 去取。
            # 仍是**纯出站请求**，不破坏"零入站暴露"。取不到就如实申报 unsupported——
            # 静默丢附件等于让人对着石沉大海干等（本仓栽过好几次的老毛病）。
            imgs, files, bad = await self._fetch_attachments(data)
            ev = self._to_event(sender, text, is_group=_is_group(data),
                                mentioned=bool(data.get("isInAtList")),
                                has_attachment=bool(imgs or files or bad))
            if ev is not None and ev.kind == "message":
                ev.images, ev.files, ev.unsupported = imgs, files, bad
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
                  mentioned: bool = False, has_attachment: bool = False) -> Optional[ChannelEvent]:
        if not text and not has_attachment:       # 纯附件消息也要成事件，否则发图=石沉大海
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
        """发文本给 owner——两条通道，**绝不静默丢**（真机 2026-07-28 早上的事故就是这里）。

        ① 有 sessionWebhook（主人最近说过话）→ 回到那个会话里；
        ② 没有（服务刚重启、主人一夜没发消息）或 webhook 已失效 → 走**主动通道**
           `oToMessages/batchSend` 推到 owner 单聊（`_send_msg`，发图/发文件早就在用它，
           唯独纯文本一直没接）。

        旧实现是 `if self._webhook: 发`——没有 webhook 时**什么都不发、然后返回成功**：
        01:32 改时区重启清掉内存里的 webhook，主人睡着没再说话，于是凌晨值班通报、
        早 9 点新闻、重启后的"已就绪"横幅全部无声蒸发；连异常都没有，台账/日志零痕迹。
        还有一条单测把这个行为当特性钉着（"还没 webhook → 不发（钉钉反应式）"）。

        两条通道都失败 → **抛异常**。上层 `notify_owner` 据此返回 False，
        投递器才有机会往台账落一条"未送达"（见 gateway/notices.py）。
        """
        payload = {"msgtype": "text", "text": {"content": _clip(text)}}
        if self._webhook:
            try:
                await self._reply_fn(self._webhook, payload)
                return ""                         # 钉钉无 message_id 供编辑
            except Exception as e:  # noqa: BLE001 —— webhook 失效（过期/网络）→ 丢弃它，走主动通道
                _log.warning("sessionWebhook 回复失败（%s）——回退主动推送通道", str(e)[:120])
                self._webhook = None              # 失效目标别留着挨个撞；下条过闸的入站会重新提交
        await self._send_msg("sampleText", {"content": _clip(text)})
        return ""

    async def edit_text(self, message_id: str, text: str) -> None:
        await self.send_text(text)                # 不支持编辑 → 发新消息（bridge 已放慢节流）

    async def send_confirm(self, text: str, callback_id: str) -> None:
        """发一次确认：配了卡片模板走**真按钮**，否则（或卡片挂了）退回文本 y/n。

        **文本待确认态照样置位**：即便卡片发成功了，主人仍可以直接回一句 y——他不一定
        想去点按钮，而且卡片万一在他手机上渲染不出来，打字必须始终是可用的兜底。
        确认能力本身一秒都不能丢（2026-07-28 刚被"投递静默失败"教育过）。
        """
        self._awaiting_confirm = callback_id
        if self._card is not None:
            try:
                await self._card.send(text, callback_id)
                return
            except Exception as e:  # noqa: BLE001 —— 卡片是体验优化，挂了就退回文本
                _log.warning("互动卡片发送失败，退回文本确认：%s", str(e)[:160])
        await self.send_text(text + "\n\n请回复 **y**（批准）或 **n**（拒绝）。")

    # ------------------------------------------------------------ 入站附件（图片/文件）
    async def _fetch_attachments(self, data: dict) -> tuple[list, list, str]:
        """把消息里的附件下载到本地，返回 (图片路径, 文件路径, 取不到的说明)。

        钉钉帧里**不带内容**，只给 downloadCode，要拿它再换一次临时 URL 才能下载
        （仍是纯出站请求，"零入站暴露"不受影响）。

        失败一律降级成 `unsupported` 文本，**绝不静默丢**：附件石沉大海是最差的体验，
        人会以为机器人死了（2026-07-26 这类"不告诉人发生了什么"的毛病栽过好几次）。
        """
        items: list = []
        mt = str(data.get("msgtype") or "").lower()
        if mt == "picture":
            c = ((data.get("content") or {}).get("downloadCode")
                 or (data.get("picture") or {}).get("downloadCode"))
            if c:
                items.append(("image", c, "image.jpg"))
        elif mt in ("file", "audio", "video"):
            blk = data.get("content") or data.get(mt) or {}
            c = blk.get("downloadCode")
            if c:
                items.append(("file", c, str(blk.get("fileName") or f"{mt}.bin")))
        elif mt == "richText":                     # 图文混排：正文里可能夹若干张图
            for it in (data.get("content") or {}).get("richText") or []:
                if isinstance(it, dict) and it.get("downloadCode"):
                    items.append(("image", it["downloadCode"], "image.jpg"))
        if not items:
            # 认不出的类型：只有在**确实不是纯文本**时才报（避免把普通消息也说成不支持）
            return [], [], (f"收到 {mt} 类型的消息，暂不支持读取其内容。"
                            if mt and mt not in ("text", "") else "")

        imgs: list = []
        files: list = []
        bad: list = []
        for kind, code, name in items:
            try:
                path = await self._download_one(code, name)
            except Exception as e:  # noqa: BLE001 —— 单个附件失败不拖垮整条消息
                bad.append(f"{name}（{str(e)[:60]}）")
                continue
            (imgs if kind == "image" else files).append(path)
        note = ("有 %d 个附件没取到：%s" % (len(bad), "；".join(bad))) if bad else ""
        return imgs, files, note

    async def _download_one(self, code: str, name: str) -> str:
        """downloadCode → 临时 URL → 落盘，返回本地路径。"""
        import os
        import time as _t
        import uuid as _u

        import aiohttp
        tok = await self._token(legacy=False)
        if self._session is None:
            self._session = aiohttp.ClientSession()
        async with self._session.post(
                "https://api.dingtalk.com/v1.0/robot/messageFiles/download",
                json={"downloadCode": code, "robotCode": self._cid},
                headers={"x-acs-dingtalk-access-token": tok}) as r:
            d = await r.json(content_type=None)
        url = d.get("downloadUrl")
        if not url:
            raise RuntimeError(f"换取下载地址失败: {str(d)[:120]}")
        # 落在收件目录：**不进仓库工作区**，避免用户发来的文件被误当成代码改动收进 diff
        base = os.path.join(self._inbox_dir, _t.strftime("%Y%m%d"))
        os.makedirs(base, exist_ok=True)
        safe = os.path.basename(name).replace("/", "_")[:80] or "file.bin"
        path = os.path.join(base, f"{_t.strftime('%H%M%S')}-{_u.uuid4().hex[:6]}-{safe}")
        async with self._session.get(url) as r:
            if r.status != 200:
                raise RuntimeError(f"下载失败 HTTP {r.status}")
            with open(path, "wb") as fh:
                while chunk := await r.content.read(65536):
                    fh.write(chunk)
        return path

    # ------------------------------------------------------------ 媒体（图片/文件）
    async def _ensure_session(self):
        import aiohttp
        if self._session is None:
            self._session = aiohttp.ClientSession()
        return self._session

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
        目标固定是这条通道能作为出站面的前提（对比 web_fetch：URL 由模型决定，那才是真外传）。

        transport 可注入（`oto_fn`），与 connect_fn/reply_fn 同款：三条真实通道（长连收帧、
        会话回复、主动推送）**每条**都要有注入口，测试才可能真离线。#254 启用本通道时漏了
        这个口，注入了前两条 transport 的十几条存量测试开始悄悄真连 api.dingtalk.com——
        网络快就绿、慢就红（2026-07-28 在 canary 门禁上第一次现形，红的还是别人家的群聊测试）。
        """
        if self._oto_fn is not None:
            await self._oto_fn(msg_key, msg_param)
            return
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
        if getattr(event, "ack", None) != "card":
            return                            # 文本式确认无需回执
        # 只有**过了 bridge 三道闸**的按钮点击才走到这里（bridge 是唯一调用点）。
        # 此刻才消费文本待确认态、把卡片刷成终态——settle 失败只记日志（确认已生效）。
        if self._awaiting_confirm == event.callback_id:
            self._awaiting_confirm = None
        if self._card is not None:
            await self._card.settle(event.callback_id, bool(event.approved))

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
                      "subscriptions": self._subscriptions(),
                      "ua": "vortocode-im/1.0"}) as resp:
            data = await resp.json()
        endpoint = str(data["endpoint"]).rstrip("/")
        ticket = data["ticket"]
        base = endpoint if endpoint.endswith("/connect") else endpoint + "/connect"
        ws = await self._session.ws_connect(f"{base}?ticket={ticket}")
        return _AioWS(ws)

    def _subscriptions(self) -> list:
        """建连要订阅的主题。**没配卡片模板时与不加本功能时逐字节相同**——多订阅一个未知
        主题万一被钉钉拒绝，长连就建不起来，那是 bot 直接死。这个风险本地验不了，
        所以锁在"配了才有"的门后面。"""
        subs = [{"type": "CALLBACK", "topic": BOT_TOPIC}]
        if self._card is not None:
            from src.im.dingtalk_card import CARD_CALLBACK_TOPIC
            subs.append({"type": "CALLBACK", "topic": CARD_CALLBACK_TOPIC})
        return subs

    async def _default_reply(self, webhook: str, payload: dict) -> None:
        import aiohttp
        if self._session is None:
            self._session = aiohttp.ClientSession()
        async with self._session.post(webhook, json=payload) as resp:
            body = await resp.read()
            # 失败必须抛（send_text 靠它决定要不要回退主动通道）。旧实现连响应都不看：
            # webhook 过期后钉钉照样回 HTTP 200 + 非零 errcode，每条回复都"成功"地消失。
            problem = _webhook_reply_problem(resp.status, body)
            if problem:
                raise RuntimeError(problem)


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


def _webhook_reply_problem(status: int, body: bytes) -> str:
    """sessionWebhook 回复的失败判定；正常返回空串。抽成纯函数是为了可测。

    这个接口的失败有两种长相，都要认：非 200；以及 **HTTP 200 但 body 带非零 errcode**
    （webhook 过期就长这样）。非 JSON 响应当成功——别把好消息误杀。
    """
    if status != 200:
        return f"sessionWebhook 回复失败 HTTP {status}"
    try:
        d = json.loads(body or b"{}")
    except (ValueError, TypeError):
        return ""
    if not isinstance(d, dict):
        return ""
    code = d.get("errcode")
    if code not in (None, 0, "0"):
        return f"sessionWebhook errcode {code}: {str(d.get('errmsg') or '')[:80]}"
    return ""


def _clip(text: str, limit: int = 4000) -> str:
    text = text or "（空）"
    return text if len(text) <= limit else text[: limit - 20] + "\n…（已截断）"
