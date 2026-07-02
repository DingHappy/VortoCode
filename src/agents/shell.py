"""通用命令执行——让主 agent 能跑任意 shell 命令（测试/lint/git/构建/安装…）。

VortoCode 此前只有专用工具（read/edit/dev_*），缺一个通用 shell，这是对齐 Claude Code /
opencode 的核心缺口。**高危/外向**：调用方务必门控（build + 人工确认）。本模块只负责
执行 + 基本危险拦截（defense-in-depth，非完整沙箱；真正的关口是"人看着命令点确认"）。

危险拦截（is_dangerous）思路（2026-07 审计 P0#3）：**不再对整条命令做正则**（易被
`rm -rf /etc`、长选项 `--recursive --force`、`+refspec` 强推、`curl|sh`、`${IFS}` 绕过），
而是 归一化 IFS → 按 shell 操作符分段 → shlex 分词 → 按可执行名逐段判定操作数。
仍是**非穷举兜底**：目标是挡住"一眼灾难"，不误伤 `rm -rf build/`、`find . -name … -delete`。
"""
from __future__ import annotations

import re
import shlex
import subprocess

# 可对整条（归一化后）命令直接匹配的灾难图案——与具体操作数无关
_GLOBAL_DANGER = [
    (r"\b(mkfs|fdisk)\b", "磁盘格式化"),
    (r"\bdd\b.+\bof=/dev/", "dd 写裸设备"),
    (r">\s*/dev/sd", "写裸设备"),
    (r":\s*\(\)\s*\{.*:\s*\|\s*:.*\}", "fork bomb"),
    (r"\b(shutdown|reboot|halt|poweroff|init\s+0)\b", "关机/重启"),
    # 下载即执行：curl/wget 管道喂给 shell/解释器（供应链/RCE 高危）
    (r"\b(curl|wget)\b[^|]*\|\s*(sudo\s+)?(sh|bash|zsh|dash|ksh|python[0-9.]*|perl|ruby|node)\b",
     "下载内容直接管道给解释器执行"),
]

# 拆子命令段（| || && ; & 换行）——逐段判可执行名，避免一条串里藏危险命令漏检
_SHELL_SPLIT = re.compile(r"\|\||&&|[|;&\n]")
# env 赋值 / 常见前缀包装，跳过后取真正的可执行名
_ENV_ASSIGN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
_PREFIX_WRAPPERS = {"sudo", "command", "env", "nice", "nohup", "time", "xargs", "stdbuf"}
# 危险到"哪怕相对也别删"的操作数
_CATASTROPHIC_OPERANDS = {"/", "*", ".", "..", "./", "../", "~"}


def _normalize(cmd: str) -> str:
    """归一化常见 IFS 绕过，让 `rm${IFS}-rf${IFS}/` 也能被识别。"""
    return (cmd or "").replace("${IFS}", " ").replace("$IFS", " ").replace("\t", " ")


def _tokens(seg: str) -> list[str]:
    try:
        return shlex.split(seg)
    except ValueError:                       # 引号不配对等 → 退化为空白切分（宁可多查）
        return seg.split()


def _exe_and_args(tokens: list[str]) -> tuple[str, list[str]]:
    """跳过 FOO=bar 赋值与 sudo/env/nice 等包装前缀，返回 (可执行名, 其余参数)。"""
    i = 0
    while i < len(tokens):
        t = tokens[i]
        if _ENV_ASSIGN.match(t) and "=" in t:
            i += 1
            continue
        if t in _PREFIX_WRAPPERS:
            i += 1
            continue
        break
    if i >= len(tokens):
        return "", []
    return tokens[i].rsplit("/", 1)[-1], tokens[i + 1:]      # /bin/rm → rm


def _is_abs_or_home(op: str) -> bool:
    return (op.startswith("/") or op == "~" or op.startswith("~/")
            or op.startswith("$HOME") or op.startswith("/*"))


def _operands(args: list[str]) -> list[str]:
    return [a for a in args if not a.startswith("-")]


def _has_flag(args: list[str], *, short: str = "", longs: tuple[str, ...] = ()) -> bool:
    """短选项按字符命中（-rf 含 r/f，可合并簇），长选项精确匹配。"""
    for a in args:
        if a in longs:
            return True
        if short and a.startswith("-") and not a.startswith("--") and short in a[1:]:
            return True
    return False


def _check_rm(args: list[str]) -> str:
    if _has_flag(args, longs=("--no-preserve-root",)):
        return "rm --no-preserve-root（删根保护被关）"
    recursive = _has_flag(args, short="r", longs=("--recursive",)) or _has_flag(args, short="R")
    force = _has_flag(args, short="f", longs=("--force",))
    if not (recursive and force):
        return ""
    for op in _operands(args):
        if op in _CATASTROPHIC_OPERANDS or _is_abs_or_home(op):
            return "rm -rf 根/家目录/绝对路径/通配"
    return ""


def _check_git(args: list[str]) -> str:
    if "push" not in args:
        return ""
    force = _has_flag(args, short="f", longs=("--force", "--force-with-lease"))
    refspec_force = any(a.startswith("+") for a in args)      # `git push origin +main` 也是强推
    return "git 强推" if (force or refspec_force) else ""


def _check_perm(base: str, args: list[str]) -> str:
    if not _has_flag(args, short="R", longs=("--recursive",)):
        return ""
    for op in _operands(args):
        if _is_abs_or_home(op) or op in _CATASTROPHIC_OPERANDS:
            return f"{base} -R 作用到根/家目录/绝对路径"
    return ""


def _check_find(args: list[str]) -> str:
    deletes = "-delete" in args or ("-exec" in args and "rm" in args)
    if not deletes:
        return ""
    # find 的路径实参是开头连续的非 - token；其后是表达式
    roots = []
    for a in args:
        if a.startswith("-"):
            break
        roots.append(a)
    if any(_is_abs_or_home(r) for r in roots):               # `.` 作 find 起点是正常的，只拦绝对/家目录
        return "find 在根/家目录/绝对路径下删除"
    # 无任何筛选谓词的 `find . -delete` = 删光当前目录
    filters = {"-name", "-iname", "-path", "-ipath", "-regex", "-iregex",
               "-type", "-newer", "-mtime", "-size", "-user", "-group"}
    if not any(f in args for f in filters):
        return "find 无筛选条件全量删除"
    return ""


def is_dangerous(cmd: str) -> str:
    """命中明显危险操作则返回原因（非空），否则空串。非穷举——真正的关口是人工确认。"""
    norm = _normalize(cmd)
    for pat, why in _GLOBAL_DANGER:
        if re.search(pat, norm):
            return why
    for seg in _SHELL_SPLIT.split(norm):
        seg = seg.strip()
        if not seg:
            continue
        base, args = _exe_and_args(_tokens(seg))
        if base == "rm":
            why = _check_rm(args)
        elif base == "git":
            why = _check_git(args)
        elif base in ("chmod", "chown"):
            why = _check_perm(base, args)
        elif base == "find":
            why = _check_find(args)
        else:
            why = ""
        if why:
            return why
    return ""


def run_command(repo_root, cmd: str, timeout: int = 300) -> dict:
    """在 repo_root 跑 shell 命令，返回 {ok, code, output}。输出截尾、超时/异常兜底。

    若启用了 OS 沙箱（env VORTOCODE_SANDBOX + macOS sandbox-exec，见 src/agents/sandbox.py），
    则把命令包进 Seatbelt——文件写入限制在仓库内、写不出去；未启用/不支持则照常 shell 直跑（行为不变）。
    """
    from src.agents.sandbox import sandbox_enabled, sandboxed_argv
    try:
        if sandbox_enabled():
            r = subprocess.run(sandboxed_argv(repo_root, cmd), cwd=str(repo_root),
                               capture_output=True, text=True, timeout=timeout)
        else:
            r = subprocess.run(cmd, shell=True, cwd=str(repo_root),
                               capture_output=True, text=True, timeout=timeout)
        return {"ok": r.returncode == 0, "code": r.returncode, "output": (r.stdout + r.stderr)[-8000:]}
    except subprocess.TimeoutExpired:
        return {"ok": False, "code": -1, "output": f"命令超时（>{timeout}s）"}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "code": -1, "output": f"无法执行: {e}"}
