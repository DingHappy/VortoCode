"""MCP 工具接入（UI 无关）：把已连接的 ToolManager 的 MCP server 工具包成主 agent 的 Tool。

TUI 早有 `/mcp`（src/tui/app.py），但只在 TUI；这里抽成共享，让 CLI/Web 也接同一套
`config/mcp.yaml`。命名 `mcp__<server>__<tool>` 防冲突；外部工具一律 build 门控
（read_only=False，人在关口）。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, List, Optional, Tuple
from urllib.parse import parse_qsl, unquote_plus, urlsplit
from src.utils.async_ops import timeout_scope
from src.utils.exc_utils import add_note


MCP_CLEANUP_TIMEOUT = 5
MCP_CLEANUP_UNCONFIRMED = 'MCP cleanup unconfirmed'


def wrap_mcp_manager(manager: Any, read_only_servers: frozenset = frozenset()) -> List:
    """把已初始化的 ToolManager 的 MCP 工具包成 main_agent.Tool 列表。

    read_only_servers：只读连接器。只收标了 `readOnlyHint: true` 的工具（没标的一律不给——
    服务端的只读模式之外再筛一道，令牌权限给大了也用不上写工具），并按只读工具登记。
    """
    from src.agents.tool import Tool
    wrapped: List = []
    for mt in manager.list_tools():
        orig = mt.name
        server = getattr(mt, "server_name", "") or "mcp"
        read_only = server in read_only_servers
        if read_only and (getattr(mt, "metadata", {}) or {}).get("annotations", {}).get("readOnlyHint") is not True:
            continue
        from copy import deepcopy
        schema = {"type": "object", **deepcopy(getattr(mt, "input_schema", None) or {})}
        props = schema.get("properties", {}) or {}
        targs = {k: str(v.get("description") or v.get("type") or "") for k, v in props.items()}

        async def handler(a: dict, _orig=orig) -> str:
            res = await manager.execute_tool(_orig, a)
            if getattr(res, "success", True):
                from src.agents.mcp_media import render_result
                return render_result(getattr(res, "output", res), getattr(res, "structured_content", None))
            return {"ok": False, "error": f"MCP 工具出错: {getattr(res, 'error', res)}"}

        # untrusted_source=True：MCP server 返回的是**外部不可信内容**，摄入即给本回合打污点，
        # 之后同回合的对外动作会被提升确认等级（D0 防提示注入外发）。
        public_name = orig if getattr(mt, "metadata", {}).get("mcp_tool_name") else f"mcp__{server}__{orig}"
        wrapped.append(Tool(public_name, f"[MCP:{server}] {mt.description}",
                            targs, handler, read_only=read_only, untrusted_source=True,
                            external_content=True, input_schema=schema))
    return wrapped


_CREDENTIAL_QUERY_KEYS = {
    "apikey", "key", "token", "auth", "authorization", "bearer", "jwt",
    "accesstoken", "authtoken", "sessiontoken", "sessionid", "accesskey",
    "secret", "secretkey", "privatekey", "clientsecret", "clientid",
    "password", "passwd", "credential", "signature", "sig", "code", "ticket",
    "xamzcredential", "xamzsignature", "xamzsecuritytoken",
}
_CREDENTIAL_QUERY_SUFFIXES = (
    "token", "secret", "password", "passwd", "credential", "signature",
)


def _credential_free_http_url(value: object) -> bool:
    url = str(value or "").strip()
    if not url:
        return False
    try:
        parsed = urlsplit(url)
        hostname = parsed.hostname
        query = parse_qsl(parsed.query, keep_blank_values=True, max_num_fields=64)
    except (TypeError, ValueError):
        return False
    if parsed.scheme.lower() not in {"http", "https"} or not hostname:
        return False
    # Fragments are never sent to the HTTP server, so they have no legitimate
    # place in an MCP endpoint definition and can otherwise conceal credentials
    # from query-specific checks.
    if parsed.fragment:
        return False
    if parsed.username is not None or parsed.password is not None or "@" in parsed.netloc:
        return False
    from src.memory.write_policy import redact_secret_like
    _redacted, url_reasons = redact_secret_like(url)
    if url_reasons:
        return False
    for key, item_value in query:
        # Decode a second time as a fail-closed guard for frameworks/proxies
        # that normalize percent-encoded query names more than once.
        for _ in range(2):
            decoded = unquote_plus(key)
            if decoded == key:
                break
            key = decoded
        normalized = "".join(ch for ch in key.lower() if ch.isalnum())
        if normalized in _CREDENTIAL_QUERY_KEYS or normalized.endswith(_CREDENTIAL_QUERY_SUFFIXES):
            return False
        _safe_value, value_reasons = redact_secret_like(item_value)
        if value_reasons:
            return False
    return True


def _credential_free_http_servers(config: dict) -> list[dict]:
    """Return explicitly credential-free HTTP MCP definitions.

    Stdio servers execute repository-controlled commands on the host, while
    configured HTTP headers are credentials.  Neither is safe in an external
    content session.  ``credentialed: false`` is an explicit operator signal;
    omission fails closed.
    """
    servers = config.get("servers") if isinstance(config, dict) else []
    if not isinstance(servers, list):
        return []
    allowed = []
    for server in servers:
        if not isinstance(server, dict) or not server.get("enabled", True):
            continue
        transport = str(server.get("transport") or "stdio").strip().lower()
        if transport != "http" or server.get("credentialed") is not False:
            continue
        if server.get("headers") or server.get("credential"):
            continue
        if not _credential_free_http_url(server.get("url")):
            continue
        allowed.append(dict(server))
    return allowed


_CONNECTOR_NAME = r"[a-z][a-z0-9_-]{0,31}"
_MAX_SECRET_BYTES = 4096


def connector_secret_path(name: str) -> Path:
    return Path.home() / ".vortocode" / "connectors" / f"{name}.token"


def load_connector_secret(name: str) -> str:
    """读连接器凭据：只认 `~/.vortocode/connectors/<name>.token`，且必须是只有本人可读的普通文件。

    凭据**只在这里**进内存、只进 HTTP 头：不进配置、不进系统提示、不进任何工具结果。
    权限放宽了（组/其他人可读）就拒绝——别把一个谁都能读的令牌当成主人的授权。"""
    import re
    if not re.fullmatch(_CONNECTOR_NAME, str(name or "")):
        raise ValueError("连接器名称不合法")
    return _read_connector_secret(connector_secret_path(name))


def _read_connector_secret(path: Path) -> str:
    import os
    import stat
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077 or info.st_uid != os.getuid():
        raise PermissionError(f"连接器凭据 {path} 必须是本人所有、权限 600 的普通文件")
    if info.st_size > _MAX_SECRET_BYTES:
        raise ValueError("连接器凭据文件过大")
    secret = path.read_text(encoding="utf-8").strip()
    if not secret or any(ch.isspace() for ch in secret):
        raise ValueError("连接器凭据为空或格式不对")
    return secret


def _connector_identity(path: Path) -> tuple:
    import hashlib
    def stamp():
        value = path.lstat()
        return value.st_dev, value.st_ino, value.st_mtime_ns, value.st_ctime_ns, value.st_size
    before = stamp()
    secret = _read_connector_secret(path)
    after = stamp()
    if before != after:
        raise PermissionError("连接器凭据在读取期间发生变化")
    return (*after, hashlib.sha256(secret.encode("utf-8")).digest())


def connector_locally_revoked(name: str) -> bool:
    from src.tools.connector_access import connector_identity_revoked
    try:
        return connector_identity_revoked(name, _connector_identity(connector_secret_path(name)))
    except (OSError, ValueError):
        return False


def revoke_connector(name: str):
    from src.tools.connector_access import revoke_connector_access
    try:
        identity = _connector_identity(connector_secret_path(name))
    except (OSError, ValueError):
        identity = None
    return revoke_connector_access(name, identity)


def _credentialed_connectors(config: dict) -> list[dict]:
    """带凭据的 HTTP 连接器（如 GitHub）。**只在通用范围**（常驻助理：没有文件/命令/Git）可用。

    配置里只写凭据**名字**（`credential: github`），令牌原文在 `~/.vortocode/connectors/` 下；
    配置自带 headers 一律拒绝（防止把令牌直接写进仓库文件）。`read_only: true` 时附上
    GitHub 的只读头，并在客户端只收 readOnlyHint 工具。缺凭据、权限不对 → 跳过这一个。"""
    import logging
    servers = config.get("servers") if isinstance(config, dict) else []
    out = []
    for server in servers if isinstance(servers, list) else []:
        if not isinstance(server, dict) or not server.get("enabled", True) or not server.get("credential"):
            continue
        url = str(server.get("url") or "")
        if (str(server.get("transport") or "").lower() != "http" or server.get("headers")
                or not url.startswith("https://") or not _credential_free_http_url(url)):
            continue
        try:
            secret = load_connector_secret(str(server["credential"]))
        except (OSError, ValueError) as error:
            logging.getLogger(__name__).warning("连接器 %s 未启用：%s", server.get("name"), error)
            continue
        headers = {"Authorization": f"Bearer {secret}"}
        if server.get("read_only", True):
            headers["X-MCP-Readonly"] = "true"
        entry = {key: value for key, value in server.items() if key not in ("credential",)}
        entry.update(headers=headers, credential_ref=str(server["credential"]), read_only=bool(server.get("read_only", True)),
                     protocol=server.get("protocol", "streamable_http"),
                     # 托管的 MCP 服务（GitHub 等）目前走 2025-06-18 的握手；配置里可单独指定
                     protocol_version=server.get("protocol_version", "2025-06-18"))
        out.append(entry)
    return out


async def connect_mcp(repo_root, *, capability_profile: str | None = None,
                      confirm: Any = None, allow_connectors: bool = False) -> Tuple[Optional[Any], List]:
    """据 `repo_root/config/mcp.yaml` 连 MCP 服务器，返回 (manager, wrapped_tools)。

    无配置文件 → (None, [])，不报错（多数仓库没 MCP）。连接异常上抛由调用方兜（打印/忽略）。
    用完务必 `await manager.shutdown()`（外部 server 多为子进程，不关会残留）。

    confirm: `action: ask` 权限规则的确认门（async (msg)->bool）。**不传则 ASK 规则一律拒绝**
      （fail-closed，绝不降级成放行）——调用方有真人守着就该传自己的确认门进来。
    allow_connectors: 带凭据的连接器（GitHub 等）只在**没有文件/命令/Git 的会话**里接，
      由调用方按范围决定（常驻助理的通用范围才传 True），默认不接。
    """
    from src.agents.capabilities import EXTERNAL_PROFILE, UNATTENDED_PROFILE, normalize_profile

    profile = normalize_profile(capability_profile)
    if profile not in {EXTERNAL_PROFILE, UNATTENDED_PROFILE}:
        raise PermissionError(
            f"MCP 是外部内容能力，当前 profile={profile}；请新建 external 会话"
        )
    cfg = Path(repo_root) / "config" / "mcp.yaml"
    if not cfg.is_file():
        return None, []
    if cfg.stat().st_size > 256 * 1024 or not cfg.resolve().is_relative_to(Path(repo_root).resolve()):
        raise ValueError("MCP configuration exceeds workspace or size boundary")
    from src.tools.manager import ToolManager
    mgr = ToolManager(str(cfg), confirm=confirm)
    safe_servers = _credential_free_http_servers(mgr.config)
    connectors = _credentialed_connectors(mgr.config) if allow_connectors and profile == EXTERNAL_PROFILE else []
    safe_servers = safe_servers + connectors
    read_only_servers = frozenset(str(item.get("name")) for item in connectors if item.get("read_only"))
    if not safe_servers:
        return None, []
    if len(safe_servers) > 16:
        raise ValueError("MCP server budget exceeded (16)")
    import re
    if any(not isinstance(server.get("name"), str)
           or not re.fullmatch(r"[A-Za-z0-9_-]{1,56}", server["name"]) or "__" in server["name"]
           for server in safe_servers):
        raise ValueError("Unsupported MCP server identity")
    if len({str(server.get("name")) for server in safe_servers}) != len(safe_servers):
        raise ValueError("Duplicate MCP server identity")
    mgr.config = {**mgr.config, "servers": safe_servers}
    if read_only_servers:
        mgr.set_read_only_servers(read_only_servers)
    try:
        import hashlib
        from src.tools.connector_access import ConnectorAccess, bind_connector_access
        accesses = {}
        for entry in connectors:
            credential = entry["credential_ref"]
            path = connector_secret_path(credential)
            access = ConnectorAccess(credential, lambda path=path: _connector_identity(path))
            expected = hashlib.sha256(entry["headers"]["Authorization"][7:].encode("utf-8")).digest()
            if not access.matches_digest(expected):
                access.revoke()
                raise PermissionError("连接器凭据在建立连接前发生变化")
            accesses[str(entry.get("name"))] = access
        bind_connector_access(mgr, accesses)
        if not await mgr.initialize():
            raise RuntimeError("MCP manager initialization failed")
        return mgr, wrap_mcp_manager(mgr, read_only_servers)
    except BaseException as error:
        import asyncio
        try:
            async with timeout_scope(MCP_CLEANUP_TIMEOUT):
                await mgr.shutdown()
        except asyncio.CancelledError as cancelled:
            add_note(cancelled, MCP_CLEANUP_UNCONFIRMED)
            raise
        except Exception:
            # Preserve the initialization/discovery failure; cleanup cannot
            # turn it into success or hide its cause. No remote-stop claim.
            add_note(error, MCP_CLEANUP_UNCONFIRMED)
        raise
