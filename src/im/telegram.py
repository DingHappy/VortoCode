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
                 poll_timeout: int = 25):
        self._token = token
        self.owner_id = str(owner_id)
        self._poll_timeout = poll_timeout
        self._offset = 0
        self._session = None
        self._request_fn = request_fn or self._default_request

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
                ev = _to_event(up)
                if ev is not None:
                    yield ev

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
def _to_event(up: dict) -> Optional[ChannelEvent]:
    msg = up.get("message")
    if isinstance(msg, dict) and isinstance(msg.get("text"), str):
        return ChannelEvent(kind="message", sender_id=str((msg.get("from") or {}).get("id", "")),
                            text=msg["text"])
    cq = up.get("callback_query")
    if isinstance(cq, dict):
        data = str(cq.get("data", ""))
        approved = data.startswith("approve:")
        cid = data.split(":", 1)[1] if ":" in data else ""
        return ChannelEvent(kind="callback", sender_id=str((cq.get("from") or {}).get("id", "")),
                            callback_id=cid, approved=approved, ack=cq.get("id"))
    return None


def _clip(text: str, limit: int = 4000) -> str:
    text = text or "（空）"
    return text if len(text) <= limit else text[: limit - 20] + "\n…（已截断）"
