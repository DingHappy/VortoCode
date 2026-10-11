"""One-time, private-chat owner pairing for a Telegram bot.

The token stays in the process environment. The pairing code is shown only in
the operator's terminal and expires after ten minutes. The resulting owner ID
is stored outside the repository, with a separate file for each bot ID.
"""

from __future__ import annotations

import json
import os
import secrets
import stat
import time
from pathlib import Path

from .telegram import TelegramAdapter


class PairingError(Exception):
    pass


def _bot_id(token: str) -> str:
    bot_id, separator, _secret = str(token).partition(":")
    if not separator or not bot_id.isdecimal():
        raise PairingError("Telegram token 格式不正确")
    return bot_id


def _pairing_dir(state_dir: Path | None = None) -> Path:
    return state_dir or Path.home() / ".vortocode" / "im-pairings"


def _owner_path(token: str, state_dir: Path | None = None) -> Path:
    return _pairing_dir(state_dir) / f"telegram-{_bot_id(token)}.json"


def _validate_pairing_directory(directory: Path) -> None:
    """Reject directories where another account could replace the owner file."""
    if directory.is_symlink() or not directory.is_dir():
        raise PairingError("Telegram 配对目录不是普通目录")
    info = directory.stat()
    if stat.S_IMODE(info.st_mode) & 0o077:
        raise PairingError("Telegram 配对目录权限过宽；请改为 700")
    if hasattr(os, "geteuid") and info.st_uid != os.geteuid():
        raise PairingError("Telegram 配对目录不属于当前系统用户")


def load_paired_owner(token: str, state_dir: Path | None = None) -> str:
    """Return the paired ID, or empty string when unpaired; reject bad state."""
    path = _owner_path(token, state_dir)
    if path.parent.exists() or path.parent.is_symlink():
        _validate_pairing_directory(path.parent)
    if not path.exists() and not path.is_symlink():
        return ""
    if path.is_symlink() or not path.is_file():
        raise PairingError("Telegram 配对记录不是普通文件")
    info = path.stat()
    if stat.S_IMODE(info.st_mode) & 0o077:
        raise PairingError("Telegram 配对记录权限过宽；请改为 600")
    if hasattr(os, "geteuid") and info.st_uid != os.geteuid():
        raise PairingError("Telegram 配对记录不属于当前系统用户")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        owner = str(data["owner_id"])
        if (data.get("version") != 1 or data.get("bot_id") != _bot_id(token)
                or not owner.isdecimal() or int(owner) <= 0):
            raise ValueError("invalid pairing")
        return owner
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise PairingError("Telegram 配对记录无效；请人工检查，不能自动重新配对") from exc


def _save_owner(token: str, owner_id: str, state_dir: Path | None = None) -> None:
    path = _owner_path(token, state_dir)
    directory = path.parent
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    _validate_pairing_directory(directory)
    payload = json.dumps({"version": 1, "bot_id": _bot_id(token), "owner_id": owner_id}) + "\n"
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError as exc:
        raise PairingError("这个 Bot 已有主人；不会覆盖现有配对") from exc
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        path.unlink(missing_ok=True)
        raise


async def pair_telegram(token: str, *, state_dir: Path | None = None,
                        request_fn=None, lifetime: float = 600,
                        show_code=print) -> str:
    """Wait for `/bind <code>` in a private chat and persist its sender as owner."""
    if load_paired_owner(token, state_dir):
        raise PairingError("这个 Bot 已有主人；不会重新配对")
    adapter = TelegramAdapter(token, "", request_fn=request_fn)
    try:
        adapter.claim_polling()
        me = await adapter._api("getMe")
        if not isinstance(me, dict) or str(me.get("id")) != _bot_id(token):
            raise PairingError("无法确认 Bot 身份；没有开启配对")
        code = secrets.token_urlsafe(12)
        deadline = time.monotonic() + lifetime
        show_code(f"给 @{me.get('username') or '你的机器人'} 私聊发送：/bind {code}\n"
                  f"配对码 {int(lifetime // 60)} 分钟内有效，只能在私聊使用；不要转发给别人。")
        offset = 0
        attempts = 0
        while time.monotonic() < deadline:
            remaining = deadline - time.monotonic()
            updates = await adapter._api("getUpdates", offset=offset,
                                         timeout=max(1, min(25, int(remaining))),
                                         allowed_updates=["message"])
            for update in updates or []:
                offset = max(offset, int(update.get("update_id", 0)) + 1)
                message = update.get("message") or {}
                chat = message.get("chat") or {}
                sender = message.get("from") or {}
                sender_id = str(sender.get("id") or "")
                if (chat.get("type") != "private" or sender.get("is_bot")
                        or str(chat.get("id") or "") != sender_id
                        or not sender_id.isdecimal()):
                    continue
                words = str(message.get("text") or "").split()
                if not words or words[0] != "/bind":
                    continue
                attempts += 1
                if (len(words) == 2 and words[1].isascii()
                        and secrets.compare_digest(words[1], code)):
                    if time.monotonic() >= deadline:
                        break
                    _save_owner(token, sender_id, state_dir)
                    try:
                        await adapter._api("sendMessage", chat_id=sender_id,
                                           text="✅ 已绑定为主人。请启动 VortoCode Telegram 桥。")
                    except Exception:  # noqa: BLE001 - persisted pairing remains authoritative
                        pass
                    # Confirm this update before the normal bridge starts, so the
                    # one-time code never enters an agent conversation on restart.
                    try:
                        await adapter._api("getUpdates", offset=offset, timeout=0,
                                           allowed_updates=["message"])
                    except Exception:  # noqa: BLE001 - bridge also handles replayed /bind
                        pass
                    return sender_id
                if attempts >= 10:
                    raise PairingError("错误配对尝试过多；本次配对已终止")
        raise PairingError("配对码已过期；请重新运行配对命令")
    finally:
        await adapter.close()
