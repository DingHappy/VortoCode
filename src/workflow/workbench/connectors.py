"""连接器授权：在 GitHub 上点一下就连上（OAuth 设备授权流程，Device Flow）。

常驻电脑没有浏览器，所以走设备授权：Desktop 显示一个验证码和链接，你在任何设备的浏览器里
打开 github.com/login/device、输入验证码、点 Authorize；常驻电脑在后台按 GitHub 给的间隔
轮询，拿到令牌后写进 `~/.vortocode/connectors/<名字>.token`（权限 600）——与手放令牌是同一个
位置、同一套读取规则（见 agents/mcp_tools.load_connector_secret）。

- 授权挂在主人自己建的 GitHub App 名下（配置里写它的 Client ID，公开信息，不是密钥）；
  设备授权不需要 Client Secret，常驻电脑上也不放。
- 设备码只留在本进程内存里，不出接口；接口只给人看的验证码与链接。
- 令牌只写文件，不进接口返回、日志或任何模型上下文；状态文件（何时连上、何时过期）不含令牌。
"""
from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import re
import stat
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_DEVICE_CODE_URL = "https://github.com/login/device/code"
_TOKEN_URL = "https://github.com/login/oauth/access_token"
_GRANT = "urn:ietf:params:oauth:grant-type:device_code"
_NAME = re.compile(r"[a-z][a-z0-9_-]{0,31}")
_CLIENT_ID = re.compile(r"(Iv1\.|Iv2[0-9]|Ov2[0-9])[A-Za-z0-9_.]{6,64}")

# 进行中的授权：{name: {...}}。设备码只在这里。
_PENDING: Dict[str, Dict[str, Any]] = {}
_AUTH_ATTEMPTS: Dict[str, object] = {}


class AuthorizationSuperseded(RuntimeError):
    def __init__(self):
        super().__init__("授权已取消或被新的请求替换，请核对当前状态。")


def _connectors_dir() -> Path:
    return Path.home() / ".vortocode" / "connectors"


def _token_path(name: str) -> Path:
    return _connectors_dir() / f"{name}.token"


def _meta_path(name: str) -> Path:
    return _connectors_dir() / f"{name}.meta.json"


def connector_configs(root: str) -> List[Dict[str, Any]]:
    """`config/mcp.yaml` 里带 `oauth` 的连接器（名字、提供方、Client ID）。坏配置当作没有。"""
    try:
        import yaml
        data = yaml.safe_load((Path(root) / "config" / "mcp.yaml").read_text(encoding="utf-8")) or {}
    except Exception:  # noqa: BLE001
        return []
    out = []
    for server in data.get("servers") or [] if isinstance(data, dict) else []:
        if not isinstance(server, dict):
            continue
        oauth = server.get("oauth") if isinstance(server.get("oauth"), dict) else {}
        name, credential = str(server.get("name") or ""), str(server.get("credential") or "")
        client_id = str(oauth.get("client_id") or "")
        if (oauth.get("provider") == "github" and _NAME.fullmatch(name) and credential == name
                and _CLIENT_ID.fullmatch(client_id)):
            out.append({"name": name, "provider": "github", "client_id": client_id,
                        "read_only": bool(server.get("read_only", True))})
    return out


def _find(root: str, name: str) -> Dict[str, Any]:
    for item in connector_configs(root):
        if item["name"] == name:
            return item
    raise KeyError(f"没有可授权的连接器：{name}")


def _read_meta(name: str) -> Optional[Dict[str, Any]]:
    try:
        path = _meta_path(name)
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_size > 8192:
            return None
        with path.open("rb") as handle:
            raw = handle.read(8193)
        if len(raw) > 8192:
            return None
        data = json.loads(raw)
        return data if isinstance(data, dict) else None
    except FileNotFoundError:
        return {}  # Manually provisioned credentials need no OAuth metadata.
    except (OSError, ValueError, RecursionError):
        return None


def _timestamp(value: object):
    if isinstance(value, (int, float)) and not isinstance(value, bool) and 0 <= value <= 2**53 - 1 and math.isfinite(value):
        return value
    return None


def status(root: str) -> List[Dict[str, Any]]:
    """Local credential readiness only; no provider probe or MCP connection inference.

    ``connected`` is retained as a legacy alias for credential availability.
    Session-owned MCP observations remain on /api/extensions/mcp.
    """
    from src.agents.mcp_tools import load_connector_secret, connector_locally_revoked

    rows = []
    for item in connector_configs(root):
        name = item["name"]
        metadata = _read_meta(name)
        meta = metadata or {}
        expires_at = _timestamp(meta.get("expires_at"))
        expired = expires_at is not None and expires_at <= time.time()
        try:
            load_connector_secret(name)
            credential_status = "available"
        except FileNotFoundError:
            credential_status = "missing"
        except (OSError, ValueError):
            credential_status = "invalid"
        locally_revoked = connector_locally_revoked(name)
        if credential_status == "available":
            if locally_revoked:
                credential_status = "invalid"
            elif metadata is None or (meta.get("expires_at") is not None and expires_at is None):
                credential_status = "unknown"
            elif expired:
                credential_status = "expired"
        available = credential_status == "available"
        credential_error = {
            "invalid": "凭据不可用，请重新授权或检查本机凭据权限。",
            "unknown": "授权有效期信息无法确认，请检查后重新授权。",
            "expired": "凭据已过期，请重新授权。",
        }.get(credential_status, "")
        if locally_revoked:
            credential_error = "本机已撤销此凭据，请重新授权或完成凭据清理。"
        authorized_at = _timestamp(meta.get("connected_at"))
        pending = _PENDING.get(name)
        if pending and pending.get("expires_at", 0) <= time.time():
            pending = None
        rows.append({
            "name": name, "provider": item["provider"], "read_only": item["read_only"],
            "connected": available,  # Compatibility only; never proof of a live service.
            "credential_status": credential_status, "credential_available": available,
            "credential_error": credential_error, "connection_status": "unobserved",
            "locally_revoked": locally_revoked, "local_revocation_supported": True,
            "expired": expired,
            "authorized_at": authorized_at, "connected_at": authorized_at,
            "expires_at": expires_at,
            "authorizing": ({"user_code": pending["user_code"], "verification_uri": pending["verification_uri"],
                             "expires_at": pending["expires_at"]} if pending and not pending.get("error") else None),
            "error": (pending or {}).get("error") or "",
        })
    return rows


async def _post(url: str, data: Dict[str, str]) -> Dict[str, Any]:
    import aiohttp
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20), trust_env=False) as client:
        async with client.post(url, data=data, headers={"Accept": "application/json"}) as response:
            payload = await response.json(content_type=None)
            if not isinstance(payload, dict):
                raise ValueError("GitHub 返回的不是 JSON 对象")
            if response.status >= 400 and "error" not in payload:
                payload["error"] = f"http_{response.status}"
            return payload


def _save_token(name: str, token: str, expires_in: Optional[int]) -> None:
    """令牌写成本人只读的文件（原子替换）；状态另存，不含令牌。"""
    directory = _connectors_dir()
    directory.mkdir(parents=True, exist_ok=True)
    os.chmod(directory, 0o700)
    tmp = directory / f".{name}.token.tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(token + "\n")
    os.replace(tmp, _token_path(name))
    meta = {"connected_at": time.time(), "expires_at": (time.time() + expires_in) if expires_in else None}
    _meta_path(name).write_text(json.dumps(meta), encoding="utf-8")


async def _poll(name: str, client_id: str, post=_post, sleep=asyncio.sleep) -> None:
    pending = _PENDING.get(name)
    if pending is None:
        return
    interval = max(5, int(pending.get("interval") or 5))
    while time.time() < pending["expires_at"]:
        await sleep(interval)
        if _PENDING.get(name) is not pending:
            return                                  # 被新一轮授权或断开取代
        try:
            result = await post(_TOKEN_URL, {"client_id": client_id, "device_code": pending["device_code"],
                                             "grant_type": _GRANT})
        except Exception as error:  # noqa: BLE001 —— 网络抖动：下一轮再问
            if _PENDING.get(name) is not pending:
                return
            logger.warning("连接器 %s 授权轮询失败（%s）", name, type(error).__name__)
            continue
        if _PENDING.get(name) is not pending or time.time() >= pending["expires_at"]:
            return
        token = result.get("access_token")
        if isinstance(token, str) and token and not any(ch.isspace() for ch in token):
            expires_in = result.get("expires_in")
            _save_token(name, token, int(expires_in) if isinstance(expires_in, int) else None)
            _PENDING.pop(name, None)
            _AUTH_ATTEMPTS.pop(name, None)
            return
        error_code = str(result.get("error") or "")
        if error_code == "authorization_pending":
            continue
        if error_code == "slow_down":
            interval += 5
            continue
        pending["error"] = {"expired_token": "验证码过期了，重新点一次连接",
                            "access_denied": "你在 GitHub 上拒绝了授权"}.get(error_code, "授权失败，请重新授权。")
        return
    if _PENDING.get(name) is pending:
        pending["error"] = "验证码过期了，重新点一次连接"


async def start_authorization(root: str, name: str, *, post=_post) -> Dict[str, Any]:
    """发起设备授权：返回给人看的验证码与链接；后台开始轮询。设备码不出本进程。"""
    item = _find(root, name)
    attempt = object()
    _AUTH_ATTEMPTS[name] = attempt
    previous = _PENDING.pop(name, None)
    if previous and previous.get("task"):
        previous["task"].cancel()
    try:
        result = await post(_DEVICE_CODE_URL, {"client_id": item["client_id"]})
    except BaseException:
        if _AUTH_ATTEMPTS.get(name) is attempt:
            _AUTH_ATTEMPTS.pop(name, None)
        raise
    if _AUTH_ATTEMPTS.get(name) is not attempt:
        raise AuthorizationSuperseded()
    user_code, uri, device_code = result.get("user_code"), result.get("verification_uri"), result.get("device_code")
    if not (isinstance(user_code, str) and isinstance(device_code, str)
            and isinstance(uri, str) and uri.startswith("https://github.com/")):
        _AUTH_ATTEMPTS.pop(name, None)
        raise ValueError("GitHub 设备授权响应无效")
    _PENDING[name] = {"device_code": device_code, "user_code": user_code, "verification_uri": uri,
                      "interval": int(result.get("interval") or 5),
                      "expires_at": time.time() + int(result.get("expires_in") or 900)}
    task = asyncio.create_task(_poll(name, item["client_id"], post=post))
    _PENDING[name]["task"] = task
    return {"user_code": user_code, "verification_uri": uri, "expires_at": _PENDING[name]["expires_at"]}


def disconnect(root: str, name: str, *, on_revoke=None) -> bool:
    """断开：删令牌与状态（GitHub 上的授权请到 App 设置里撤销）。"""
    if not _NAME.fullmatch(name):
        raise KeyError("连接器名称无效")
    try:
        _find(root, name)
    except KeyError:
        from src.tools.connector_access import has_connector_access
        if not (name in _PENDING or name in _AUTH_ATTEMPTS or has_connector_access(name)
                or os.path.lexists(_token_path(name)) or os.path.lexists(_meta_path(name))):
            raise
    from src.agents.mcp_tools import revoke_connector
    revocation = revoke_connector(name)
    _AUTH_ATTEMPTS.pop(name, None)
    pending = _PENDING.pop(name, None)
    if pending and pending.get("task"):
        pending["task"].cancel()
    if on_revoke is not None:
        on_revoke(revocation)
    removed = False
    for path in (_token_path(name), _meta_path(name)):
        try:
            path.unlink()
            removed = True
        except FileNotFoundError:
            pass
    return removed


async def disconnect_with_connections(root: str, name: str, *, on_revoke=None, on_cleanup=None) -> dict:
    """Acknowledge local revocation separately from file/transport cleanup and remote effects."""
    from src.tools.connector_access import cleanup_revocation
    revocations = []
    def disabled(value):
        revocations.append(value)
        if on_revoke is not None:
            on_revoke(value)
    removed, credential_cleanup = False, True
    try:
        removed = disconnect(root, name, on_revoke=disabled)
    except OSError:
        credential_cleanup = False
    cleanup = {"connections_disabled": 0, "closed_connections": 0, "cleanup_status": "unconfirmed"}
    try:
        cleanup = await cleanup_revocation(revocations[0])
    finally:
        if on_cleanup is not None:
            on_cleanup(cleanup["cleanup_status"])
    return {"removed": removed, "credential_cleanup_confirmed": credential_cleanup,
            "calls_blocked": True, "revocation_scope": "current_process", **cleanup,
            "remote_requests_verified": False, "provider_authorization_revoked": False}
