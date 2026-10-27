"""Runtime ownership and short read leases in the existing device-grant file.

Only a trusted admin/native bridge can enroll an owner or exchange a verified
account session. No provider credentials, email matching or second task store.
"""

from __future__ import annotations

import hashlib
import hmac
import uuid

from src.gateway.account_access import AccountIdentity
from src.gateway.mobile_devices import (
    MAX_DEVICES,
    SCOPE,
    MobileDevices,
    MobileStoreError,
    _ID,
    _TOKEN,
    _name,
)
from src.gateway.account_service import AccountSession

READ_LEASE_SECONDS = 300


class AccountOwnerMismatch(ValueError):
    pass


class AccountRegistration(MobileDevices):
    def _load(self) -> dict | None:
        data = super()._load()
        if data is None:
            return None
        try:
            revision = data.setdefault("account_revision", 0)
            registration = data.setdefault("account_registration", None)
            if type(revision) is not int or revision < 0:
                raise ValueError
            if registration is not None:
                if not isinstance(registration, dict) or revision < 1:
                    raise ValueError
                owner = registration.get("owner")
                if not isinstance(owner, dict) or set(owner) != {"issuer", "subject"}:
                    raise ValueError
                identity = AccountIdentity(**owner)
                if owner != {"issuer": identity.issuer, "subject": identity.subject}:
                    raise ValueError
                _name(registration.get("name"))
                if (
                    type(registration.get("host_device_id")) is not int
                    or registration["host_device_id"] < 1
                ):
                    raise ValueError
            return data
        except (ValueError, TypeError):
            raise MobileStoreError("助手账号归属记录不可用") from None

    @staticmethod
    def _view(data: dict | None) -> dict:
        return {
            "schema_version": 1,
            "instance_id": data["instance_id"] if data else None,
            "revision": data["account_revision"] if data else 0,
            "registration": data["account_registration"] if data else None,
        }

    def read(self) -> dict:
        return self._view(self._load())

    @staticmethod
    def _revision(data: dict, revision: int) -> None:
        if type(revision) is not int or revision != data["account_revision"]:
            raise ValueError("助手归属已变化，请重新读取后确认")

    def register(self, session: AccountSession, name: str, revision: int, instance_id=...) -> dict:
        name = _name(name)
        if not isinstance(session, AccountSession):
            raise ValueError("需要已经核验的账号会话")
        with self._lock():
            data = self._load()
            if instance_id is not ... and instance_id != (data["instance_id"] if data else None):
                raise ValueError("运行端身份已变化，请重新核对")
            data = data or {
                "schema_version": 1,
                "instance_id": uuid.uuid4().hex,
                "devices": [],
                "account_revision": 0,
                "account_registration": None,
            }
            self._revision(data, revision)
            owner = {"issuer": session.identity.issuer, "subject": session.identity.subject}
            current = data["account_registration"]
            if current and current["owner"] != owner:
                raise AccountOwnerMismatch("助手已属于另一个账号，请先明确解除原归属")
            next_registration = {"owner": owner, "name": name, "host_device_id": session.device_id}
            if next_registration == current:
                return self._view(data)
            self._revoke_account_leases(data)
            data["account_revision"] += 1
            data["account_registration"] = next_registration
            self._save(data)
            return self._view(data)

    def unregister(self, revision: int, instance_id=...) -> dict:
        with self._lock():
            data = self._load()
            if data is None:
                raise ValueError("助手尚未登记账号归属")
            if instance_id is not ... and instance_id != data["instance_id"]:
                raise ValueError("运行端身份已变化，请重新核对")
            self._revision(data, revision)
            if data["account_registration"] is not None:
                self._revoke_account_leases(data)
                data["account_registration"] = None
                data["account_revision"] += 1
                self._save(data)
            return self._view(data)

    def _revoke_account_leases(self, data: dict) -> None:
        for item in data["devices"]:
            if "account_binding" in item and item["revoked_at"] is None:
                item["revoked_at"] = int(self.clock())

    def read_lease(
        self,
        session: AccountSession,
        name: str,
        revision: int,
        request_id: str,
        token: str,
        previous_expires_at: int = 0,
    ) -> dict:
        """Five-minute resource credential. Provider tokens are not returned/saved."""
        name = _name(name)
        if (
            not isinstance(session, AccountSession)
            or not isinstance(request_id, str)
            or not _ID.fullmatch(request_id)
            or not isinstance(token, str)
            or not _TOKEN.fullmatch(token)
            or type(previous_expires_at) is not int
            or previous_expires_at < 0
        ):
            raise ValueError("需要已经核验的账号会话")
        with self._lock():
            data = self._load()
            if data is None:
                raise AccountOwnerMismatch("助手尚未登记账号归属")
            self._revision(data, revision)
            registration = data["account_registration"]
            owner = {"issuer": session.identity.issuer, "subject": session.identity.subject}
            if not registration or registration["owner"] != owner:
                raise AccountOwnerMismatch("当前账号不拥有此助手")
            binding = {**owner, "revision": revision, "device_id": session.device_id}
            digest, now = hashlib.sha256(token.encode("ascii")).hexdigest(), int(self.clock())
            item = next((item for item in data["devices"] if item["id"] == request_id), None)
            if item:
                if (
                    item.get("account_binding") != binding
                    or item["revoked_at"] is not None
                    or item["name"] != name
                    or not hmac.compare_digest(item["digest"], digest)
                ):
                    raise AccountOwnerMismatch("原设备授权已变化，不能续用")
                if previous_expires_at != item.get("renewed_from"):
                    if previous_expires_at != item["expires_at"]:
                        raise ValueError("读取授权已续期，请核对原回执")
                    item["renewed_from"] = previous_expires_at
                    item["expires_at"] = max(now + READ_LEASE_SECONDS, previous_expires_at + 1)
                    self._save(data)
            else:
                if previous_expires_at != 0:
                    raise ValueError("原设备授权不存在")
                if any(hmac.compare_digest(item["digest"], digest) for item in data["devices"]):
                    raise ValueError("凭证已用于其它设备")
                if len(data["devices"]) >= MAX_DEVICES:
                    raise ValueError("设备授权记录已达上限")
                item = {
                    "id": request_id,
                    "name": name,
                    "scope": SCOPE,
                    "digest": digest,
                    "created_at": now,
                    "expires_at": now + READ_LEASE_SECONDS,
                    "revoked_at": None,
                    "account_binding": binding,
                    "renewed_from": 0,
                }
                data["devices"].append(item)
                self._save(data)
            return {
                "instance_id": data["instance_id"],
                "device": self.public(item),
                "token": token,
                "registration_revision": revision,
                "authorization": "account_read_lease",
            }
