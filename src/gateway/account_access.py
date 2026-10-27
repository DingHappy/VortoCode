"""Account-first assistant ownership contract, independent of login transport.

Values must come from the trusted account service / registry, never from a
phone's claimed email or user ID. This module does not verify OAuth tokens,
persist registrations, issue grants, or turn account ownership into approval
or desktop-control authority.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from urllib.parse import urlsplit

DEFAULT_ACCOUNT_ORIGIN = "https://account.vortotech.com"


def account_origin(value: str) -> str:
    try:
        if not isinstance(value, str) or any(char.isspace() or ord(char) < 32 for char in value):
            raise ValueError
        parsed = urlsplit(value)
        port = parsed.port
        if port is not None and port < 1:
            raise ValueError
        if (
            not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or parsed.path not in {"", "/"}
            or (
                parsed.scheme != "https"
                and not (
                    parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1", "::1"}
                )
            )
        ):
            raise ValueError
        host = parsed.hostname.lower()
        if ":" in host:
            host = f"[{host}]"
        default_port = 443 if parsed.scheme == "https" else 80
        return f"{parsed.scheme}://{host}" + (f":{port}" if port and port != default_port else "")
    except (ValueError, TypeError, AttributeError):
        raise ValueError("账号服务需要 HTTPS origin 或本机开发 origin") from None


@dataclass(frozen=True)
class AccountIdentity:
    issuer: str
    subject: str

    def __post_init__(self):
        object.__setattr__(self, "issuer", account_origin(self.issuer))
        if not isinstance(self.subject, str) or not re.fullmatch(
            r"[A-Za-z0-9_-]{1,128}", self.subject
        ):
            raise ValueError("需要稳定账号身份")


def identity_from_account_service(body: object, issuer: str) -> AccountIdentity:
    """Parse an already-authenticated /api/me result. Email is display-only.

    The current service still needs to add user_id. Refuse its legacy email-only
    response rather than misidentifying a renamed, recycled or forged email.
    """
    if not isinstance(body, dict) or type(body.get("user_id")) is not int or body["user_id"] < 1:
        raise ValueError("账号服务尚未提供稳定 user_id")
    return AccountIdentity(issuer, str(body["user_id"]))


@dataclass(frozen=True)
class AccountAssistant:
    instance_id: str
    owner: AccountIdentity
    name: str
    availability: str = "unknown"

    def __post_init__(self):
        if not isinstance(self.instance_id, str) or not re.fullmatch(
            r"[a-f0-9]{32}", self.instance_id
        ):
            raise ValueError("需要持久的助手身份")
        if (
            not isinstance(self.owner, AccountIdentity)
            or not isinstance(self.name, str)
            or not self.name.strip()
            or not isinstance(self.availability, str)
        ):
            raise ValueError("助手登记无效")


def own_assistants(identity: AccountIdentity, registry: list[AccountAssistant]) -> list[dict]:
    """Project a trusted registry for one logged-in account; offline is discoverable.

    Ownership is identity equality, not an email/device-name match. This is a
    projection only; the runtime must independently verify each access grant.
    """
    if not isinstance(identity, AccountIdentity):
        raise ValueError("需要已经核验的账号身份")
    seen: set[str] = set()
    result = []
    for assistant in registry:
        if not isinstance(assistant, AccountAssistant) or assistant.instance_id in seen:
            raise ValueError("助手登记重复或格式无效")
        seen.add(assistant.instance_id)
        if assistant.owner != identity:
            continue
        result.append(
            {
                "instance_id": assistant.instance_id,
                "name": assistant.name,
                "availability": assistant.availability
                if assistant.availability in {"online", "offline"}
                else "unknown",
            }
        )
    return result


def account_grant_current(data: dict, item: dict) -> bool:
    """An account lease cannot survive owner removal/replacement or malformed binding."""
    if "account_binding" not in item:
        return True  # Explicit admin-managed advanced grants retain their original policy.
    try:
        binding, registration = item["account_binding"], data.get("account_registration")
        if not isinstance(binding, dict) or not isinstance(registration, dict):
            return False
        if (
            not isinstance(registration.get("name"), str)
            or not registration["name"].strip()
            or type(registration.get("host_device_id")) is not int
            or registration["host_device_id"] < 1
        ):
            return False
        revision = data.get("account_revision")
        if (
            type(revision) is not int
            or revision < 1
            or type(binding.get("revision")) is not int
            or binding["revision"] != revision
            or type(binding.get("device_id")) is not int
            or binding["device_id"] < 1
        ):
            return False
        owner = AccountIdentity(**registration["owner"])
        return owner == AccountIdentity(binding["issuer"], binding["subject"])
    except (KeyError, TypeError, ValueError):
        return False
