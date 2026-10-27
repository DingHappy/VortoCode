"""Two-sided device pairing in the existing atomic device-grant store.

The phone generates its own credential. Only its digest is saved, so approval
and response-loss recovery never require storing or returning a plaintext token.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import uuid

from src.gateway.mobile_devices import (
    MAX_DEVICES,
    SCOPE,
    MobileDevices,
    MobileStoreError,
    _ID,
    _TOKEN,
    _name,
)

WINDOW_SECONDS = 300
MAX_WINDOW_REQUESTS = 8
MAX_PAIRINGS = 64
_CODE = re.compile(r"[A-HJ-NP-Z2-9]{8}")


class PairingDenied(ValueError):
    """The caller does not possess the pairing's credential proof."""


class MobilePairing(MobileDevices):
    def _load(self) -> dict | None:
        data = super()._load()
        if data is None:
            return None
        try:
            window = data.setdefault("pairing_window", None)
            if window is not None and (
                not isinstance(window, dict)
                or not _ID.fullmatch(window.get("id", ""))
                or type(window.get("expires_at")) is not int
                or window["expires_at"] < 0
            ):
                raise ValueError
            pairings = data.setdefault("pairings", [])
            if not isinstance(pairings, list) or len(pairings) > MAX_PAIRINGS:
                raise ValueError
            seen: set[str] = set()
            for item in pairings:
                if (
                    not isinstance(item, dict)
                    or not _ID.fullmatch(item.get("id", ""))
                    or item["id"] in seen
                    or not _ID.fullmatch(item.get("window_id", ""))
                    or not _CODE.fullmatch(item.get("code", ""))
                    or not re.fullmatch(r"[a-f0-9]{64}", item.get("digest", ""))
                    or type(item.get("expires_at")) is not int
                    or item["expires_at"] < 0
                    or item.get("status") not in {"pending", "approved", "rejected"}
                ):
                    raise ValueError
                _name(item.get("name"))
                if item["status"] == "approved" and not any(
                    device["id"] == item["id"] for device in data["devices"]
                ):
                    raise ValueError
                seen.add(item["id"])
            return data
        except (ValueError, TypeError):
            raise MobileStoreError("配对记录不可用") from None

    def _state(self, data: dict, item: dict) -> str:
        now, window = self.clock(), data["pairing_window"]
        if now >= item["expires_at"]:
            return "expired"
        if item["status"] == "pending" and (
            not window or window["id"] != item["window_id"] or now >= window["expires_at"]
        ):
            return "expired"
        if item["status"] == "approved":
            device = next(device for device in data["devices"] if device["id"] == item["id"])
            if device["revoked_at"] is not None:
                return "revoked"
            if now >= device["expires_at"]:
                return "expired"
        return item["status"]

    def _view(self, data: dict, item: dict) -> dict:
        state = self._state(data, item)
        device = next((device for device in data["devices"] if device["id"] == item["id"]), None)
        return {
            "schema_version": 1,
            "instance_id": data["instance_id"],
            "id": item["id"],
            "name": item["name"],
            "code": item["code"][:4] + "-" + item["code"][4:],
            "expires_at": item["expires_at"],
            "status": state,
            "scopes": [SCOPE],
            "device": self.public(device) if state == "approved" and device else None,
        }

    def overview(self) -> dict:
        data = self._load()
        if data is None:
            return {"instance_id": None, "window_expires_at": None, "pairings": []}
        window = data["pairing_window"]
        active = window and self.clock() < window["expires_at"]
        return {
            "instance_id": data["instance_id"],
            "window_expires_at": window["expires_at"] if active else None,
            "pairings": [
                self._view(data, item)
                for item in data["pairings"]
                if self.clock() < item["expires_at"]
            ],
        }

    def window(self, enabled: bool) -> dict:
        if type(enabled) is not bool:
            raise ValueError("需要明确配对窗口状态")
        with self._lock():
            data = self._load() or {
                "schema_version": 1,
                "instance_id": uuid.uuid4().hex,
                "devices": [],
                "pairings": [],
            }
            data["pairing_window"] = (
                {"id": uuid.uuid4().hex, "expires_at": int(self.clock()) + WINDOW_SECONDS}
                if enabled
                else None
            )
            # Keep recent terminal receipts; changing window never reactivates pending requests.
            data["pairings"] = [
                item for item in data["pairings"] if self.clock() < item["expires_at"]
            ]
            self._save(data)
        return self.overview()

    @staticmethod
    def _proof(token: str) -> str:
        if not isinstance(token, str) or not _TOKEN.fullmatch(token):
            raise PairingDenied("配对身份无效")
        return hashlib.sha256(token.encode("ascii")).hexdigest()

    def start(self, request_id: str, name: str, token: str) -> dict:
        name, digest = _name(name), self._proof(token)
        if not isinstance(request_id, str) or not _ID.fullmatch(request_id):
            raise ValueError("配对请求身份无效")
        with self._lock():
            data = self._load()
            if data is None:
                raise ValueError("请先在电脑端开放配对窗口")
            existing = next((item for item in data["pairings"] if item["id"] == request_id), None)
            if existing:
                if not hmac.compare_digest(existing["digest"], digest) or existing["name"] != name:
                    raise PairingDenied("配对身份无效")
                return self._view(data, existing)
            window = data["pairing_window"]
            if not window or self.clock() >= window["expires_at"]:
                raise ValueError("请先在电脑端开放配对窗口")
            if (
                sum(item["window_id"] == window["id"] for item in data["pairings"])
                >= MAX_WINDOW_REQUESTS
            ):
                raise ValueError("本次配对请求已达上限，请在电脑端重新开放窗口")
            data["pairings"] = [
                item for item in data["pairings"] if self.clock() < item["expires_at"]
            ]
            if len(data["pairings"]) >= MAX_PAIRINGS:
                raise ValueError("配对记录已达上限，请稍后重试")
            if any(
                device["digest"] == digest or device["id"] == request_id
                for device in data["devices"]
            ) or any(item["digest"] == digest for item in data["pairings"]):
                raise ValueError("需要新的设备凭证和请求身份")
            item = {
                "id": request_id,
                "name": name,
                "digest": digest,
                "window_id": window["id"],
                "code": "".join(
                    secrets.choice("ABCDEFGHJKLMNPQRSTUVWXYZ23456789") for _ in range(8)
                ),
                "expires_at": min(int(self.clock()) + WINDOW_SECONDS, window["expires_at"]),
                "status": "pending",
            }
            data["pairings"].append(item)
            self._save(data)
            return self._view(data, item)

    def status(self, request_id: str, token: str) -> dict:
        digest, data = self._proof(token), self._load()
        item = (
            next((item for item in data["pairings"] if item["id"] == request_id), None)
            if data
            else None
        )
        if not item or not hmac.compare_digest(item["digest"], digest):
            raise PairingDenied("配对身份无效")
        return self._view(data, item)

    def cancel(self, request_id: str, token: str) -> dict:
        digest = self._proof(token)
        with self._lock():
            data = self._load()
            item = (
                next((item for item in data["pairings"] if item["id"] == request_id), None)
                if data
                else None
            )
            if not item or not hmac.compare_digest(item["digest"], digest):
                raise PairingDenied("配对身份无效")
            if item["status"] == "pending":
                item["status"] = "rejected"
            elif item["status"] == "approved":
                device = next(device for device in data["devices"] if device["id"] == item["id"])
                if device["revoked_at"] is None:
                    device["revoked_at"] = int(self.clock())
            self._save(data)
            return self._view(data, item)

    def decide(self, request_id: str, code: str, approve: bool, ttl_days: int = 30) -> dict:
        if (
            not isinstance(code, str)
            or type(approve) is not bool
            or type(ttl_days) is not int
            or not 1 <= ttl_days <= 90
        ):
            raise ValueError("配对决定或有效期无效")
        code = code.replace("-", "").replace(" ", "").upper()
        with self._lock():
            data = self._load()
            item = (
                next((item for item in data["pairings"] if item["id"] == request_id), None)
                if data
                else None
            )
            if not item:
                raise KeyError(request_id)
            if not hmac.compare_digest(code.encode("utf-8"), item["code"].encode("ascii")):
                raise ValueError("核对码不一致")
            state, wanted = self._state(data, item), "approved" if approve else "rejected"
            if state == wanted:
                return self._view(
                    data, item
                )  # Recover a lost response without issuing another grant.
            if state != "pending":
                raise ValueError("配对已过期、结束或撤销，请重新核对")
            if approve:
                if len(data["devices"]) >= MAX_DEVICES:
                    raise ValueError("设备授权记录已达上限")
                now = int(self.clock())
                data["devices"].append(
                    {
                        "id": item["id"],
                        "name": item["name"],
                        "scope": SCOPE,
                        "digest": item["digest"],
                        "created_at": now,
                        "expires_at": now + ttl_days * 86400,
                        "revoked_at": None,
                    }
                )
            item["status"] = wanted
            self._save(data)
            return self._view(data, item)
