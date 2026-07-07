"""Reusable runtime verification profiles for TUI `/verify`.

Profiles are intentionally small: they resolve to a single shell command, then
the normal command confirmation and danger guard handle execution.
"""
from __future__ import annotations

from fnmatch import fnmatch
from pathlib import Path


def _builtin_profiles(repo_root: str) -> dict[str, dict]:
    root = Path(repo_root)
    profiles: dict[str, dict] = {}
    if (root / "main.py").is_file():
        profiles["self-analyze"] = {
            "cmd": "python main.py self-analyze",
            "description": "run VortoCode deterministic repository scan",
            "source": "builtin",
        }
    if (root / "tests" / "unit").is_dir():
        profiles["unit"] = {
            "cmd": "pytest -q tests/unit",
            "description": "run unit tests",
            "source": "builtin",
        }
    if (root / "tests" / "unit" / "test_tui.py").is_file():
        profiles["tui"] = {
            "cmd": "pytest -q tests/unit/test_tui.py",
            "description": "run TUI unit tests",
            "source": "builtin",
        }
    if (root / "tests" / "unit" / "test_git_workflow.py").is_file():
        profiles["git-workflow"] = {
            "cmd": "pytest -q tests/unit/test_git_workflow.py",
            "description": "run git workflow helper tests",
            "source": "builtin",
        }
    return profiles


def _normalize_profile(name: str, value) -> dict | None:
    if isinstance(value, str):
        cmd = value.strip()
        desc = ""
        patterns: list[str] = []
    elif isinstance(value, dict):
        cmd = str(value.get("cmd") or value.get("command") or "").strip()
        desc = str(value.get("description") or value.get("desc") or "").strip()
        raw_patterns = value.get("paths") or value.get("match") or value.get("matches") or []
        if isinstance(raw_patterns, str):
            patterns = [raw_patterns]
        elif isinstance(raw_patterns, list):
            patterns = [str(p).strip() for p in raw_patterns if str(p).strip()]
        else:
            patterns = []
    else:
        return None
    if not name or not cmd:
        return None
    return {"cmd": cmd, "description": desc, "paths": patterns, "source": "project"}


def load_verify_profiles(repo_root: str) -> dict:
    """Load builtin profiles plus `.vortocode/verify.yaml` profiles.

    Accepted YAML forms:

    profiles:
      smoke:
        cmd: python main.py self-analyze

    or:

    smoke: python main.py self-analyze
    """
    profiles = _builtin_profiles(repo_root)
    path = Path(repo_root) / ".vortocode" / "verify.yaml"
    if not path.is_file():
        return {"ok": True, "profiles": profiles, "path": str(path)}
    try:
        import yaml
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"verify.yaml 解析失败: {e}", "profiles": profiles, "path": str(path)}
    if not isinstance(data, dict):
        return {"ok": False, "error": "verify.yaml 结构不对，应为映射", "profiles": profiles, "path": str(path)}
    raw_profiles = data.get("profiles", data)
    if not isinstance(raw_profiles, dict):
        return {"ok": False, "error": "verify.yaml profiles 应为映射", "profiles": profiles, "path": str(path)}
    for raw_name, raw_value in raw_profiles.items():
        name = str(raw_name or "").strip()
        profile = _normalize_profile(name, raw_value)
        if profile:
            profiles[name] = profile
    return {"ok": True, "profiles": profiles, "path": str(path)}


def format_verify_profiles(result: dict) -> str:
    if not result.get("ok"):
        return str(result.get("error") or "verify profile 加载失败")
    profiles = result.get("profiles") or {}
    if not profiles:
        return "没有可用 verify profile。可在 .vortocode/verify.yaml 中添加 profiles。"
    lines = ["Verify profiles:"]
    for name in sorted(profiles):
        item = profiles[name] or {}
        source = item.get("source") or "project"
        desc = f" · {item.get('description')}" if item.get("description") else ""
        lines.append(f"- {name} [{source}]: {item.get('cmd')}{desc}")
    lines.append("")
    lines.append("用法: /verify <profile> 或 /verify profile <profile>")
    return "\n".join(lines)


def resolve_verify_profile(repo_root: str, name: str) -> dict:
    loaded = load_verify_profiles(repo_root)
    if not loaded.get("ok"):
        return loaded
    profiles = loaded.get("profiles") or {}
    key = str(name or "").strip()
    if key in profiles:
        return {"ok": True, "name": key, "profile": profiles[key], "profiles": profiles}
    lowered = {str(k).lower(): k for k in profiles}
    if key.lower() in lowered:
        actual = lowered[key.lower()]
        return {"ok": True, "name": actual, "profile": profiles[actual], "profiles": profiles}
    return {"ok": False, "error": f"找不到 verify profile {name}", "profiles": profiles}


def _add_recommendation(out: list[dict], profiles: dict, name: str, reason: str) -> None:
    if name not in profiles:
        return
    if any(item.get("name") == name for item in out):
        return
    profile = profiles[name] or {}
    out.append({
        "name": name,
        "cmd": profile.get("cmd") or "",
        "description": profile.get("description") or "",
        "source": profile.get("source") or "project",
        "reason": reason,
    })


def _matches_any(path: str, patterns: list[str]) -> str:
    p = str(path or "").strip().strip("/")
    for raw in patterns:
        pat = str(raw or "").strip().strip("/")
        if not pat:
            continue
        if fnmatch(p, pat) or fnmatch(p, pat.rstrip("/") + "/**"):
            return pat
        if not any(ch in pat for ch in "*?[]") and (p == pat or p.startswith(pat.rstrip("/") + "/")):
            return pat
    return ""


def recommend_verify_profiles(repo_root: str, changed_paths: list[str] | None = None,
                              max_profiles: int = 5) -> dict:
    """Recommend verify profiles for a changed path set.

    Project profiles can opt into recommendations with `paths` / `match`
    patterns in `.vortocode/verify.yaml`. Builtin profiles use conservative
    repository-specific heuristics.
    """
    loaded = load_verify_profiles(repo_root)
    if not loaded.get("ok"):
        return {
            "ok": False,
            "error": loaded.get("error", "verify profile 加载失败"),
            "recommendations": [],
            "profiles": loaded.get("profiles") or {},
        }
    profiles = loaded.get("profiles") or {}
    paths = [str(p).strip().strip("/") for p in (changed_paths or []) if str(p).strip()]
    recs: list[dict] = []
    for name, profile in profiles.items():
        patterns = list((profile or {}).get("paths") or [])
        if not patterns:
            continue
        for p in paths:
            matched = _matches_any(p, patterns)
            if matched:
                _add_recommendation(recs, profiles, name, f"匹配 {matched}")
                break
    if any(p.startswith("src/tui/") or p == "tests/unit/test_tui.py" for p in paths):
        _add_recommendation(recs, profiles, "tui", "TUI 相关文件有改动")
    if any(p in {"src/agents/git_workflow.py", "src/agents/verify_profiles.py",
                 "tests/unit/test_git_workflow.py"} for p in paths):
        _add_recommendation(recs, profiles, "git-workflow", "Git workflow / verify profile 相关文件有改动")
    if any(Path(p).suffix == ".py" or p.startswith("tests/") for p in paths):
        _add_recommendation(recs, profiles, "unit", "Python 源码或测试有改动")
    if paths:
        _add_recommendation(recs, profiles, "self-analyze", "提交前做一次确定性仓库扫描")
    return {"ok": True, "recommendations": recs[:max_profiles], "profiles": profiles}
