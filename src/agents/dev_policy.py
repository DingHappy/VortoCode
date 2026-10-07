"""Development preflight, retry policy and honest verification summaries.

No model, client or execution scheduling dependencies.
"""
from __future__ import annotations

import re
from typing import Callable, Optional

def _dev_review_enabled() -> bool:
    """dev_auto 的 PR 前对抗审查段开关：env `VORTOCODE_DEV_REVIEW`，**默认开**（=0/false/no/off 关）。

    集成绿后、开 PR 前跑一个专职挑刺的 reviewer 子 agent（见 src/agents/review.py），把 codex 外审
    反复抓真 bug 的经验内化进流水线。关掉可省一轮 LLM（评测/省钱场景）。
    """
    import os
    return os.getenv("VORTOCODE_DEV_REVIEW", "1").strip().lower() not in ("0", "false", "no", "off")






def _dev_attempts() -> int:
    """隔离实现的最多尝试次数（1 次初始 + 自修复重试）。env VORTOCODE_DEV_ATTEMPTS 调，默认 2、下限 1。"""
    import os
    try:
        return max(1, int(os.getenv("VORTOCODE_DEV_ATTEMPTS") or 2))
    except ValueError:
        return 2


def _dev_parallelism() -> int:
    """并行隔离实现时最多同时在跑的子任务数。env VORTOCODE_DEV_PARALLEL 调，默认 4、下限 1。

    防 dev_auto 分解出很多独立子任务时一次性 fan-out 几十个 worktree+LLM 把中转站/磁盘打爆
    （dev_parallel 另有 [:5] 上限，但 dev_auto 的独立批数量取决于分解结果、原先无界）。
    """
    import os
    try:
        return max(1, int(os.getenv("VORTOCODE_DEV_PARALLEL") or 4))
    except (TypeError, ValueError):
        return 4






def preflight_dev(repo_root: str, *, want_pr: bool = False, heal: bool = True,
                  on_heal: Optional[Callable[[str], None]] = None) -> list[str]:
    """跑流水线**之前**验环境，返回**仍然阻塞**的问题清单（空 = 可以开跑）。

    为什么要有这一步：真机 2026-07-26，新机器上没配 git 提交身份，任务照常分解、实现、自测全绿，
    最后一步 commit 才挂——**烧掉几十秒 LLM 和一整轮工作，才撞上一条 `git config` 就能解决的事**。
    而 `vc doctor` 当时是绿的：它验了"git 在不在、这儿是不是仓库"，没验"提交得了吗"——
    检查了必要条件，漏了充分条件。

    纪律：只查**流水线必然依赖、缺了必然失败**的东西，每条都给可直接粘贴的修复命令。
    可有可无的一律不进来——预检一旦变成噪音就会被无视。

    `heal=True`（默认）：白名单内**能安全自动处置**的问题（见 gateway/remediation.py）就地修好，
    修成了就不再算阻塞——"检查出来只会拦住你"和"检查出来顺手修好"差的正是"离不离得开作者"。
    自动处置的作用域一律限本仓库、可逆、幂等；碰全局配置/要装东西/要联网的一律不自动做。

    `on_heal`：修好了要**说出来**。静默自愈比不自愈更可怕——人会以为环境一直是好的，
    而它其实是被悄悄补过的（下次换台机器又原形毕露，却没人知道上次发生过什么）。
    """
    import shutil
    import subprocess

    problems: list[str] = []

    def _git_cfg(key: str) -> str:
        try:
            r = subprocess.run(["git", "-C", repo_root, "config", "--get", key],
                               capture_output=True, text=True, timeout=5)
            return (r.stdout or "").strip()
        except Exception:  # noqa: BLE001
            return ""

    if not (_git_cfg("user.name") and _git_cfg("user.email")):
        problems.append(
            "git 提交身份未配置——落分支时 commit 必然失败（Author identity unknown）。修：\n"
            '    git config --global user.name "你的名字"\n'
            '    git config --global user.email "你的邮箱"')

    if want_pr and not shutil.which("gh"):
        problems.append(
            "要开 PR 但 gh CLI 不在 PATH——分支能落、PR 开不了。装 gh 并 `gh auth login`；"
            "若确认已装，检查**服务进程**的 PATH（systemd/launchd 给的 PATH 比登录 shell 窄）。")

    if not heal or not problems:
        return problems

    from src.gateway.remediation import remediate
    remaining: list[str] = []
    for problem in problems:
        outcome = remediate(repo_root, problem, source="preflight_dev")
        if outcome.healed:
            if on_heal is not None:
                try:
                    on_heal(f"🔧 环境预检自愈：{outcome.detail}")
                except Exception:  # noqa: BLE001 —— 播报失败不该把已经修好的判成没修好
                    pass
            continue
        remaining.append(problem)
    return remaining


def land_note(msg: str, applied_msgs, failed_errs: dict) -> tuple[str, str]:
    """一个块落分支之后该记什么状态与原因 → (status, note)。

    抽成纯函数是为了让这条契约可测：**落分支失败必须报真实 git 报错**。此前这里把
    `failed[].error` 丢掉、一律硬写"与其它块文本冲突"——真机 2026-07-26 撞到的其实是
    `commit 失败: Author identity unknown`（新机器没配 git user.name/email），而且当时
    只有一个块，"与其它块冲突"这句话把人引向完全错的排查方向。
    """
    if msg in applied_msgs:
        return "landed", ""                       # 真在 applied 里才算落地
    if msg in failed_errs:
        why = (failed_errs.get(msg) or "").strip()
        if why:
            return "failed", f"自测绿但落分支失败：{why[:160]}"
        # 拿不到真错误才退回最常见的猜测，且措辞标明是猜的
        return "failed", "自测绿但未能干净落分支（疑与其它块文本冲突）"
    all_err = "；".join(v for v in failed_errs.values() if v)[:160]
    return "failed", f"落分支整体失败（未落地）：{all_err or '见日志'}"


def _repair_prompt(base: str, failure_tail: str) -> str:
    """把上一次的测试失败输出拼回子任务描述，引导下一个全新隔离子 agent 定向修复。"""
    return (f"{base}\n\n【上一次尝试失败】测试未过，失败输出尾部：\n{failure_tail}\n"
            f"请据此定位并修正实现，确保 run_tests 通过。")


def _noop_retry_prompt(base: str) -> str:
    """上一次子 agent 没真正改文件（no-op）→ 下一次用更命令式的提示逼它实际动手。

    推理模型对模糊描述常"只看代码/只跑下已有测试就交差"（diff 0 行）。这里把"必须真改文件"
    说死，配合换全新 worktree 重试，能把命中率从"看运气"拉回来。
    """
    return (f"{base}\n\n【上一次尝试没有产生任何改动】你只是查看或跑了测试，并没有真正实现。"
            f"必须用 edit_file / write_file **实际修改文件**来完成任务，再用 run_tests 自测通过——"
            f"只跑测试而不改文件不算完成。")


def _missing_tests_retry_prompt(base: str) -> str:
    """实现绿了、但任务要求补的测试文件一个没动 → 下一次把"必须落到测试文件里"说死。"""
    return (f"{base}\n\n【上一次尝试只改了源码、没有补测试】既有测试仍然绿，但任务要求的测试**没写**。"
            f"这次必须新建或修改测试文件（tests/ 下，或 test_*.py / *_test.py / *.test.* / *.spec.*），"
            f"写出真正覆盖新行为的用例，再用 run_tests 自测通过——只改源码不算完成。")


def _dropped_tests_retry_prompt(base: str, gone: list[str]) -> str:
    """上一次把既有测试删了 → 说死：新测试是**加**上去的，旧的一个都不许动。"""
    names = "、".join(gone[:5]) + ("…" if len(gone) > 5 else "")
    return (f"{base}\n\n【上一次尝试删掉了既有测试】被删的是：{names}。没有人要求删它们——"
            f"删掉测试让套件变绿是**最严重的作弊**。这次必须保留原有的每一个测试函数一字不动，"
            f"新的用例是**另外加**上去的（在文件末尾追加，别改已有的函数名/函数体），"
            f"再用 run_tests 自测通过。")
























from src.utils.exc_utils import _exc_text  # noqa: F401 — re-exported so existing "from src.agents.main_agent import _exc_text" keeps working


def _fail_note(result: dict) -> str:
    """接力块失败时给人的说明——**按真实死因分流，且两种线索都带**。

    run_dependent_on_branch 有五个失败出口（worktree add 挂 / LLM 通道故障 / 真的无改动 /
    测试红 / 提交失败），只有一个是"无改动"：

    - **真无改动** → output 只有一句「无改动（…）」没信息量，要的是子 agent 说的**为什么没动手**
    - **其余** → output 才是机器真相（哪个测试红了、通道怎么挂的），是首要线索；
      子 agent 的话作为补充——它有时会说"我改了，但 X 测试红是因为 Y"，那句很值钱

    两次教训叠出来的设计：① 2026-08-03 我让 agent 修这处，它把**所有**失败都写成
    `_noop_note(conclusion)`，于是测试红时那 1500 字失败尾部被换成一句错误的"无改动"，
    比原来还糟——我合得太快，还在 PR 里夸它比我准。② 我改成一律用 output 后，
    它写的那条测试红了——它假设 conclusion 有诊断信息。**双方各对一半，所以两个都带。**
    """
    output = str(result.get("output") or "").strip()
    said = str(result.get("conclusion") or "").strip()
    if not output or output.startswith("无改动（"):     # 唯一确实是 no-op 的出口
        return _noop_note(said)
    note = output[-320:]
    if said:                                            # 补上子 agent 的读法（次要线索）
        note += f"\n（子 agent 说：{said[:120]}）"
    return note


def _noop_note(conclusion) -> str:
    """子 agent 没产出任何改动时给人的说明。

    只写「无改动/出错」等于把唯一的线索扔了：子 agent 通常**说过**为什么没动手
    （找不到目标段落 / 认为已经满足 / 把任务理解成了别的事）。真机代价——2026-07-24/25
    同一个「给 README 补一行」的任务在 14 小时里重试 8 次，全走这条路，
    而人每次拿到的都是那五个字，于是只能盲改提示词再试。

    带上原话，人一眼就知道该把指令改成什么样（那两次最终成功的，正是把指令写具体了）。
    """
    text = str(conclusion or "").strip()
    if not text:
        return "无改动/出错（子 agent 也没说明原因）"
    return f"无改动——子 agent 说：{text[:300]}"


def _log_stage_usage(stage: str, usage: dict) -> None:
    """把一个 dev 阶段的 token 用量记进日志（INFO）。

    为什么落日志而不是新开一张表：这一步的目的是**攒真实数据**，好回答"分解 vs 执行
    各占多少、分别用了哪些模型"。有了几次真跑的数字，才谈得上模型分层——
    没数据就调模型是照直觉使力气（2026-08-03 门禁提速那轮刚验证过先量后动的价值）。
    等数据说话了，再决定要不要把它升级成结构化台账。

    日志本身直到 2026-08-02 才真的会产出（此前全仓无 logging 配置，60 处 .info() 全是哑的）。
    """
    import logging

    total = int(usage.get("total_tokens") or 0)
    if total <= 0:
        return
    # by_model 的桶里**只有** prompt/completion，没有 total_tokens——现场相加。
    # （第一版直接读 v["total_tokens"]，冒烟一跑全是 0：日志能打出来不等于打对了。）
    by_model = ", ".join(
        f"{m}={int(v.get('prompt_tokens') or 0) + int(v.get('completion_tokens') or 0)}"
        for m, v in sorted((usage.get("by_model") or {}).items()))
    logging.getLogger("vortocode.dev.usage").info(
        "阶段用量 %s: %d tokens（%d 次调用）%s",
        stage, total, int(usage.get("calls") or 0), f" · {by_model}" if by_model else "")


def _remote_has_branch(repo_root: str, branch: str, remote: str = "origin") -> bool:
    """远端存不存在这个分支。空/出错一律 False（宁可回退到默认分支，也不要开不出 PR）。"""
    import subprocess
    if not branch:
        return False
    try:
        r = subprocess.run(
            ["git", "-C", str(repo_root), "ls-remote", "--heads", remote, branch],
            capture_output=True, text=True, timeout=20)
        return r.returncode == 0 and bool((r.stdout or "").strip())
    except Exception:  # noqa: BLE001
        return False


def _detect_base_branch(repo_root: str) -> str:
    """dev_auto 开 PR 时的 base：取当前 HEAD 所在分支（PR 合回你出发的地方）。

    **但 base 必须在远端存在**——本地分支 GitHub 看不见。真机 2026-08-03：我在
    `vorto/idle-7`（本地建的、从没推过）上跑 dev_auto，集成全绿、分支也 push 了，
    开 PR 却挂在 `Base ref must be a branch / No commits between …`。
    整条流水线唯一的产出口就这么堵死了，而人只看到一句 GraphQL 报错。

    远端没有就回退到远端默认分支（origin/HEAD，通常是 main）——那才是 PR 真正该合回去的地方。
    """
    import subprocess
    try:
        r = subprocess.run(["git", "-C", str(repo_root), "rev-parse", "--abbrev-ref", "HEAD"],
                           capture_output=True, text=True)
        b = (r.stdout or "").strip()
    except Exception:  # noqa: BLE001
        b = ""
    if not b or b == "HEAD":
        return _remote_default_branch(repo_root)
    # **只在"有远端、但远端没有这个分支"时才回退**——那才是真坏的场景。
    # 压根没配远端时开不了 PR，base 取什么都无所谓，保持"合回你出发的地方"的原语义。
    if not _has_remote(repo_root) or _remote_has_branch(repo_root, b):
        return b
    return _remote_default_branch(repo_root)


def _has_remote(repo_root: str, remote: str = "origin") -> bool:
    import subprocess
    try:
        r = subprocess.run(["git", "-C", str(repo_root), "remote", "get-url", remote],
                           capture_output=True, text=True, timeout=10)
        return r.returncode == 0 and bool((r.stdout or "").strip())
    except Exception:  # noqa: BLE001
        return False


def _remote_default_branch(repo_root: str, remote: str = "origin") -> str:
    """远端默认分支（origin/HEAD 指向谁）；问不出来回退 "main"。"""
    import subprocess
    try:
        r = subprocess.run(
            ["git", "-C", str(repo_root), "symbolic-ref", "--short", f"refs/remotes/{remote}/HEAD"],
            capture_output=True, text=True, timeout=20)
        name = (r.stdout or "").strip()
        if name.startswith(f"{remote}/"):
            return name[len(remote) + 1:] or "main"
    except Exception:  # noqa: BLE001
        pass
    return "main"


def _is_test_path(path: str) -> bool:
    """路径看着像测试文件（tests/、test_*.py、*_test.py、*.test./*.spec.）。"""
    p = (path or "").strip().lower()
    base = p.rsplit("/", 1)[-1]
    return ("tests/" in p or p.startswith("test/") or "/test/" in p
            or base.startswith("test_") or base.endswith("_test.py")
            or ".test." in base or ".spec." in base)


def _count_test_files(diff: str) -> int:
    """数一个 unified diff 里改动的**测试文件**数（从 `+++ b/<path>` 行取）。"""
    import re as _re
    return sum(1 for p in _re.findall(r"^\+\+\+ b/(.+)$", diff or "", _re.M) if _is_test_path(p))


_WANTS_TESTS_RE = re.compile(
    r"(补|加上|加一|加个|加几|新增|添加|增加|写)[^。；;\n]{0,12}?(测试|用例|test)"
    r"|\b(add|adding|writ(e|ing))\b[^.;\n]{0,24}?\b(test|tests|spec|specs)\b"
    r"|(测试|用例)[^。；;\n]{0,8}?(补上|补一|写一|写个|加上)",
    re.I)


_REMOVED_TEST_RE = re.compile(r"^-\s*(?:async\s+)?def\s+(test_\w+)", re.M)
_ADDED_TEST_RE = re.compile(r"^\+\s*(?:async\s+)?def\s+(test_\w+)", re.M)
# 描述本身就要求动既有测试（删旧的/改名/重写）——这时"测试没了"是人要的，不该当成事故。
# 动词必须**紧挨着**测试才算数：任务里的 `remove(index)` 说的是给类加个方法，不是要删测试，
# 光看见 remove 就放行会让这道信号在最该响的那次哑掉（本次真机 diff 就是这么被放过的）。
_DROP_VERB = "删|移除|去掉|重写|改名|重命名|替换|合并"
_MAY_DROP_TESTS_RE = re.compile(
    rf"({_DROP_VERB})[^。；;\n]{{0,12}}?(测试|用例|test)"
    rf"|(测试|用例|test\w*)[^。；;\n]{{0,12}}?({_DROP_VERB})"
    r"|\b(remove|delete|drop|rewrite|rename|replace)\s+(?:the\s+|an?\s+)?(?:\w+\s+){0,2}"
    r"(test|tests|spec|specs)\b", re.I)


def _removed_test_funcs(diff: str) -> list[str]:
    """diff 里从**测试文件**中删掉的测试函数名（去掉同一次又加回来的，那是改名/移动）。

    真机（2026-09-20，打包版冒烟）：任务是"加 remove(index) 并补 test_remove"，子 agent 把既有的
    `test_add_and_pending` 直接删掉、原地改成 `test_remove`。于是测试绿（1 passed）、测试增量提示
    报"含 1 个测试文件"、分支看着完全正常——**既有覆盖被抹掉了，没有任何信号报警**。

    只数测试文件里的，且排除"删了又加"的同名函数（那多半是原地重写，不是丢覆盖）。
    """
    removed: list[str] = []
    cur_is_test = False
    chunk: list[str] = []
    for line in (diff or "").splitlines():
        if line.startswith("+++ b/"):
            if cur_is_test:
                removed.extend(_dropped_in_chunk("\n".join(chunk)))
            cur_is_test = _is_test_path(line[6:].strip())
            chunk = []
            continue
        if cur_is_test:
            chunk.append(line)
    if cur_is_test:
        removed.extend(_dropped_in_chunk("\n".join(chunk)))
    return removed


def _dropped_in_chunk(chunk: str) -> list[str]:
    added = set(_ADDED_TEST_RE.findall(chunk))
    return [n for n in dict.fromkeys(_REMOVED_TEST_RE.findall(chunk)) if n not in added]


def _dropped_tests(desc: str, diff: str) -> list[str]:
    """本次改动里**没人要求却被删掉**的既有测试函数名。描述本就要求动测试时返回空。"""
    if _MAY_DROP_TESTS_RE.search(desc or ""):
        return []
    return _removed_test_funcs(diff)


def _dropped_tests_note(diff: str, desc: str) -> str:
    """删掉了既有测试却没人要求删 → 在结论里说死。任务本就要求动测试时不报（那是人要的）。"""
    gone = _dropped_tests(desc, diff)
    if not gone:
        return ""
    names = "、".join(gone[:5]) + ("…" if len(gone) > 5 else "")
    return (f"（⚠ 本次改动**删除了既有测试** {names}——没有人要求删它们，"
            f"“测试通过”很可能只是因为覆盖被移除了。落分支前请核对这是不是你要的。）")


def _wants_new_tests(desc: str) -> bool:
    """任务描述里是否**明确要求新写测试**（而不只是"让测试变绿"）。

    刻意保守：必须出现"补/加/新增/写 … 测试/用例"这类**创建**意图才算数。像"修好 X，让现有
    测试通过""修复 test_foo 的报错"都不该命中——误判的代价是白烧一轮子 agent。
    """
    return bool(_WANTS_TESTS_RE.search(desc or ""))


def _branch_changed_files(repo_root: str, base: str, branch: str) -> Optional[list[str]]:
    """branch 相对 base 的改动文件路径（三点差：branch 分出后的全部改动）。

    dev_auto 用它算测试增量——**依赖接力**子任务经 run_dependent_on_branch 直接 commit 到分支，
    其 diff 不在内存 greens 里，只有分支上才看得全（否则纯依赖成功时会漏掉诚实提示）。出错返回 None。
    """
    import subprocess
    try:
        r = subprocess.run(["git", "-C", str(repo_root), "diff", "--name-only", f"{base}...{branch}"],
                           capture_output=True, text=True, timeout=20)
        if r.returncode != 0:
            return None
        return [ln.strip() for ln in (r.stdout or "").splitlines() if ln.strip()]
    except Exception:  # noqa: BLE001
        return None


def _test_delta_msg(n_tests: int) -> str:
    """给定"本次改动的测试文件数"，生成诚实的测试增量说明。

    dogfood（2026-07-02）暴露：隔离实现子 agent 只加了源码、没加要求的测试，但 run_tests 里
    **既有测试仍绿** → 报"测试通过" → 主 agent 据此**谎称补了测试**。根因是"测试通过"这个信号
    不区分"既有测试还绿"与"新代码被覆盖"。这里如实点出测试文件增量，既给用户诚实信号，也给主
    agent 据实依据（别再编造补了测试）。
    """
    if n_tests:
        return f"（本次改动含 {n_tests} 个测试文件）"
    return ("（⚠ 本次改动**未新增/改动任何测试文件**——“测试通过”仅表示既有测试仍绿，"
            "新增/改动的代码未必被测试覆盖；若任务要求测试请核对是否真的补了）")


def _test_delta_note(diff: str) -> str:
    """按 unified diff 生成诚实的测试增量说明（dev_isolated/dev_parallel 用；它们的 diff 在内存里齐全）。"""
    return _test_delta_msg(_count_test_files(diff))


def _diff_change_counts(diff: str) -> tuple[int, int]:
    """只计 hunk 中的新增/删除行，避免把 diff 元数据误报成代码改动。"""
    lines = diff.splitlines()
    additions = sum(line.startswith("+") and not line.startswith("+++ ") for line in lines)
    deletions = sum(line.startswith("-") and not line.startswith("--- ") for line in lines)
    return additions, deletions
