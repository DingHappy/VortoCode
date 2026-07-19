"""Bounded, non-executing inventory of project extensions used by Desktop.

The inspect surface intentionally reports structure and effective status only.
It never returns rule/skill bodies, MCP endpoints, commands, arguments, headers,
or environment variables.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import yaml

_MAX_ITEMS = 100
_MAX_FILE_BYTES = 256 * 1024
_RULE_CANDIDATES = ("AGENTS.md", "CLAUDE.md", "VORTO.md", ".vortocode/AGENTS.md")
_ENABLED_STATUSES = {"active", "available"}
_ATTENTION_STATUSES = {"blocked", "needs_trust", "error"}


def _safe_text(value: object, limit: int = 240) -> str:
    from src.memory.write_policy import redact_secret_like

    redacted, _reasons = redact_secret_like(str(value or "").replace("\x00", ""))
    return redacted.strip()[:limit]


def _stable_id(kind: str, source: str, name: str) -> str:
    value = f"{kind}\0{source}\0{name}".encode("utf-8", errors="replace")
    return hashlib.sha256(value).hexdigest()[:16]


def _item(kind: str, name: object, source: str, status: str, **extra: Any) -> dict[str, Any]:
    safe_name = _safe_text(name, 80) or kind
    return {
        "id": _stable_id(kind, source, safe_name),
        "kind": kind,
        "name": safe_name,
        "source": source,
        "source_scope": "project",
        "status": status,
        "enabled": status in _ENABLED_STATUSES,
        "trusted": extra.pop("trusted", None),
        "description": _safe_text(extra.pop("description", ""), 300),
        "capabilities": [_safe_text(value, 60) for value in extra.pop("capabilities", [])[:20]],
        "detail": _safe_text(extra.pop("detail", ""), 300),
        "issue": _safe_text(extra.pop("issue", ""), 300),
        **extra,
    }


def _within(root: Path, path: Path) -> bool:
    try:
        path.resolve().relative_to(root)
        return True
    except (OSError, RuntimeError, ValueError):
        return False


def _rules(root: Path) -> tuple[list[dict[str, Any]], list[str]]:
    items: list[dict[str, Any]] = []
    issues: list[str] = []
    selected = ""
    candidates: list[tuple[str, int]] = []
    for source in _RULE_CANDIDATES:
        path = root / source
        try:
            if not path.is_file():
                continue
            if not _within(root, path):
                issues.append(f"规则 {source} 指向工作区外，已忽略")
                items.append(_item("rules", path.name, source, "blocked", issue="符号链接指向工作区外"))
                continue
            size = path.stat().st_size
            if size <= 0:
                continue
            if not path.read_text(encoding="utf-8", errors="ignore").strip():
                continue
            candidates.append((source, size))
            if not selected:
                selected = source
        except OSError as exc:
            issues.append(f"规则 {source} 无法读取")
            items.append(_item("rules", path.name, source, "error", issue=str(exc)))

    for source, size in candidates:
        active = source == selected
        items.append(_item(
            "rules",
            Path(source).name,
            source,
            "active" if active else "available",
            description="项目级 Agent 指令",
            enabled=active,
            detail=(f"当前生效 · {size} bytes" if active
                    else f"被更高优先级规则覆盖 · {size} bytes"),
        ))
    return items, issues


def _skills(root: Path) -> tuple[list[dict[str, Any]], list[str]]:
    from src.skills.parser import SkillParser

    items: list[dict[str, Any]] = []
    issues: list[str] = []
    parsed: list[dict[str, Any]] = []
    for directory in (root / "skills", root / ".vortocode" / "skills"):
        if not directory.exists():
            continue
        if not _within(root, directory):
            issues.append(f"技能目录 {directory.name} 指向工作区外，已忽略")
            continue
        try:
            paths = sorted(directory.rglob("SKILL.md"), key=lambda value: str(value))
        except OSError:
            issues.append(f"技能目录 {directory.name} 无法扫描")
            continue
        for path in paths:
            if len(parsed) >= _MAX_ITEMS:
                issues.append(f"技能超过 {_MAX_ITEMS} 项，仅显示前 {_MAX_ITEMS} 项")
                break
            try:
                source = path.resolve().relative_to(root).as_posix()
            except (OSError, RuntimeError, ValueError):
                issues.append("发现指向工作区外的 SKILL.md，已忽略")
                continue
            try:
                if path.stat().st_size > _MAX_FILE_BYTES:
                    items.append(_item("skill", path.parent.name, source, "blocked", issue="SKILL.md 超过 256 KiB"))
                    continue
                metadata, _instructions, _arguments, _hooks = SkillParser.parse_file(path)
                parsed.append({
                    "name": metadata.name or path.parent.name,
                    "source": source,
                    "description": metadata.description,
                    "capabilities": list(metadata.capabilities or []),
                    "user_invocable": bool(metadata.user_invocable),
                })
            except Exception as exc:  # noqa: BLE001 - invalid project extension is reported, not fatal
                items.append(_item("skill", path.parent.name, source, "error", issue=str(exc)))

    # Main-agent loading is last-definition-wins for duplicate skill names.
    winners: dict[str, str] = {}
    for skill in parsed:
        winners[_safe_text(skill["name"], 80)] = str(skill["source"])
    for skill in parsed:
        name = _safe_text(skill["name"], 80)
        active = winners.get(name) == skill["source"]
        items.append(_item(
            "skill",
            name,
            str(skill["source"]),
            "active" if active else "available",
            description=skill["description"],
            capabilities=skill["capabilities"],
            enabled=active,
            detail=("可由用户或 Agent 调用" if skill["user_invocable"] else "仅允许 Agent 自动调用")
            if active else "被较高优先级的同名技能覆盖",
        ))
    return items, issues


def _hooks(root: Path) -> tuple[list[dict[str, Any]], list[str]]:
    from src.hooks.trust import hook_config_status

    status = hook_config_status(str(root))
    if not status["configured"]:
        return [], []
    issues: list[str] = []
    if status["error"]:
        issues.append("Hook 配置无法加载")
        return [
            _item("hook", "Hook 配置", status["config_path"], "error", issue=status["error"], trusted=status["trusted"])
        ], issues
    item_status = "active" if status["active"] else "needs_trust"
    items = [
        _item(
            "hook",
            hook.get("name") or "Hook",
            status["config_path"],
            item_status,
            trusted=status["trusted"],
            description=f"{hook.get('type') or 'command'} · {' / '.join(hook.get('events') or []) or '未声明事件'}",
            capabilities=list(hook.get("capabilities") or []),
            detail=f"超时 {hook.get('timeout') or 5}s",
            issue="需要用户明确检查并信任项目 Hook" if item_status == "needs_trust" else "",
        )
        for hook in status["hooks"][:_MAX_ITEMS]
    ]
    if item_status == "needs_trust":
        issues.append("项目 Hook 尚未信任")
    return items, issues


def _mcp(root: Path) -> tuple[list[dict[str, Any]], list[str]]:
    config = root / "config" / "mcp.yaml"
    if not config.is_file():
        return [], []
    source = "config/mcp.yaml"
    try:
        if not _within(root, config):
            return [_item("mcp", "MCP 配置", source, "blocked", issue="配置指向工作区外")], ["MCP 配置指向工作区外"]
        if config.stat().st_size > _MAX_FILE_BYTES:
            return [_item("mcp", "MCP 配置", source, "blocked", issue="配置超过 256 KiB")], ["MCP 配置过大"]
        value = yaml.safe_load(config.read_text(encoding="utf-8")) or {}
        servers = value.get("servers") if isinstance(value, dict) else None
        if not isinstance(servers, list):
            raise ValueError("servers 必须是列表")
    except (OSError, UnicodeError, ValueError, TypeError, yaml.YAMLError) as exc:
        return [_item("mcp", "MCP 配置", source, "error", issue=str(exc))], ["MCP 配置无法加载"]

    from src.agents.mcp_tools import _credential_free_http_url

    items: list[dict[str, Any]] = []
    issues: list[str] = []
    for index, server in enumerate(servers[:_MAX_ITEMS]):
        if not isinstance(server, dict):
            continue
        name = server.get("name") or f"mcp-{index + 1}"
        enabled = bool(server.get("enabled", True))
        transport = _safe_text(server.get("transport") or "stdio", 20).lower()
        status = "disabled"
        detail = f"{transport} 传输"
        issue = ""
        if enabled and transport == "stdio":
            status = "blocked"
            issue = "安全配置禁止执行仓库声明的 stdio 命令"
        elif enabled and transport != "http":
            status = "blocked"
            issue = "不支持的 MCP 传输类型"
        elif enabled and server.get("credentialed") is not False:
            status = "blocked"
            issue = "外部会话要求显式 credentialed: false"
        elif enabled and server.get("headers"):
            status = "blocked"
            issue = "外部会话不加载带请求头的 MCP"
        elif enabled and not _credential_free_http_url(server.get("url")):
            status = "blocked"
            issue = "MCP 地址无效或可能包含凭据"
        elif enabled:
            status = "available"
            detail = "credential-free HTTP · external/unattended 会话可连接"
        if status == "blocked":
            issues.append(f"MCP { _safe_text(name, 80) } 已阻止")
        items.append(_item("mcp", name, source, status, description=detail, detail="未执行连接探测", issue=issue))
    if len(servers) > _MAX_ITEMS:
        issues.append(f"MCP 超过 {_MAX_ITEMS} 项，仅显示前 {_MAX_ITEMS} 项")
    return items, issues


def inspect_extensions(repo_root: str) -> dict[str, Any]:
    """Return the effective project extension inventory without executing it."""
    try:
        root = Path(repo_root).expanduser().resolve(strict=True)
    except (OSError, RuntimeError):
        return {
            "version": 1,
            "summary": {"total": 0, "active": 0, "attention": 1},
            "items": [],
            "issues": ["工作区目录不存在或无法访问"],
        }

    items: list[dict[str, Any]] = []
    issues: list[str] = []
    for collector in (_rules, _skills, _hooks, _mcp):
        discovered, discovered_issues = collector(root)
        items.extend(discovered)
        issues.extend(discovered_issues)
    items.sort(key=lambda item: (str(item["kind"]), str(item["name"]).lower(), str(item["source"])))
    return {
        "version": 1,
        "summary": {
            "total": len(items),
            "active": sum(item["status"] == "active" for item in items),
            "attention": sum(item["status"] in _ATTENTION_STATUSES for item in items),
        },
        "items": items,
        "issues": list(dict.fromkeys(_safe_text(issue, 300) for issue in issues if issue))[:50],
    }
