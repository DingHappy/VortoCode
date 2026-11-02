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
from typing import Callable, List, Tuple
import uuid

from src.utils.state_dir import ensure_state_gitignore

SCOPE = "assistant:read"
MAX_DEVICES = 256
_MAX_BYTES = 1024 * 1024
_ID = re.compile(r"[a-f0-9]{32}")
_TOKEN = re.compile(r"vcm_[A-Za-z0-9_-]{43}")
_KEY = re.compile(r"[A-Za-z0-9+/]{86}[A-Za-z0-9+/=]{2}")   # 65 字节的 base64
_NONCE_KEEP = 900                                           # 秒；大于签名时间窗的两倍
PUSH_STALE = 30 * 86400                                     # App 每次启动都会重新登记；30 天没登记就不再推
MAX_TRUSTED = 16


def key_code(device_key: str, approval_key: str) -> str:
    """一对钥匙的核对码：手机上和 Desktop 上显示同一串，批准前对一眼，防止批错申请。"""
    digest = hashlib.sha256(f"{device_key}\n{approval_key}".encode("ascii")).hexdigest().upper()
    return f"{digest[:4]}-{digest[4:8]}"


def _check_keys(keys: object) -> None:
    """设备公钥（可选）：两把都在、都是 base64 的 65 字节点；绑定时已在曲线上校验过。"""
    if keys is None:
        return
    if (not isinstance(keys, dict) or set(keys) != {"device", "approval", "bound_at"}
            or type(keys["bound_at"]) is not int
            or not all(isinstance(keys[kind], str) and _KEY.fullmatch(keys[kind]) for kind in ("device", "approval"))):
        raise ValueError


def _check_pending(pending: object) -> None:
    if pending is None:
        return
    if (not isinstance(pending, dict) or set(pending) != {"device", "approval", "requested_at"}
            or type(pending["requested_at"]) is not int
            or not all(isinstance(pending[kind], str) and _KEY.fullmatch(pending[kind]) for kind in ("device", "approval"))):
        raise ValueError


def _check_trusted(trusted: object) -> None:
    if trusted is None:
        return
    if not isinstance(trusted, list) or len(trusted) > MAX_TRUSTED:
        raise ValueError
    for item in trusted:
        if (not isinstance(item, dict) or set(item) != {"device", "approval", "name", "approved_at"}
                or type(item["approved_at"]) is not int
                or not all(isinstance(item[kind], str) and _KEY.fullmatch(item[kind]) for kind in ("device", "approval"))):
            raise ValueError
        _name(item["name"])


def _check_push(push: object) -> None:
    if push is None:
        return
    if (not isinstance(push, dict) or set(push) != {"token", "key", "environment", "refreshed_at"}
            or not isinstance(push["token"], str) or not re.fullmatch(r"[0-9a-f]{64,200}", push["token"])
            or not isinstance(push["key"], str) or not _KEY.fullmatch(push["key"])
            or push["environment"] not in ("production", "sandbox") or type(push["refreshed_at"]) is not int):
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
                _check_pending(item.get("pending_keys"))
                _check_push(item.get("push"))
                seen.add(item["id"])
            _check_trusted(data.get("trusted_keys"))
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
        """给设备绑定签名公钥。只能绑一次：要换钥匙，先撤销这台设备再重新授权。

        手机入口在公网上，光凭账号登录不能拿到写入能力（ADR-011 修订）：信任挂在钥匙上，不挂在设备记录上。
        - 已信任的钥匙（同一台手机重新登录）直接绑定；
        - 还没有任何信任过的钥匙时，第一台直接绑定并记为信任（首次使用即信任）；
        - 其余一律停在「待确认」，主人在 Desktop 上对过核对码批准后才生效。
        """
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
                code = key_code(device_key, approval_key)
                if current is not None:
                    if {kind: current[kind] for kind in wanted} == wanted:
                        return {"bound_at": current["bound_at"], "code": code}  # 同一对钥匙重试：返回原回执
                    raise ValueError("这台设备已经绑定过钥匙；要更换请先撤销设备再重新授权")
                # 首次使用即信任只发生一次：名单一旦建立（哪怕之后被移空），再来的钥匙都要批准
                first = "trusted_keys" not in data and not any(other.get("keys") for other in data["devices"])
                trusted = data.setdefault("trusted_keys", [])
                if first or any({kind: entry[kind] for kind in wanted} == wanted for entry in trusted):
                    now = int(self.clock())
                    item["keys"] = {**wanted, "bound_at": now}
                    item.pop("pending_keys", None)
                    if first:
                        trusted.append({**wanted, "name": item["name"], "approved_at": now})
                    self._save(data)
                    return {"bound_at": now, "code": code}
                pending = item.get("pending_keys")
                if pending is not None and {kind: pending[kind] for kind in wanted} != wanted:
                    raise ValueError("这台设备已有一对钥匙在等确认")
                if pending is None:
                    pending = item["pending_keys"] = {**wanted, "requested_at": int(self.clock())}
                    self._save(data)
                return {"pending": True, "requested_at": pending["requested_at"], "code": code}
            raise KeyError(device_id)

    def key_requests(self) -> dict:
        """等确认的钥匙申请与已信任的钥匙（给 Desktop 列出来批准或移除）。不含公钥本身，只给核对码。"""
        data = self._load()
        devices = data["devices"] if data else []
        pending = [{"device_id": item["id"], "name": item["name"], "requested_at": item["pending_keys"]["requested_at"],
                    "code": key_code(item["pending_keys"]["device"], item["pending_keys"]["approval"])}
                   for item in devices if item.get("pending_keys") and item["revoked_at"] is None]
        trusted = [{"code": key_code(entry["device"], entry["approval"]), "name": entry["name"],
                    "approved_at": entry["approved_at"]} for entry in (data or {}).get("trusted_keys") or []]
        return {"pending": pending, "trusted": trusted}

    def decide_keys(self, device_id: str, approve: bool) -> dict:
        """主人批准或拒绝一份钥匙申请。批准的是这对钥匙：以后这台手机重新登录也不用再批。"""
        if not isinstance(device_id, str) or not _ID.fullmatch(device_id) or type(approve) is not bool:
            raise KeyError(device_id)
        with self._lock():
            data = self._load()
            for item in data["devices"] if data else []:
                pending = item.get("pending_keys")
                if item["id"] != device_id or not pending or item["revoked_at"] is not None:
                    continue
                del item["pending_keys"]
                wanted = {"device": pending["device"], "approval": pending["approval"]}
                if approve:
                    trusted = data.setdefault("trusted_keys", [])
                    if len(trusted) >= MAX_TRUSTED:
                        raise ValueError("信任的手机已达上限，请先移除不用的")
                    now = int(self.clock())
                    trusted.append({**wanted, "name": item["name"], "approved_at": now})
                    item["keys"] = {**wanted, "bound_at": now}
                self._save(data)
                return {"approved": approve, "code": key_code(wanted["device"], wanted["approval"])}
            raise KeyError(device_id)

    def untrust_keys(self, code: str) -> dict:
        """移除一台信任的手机：从信任名单删掉，并清掉所有设备记录上的这对钥匙，下一次写入就签不过。"""
        with self._lock():
            data = self._load()
            trusted = (data or {}).get("trusted_keys") or []
            match = [entry for entry in trusted if key_code(entry["device"], entry["approval"]) == code]
            if not data or not match:
                raise KeyError(code)
            data["trusted_keys"] = [entry for entry in trusted if entry not in match]
            for item in data["devices"]:
                keys = item.get("keys")
                if keys and any(keys["device"] == entry["device"] for entry in match):
                    del item["keys"]
            self._save(data)
            return {"removed": code}

    def is_active(self, device_id: str) -> bool:
        """设备记录存在且没被撤销（常驻助理核对转发来的签名请求时用；会话有效期由网关核对）。"""
        if not isinstance(device_id, str) or not _ID.fullmatch(device_id):
            return False
        data = self._load()
        return any(item["id"] == device_id and item["revoked_at"] is None for item in (data["devices"] if data else []))

    def signing_key(self, device_id: str, kind: str) -> str | None:
        data = self._load()
        for item in data["devices"] if data else []:
            if item["id"] == device_id:
                keys = item.get("keys")
                return keys.get(kind) if keys else None
        return None

    def register_push(self, device_id: str, registration: dict) -> None:
        """登记或刷新这台设备的推送目标（APNs 令牌 + 解密推送用的公钥）。"""
        if not isinstance(device_id, str) or not _ID.fullmatch(device_id):
            raise KeyError(device_id)
        with self._lock():
            data = self._load()
            for item in data["devices"] if data else []:
                if item["id"] == device_id:
                    if item["revoked_at"] is not None:
                        raise KeyError(device_id)
                    item["push"] = {**registration, "refreshed_at": int(self.clock())}
                    self._save(data)
                    return
            raise KeyError(device_id)

    def push_targets(self) -> List[Tuple[str, dict]]:
        """可以推送的设备。不按读取租约的有效期筛（App 在后台时租约本来就会过期），
        只排除已撤销的和 30 天没重新登记的；推送正文是加密的，过期登记最多泄露「有一条新消息」。"""
        data = self._load()
        now = self.clock()
        return [(item["id"], item["push"]) for item in (data["devices"] if data else [])
                if item.get("push") and item["revoked_at"] is None
                and now - item["push"]["refreshed_at"] < PUSH_STALE]

    def forget_push(self, device_id: str, token: str) -> None:
        """APNs 说这个令牌已失效：清掉登记（只清同一个令牌，App 期间换了新令牌就不动）。"""
        with self._lock():
            data = self._load()
            for item in data["devices"] if data else []:
                if item["id"] == device_id and (item.get("push") or {}).get("token") == token:
                    del item["push"]
                    self._save(data)
                    return

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
