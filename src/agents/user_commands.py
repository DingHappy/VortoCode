"""用户自定义斜杠命令（对标 Claude Code 的 .claude/commands/）。

在 `.vortocode/commands/<名>.md` 放一个 Markdown 文件，正文就是提示模板，用户在 TUI 里
敲 `/<名> [参数]` 即可把模板（替换占位符后）作为一轮输入交给主 agent。

与 SKILL.md 的区别：技能是 **agent 自主**按需调用（渐进披露进系统提示）；自定义命令是
**用户主动**触发的可复用提示。两者互补，这里只管前者。

占位符：
- `$ARGUMENTS`：替换为命令后的全部参数串。
- `$1`/`$2`/…：替换为按空白切分的第 N 个参数（越界→空串）。
- 模板里既无 `$ARGUMENTS` 也无 `$N` 而又给了参数时，把参数追加到末尾（不丢用户输入）。

可选 YAML frontmatter：
- `description:` 用于补全面板的一句话说明；无则取正文首个非空行。
- `mode:` 可写 `plan` / `build`，执行命令前自动切到对应模式。
- `argument-hint:` 或 `args:` 用于提示参数格式。
- `model:` 仅解析和展示，具体模型切换由 UI/调用方决定。
"""

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List

# 命令名：字母/数字/下划线/连字符（和文件名一致，避免和内置命令解析冲突）
_NAME_RE = re.compile(r"^[A-Za-z0-9_-]+$")


@dataclass
class UserCommand:
    """一条用户自定义命令：名字、说明、可选执行元数据、提示模板正文。"""
    name: str
    description: str
    template: str
    mode: str = ""
    argument_hint: str = ""
    model: str = ""
    args_schema: List[dict] = field(default_factory=list)


def _clean_scalar(value: object) -> str:
    return str(value or "").strip().strip("'\"")


def is_valid_command_name(name: str) -> bool:
    """命令文件名是否合法。"""
    return bool(_NAME_RE.match(str(name or "").strip()))


def _normalize_args_schema(value: object) -> list[dict]:
    """解析 frontmatter args/arguments 为轻量 schema，供列表和 preview 展示。

    支持：
    args: "<file> <question>"
    args:
      - name: file
        required: true
        description: 要读取的文件
    """
    if not value:
        return []
    if isinstance(value, str):
        return [{"name": value.strip(), "required": False, "description": ""}] if value.strip() else []
    raw = value
    if isinstance(value, dict):
        raw = value.get("items") or value.get("arguments") or value.get("args") or []
        if not isinstance(raw, list):
            raw = [value]
    if not isinstance(raw, list):
        return []
    out: list[dict] = []
    for item in raw:
        if isinstance(item, str):
            name = item.strip()
            if name:
                out.append({"name": name, "required": False, "description": ""})
            continue
        if not isinstance(item, dict):
            continue
        name = _clean_scalar(item.get("name") or item.get("id") or item.get("key"))
        if not name:
            continue
        required = item.get("required", False)
        required = required if isinstance(required, bool) else str(required).strip().lower() in {"1", "true", "yes", "on"}
        out.append({
            "name": name,
            "required": required,
            "description": _clean_scalar(item.get("description") or item.get("desc")),
            "default": _clean_scalar(item.get("default")),
        })
    return out


def _schema_hint(schema: list[dict]) -> str:
    bits = []
    for item in schema:
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        bits.append(f"<{name}>" if item.get("required") else f"[{name}]")
    return " ".join(bits)


def default_command_template(name: str) -> str:
    """新建自定义命令时写入的安全模板。"""
    name = str(name or "custom").strip() or "custom"
    return (
        "---\n"
        f"description: {name} workflow\n"
        "mode: plan\n"
        "args:\n"
        "  - name: target\n"
        "    required: false\n"
        "    description: 文件、分支、PR 或任务描述\n"
        "---\n"
        "\n"
        "请根据以下目标给出执行计划或检查结果：\n"
        "\n"
        "$ARGUMENTS\n"
    )


def _parse(text: str, name: str) -> UserCommand:
    """把一个命令文件的内容解析成 UserCommand（可选 frontmatter + 正文模板）。"""
    desc = ""
    mode = ""
    argument_hint = ""
    model = ""
    args_schema: list[dict] = []
    body = text
    if text.lstrip().startswith("---"):
        parts = text.split("---", 2)
        if len(parts) == 3:                       # 有完整 frontmatter
            front, body = parts[1], parts[2]
            try:
                import yaml
                meta = yaml.safe_load(front) or {}
            except Exception:  # noqa: BLE001
                meta = {}
            if isinstance(meta, dict):
                desc = _clean_scalar(meta.get("description"))
                raw_mode = _clean_scalar(meta.get("mode")).lower()
                mode = raw_mode if raw_mode in {"plan", "build"} else ""
                args_schema = _normalize_args_schema(meta.get("arguments") or meta.get("args"))
                argument_hint = _clean_scalar(meta.get("argument-hint")) or _schema_hint(args_schema)
                model = _clean_scalar(meta.get("model"))
    body = body.strip()
    if not desc:                                  # 没写 description → 取正文首个非空行
        for line in body.splitlines():
            if line.strip():
                desc = line.strip()[:80]
                break
    return UserCommand(
        name=name,
        description=desc or f"自定义命令 {name}",
        template=body,
        mode=mode,
        argument_hint=argument_hint,
        model=model,
        args_schema=args_schema,
    )


def load_commands(repo_root: str) -> Dict[str, UserCommand]:
    """扫描 `<repo>/.vortocode/commands/*.md`，返回 {名: UserCommand}。

    目录不存在/文件读不动/名字非法都安全跳过，绝不让坏文件影响 TUI。
    """
    out: Dict[str, UserCommand] = {}
    d = Path(repo_root) / ".vortocode" / "commands"
    if not d.is_dir():
        return out
    for f in sorted(d.glob("*.md")):
        name = f.stem.strip()
        if not _NAME_RE.match(name):
            continue
        try:
            text = f.read_text(encoding="utf-8")
        except OSError:
            continue
        if text.strip():
            out[name] = _parse(text, name)
    return out


def expand_command(template: str, args: str) -> str:
    """把模板里的占位符用参数替换；既无占位符又有参数时把参数追加到末尾。"""
    args = (args or "").strip()
    has_all = "$ARGUMENTS" in template
    pos = re.findall(r"\$(\d+)", template)
    parts = args.split()

    def repl_pos(m: "re.Match") -> str:
        i = int(m.group(1))
        return parts[i - 1] if 1 <= i <= len(parts) else ""

    out = template.replace("$ARGUMENTS", args)
    if pos:
        out = re.sub(r"\$(\d+)", repl_pos, out)
    if not has_all and not pos and args:          # 模板没用占位符 → 别丢用户参数
        out = f"{out}\n\n{args}"
    return out
