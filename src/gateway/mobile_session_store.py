"""手机 App 原生会话落盘：手机网关重启后 App 不用重新登录（ADR-012，v4 审查 V6）。

原来原生会话只在网关进程内存里，每次部署重启都会把所有手机登出；App 成为主入口后这不可接受。

- 只存原生会话（App），不存浏览器会话；键是会话凭据的摘要，凭据本身不落盘。
- 记录含账号服务的访问 / 刷新令牌和设备读取授权凭据，用 AES-GCM 加密写在设备授权目录
  （网关唯一可写的目录，0700）里，钥匙另存一个 0600 文件。它防的是只拷走状态文件的备份 / 同步，
  不防拿到整个目录的人——那种情况设备授权本身也已经暴露。
- 读不出、解不开、版本不对：一律当作没有（App 重新登录），绝不半恢复。
- 没装 ``cryptography``（手机网关的 ``mobile`` 依赖）时不落盘，行为与原来一样。
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
import secrets
import tempfile
import threading

logger = logging.getLogger(__name__)

_VERSION = 1
_MAX_SESSIONS = 64
_LOCK = threading.Lock()


class NativeSessionStore:
    def __init__(self, root: str):
        self.directory = Path(root) / ".vortocode" / "mobile-devices"
        self.path = self.directory / "native-sessions.bin"
        self.key_path = self.directory / "native-sessions.key"

    @staticmethod
    def available() -> bool:
        try:
            from cryptography.hazmat.primitives.ciphers.aead import AESGCM  # noqa: F401
        except ImportError:
            return False
        return True

    def _key(self, *, create: bool) -> bytes | None:
        try:
            data = self.key_path.read_bytes()
            return data if len(data) == 32 else None
        except FileNotFoundError:
            if not create:
                return None
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        key = secrets.token_bytes(32)
        fd = os.open(self.key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
        with os.fdopen(fd, "wb") as handle:
            handle.write(key)
        return key

    def _read(self) -> dict:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM

        key = self._key(create=False)
        if key is None:
            return {}
        try:
            blob = self.path.read_bytes()
            data = json.loads(AESGCM(key).decrypt(blob[:12], blob[12:], b"vortocode-native-sessions"))
        except FileNotFoundError:
            return {}
        except Exception:  # noqa: BLE001 —— 坏文件 / 换了钥匙：当作没有，App 重新登录
            logger.warning("手机会话记录无法读取，已忽略")
            return {}
        if not isinstance(data, dict) or data.get("version") != _VERSION or not isinstance(data.get("sessions"), dict):
            return {}
        return data["sessions"]

    def _write(self, sessions: dict) -> None:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM

        key = self._key(create=True)
        assert key is not None
        nonce = secrets.token_bytes(12)
        plain = json.dumps({"version": _VERSION, "sessions": sessions}).encode()
        blob = nonce + AESGCM(key).encrypt(nonce, plain, b"vortocode-native-sessions")
        fd, temporary = tempfile.mkstemp(prefix=".native-sessions-", dir=self.directory)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(blob)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
        finally:
            Path(temporary).unlink(missing_ok=True)

    def load(self, digest: str) -> dict | None:
        if not self.available():
            return None
        with _LOCK:
            record = self._read().get(digest)
        return record if isinstance(record, dict) else None

    def save(self, digest: str, record: dict, *, now: float) -> None:
        if not self.available():
            return
        try:
            with _LOCK:
                sessions = {key: value for key, value in self._read().items()
                            if isinstance(value, dict) and float(value.get("expires", 0)) > now}
                sessions[digest] = record
                if len(sessions) > _MAX_SESSIONS:   # 只留最近到期的那些之外的最新一批
                    keep = sorted(sessions, key=lambda key: float(sessions[key].get("expires", 0)))[-_MAX_SESSIONS:]
                    sessions = {key: sessions[key] for key in keep}
                self._write(sessions)
        except (OSError, ValueError) as error:  # 落盘失败只影响「重启后免登录」，不影响这次请求
            logger.warning("手机会话记录写入失败：%s", type(error).__name__)

    def delete(self, digest: str) -> None:
        if not self.available():
            return
        try:
            with _LOCK:
                sessions = self._read()
                if sessions.pop(digest, None) is not None:
                    self._write(sessions)
        except (OSError, ValueError) as error:
            logger.warning("手机会话记录删除失败：%s", type(error).__name__)
