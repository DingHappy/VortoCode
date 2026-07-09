""".vortocode/permissions.yaml —— 细粒度工具权限。

deny 规则在工具执行前硬拦；allow 规则只用于跳过人工确认，不绕过 deny、plan/build 门或危险命令黑名单。
补在 plan/build 门 + is_dangerous 黑名单之上的"按工具/按参数"精细控制（对标 CC 的 allow/deny）。
规则写在 `.vortocode/permissions.yaml`：

    deny:
      - run_command: "rm *"        # 工具 + 主参数 glob 命中 → 拦
      - web_fetch                  # 整个工具禁用（字符串无冒号）
      - edit_file: "*/secrets/*"   # 改 secrets 路径 → 拦
    allow:
      - run_command: "pytest *"    # 命中 → build 下免确认；危险命令仍拒绝
      - write_file: "docs/*.md"
    profile: dev
    profiles:
      dev:
        allow:
          - "run_command: ruff *"
        deny:
          - edit_file: "*/secrets/*"

默认（无文件/空）= 不拦任何、不免确认（行为完全不变）。glob 用 fnmatch。
"""

from __future__ import annotations

from fnmatch import fnmatch
from pathlib import Path
from typing import List, Optional, Tuple

# 各工具的"主参数"键（按它取值做 glob 匹配）；不在表里的工具只支持整工具 deny
_PRIMARY_ARG = {
    "run_command": ("command", "cmd"),
    "read_file": ("path",),
    "edit_file": ("path",),
    "write_file": ("path",),
    "web_fetch": ("url", "href"),
    "open_pr": ("branch",),
    "grep": ("pattern",),
    "dev_isolated": ("description", "task"),
    "dev_parallel": ("tasks",),
    "dev_auto": ("task", "goal"),
}


def _primary_value(tool: str, args: dict) -> str:
    for k in _PRIMARY_ARG.get(tool, ()):
        v = args.get(k)
        if v:
            return str(v)
    return ""


def primary_arg_keys(tool: str) -> tuple[str, ...]:
    """Return the argument keys used for pattern matching for a tool."""
    return _PRIMARY_ARG.get(tool, ())


class Permissions:
    """项目权限规则。deny 硬拦；allow 只表示命中时可免人工确认。"""

    def __init__(
        self,
        deny: Optional[List[Tuple[str, Optional[str]]]] = None,
        allow: Optional[List[Tuple[str, Optional[str]]]] = None,
        *,
        profile: Optional[str] = None,
        profile_found: bool = True,
    ) -> None:
        self._deny = list(deny or [])
        self._allow = list(allow or [])
        self._profile = str(profile).strip() if profile else None
        self._profile_found = bool(profile_found)

    @property
    def rules(self) -> List[Tuple[str, Optional[str]]]:
        """Backward-compatible alias for deny rules."""
        return list(self._deny)

    @property
    def allow_rules(self) -> List[Tuple[str, Optional[str]]]:
        return list(self._allow)

    @property
    def profile(self) -> Optional[str]:
        return self._profile

    @property
    def profile_found(self) -> bool:
        return self._profile_found

    def rules_for(self, tool: str) -> List[Tuple[str, Optional[str]]]:
        """Return deny rules that target a specific tool."""
        return [(rtool, glob) for rtool, glob in self._deny if rtool == tool]

    def allow_rules_for(self, tool: str) -> List[Tuple[str, Optional[str]]]:
        """Return allow rules that target a specific tool."""
        return [(rtool, glob) for rtool, glob in self._allow if rtool == tool]

    def denied(self, tool: str, args: dict) -> Optional[str]:
        """命中 deny → 返回原因串（调用方据此拦下并回灌模型）；否则 None。"""
        val = None
        for rtool, glob in self._deny:
            if rtool != tool:
                continue
            if glob is None:                       # 整工具禁用
                return f"工具 {tool} 被 .vortocode/permissions.yaml 禁用"
            if val is None:
                val = _primary_value(tool, args)
            if val and fnmatch(val, glob):
                return f"{tool}（{val[:60]}）命中 deny 规则「{glob}」（.vortocode/permissions.yaml）"
        return None

    def allowed(self, tool: str, args: dict) -> Optional[str]:
        """命中 allow → 返回原因串；否则 None。allow 只用于免确认，不代表可执行。"""
        val = None
        for rtool, glob in self._allow:
            if rtool != tool:
                continue
            if glob is None:
                return f"工具 {tool} 命中 allow 规则（.vortocode/permissions.yaml）"
            if val is None:
                val = _primary_value(tool, args)
            if val and fnmatch(val, glob):
                return f"{tool}（{val[:60]}）命中 allow 规则「{glob}」（.vortocode/permissions.yaml）"
        return None


def _parse_rule(item) -> Optional[Tuple[str, Optional[str]]]:
    """把一条 yaml 规则项规整成 (tool, glob_or_None)。支持 'tool' / 'tool: glob' / {tool: glob}。"""
    if isinstance(item, dict):
        for tool, glob in item.items():           # 取第一对
            t = str(tool).strip()
            return (t, str(glob).strip()) if (t and glob is not None and str(glob).strip()) else (t, None)
        return None
    if isinstance(item, str):
        s = item.strip()
        if not s:
            return None
        if ":" in s:                              # "tool: glob"
            tool, glob = s.split(":", 1)
            tool, glob = tool.strip(), glob.strip().strip('"').strip("'")
            return (tool, glob) if glob else (tool, None)
        return (s, None)                          # 整工具
    return None


def _parse_rules(data: dict, key: str) -> List[Tuple[str, Optional[str]]]:
    items = data.get(key) or []
    if isinstance(items, (str, dict)):
        items = [items]
    if not isinstance(items, list):
        return []
    rules: List[Tuple[str, Optional[str]]] = []
    for item in items:
        r = _parse_rule(item)
        if r:
            rules.append(r)
    return rules


def load_permissions(repo_root) -> Permissions:
    """读 `repo_root/.vortocode/permissions.yaml` 的权限规则；缺失/损坏 → 空。"""
    p = Path(repo_root) / ".vortocode" / "permissions.yaml"
    if not p.is_file():
        return Permissions([])
    try:
        import yaml
        data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    except Exception:  # noqa: BLE001 —— 配置坏了不该让 agent 崩，按"无规则"处理
        return Permissions([])
    if not isinstance(data, dict):
        return Permissions([])
    deny = _parse_rules(data, "deny")
    allow = _parse_rules(data, "allow")
    profile = str(data.get("profile") or data.get("active_profile") or "").strip()
    profile_found = True
    profiles = data.get("profiles") or {}
    if profile:
        selected = profiles.get(profile) if isinstance(profiles, dict) else None
        if isinstance(selected, dict):
            deny += _parse_rules(selected, "deny")
            allow += _parse_rules(selected, "allow")
        else:
            profile_found = False
    return Permissions(deny, allow, profile=profile or None, profile_found=profile_found)
