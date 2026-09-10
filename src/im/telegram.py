"""Telegram 通道适配器——Bot API `getUpdates` 长轮询（**纯出站**，gateway 无需入站暴露端口）。

HTTP 层可注入（`request_fn`）：默认用 aiohttp（硬依赖）；测试传一个假 request_fn 即可不触网跑全逻辑。
"""

from __future__ import annotations

import asyncio
import os
import time
import uuid as _uuid
from pathlib import Path
from typing import AsyncIterator, Awaitable, Callable, Optional

from .channel import ChannelAdapter, ChannelEvent, chunk_text
from src.utils.http import outbound_session

# 注入式 HTTP：async (method, payload) -> Telegram 返回的 result（ok=False 时应抛异常）
RequestFn = Callable[[str, dict], Awaitable[object]]
# 注入式下载：async (file_path) -> 文件字节。与 RequestFn 分开，因为附件走的是另一个 host 路径
# （/file/bot<token>/<file_path>）而不是 JSON API，测试也要能单独假掉它。
DownloadFn = Callable[[str], Awaitable[bytes]]

_ME_RETRY_BASE = 5.0        # getMe 失败后首次重试的最短间隔（秒）
_ME_RETRY_MAX = 300.0       # 退避上限：持续失败时最多 5 分钟试一次（别把群消息变成 getMe 洪水）
# Bot API 的 getFile 只服务 ≤20MB 的文件（官方硬限制）。超了**提前如实说**，
# 而不是发一次注定失败的请求再把 API 的英文报错甩给用户。
_TEXT_LIMIT = 4000                # Bot API 单条文本硬限制 4096，留点余量
_MAX_FILE_BYTES = 20 * 1024 * 1024
# 按 Telegram 的消息字段分两类：能当图看的进 images（agent 读图路径），其余进 files。
_IMAGE_FIELDS = ("photo", "sticker")
_FILE_FIELDS = ("document", "video", "audio", "voice", "video_note", "animation")


def _now() -> float:
    """单调钟（getMe 退避用）。独立函数是为了测试能注入假时钟，不去动 stdlib 的 time。"""
    return time.monotonic()


class TelegramError(Exception):
    pass


class TelegramAdapter(ChannelAdapter):
    def __init__(self, token: str, owner_id: str, *, request_fn: Optional[RequestFn] = None,
                 poll_timeout: int = 25, bot_username: Optional[str] = None,
                 inbox_dir: Optional[str] = None,
                 download_fn: Optional[DownloadFn] = None):
        self._token = token
        self.owner_id = str(owner_id)
        self._poll_timeout = poll_timeout
        self._offset = 0
        self._session = None
        self._request_fn = request_fn or self._default_request
        self._download_fn = download_fn or self._default_download
        # 收件落盘目录：**不进仓库工作区**，免得用户发来的文件被当成代码改动收进 diff。
        # 与钉钉同一处（~/.vortocode/im_inbox），两个通道的收件箱不分家。
        self._inbox_dir = str(Path(inbox_dir or Path.home() / ".vortocode" / "im_inbox"))
        # 群提及门要判"@ 的是不是**我**"，就得知道自己叫什么。可显式配置（省一次 API 调用），
        # 否则**遇到第一条群消息时**才惰性 getMe——私聊-only 的部署因此一次额外请求都不发。
        self._bot_username = (bot_username or "").strip().lstrip("@").lower() or None
        self._bot_id: Optional[str] = None
        self._me_resolved = self._bot_username is not None
        self._me_retry_at = 0.0        # 下次允许重试 getMe 的单调时刻（退避窗口内不再打 API）
        self._me_backoff = 0.0         # 当前退避长度（秒），成功即作废

    # ------------------------------------------------------------ HTTP（默认 aiohttp，可注入替换）
    async def _default_request(self, method: str, payload: dict):
        import aiohttp
        if self._session is None:
            self._session = outbound_session()
        url = f"https://api.telegram.org/bot{self._token}/{method}"
        timeout = aiohttp.ClientTimeout(total=self._poll_timeout + 15)
        async with self._session.post(url, json=payload, timeout=timeout) as resp:
            data = await resp.json()
        if not data.get("ok"):
            raise TelegramError(str(data.get("description", "unknown")))
        return data.get("result")

    async def _default_download(self, file_path: str) -> bytes:
        """按 getFile 给的 file_path 取字节。走的是 /file/bot<token>/ 前缀，与 JSON API 不同 host 路径。

        整读进内存而不流式：Bot API 本身封顶 20MB（见 _MAX_FILE_BYTES），为这点体积引入流式
        只会让注入测试变复杂。
        """
        if self._session is None:
            self._session = outbound_session()
        url = f"https://api.telegram.org/file/bot{self._token}/{file_path}"
        async with self._session.get(url) as resp:
            if resp.status != 200:
                raise TelegramError(f"下载失败 HTTP {resp.status}")
            return await resp.read()

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
                # 长轮询正常返回（哪怕是空的）就是"线还活着"——不是"收到用户消息"，
                # 主人一夜不说话是常态，拿消息当活性会天天误报。
                if not self._lv()["connected"]:
                    self.note_connected()
                self.note_frame()
            except Exception as e:  # noqa: BLE001 —— 网络抖动/超时：退避重连，绝不把桥拖垮
                self.note_disconnected(f"长轮询失败: {type(e).__name__}: {e}")
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30.0)
                continue
            for up in updates or []:
                self._offset = max(self._offset, int(up.get("update_id", 0)) + 1)
                if _is_group_update(up):
                    await self._resolve_me()          # 只有群消息才需要知道自己的 @ 名
                ev = _to_event(up, self._bot_username, self._bot_id)
                if ev is None:
                    continue
                refs = _attachment_refs(up.get("message") or {})
                if refs:
                    # 下载放在 poll 而不是 _to_event：后者是纯函数（好测），发请求的活归适配器。
                    ev.images, ev.files, ev.unsupported = await self._fetch_attachments(refs)
                yield ev

    async def _fetch_attachments(self, refs: list) -> tuple:
        """把附件下载到本地，返回 (图片路径, 文件路径, 取不到的说明)。与钉钉同形状同契约。

        两跳：`getFile(file_id)` 换到 file_path，再按 /file/bot<token>/<file_path> 取字节
        （仍是纯出站请求，"零入站暴露"不受影响）。

        失败一律降级成说明文本，**绝不静默丢**——附件石沉大海是最差的体验，人会以为机器人死了。
        单个附件失败也不拖垮整条消息：其余照常交付，坏的那个如实点名。
        """
        imgs: list = []
        files: list = []
        bad: list = []
        for kind, file_id, name, size in refs:
            if size and size > _MAX_FILE_BYTES:
                bad.append(f"{name}（{size // 1024 // 1024}MB 超过 Bot API 的 20MB 上限）")
                continue
            try:
                info = await self._api("getFile", file_id=file_id)
                file_path = str((info or {}).get("file_path") or "")
                if not file_path:
                    raise TelegramError("getFile 没给 file_path")
                blob = await self._download_fn(file_path)
                if not blob:
                    raise TelegramError("下载到 0 字节")
                # 用远端 file_path 的后缀补名字：Telegram 对 photo 不给文件名，
                # 而下游读图要靠后缀判类型。
                ext = os.path.splitext(file_path)[1]
                if ext and not os.path.splitext(name)[1]:
                    name = name + ext
                imgs.append(self._save_inbox(name, blob)) if kind == "image" \
                    else files.append(self._save_inbox(name, blob))
            except Exception as e:  # noqa: BLE001 —— 单个附件失败不拖垮整条消息
                bad.append(f"{name}（{type(e).__name__}: {str(e)[:60]}）")
        note = ("有 %d 个附件没取到：%s" % (len(bad), "；".join(bad))) if bad else ""
        return imgs, files, note

    def _save_inbox(self, name: str, blob: bytes) -> str:
        """落到收件目录（按天分目录），返回本地路径。

        文件名过 basename + 去斜杠 + 截断：**用户发来的文件名是外部输入**，直接拼进路径
        就是路径穿越（`../../.ssh/authorized_keys`）。前缀加时间与随机串，避免同名互相覆盖。
        """
        base = os.path.join(self._inbox_dir, time.strftime("%Y%m%d"))
        os.makedirs(base, exist_ok=True)
        safe = os.path.basename(str(name)).replace("/", "_").replace("\\", "_")[:80] or "file.bin"
        path = os.path.join(base, f"{time.strftime('%H%M%S')}-{_uuid.uuid4().hex[:6]}-{safe}")
        with open(path, "wb") as fh:
            fh.write(blob)
        return path

    async def _resolve_me(self) -> None:
        """惰性取自己的 username/id（群提及门要用），**失败退避重试、绝不永久放弃**。

        两个方向要同时守住：

        - **fail-closed**：解析不出来的期间认不出自己 → 判不出群里有没有被 @ → 那些群消息照样
          被 bridge 丢掉。不为了"能用"改成放行。
        - **不永久群聋**：一次网络抖动/限流让 getMe 失败，若就此把 `_me_resolved` 永久置位，
          此后**整个进程生命周期**里的群消息全成哑弹（重启才恢复）——那是可用性事故，不是安全。
          故失败只记退避（指数、上限 `_ME_RETRY_MAX`），下一条过了退避窗口的群消息会再试。

        退避是必须的：群消息可能很密，无节制重试就是拿群聊流量打自己的 getMe（还会撞限流）。
        """
        if self._me_resolved:
            return
        now = _now()
        if now < self._me_retry_at:        # 退避窗口内：不重试；本条群消息照样判不出 @ → 被丢
            return
        try:
            me = await self._api("getMe")
        except Exception:  # noqa: BLE001
            me = None
        if not isinstance(me, dict):       # 失败/返回体不是对象 → 按认不出自己处理，绝不往外抛：
            me = {}                        # 本函数在 poll 的 for 循环里被 await，抛出去会掀翻长轮询
        username = str(me.get("username", "")).lstrip("@").lower() or None
        bot_id = str(me.get("id", "")) or None
        if username is None and bot_id is None:
            # 失败、或返回体里既没 username 也没 id（认不出自己 = 和失败等价）→ 退避后再来一次
            self._me_backoff = min(max(self._me_backoff * 2, _ME_RETRY_BASE), _ME_RETRY_MAX)
            self._me_retry_at = now + self._me_backoff
            return
        self._bot_username, self._bot_id = username, bot_id
        self._me_resolved = True
        self._me_backoff, self._me_retry_at = 0.0, 0.0

    async def send_text(self, text: str) -> str:
        """超长**拆成多条**而不是截断丢弃（4096 是 Bot API 的硬限制）。

        返回**最后一条**的 message_id：进度条的滚动编辑要接着往下改，指向第一条会把
        后面几条晾在那儿。
        """
        mid = ""
        for part in chunk_text(text, _TEXT_LIMIT) or ["（空）"]:
            res = await self._api("sendMessage", chat_id=self.owner_id, text=part,
                                  disable_web_page_preview=True)
            mid = str((res or {}).get("message_id", "")) or mid
        return mid

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

    async def _send_media(self, method: str, field: str, path: str, caption: str) -> bool:
        """Telegram 的媒体接口要 multipart，走不了 _api 的 JSON 通道，所以自己发一次。

        注入了假 request_fn 的测试环境不该真联网——此时直接报"不支持"退回文本。
        """
        import aiohttp
        import os
        if self._request_fn is not self._default_request:   # 测试注入态：不真发
            return False
        if self._session is None:
            self._session = outbound_session()
        form = aiohttp.FormData()
        form.add_field("chat_id", str(self.owner_id))
        if caption:
            form.add_field("caption", _clip(caption))
        with open(path, "rb") as fh:
            form.add_field(field, fh.read(), filename=os.path.basename(path))
        url = f"https://api.telegram.org/bot{self._token}/{method}"
        async with self._session.post(url, data=form) as r:
            if r.status != 200:
                raise RuntimeError(f"{method} 失败 HTTP {r.status}: {(await r.text())[:160]}")
        return True

    async def send_image(self, path: str, caption: str = "") -> bool:
        return await self._send_media("sendPhoto", "photo", path, caption)

    async def send_file(self, path: str, caption: str = "") -> bool:
        return await self._send_media("sendDocument", "document", path, caption)

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


def _attachment_refs(msg: dict) -> list:
    """从一条消息里认出附件，返回 [(kind, file_id, 文件名, 字节数)]。**纯函数，不发请求。**

    Telegram 的附件按**字段名**区分类型，不像钉钉塞在 msgtype 里：
      · photo —— 同一张图的多档分辨率数组，取**最后一个**（最大档）；
      · document/video/audio/... —— 各自一个对象，带 file_id。
    file_size 是 Bot API 给的，可能缺；缺就当 0 放行，让后面的下载去发现真实大小。
    """
    refs: list = []
    if not isinstance(msg, dict):
        return refs
    for field in _IMAGE_FIELDS + _FILE_FIELDS:
        blk = msg.get(field)
        if field == "photo":
            sizes = [x for x in (blk or []) if isinstance(x, dict) and x.get("file_id")]
            if sizes:
                big = sizes[-1]                       # Bot API 按尺寸升序给，最后一个最大
                refs.append(("image", str(big["file_id"]), "photo.jpg",
                             int(big.get("file_size") or 0)))
            continue
        if not isinstance(blk, dict) or not blk.get("file_id"):
            continue
        kind = "image" if field in _IMAGE_FIELDS else "file"
        name = str(blk.get("file_name") or f"{field}.bin")
        refs.append((kind, str(blk["file_id"]), name, int(blk.get("file_size") or 0)))
    return refs


def _mentions_bot(msg: dict, username: Optional[str], bot_id: Optional[str]) -> bool:
    """本条消息是否**显式 @ 了本机器人**。

    只认 Telegram 的结构化 entities，不做裸文本 "@xxx" 子串匹配——后者会把"聊天里提到机器人名字"
    误判成召唤。三种命中：`mention`（@username）、`bot_command`（/status@username）、
    `text_mention`（无 username 用户按 id 挂）。认不出自己（username/id 都没解析到）→ 一律 False。
    """
    # 带附件时正文与 entities 都在 caption 侧——不一并看的话，群里"发图 @我"会被提及门丢掉，
    # 而提及门是从严的（申报了 is_group 却没申报 mentioned 就丢事件）。
    text = msg.get("text") or msg.get("caption") or ""
    at = f"@{username}" if username else None
    for ent in (msg.get("entities") or []) + (msg.get("caption_entities") or []):
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
    if isinstance(msg, dict):
        # 带附件的消息正文在 caption 而不是 text——只认 text 的话，"图 + 一句说明"会被整条丢掉。
        body = msg.get("text")
        if not isinstance(body, str):
            body = msg.get("caption") if isinstance(msg.get("caption"), str) else ""
        refs = _attachment_refs(msg)
        # 既没文字也没附件（入群通知之类的服务消息）→ 照旧不产生事件。
        if body or refs:
            is_group = _chat_is_group(msg.get("chat"))
            return ChannelEvent(kind="message",
                                sender_id=str((msg.get("from") or {}).get("id", "")),
                                text=body, is_group=is_group,
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
