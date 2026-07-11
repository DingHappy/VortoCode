"""MCP 工具接入（UI 无关）：把已连接的 ToolManager 的 MCP server 工具包成主 agent 的 Tool。

TUI 早有 `/mcp`（src/tui/app.py），但只在 TUI；这里抽成共享，让 CLI/Web 也接同一套
`config/mcp.yaml`。命名 `mcp__<server>__<tool>` 防冲突；外部工具一律 build 门控
（read_only=False，人在关口）。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, List, Optional, Tuple
from urllib.parse import parse_qsl, unquote_plus, urlsplit


def wrap_mcp_manager(manager: Any) -> List:
    """把已初始化的 ToolManager 的 MCP 工具包成 main_agent.Tool 列表。"""
    from src.agents.tool import Tool
    wrapped: List = []
    for mt in manager.list_tools():
        orig = mt.name
        server = getattr(mt, "server_name", "") or "mcp"
        props = (getattr(mt, "input_schema", None) or {}).get("properties", {}) or {}
        targs = {k: str(v.get("description") or v.get("type") or "") for k, v in props.items()}

        async def handler(a: dict, _orig=orig) -> str:
            res = await manager.execute_tool(_orig, a)
            if getattr(res, "success", True):
                return str(getattr(res, "output", res))
            return f"MCP 工具出错: {getattr(res, 'error', res)}"

        # untrusted_source=True：MCP server 返回的是**外部不可信内容**，摄入即给本回合打污点，
        # 之后同回合的对外动作会被提升确认等级（D0 防提示注入外发）。
        wrapped.append(Tool(f"mcp__{server}__{orig}", f"[MCP:{server}] {mt.description}",
                            targs, handler, read_only=False, untrusted_source=True,
                            external_content=True))
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
        if server.get("headers"):
            continue
        if not _credential_free_http_url(server.get("url")):
            continue
        allowed.append(dict(server))
    return allowed


async def connect_mcp(repo_root, *, capability_profile: str | None = None) -> Tuple[Optional[Any], List]:
    """据 `repo_root/config/mcp.yaml` 连 MCP 服务器，返回 (manager, wrapped_tools)。

    无配置文件 → (None, [])，不报错（多数仓库没 MCP）。连接异常上抛由调用方兜（打印/忽略）。
    用完务必 `await manager.shutdown()`（外部 server 多为子进程，不关会残留）。
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
    from src.tools.manager import ToolManager
    mgr = ToolManager(str(cfg))
    safe_servers = _credential_free_http_servers(mgr.config)
    if not safe_servers:
        return None, []
    mgr.config = {**mgr.config, "servers": safe_servers}
    await mgr.initialize()
    return mgr, wrap_mcp_manager(mgr)
