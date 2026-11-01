"""Revocable, read-only device grants; not task/session storage or admin tokens.

Only the admin REST surface issues grants. Plaintext credentials are returned
once and never persisted. Atomic snapshots let readers observe revocation
without maintaining another process-local authorization cache.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import stat
import sys
import tempfile
import time
from contextlib import contextmanager
from typing import Callable
import uuid

from src.utils.state_dir import ensure_state_gitignore

SCOPE = "assistant:read"
MAX_DEVICES = 256
_MAX_BYTES = 1024 * 1024
_ID = re.compile(r"[a-f0-9]{32}")
_TOKEN = re.compile(r"vcm_[A-Za-z0-9_-]{43}")
_KEY = re.compile(r"[A-Za-z0-9+/]{86}[A-Za-z0-9+/=]{2}")   # 65 字节的 base64
_NONCE_KEEP = 900                                           # 秒；大于签名时间窗的两倍


def _check_keys(keys: object) -> None:
    """设备公钥（可选）：两把都在、都是 base64 的 65 字节点；绑定时已在曲线上校验过。"""
    if keys is None:
        return
    if (not isinstance(keys, dict) or set(keys) != {"device", "approval", "bound_at"}
            or type(keys["bound_at"]) is not int
            or not all(isinstance(keys[kind], str) and _KEY.fullmatch(keys[kind]) for kind in ("device", "approval"))):
        raise ValueError


class MobileStoreError(RuntimeError):
    """Storage could not safely be read or updated; never reset it on error."""


def _name(value: object) -> str:
    if (
        not isinstance(value, str)
        or not value.strip()
        or len(value) > 80
        or any(ord(char) < 32 for char in value)
    ):
        raise ValueError("设备名称需要 1–80 个字符")
    return value.strip()


def _regular(path: Path) -> None:
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_size > _MAX_BYTES:
        raise MobileStoreError("设备授权存储不可用")
    if os.name != "nt" and info.st_mode & 0o077:
        raise MobileStoreError("设备授权文件权限过宽")


class MobileDevices:
    def __init__(self, root: str, *, clock: Callable[[], float] = time.time):
        self.root = Path(root)
        self.directory = self.root / ".vortocode" / "mobile-devices"
        self.path = self.directory / "grants.json"
        self.clock = clock

    def _directories(self, *, create: bool = False) -> None:
        for path in (self.directory.parent, self.directory):
            if path.is_symlink():
                raise MobileStoreError("设备授权目录不可用")
        if create:
            self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        if self.directory.exists():
            info = self.directory.lstat()
            if not stat.S_ISDIR(info.st_mode) or (os.name != "nt" and info.st_mode & 0o077):
                raise MobileStoreError("设备授权目录权限过宽")

    def _load(self) -> dict | None:
        self._directories()
        try:
            _regular(self.path)
            fd = os.open(self.path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
            with os.fdopen(fd, "rb") as handle:
                raw = handle.read(_MAX_BYTES + 1)
            if len(raw) > _MAX_BYTES:
                raise ValueError
            data = json.loads(raw)
            if (
                not isinstance(data, dict)
                or type(data.get("schema_version")) is not int
                or data["schema_version"] != 1
                or not _ID.fullmatch(data.get("instance_id", ""))
                or not isinstance(data.get("devices"), list)
                or len(data["devices"]) > MAX_DEVICES
            ):
                raise ValueError
            seen: set[str] = set()
            for item in data["devices"]:
                if (
                    not isinstance(item, dict)
                    or not isinstance(item.get("id"), str)
                    or not _ID.fullmatch(item["id"])
                    or item["id"] in seen
                    or item.get("scope") != SCOPE
                    or not isinstance(item.get("digest"), str)
                    or not re.fullmatch(r"[a-f0-9]{64}", item["digest"])
                    or type(item.get("created_at")) is not int
                    or type(item.get("expires_at")) is not int
                    or not 0 <= item["created_at"] < item["expires_at"]
                    or "revoked_at" not in item
                    or (
                        item.get("revoked_at") is not None
                        and (type(item["revoked_at"]) is not int or item["revoked_at"] < 0)
                    )
                ):
                    raise ValueError
                _name(item.get("name"))
                _check_keys(item.get("keys"))
                seen.add(item["id"])
            return data
        except FileNotFoundError:
            return None
        except (OSError, ValueError, TypeError, OverflowError, RecursionError) as error:
            raise MobileStoreError("设备授权存储不可用") from error

    @contextmanager
    def _lock(self):
        self._directories(create=True)
        ensure_state_gitignore(str(self.root))
        path = self.directory / ".lock"
        if path.is_symlink():
            raise MobileStoreError("设备授权锁不可用")
        fd = os.open(path, os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600)
        with os.fdopen(fd, "r+b") as handle:
            try:
                if sys.platform == "win32":
                    import msvcrt

                    if os.fstat(fd).st_size == 0:
                        handle.write(b"\0")
                        handle.flush()
                    handle.seek(0)
                    msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as error:
                raise MobileStoreError("设备授权正在更新，请重试") from error
            yield

    def _save(self, data: dict) -> None:
        fd, temporary = tempfile.mkstemp(prefix=".grants-", dir=self.directory)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(data, handle, ensure_ascii=False)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
            if os.name != "nt":
                directory = os.open(self.directory, os.O_RDONLY)
                try:
                    os.fsync(directory)
                finally:
                    os.close(directory)
        finally:
            Path(temporary).unlink(missing_ok=True)

    @staticmethod
    def public(item: dict) -> dict:
        return {
            key: item[key] for key in ("id", "name", "created_at", "expires_at", "revoked_at")
        } | {"scopes": [SCOPE]}

    def ensure_instance(self) -> str:
        """Create only an empty identity, never a device/account grant."""
        with self._lock():
            data = self._load()
            if data is None:
                data = {"schema_version": 1, "instance_id": uuid.uuid4().hex, "devices": []}
                self._save(data)
            return data["instance_id"]

    def issue(self, name: str, ttl_days: int = 30) -> dict:
        name = _name(name)
        if type(ttl_days) is not int or not 1 <= ttl_days <= 90:
            raise ValueError("设备有效期需要 1–90 天")
        with self._lock():
            data = self._load() or {
                "schema_version": 1,
                "instance_id": uuid.uuid4().hex,
                "devices": [],
            }
            if len(data["devices"]) >= MAX_DEVICES:
                raise ValueError("设备授权记录已达上限")
            token = "vcm_" + secrets.token_urlsafe(32)
            now = int(self.clock())
            item = {
                "id": uuid.uuid4().hex,
                "name": name,
                "scope": SCOPE,
                "digest": hashlib.sha256(token.encode("ascii")).hexdigest(),
                "created_at": now,
                "expires_at": now + ttl_days * 86400,
                "revoked_at": None,
            }
            data["devices"].append(item)
            self._save(data)
            return {"instance_id": data["instance_id"], "device": self.public(item), "token": token}

    def list(self) -> dict:
        data = self._load()
        return {
            "instance_id": data["instance_id"] if data else None,
            "devices": [self.public(item) for item in data["devices"]] if data else [],
        }

    def revoke(self, device_id: str) -> dict:
        if not isinstance(device_id, str) or not _ID.fullmatch(device_id):
            raise KeyError(device_id)
        with self._lock():
            data = self._load()
            for item in data["devices"] if data else []:
                if item["id"] == device_id:
                    if item["revoked_at"] is None:
                        item["revoked_at"] = int(self.clock())
                        self._save(data)
                    return self.public(item)
            raise KeyError(device_id)

    def bind_keys(self, device_id: str, device_key: str, approval_key: str) -> dict:
        """给设备绑定签名公钥。只能绑一次：要换钥匙，先撤销这台设备再重新授权。"""
        if not isinstance(device_id, str) or not _ID.fullmatch(device_id):
            raise KeyError(device_id)
        if device_key == approval_key:
            raise ValueError("日常密钥和批准密钥必须是两把不同的钥匙")
        with self._lock():
            data = self._load()
            for item in data["devices"] if data else []:
                if item["id"] != device_id:
                    continue
                # 有效期刚由调用方（会话层，按它自己的时钟）核验过；这里只再核对没被撤销
                if item["revoked_at"] is not None:
                    raise KeyError(device_id)
                current = item.get("keys")
                wanted = {"device": device_key, "approval": approval_key}
                if current is not None:
                    if {kind: current[kind] for kind in wanted} == wanted:
                        return {"bound_at": current["bound_at"]}      # 同一对钥匙重试：返回原回执
                    raise ValueError("这台设备已经绑定过钥匙；要更换请先撤销设备再重新授权")
                item["keys"] = {**wanted, "bound_at": int(self.clock())}
                self._save(data)
                return {"bound_at": item["keys"]["bound_at"]}
            raise KeyError(device_id)

    def signing_key(self, device_id: str, kind: str) -> str | None:
        data = self._load()
        for item in data["devices"] if data else []:
            if item["id"] == device_id:
                keys = item.get("keys")
                return keys.get(kind) if keys else None
        return None

    def consume_nonce(self, device_id: str, nonce: str) -> None:
        """记下这次签名用的 nonce；同一台设备时间窗内重复出现即重放，拒绝。"""
        path = self.directory / "nonces.json"
        with self._lock():
            now = int(self.clock())
            try:
                _regular(path)
                seen = json.loads(path.read_text(encoding="utf-8"))
                if not isinstance(seen, dict):
                    raise ValueError
            except FileNotFoundError:
                seen = {}
            except (OSError, ValueError) as error:
                raise MobileStoreError("签名记录不可用") from error
            mine = {key: stamp for key, stamp in (seen.get(device_id) or {}).items()
                    if isinstance(stamp, int) and now - stamp < _NONCE_KEEP}
            if nonce in mine:
                raise ValueError("重复的请求")
            mine[nonce] = now
            seen = {key: value for key, value in seen.items() if key != device_id and isinstance(value, dict)
                    and any(now - stamp < _NONCE_KEEP for stamp in value.values() if isinstance(stamp, int))}
            seen[device_id] = mine
            fd, temporary = tempfile.mkstemp(prefix=".nonces-", dir=self.directory)
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    json.dump(seen, handle)
                os.replace(temporary, path)
            finally:
                Path(temporary).unlink(missing_ok=True)

    def authenticate(self, token: str) -> dict | None:
        if not isinstance(token, str) or not _TOKEN.fullmatch(token):
            return None
        data = self._load()
        digest = hashlib.sha256(token.encode("ascii")).hexdigest()
        for item in data["devices"] if data else []:
            if hmac.compare_digest(digest, item["digest"]):
                if item["revoked_at"] is not None or self.clock() >= item["expires_at"]:
                    return None
                from src.gateway.account_access import account_grant_current
                if not account_grant_current(data, item):
                    return None
                return {"instance_id": data["instance_id"], "device": self.public(item)}
        return None
