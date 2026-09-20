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
from typing import Optional

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


# ---------------------------------------------------------------- 只读判定
# `is_dangerous` 回答"这条会不会闯祸"，下面这个回答另一个问题：**这条只是在看东西吗**。
# 两者独立：授权档位 READS 说的是"只读操作别老问我"，而 run_command 此前一律按执行面申报，
# 于是 `cat src/x.py` 在 READS 档下照样弹确认——那个档对命令面等于不存在（真机诊断 2026-09-17）。
#
# 这里**一律 fail-closed**：判不出来就当成执行面（多问一次），绝不反过来。判据有三层：
#   1. 整段不含重定向 / 命令替换 / 变量展开——`cat a > b` 会写盘，`$(...)` 能跑任何东西；
#   2. 每个管道段的可执行名都在白名单里——白名单只收"没有写形态"的命令；
#   3. 少数命令另有写形态（`find -delete`、`git branch -D`），逐个加参数护栏。
#
# 白名单刻意收得很紧：漏判只是多一次确认，误判是把写操作当读操作放行。`sed`/`awk` 因此
# **不在**列内（`sed -i` 原地改文件、awk 的 `print > "f"` 能写盘），要读文件用 cat/head/grep。
_READ_ONLY_EXES = frozenset({
    "cat", "head", "tail", "less", "more", "nl", "wc", "ls", "pwd", "tree",
    "echo", "printf", "basename", "dirname", "realpath", "readlink",
    "grep", "egrep", "fgrep", "rg", "ag", "ack",
    "sort", "uniq", "cut", "tr", "column", "jq",
    "file", "stat", "du", "df", "date", "whoami", "hostname", "uname",
    "printenv", "which", "type", "diff", "cmp", "md5sum", "shasum", "sha256sum",
})

# `git` 的只读子命令。只收**所有形态都只读**的那些：branch/tag/remote/config/stash/worktree/
# reflog 都有写形态（`git branch -D`、`git config --global x y`），一律不收。
_READ_ONLY_GIT = frozenset({
    "status", "log", "diff", "show", "blame", "shortlog", "whatchanged",
    "ls-files", "ls-tree", "rev-parse", "rev-list", "describe", "cat-file",
    "show-ref", "for-each-ref", "grep", "merge-base", "name-rev", "count-objects",
})

# `find` 的写/执行形态
_FIND_WRITES = ("-delete", "-exec", "-execdir", "-ok", "-okdir",
                "-fprint", "-fprint0", "-fprintf", "-fls")

# 重定向、命令替换、变量展开——任何一个出现就不算只读
_NOT_READ_ONLY_CHARS = ("<", ">", "$", "`")


def _segment_is_read_only(seg: str) -> bool:
    base, args = _exe_and_args(_tokens(seg))
    if not base:
        return False
    if base == "git":
        sub = next((a for a in args if not a.startswith("-")), "")
        return sub in _READ_ONLY_GIT
    if base == "find":
        return not any(a.startswith(w) for a in args for w in _FIND_WRITES)
    return base in _READ_ONLY_EXES


def is_read_only(cmd: str) -> bool:
    """这条命令只是在看东西吗。判不出来一律 False（多问一次，绝不少问）。"""
    norm = _normalize(cmd).strip()
    if not norm or any(ch in norm for ch in _NOT_READ_ONLY_CHARS):
        return False
    segments = [seg.strip() for seg in _SHELL_SPLIT.split(norm)]
    segments = [seg for seg in segments if seg]
    return bool(segments) and all(_segment_is_read_only(seg) for seg in segments)


# ---------------------------------------------------------------- 后台/长驻命令
# run_command 阻塞到结束，起不了 dev server / watcher / tail -f 再继续对话。这里加**后台**执行：
# Popen 起进程 → 后台线程把输出汇进环形缓冲 → agent 用 read_output 取增量、stop_command 收摊。
# 对标 CC 的后台 Bash + BashOutput。危险拦截/沙箱与前台 run_command 同源（调用方仍先过 is_dangerous 门）。
import itertools
import threading
from collections import deque

_BG_LOCK = threading.Lock()
_BG_PROCS: "dict[str, _BgProc]" = {}
_BG_COUNTER = itertools.count(1)
_BG_MAX_LINES = 4000          # 每个后台进程最多留最近这么多行（环形缓冲，防长跑 OOM）
# 进程已退出后，给收行线程收尾的宽限（秒）。见 _BgProc.read。
_BG_DRAIN_GRACE = 2.0
_BG_MAX_PROCS = 10            # 并发后台进程上限，防失控


class _BgProc:
    """一个后台进程 + 其输出环形缓冲。输出在后台线程里 drain，读取按绝对行号算增量。"""

    def __init__(self, bid: str, cmd: str, popen, sandbox: dict) -> None:
        self.id = bid
        self.cmd = cmd
        self.popen = popen
        self.sandbox = sandbox
        self.lines: "deque[str]" = deque(maxlen=_BG_MAX_LINES)
        self.total = 0                 # 迄今产出的总行数（含已被环形缓冲挤掉的）
        self.read_pos = 0              # 下一条未读的绝对行号
        self._lock = threading.Lock()
        self._reader = threading.Thread(target=self._drain, daemon=True)
        self._reader.start()

    def _drain(self) -> None:
        try:
            for line in self.popen.stdout:          # 逐行阻塞读，进程写多少读多少
                with self._lock:
                    self.lines.append(line.rstrip("\n"))
                    self.total += 1
        except Exception:  # noqa: BLE001 —— 管道关闭/进程没了都不该炸线程
            pass
        finally:
            try:
                self.popen.stdout.close()
            except Exception:  # noqa: BLE001
                pass

    def status(self) -> "tuple[str, Optional[int]]":
        code = self.popen.poll()
        return ("running", None) if code is None else ("exited", code)

    def read(self, tail: Optional[int] = None) -> dict:
        """取输出。tail=None → 上次读之后的**增量**；tail=N → 最近 N 行（不动读游标）。

        进程已退出时先等收行线程收尾：`poll()` 只说进程没了，**不说它写的行都已经进缓冲**。
        不等就会出现"status=exited 但 output 为空"——调用方据此认定命令没有输出，于是短命令
        （`echo done && exit 7` 这种）的输出被整段丢掉。CI 上真实复现过（2026-09-20）。
        管道在进程退出时关闭，收行线程随即结束，所以这里的等待正常是零成本。
        """
        if self.popen.poll() is not None:
            self._reader.join(timeout=_BG_DRAIN_GRACE)
        with self._lock:
            first_idx = self.total - len(self.lines)    # 缓冲里第一行的绝对行号
            if tail is not None:
                out = list(self.lines)[-max(0, int(tail)):] if tail else []
                dropped = 0
            else:
                start = max(self.read_pos, first_idx)
                dropped = max(0, first_idx - self.read_pos)   # 读得太慢、被环形缓冲挤掉的行数
                out = list(self.lines)[start - first_idx:]
                self.read_pos = self.total
        state, code = self.status()
        return {"id": self.id, "cmd": self.cmd, "status": state, "code": code,
                "dropped": dropped, "output": "\n".join(out), "sandbox": self.sandbox}

    def stop(self, timeout: int = 5) -> bool:
        """先 SIGTERM、宽限后仍在就 SIGKILL；对**整个进程组** killpg，把 shell 拉起的子进程一并收
        （否则 dev server/watcher 会漏杀继续跑）。killpg 不可用（非 POSIX/组已没）时退回只对主进程。"""
        import os
        import signal
        import subprocess as _sp
        if self.popen.poll() is not None:
            return True

        def _sig(sig) -> None:
            try:
                os.killpg(os.getpgid(self.popen.pid), sig)   # 整组：连根收子进程
            except (ProcessLookupError, PermissionError, OSError, AttributeError):
                try:
                    self.popen.send_signal(sig)              # 退回只对主进程（组已没/平台不支持）
                except Exception:  # noqa: BLE001
                    pass

        try:
            _sig(signal.SIGTERM)
            try:
                self.popen.wait(timeout=timeout)
            except _sp.TimeoutExpired:
                _sig(signal.SIGKILL)
        except Exception:  # noqa: BLE001
            pass
        return self.popen.poll() is not None


def run_command_background(repo_root, cmd: str, *, require_isolation: bool = False) -> dict:
    """后台起一个长驻命令（dev server / watcher / tail…），立即返回句柄 id，不阻塞回合。

    危险拦截由调用方（工具层）先过；沙箱与前台 run_command 同源。返回 {ok, id, pid} 或 {ok:False, error}。
    """
    import subprocess
    from src.agents.sandbox import child_env, resolve_sandbox, sandboxed_argv
    decision = resolve_sandbox(require_isolation=require_isolation)
    evidence = decision.to_dict()
    if not decision.allowed:
        return {"ok": False, "error": decision.reason, "sandbox": evidence}
    with _BG_LOCK:
        alive = [p for p in _BG_PROCS.values() if p.popen.poll() is None]
        if len(alive) >= _BG_MAX_PROCS:
            return {"ok": False, "error": f"后台进程已达上限 {_BG_MAX_PROCS} 个；先 stop_command 收掉一些。",
                    "sandbox": evidence,
                    "warning": decision.reason if not decision.isolated else ""}
        bid = f"bg{next(_BG_COUNTER)}"
    # start_new_session=True：把命令放进**独立进程组/会话**（子进程 = 组长，pgid==pid）。
    # 长驻命令常经 shell 再拉起子进程（npm run dev→node…），只 kill 顶层 shell 会漏掉子进程；
    # 独立进程组让 stop() 能对**整组** killpg，把 dev server/watcher 连根收干净（POSIX；见 stop()）。
    try:
        if decision.isolated:
            popen = subprocess.Popen(sandboxed_argv(repo_root, cmd, backend=decision.backend),
                                     cwd=str(repo_root), env=child_env(),
                                     stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                     text=True, bufsize=1, start_new_session=True)
        else:
            popen = subprocess.Popen(cmd, shell=True, cwd=str(repo_root), env=child_env(),
                                     stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                     text=True, bufsize=1, start_new_session=True)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"无法启动: {e}", "sandbox": evidence}
    proc = _BgProc(bid, cmd, popen, evidence)
    with _BG_LOCK:
        _BG_PROCS[bid] = proc
    return {"ok": True, "id": bid, "pid": popen.pid, "sandbox": evidence,
            "warning": decision.reason if not decision.isolated else ""}


def read_background(bid: str, tail: Optional[int] = None) -> dict:
    """读某后台命令的输出（增量或最近 N 行）+ 运行状态。找不到句柄返回 ok:False。"""
    with _BG_LOCK:
        proc = _BG_PROCS.get(bid)
    if proc is None:
        return {"ok": False, "error": f"没有后台命令 {bid}（用 list 看当前有哪些）。"}
    res = proc.read(tail=tail)
    res["ok"] = True
    return res


def stop_background(bid: str) -> dict:
    """停某后台命令（terminate→kill）。找不到句柄返回 ok:False。"""
    with _BG_LOCK:
        proc = _BG_PROCS.get(bid)
    if proc is None:
        return {"ok": False, "error": f"没有后台命令 {bid}。"}
    stopped = proc.stop()
    return {"ok": True, "id": bid, "stopped": stopped, "code": proc.popen.poll()}


def list_background() -> "list[dict]":
    """列出所有后台命令（含已退出的，直到被清理）。"""
    with _BG_LOCK:
        procs = list(_BG_PROCS.values())
    out = []
    for p in procs:
        state, code = p.status()
        out.append({"id": p.id, "cmd": p.cmd, "status": state, "code": code,
                    "pid": p.popen.pid, "sandbox": p.sandbox})
    return out


def stop_all_background() -> int:
    """停掉并清空所有后台命令（会话切换/退出时收摊）。返回停掉的个数。"""
    with _BG_LOCK:
        procs = list(_BG_PROCS.values())
        _BG_PROCS.clear()
    for p in procs:
        try:
            p.stop()
        except Exception:  # noqa: BLE001
            pass
    return len(procs)


def run_command(repo_root, cmd: str, timeout: float = 300, *,
                require_isolation: bool = False) -> dict:
    """在 repo_root 跑 shell 命令，返回 {ok, code, output}。输出截尾、超时/异常兜底。

    结果始终携带 ``sandbox`` 决策证据。无人值守调用方传
    ``require_isolation=True``；只有显式 ``VORTOCODE_SANDBOX=off`` 才可在宿主机执行。
    """
    from src.agents.sandbox import child_env, resolve_sandbox, sandboxed_argv
    decision = resolve_sandbox(require_isolation=require_isolation)
    evidence = decision.to_dict()
    if not decision.allowed:
        return {"ok": False, "code": -1, "output": decision.reason,
                "sandbox": evidence, "warning": ""}
    try:
        if decision.isolated:
            r = subprocess.run(sandboxed_argv(repo_root, cmd, backend=decision.backend),
                               cwd=str(repo_root), env=child_env(),
                               capture_output=True, text=True, timeout=timeout)
        else:
            r = subprocess.run(cmd, shell=True, cwd=str(repo_root), env=child_env(),
                               capture_output=True, text=True, timeout=timeout)
        return {"ok": r.returncode == 0, "code": r.returncode,
                "output": (r.stdout + r.stderr)[-8000:], "sandbox": evidence,
                "warning": decision.reason if not decision.isolated else ""}
    except subprocess.TimeoutExpired:
        warning = decision.reason if not decision.isolated else ""
        return {"ok": False, "code": -1, "output": f"命令超时（>{timeout}s）",
                "sandbox": evidence, "warning": warning}
    except Exception as e:  # noqa: BLE001
        warning = decision.reason if not decision.isolated else ""
        return {"ok": False, "code": -1, "output": f"无法执行: {e}",
                "sandbox": evidence, "warning": warning}
