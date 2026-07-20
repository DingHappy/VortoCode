"""Telegram 通道适配器——Bot API `getUpdates` 长轮询（**纯出站**，gateway 无需入站暴露端口）。

HTTP 层可注入（`request_fn`）：默认用 aiohttp（硬依赖）；测试传一个假 request_fn 即可不触网跑全逻辑。
"""

from __future__ import annotations

import asyncio
from typing import AsyncIterator, Awaitable, Callable, Optional

from .channel import ChannelAdapter, ChannelEvent

# 注入式 HTTP：async (method, payload) -> Telegram 返回的 result（ok=False 时应抛异常）
RequestFn = Callable[[str, dict], Awaitable[object]]


class TelegramError(Exception):
    pass


class TelegramAdapter(ChannelAdapter):
    def __init__(self, token: str, owner_id: str, *, request_fn: Optional[RequestFn] = None,
                 poll_timeout: int = 25, bot_username: Optional[str] = None):
        self._token = token
        self.owner_id = str(owner_id)
        self._poll_timeout = poll_timeout
        self._offset = 0
        self._session = None
        self._request_fn = request_fn or self._default_request
        # 群提及门要判"@ 的是不是**我**"，就得知道自己叫什么。可显式配置（省一次 API 调用），
        # 否则**遇到第一条群消息时**才惰性 getMe——私聊-only 的部署因此一次额外请求都不发。
        self._bot_username = (bot_username or "").strip().lstrip("@").lower() or None
        self._bot_id: Optional[str] = None
        self._me_resolved = self._bot_username is not None

    # ------------------------------------------------------------ HTTP（默认 aiohttp，可注入替换）
    async def _default_request(self, method: str, payload: dict):
        import aiohttp
        if self._session is None:
            self._session = aiohttp.ClientSession()
        url = f"https://api.telegram.org/bot{self._token}/{method}"
        timeout = aiohttp.ClientTimeout(total=self._poll_timeout + 15)
        async with self._session.post(url, json=payload, timeout=timeout) as resp:
            data = await resp.json()
        if not data.get("ok"):
            raise TelegramError(str(data.get("description", "unknown")))
        return data.get("result")

    async def _api(self, method: str, **payload):
        return await self._request_fn(method, payload)

    # ------------------------------------------------------------ ChannelAdapter 实现
    async def poll(self) -> AsyncIterator[ChannelEvent]:
        backoff = 1.0
        while True:
            try:
                updates = await self._api("getUpdates", offset=self._offset,
                                          timeout=self._poll_timeout,
                                          allowed_updates=["message", "callback_query"])
                backoff = 1.0
            except Exception:  # noqa: BLE001 —— 网络抖动/超时：退避重连，绝不把桥拖垮
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30.0)
                continue
            for up in updates or []:
                self._offset = max(self._offset, int(up.get("update_id", 0)) + 1)
                if _is_group_update(up):
                    await self._resolve_me()          # 只有群消息才需要知道自己的 @ 名
                ev = _to_event(up, self._bot_username, self._bot_id)
                if ev is not None:
                    yield ev

    async def _resolve_me(self) -> None:
        """惰性取自己的 username/id（群提及门要用）。失败**不重试到死**也不抛：认不出自己就等于
        群里永远判不出被 @ → 群消息全被 bridge 丢掉，这正是 fail-closed 想要的方向。"""
        if self._me_resolved:
            return
        self._me_resolved = True
        try:
            me = await self._api("getMe") or {}
            self._bot_username = str(me.get("username", "")).lstrip("@").lower() or None
            self._bot_id = str(me.get("id", "")) or None
        except Exception:  # noqa: BLE001
            self._bot_username, self._bot_id = None, None

    async def send_text(self, text: str) -> str:
        res = await self._api("sendMessage", chat_id=self.owner_id, text=_clip(text),
                              disable_web_page_preview=True)
        return str((res or {}).get("message_id", ""))

    async def edit_text(self, message_id: str, text: str) -> None:
        try:
            await self._api("editMessageText", chat_id=self.owner_id, message_id=int(message_id),
                            text=_clip(text), disable_web_page_preview=True)
        except Exception:  # noqa: BLE001 —— 编辑失败（内容未变/消息太旧）不致命
            pass

    async def send_confirm(self, text: str, callback_id: str) -> None:
        kb = {"inline_keyboard": [[
            {"text": "✅ 批准", "callback_data": f"approve:{callback_id}"},
            {"text": "❌ 拒绝", "callback_data": f"deny:{callback_id}"},
        ]]}
        await self._api("sendMessage", chat_id=self.owner_id, text=_clip(text), reply_markup=kb)

    async def ack_callback(self, event: ChannelEvent) -> None:
        if not event.ack:
            return
        try:
            await self._api("answerCallbackQuery", callback_query_id=str(event.ack))
        except Exception:  # noqa: BLE001
            pass

    async def close(self) -> None:
        if self._session is not None:
            try:
                await self._session.close()
            except Exception:  # noqa: BLE001
                pass
            self._session = None


# ------------------------------------------------------------ 归一化：Telegram update → ChannelEvent
def _is_group_update(up: dict) -> bool:
    """这条 update 是不是群里的**消息**（据此决定要不要惰性 getMe）。"""
    msg = up.get("message")
    return isinstance(msg, dict) and _chat_is_group(msg.get("chat"))


def _chat_is_group(chat) -> bool:
    """chat.type 只有 "private" 算私聊；group/supergroup/channel **以及任何未知类型**都按群处理
    （未知 → 从严：要求显式 @）。测试里构造的无 chat 字段消息按私聊处理，真实 Bot API 必有 chat。"""
    if not isinstance(chat, dict):
        return False
    return str(chat.get("type") or "") != "private"


def _slice_utf16(text: str, offset: int, length: int) -> str:
    """按 Telegram 的口径切 entity：offset/length 的单位是 **UTF-16 码元**，不是 Python 字符。

    中文每字都是 1 个码元、与 Python 索引一致，但 emoji（代理对）算 2 个——正文里有 emoji 时
    直接用 text[off:off+len] 会整体错位，把 "@bot" 切成半截，结果是"群里 @ 了却不理人"。
    """
    raw = text.encode("utf-16-le")
    return raw[2 * offset: 2 * (offset + length)].decode("utf-16-le", "ignore")


def _mentions_bot(msg: dict, username: Optional[str], bot_id: Optional[str]) -> bool:
    """本条消息是否**显式 @ 了本机器人**。

    只认 Telegram 的结构化 entities，不做裸文本 "@xxx" 子串匹配——后者会把"聊天里提到机器人名字"
    误判成召唤。三种命中：`mention`（@username）、`bot_command`（/status@username）、
    `text_mention`（无 username 用户按 id 挂）。认不出自己（username/id 都没解析到）→ 一律 False。
    """
    text = msg.get("text") or ""
    at = f"@{username}" if username else None
    for ent in msg.get("entities") or []:
        if not isinstance(ent, dict):
            continue
        etype = str(ent.get("type") or "")
        if etype == "text_mention":
            uid = str(((ent.get("user") or {}).get("id")) or "")
            if bot_id and uid == str(bot_id):
                return True
        elif etype in ("mention", "bot_command") and at:
            frag = _slice_utf16(text, int(ent.get("offset") or 0),
                                int(ent.get("length") or 0)).strip().lower()
            if frag == at or frag.endswith(at):        # "/status@bot" 也算召唤
                return True
    return False


def _to_event(up: dict, bot_username: Optional[str] = None,
              bot_id: Optional[str] = None) -> Optional[ChannelEvent]:
    msg = up.get("message")
    if isinstance(msg, dict) and isinstance(msg.get("text"), str):
        is_group = _chat_is_group(msg.get("chat"))
        return ChannelEvent(kind="message", sender_id=str((msg.get("from") or {}).get("id", "")),
                            text=msg["text"], is_group=is_group,
                            mentioned=is_group and _mentions_bot(msg, bot_username, bot_id))
    cq = up.get("callback_query")
    if isinstance(cq, dict):
        data = str(cq.get("data", ""))
        approved = data.startswith("approve:")
        cid = data.split(":", 1)[1] if ":" in data else ""
        # 按钮点击天然是"对着本机器人的动作"（inline keyboard 是我们自己发的），不适用群提及门；
        # 它的守门在别处：cid 必须匹配一个在等的确认，且 bridge 只认 owner 点的。
        return ChannelEvent(kind="callback", sender_id=str((cq.get("from") or {}).get("id", "")),
                            callback_id=cid, approved=approved, ack=cq.get("id"))
    return None


def _clip(text: str, limit: int = 4000) -> str:
    text = text or "（空）"
    return text if len(text) <= limit else text[: limit - 20] + "\n…（已截断）"
