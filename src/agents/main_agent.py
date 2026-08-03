"""VortoCode 主 Agent —— 单一 agent loop（整合 Claude Code / opencode / OpenClaw 的思路）。

设计取舍：
- **一个会话主 agent = LLM + 工具**，跑 ReAct 循环：输入 → 模型 →（工具 → 回灌）* → 最终回复。
  闲聊/提问/读代码这类轻活由主 agent 直接处理 —— 不再需要前置的"意图分类器"。
- **重型开发（dev→test→review）作为一个"工具/子 agent"** 被主 agent 按需调起
  （OpenClaw 的 coding-agent 思路：主 loop 干轻活，重活委派）。
- **plan / build 模式 = 工具权限门**（opencode 的 Plan/Build agent 思路）：plan 只给只读工具，
  写类/重型工具在 plan 下被拒、引导用户切 build —— 落实项目"人在关口"的定位。
- **模型无关**：用"提示式工具协议"（模型输出一个 {"tool","args"} JSON 即调用），
  不依赖具体模型的 function-calling API，任何 OpenAI 兼容 chat 模型都能跑、也便于确定性测试。

本模块只负责"循环 + 协议 + 工具调度"，不依赖 TUI；工具由调用方注入（见 tui/app.py）。
"""

from __future__ import annotations

from typing import Any, Callable, Optional

from src.agents.tool import Tool

# 确认门已抽到 `src/agents/gate.py`（叶子模块，进得了 mypy 的类型门禁圈）。这里只再导出
# `make_confirm_gate` —— gateway 与测试仍从 main_agent 取它（8 处）。其余名字（decide / ALLOW /
# TAINT_* …）一律直接从 `src.agents.gate` 取，不在这里转一道。
# **判定只有一处：`gate.decide()`**——别在任何端里重写那个排序（那是"加一端漏一端"的病根）。
from src.agents.gate import make_confirm_gate  # noqa: F401
















# 折叠占位符的标记：靠它认出"已折叠"，而不是往消息 dict 里塞私有键——history 的 dict 会**原样
# 进 API 请求体**，多一个未知键会被 OpenAI 兼容端点 400，且它还会随会话快照落盘（自审逮到）。

# 隔离实现子 agent 的角色指令。**所有**造实现子 agent 的地方共用这一份（经
# repo_memory.dev_subagent_system 再拼上仓库记忆）——此前 main_agent 与 TUI 各拼各的，
# 加仓库记忆时就漏掉了 TUI 那两个工具（codex 审出的真问题）。

# 工具预算用尽时的"收尾"指令：禁用工具、强制据已有上下文给最终回答（而不是丢弃一切返回空）

# 对话压缩器的系统提示：把"老段"对话压成滚动纪要，避免长会话里中段决策被硬丢弃。
# 强约束保留原始目标——这正是 #72 锚点想守住的，纪要把它连同关键决策一起守住、且语义化。




# 按模型窗口自适应历史预算的参数：
# - 只取窗口的一部分给「历史」，给系统提示/工具 schema/推理/输出留足空间。
# - 只对窗口够大的模型放大（小窗口/未知模型维持保守默认，避免历史预算反超窗口而溢出）。
# - 绝对硬顶：即便超大窗口也别把历史堆到天上（成本/失焦），用户可用 VORTOCODE_MAX_CONTEXT_TOKENS 精确覆盖。
# fraction 夹到 (0,1]：>1 会让历史预算反超模型窗口而溢出，属危险取值，直接钳掉。




# ----------------------------------------------------------------------- 协议解析














# 纠偏：模型既没给有效回答、也没正确调用工具（空收尾 / 残缺工具 JSON 当结论）时，回灌这条让它重来一次








def research_parallel_cap(args: dict, *, default: int = 2, maximum: int = 5) -> int:
    """子 agent 并行度决策：默认轻量；明确给理由/范围或 max_parallel 时才放宽。

    这不是安全边界，只是调度启发式。真正防 runaway 仍靠 MainAgent 的 plan 工具预算和这里的 maximum。
    """
    maximum = max(1, maximum)
    default = min(max(1, default), maximum)
    reason = str(args.get("reason") or args.get("scope") or args.get("why") or "").strip()
    raw = args.get("max_parallel") or args.get("parallelism") or args.get("limit")
    if raw is not None:
        try:
            requested = max(1, int(raw))
        except (TypeError, ValueError):
            requested = default
        requested = min(requested, maximum)
        return requested if reason or requested <= default else default
    return maximum if reason else default


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
























def _exc_text(e: BaseException) -> str:
    """把异常渲染成**人能据以定位**的一行。

    裸 `str(e)` 会丢掉最关键的类型：`KeyError('descriptions')` 只剩 `'descriptions'`，
    `TimeoutError()` 干脆是空串。于是用户看到「(任务分解出错: 'descriptions')」甚至
    「(出错: )」——无从下手。类型是定位的第一线索，永远带上。
    """
    detail = str(e).strip()
    return f"{type(e).__name__}: {detail}" if detail else type(e).__name__


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


def _detect_base_branch(repo_root: str) -> str:
    """dev_auto 开 PR 时的 base：取当前 HEAD 所在分支（PR 合回你出发的地方）；分离头/出错回退 main。"""
    import subprocess
    try:
        r = subprocess.run(["git", "-C", str(repo_root), "rev-parse", "--abbrev-ref", "HEAD"],
                           capture_output=True, text=True)
        b = (r.stdout or "").strip()
        return b if b and b != "HEAD" else "main"
    except Exception:  # noqa: BLE001
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


def build_dev_tools(repo_root: str, on_progress: Optional[Callable[[str], None]] = None,
                    confirm: Optional[Callable] = None, draft_pr: bool = False,
                    capabilities: Any = None,
                    on_diff: Optional[Callable[[str, str], None]] = None) -> list[Tool]:
    """UI 无关的隔离 dev 工具（给 Web/CLI agent 用）。

    `dev_isolated`：在一次性 git worktree 里让可写子 agent 实现 + 自测，再跑测试验证；✅通过就
    **自动落到 vorto/<id> 新分支**（绝不碰 main/工作区），返回结论。无模态确认——靠 build 门控 +
    完全隔离 + 落新分支保证"人在关口"。TUI 那版另带富 diff 渲染 + 确认；这版给没有模态的 Web。

    on_progress(msg)：可选进度回调。dev_parallel/dev_auto 跑大任务时一个子任务就可能要 1-2 分钟、
    整条链路十几分钟——没有它调用方只能对着静默的 prompt 干等、像卡死。有了它能边跑边播
    "并行实现中/某块修复重试/依赖接力/集成验证…"。best-effort、出错不影响流水线；不传则零开销。

    confirm(message)->awaitable bool：可选**外向操作确认门**（同 build_command_tool）。给了它，
    dev_auto 才支持 `open_pr=true`——集成测试通过后经确认把 vorto/auto 分支 push 上去并开 PR，
    补上"一句话→PR"的最后一环。不传/未确认/集成红 都不开 PR（只留分支），完全向后兼容。
    """
    def _progress(msg: str) -> None:
        if on_progress is not None:
            try:
                on_progress(msg)
            except Exception:  # noqa: BLE001
                pass

    def _emit_diff(title: str, branch: str, base: str) -> None:
        """on_diff(title, diff)：把分支 diff 结构化推给客户端（AGENT_DIFF），在请求确认**之前**——
        让人看清要批准的是什么，而不是对着一句纯文本确认。best-effort：取 diff/回调失败都不影响
        流水线（没有 on_diff 的端零开销，行为与从前一致）。"""
        if on_diff is None:
            return
        try:
            from src.agents.review import _branch_diff
            diff = _branch_diff(repo_root, base, branch, limit=20000)
            if diff.strip():
                on_diff(title, diff)
        except Exception:  # noqa: BLE001
            pass

    async def _dev_isolated(args: dict) -> str:
        import asyncio
        import re
        import uuid
        from src.agents.worktree import apply_diff_to_branch

        desc = str(args.get("description") or args.get("task") or "").strip()
        if not desc:
            return "dev_isolated 需要 description（要在隔离工作区实现的子任务）。"
        sel = str(args.get("test") or "").strip()
        from src.agents.test_detect import detect_test_cmd
        test_cmd = detect_test_cmd(repo_root, sel)          # 按仓库类型探测（pytest/npm/go/cargo/make）
        _progress(f"⚙️ 隔离实现「{desc[:40]}」中（worktree 实现+自测，可能要 1-2 分钟）…")
        try:
            # 复用自修复重试循环：子 agent no-op（没真改文件）或自测红时，换全新 worktree 自动重试，
            # 最多 _dev_attempts() 次。这样单次 dev_isolated 调用就不易交白卷，不必指望主 agent 再调一次。
            last = await _implement_with_repair(desc, test_cmd)
        except Exception as e:  # noqa: BLE001
            # 带上**异常类型**：KeyError 的 str(e) 只是 'descriptions'，
            # 光看 "(隔离实现出错: 'descriptions')" 无从下手。类型才是定位的第一线索。
            return f"(隔离实现出错: {_exc_text(e)})"
        diff = (last.get("diff") or "")
        ver = last.get("ver")
        attempts = last.get("attempts", 1)
        fixed = f"（自修复 {attempts - 1} 次后）" if attempts > 1 else ""
        if not diff.strip():
            err = str(last.get("err") or "").strip()
            if err:
                return (f"❌ 隔离实现失败：LLM 通道故障（{err[:160]}），试了 {attempts} 次。"
                        f"多为中转站/网络抖动——确认中转站健康后重试即可，不必改任务描述。")
            return (f"❌ 隔离实现未产生任何改动（试了 {attempts} 次，子 agent 始终没真正修改文件）。"
                    f"请把任务描述写得更具体、可执行（明确要改哪个文件、加什么）后再调 dev_isolated。")
        nlines = diff.count("\n")
        if ver and ver["ok"]:
            slug = re.sub(r"[^a-z0-9]+", "-", desc.lower()).strip("-")[:28] or "iso"
            branch = f"vorto/{slug}-{uuid.uuid4().hex[:8]}"
            res = await asyncio.to_thread(apply_diff_to_branch, repo_root, branch, diff, f"dev_isolated: {desc}")
            # 文档档位跳过了测试：结论里必须写明，不能和"测试通过"混为一谈（跳过 ≠ 通过）
            verdict = ("已隔离实现（**纯文档改动，已跳过测试——跳过≠通过**）"
                       if ver.get("skipped") else "已隔离实现且测试通过")
            if res["ok"]:
                return (f"✅ {verdict}{fixed}，落到新分支 {branch}（{nlines} 行，"
                        f"git checkout {branch} 查看，未碰 main）。" + _test_delta_note(diff))
            return f"✅ {verdict}{fixed}，但落分支失败：{res['error']}。diff {nlines} 行。"
        tail = (ver or {}).get("output", "")[-1000:]
        return (f"❌ 隔离实现完成但测试未过（试了 {attempts} 次）。失败输出尾部：\n{tail}\n"
                f"据此修正后重试（再调 dev_isolated）。diff {nlines} 行，未落地。")

    def _make_writer(test_cmd):
        """造一个'隔离实现子 agent'工厂：worktree 里 read+write+run_tests、自测到通过再交。"""
        # 仓库记忆是这里最值钱的落点：子 agent 每次都在全新 worktree 里从零开始，
        # 构建怪癖/测试命令/已知坑本来每次重踩——现在开局就带着。
        # 走 dev_subagent_system 统一拼装（别再各处各拼一份，那样加东西必漏）。
        from src.agents.repo_memory import dev_subagent_system
        extra = dev_subagent_system(repo_root, DEV_SUBAGENT_ROLE)

        def _mk(_desc):
            def _b(wt):
                return MainAgent(
                    build_read_tools(wt) + build_write_tools(wt) + [build_test_tool(wt, test_cmd)],
                    max_steps=16,
                    extra_system=extra,
                    capabilities=capabilities,
                    raise_llm_errors=True)   # 后台无人盯屏：通道故障要炸响，不许静默空结论
            return _b
        return _mk

    async def _implement_with_repair(desc, test_cmd):
        """隔离实现 desc + 自测；红了把失败输出拼回描述、换**全新 worktree** 再试，最多 _dev_attempts() 次。

        返回 {desc, diff, ver, attempts}。绿（ver.ok 且有 diff）即提前收口；都没绿则返回最后一次。
        每次都是干净 worktree + 全新子 agent（不背着上次的半成品），只把失败输出当线索喂进去。
        """
        import asyncio
        import uuid
        from src.agents.worktree import run_isolated_task
        mk = _make_writer(test_cmd)
        cur = desc
        last = {"desc": desc, "diff": "", "ver": None, "attempts": 0}
        for attempt in range(1, _dev_attempts() + 1):
            if attempt > 1:
                if last.get("err"):
                    # 通道故障（LLM 502/超时）≠ agent 没干活：如实播报死因，并给瞬时故障一点恢复窗口
                    _progress(f"↻ 「{desc[:32]}」上次 LLM 通道故障（{str(last['err'])[:80]}），"
                              f"第 {attempt} 次重试…")
                    await asyncio.sleep(min(10.0, 3.0 * (attempt - 1)))
                else:
                    _progress(f"↻ 「{desc[:32]}」上次未达标（红/无改动），第 {attempt} 次换全新 worktree 重试…")
            wid = "wt-" + uuid.uuid4().hex[:8]
            try:
                diff, conclusion, ver = await run_isolated_task(
                    repo_root, wid, cur, mk(cur), test_cmd=test_cmd)
            except Exception as e:  # noqa: BLE001
                # 带类型：真机上这条会直接进台账（"LLM 通道故障: …"）。裸 str(e) 遇上
                # KeyError/TimeoutError 这类只剩一个键名或空串，人拿到等于没拿到。
                last = {"desc": desc, "diff": "", "ver": None, "attempts": attempt,
                        "err": _exc_text(e)}
                continue
            # **留住子 agent 的结论**。此前它被丢进 `_c` 直接扔掉，于是"没产出任何改动"
            # 只剩五个字「无改动/出错」——人拿不到任何可行动的线索。
            # 真机代价：2026-07-24/25 同一个"给 README 补一行"的任务在 14 小时里重试了 8 次，
            # 每次都是这条路；子 agent 大概率每次都说了原因（比如找不到那个段落），全被扔了。
            # 这与本仓反复栽的"降级了但不告诉人"是同一个病。
            last = {"desc": desc, "diff": diff, "ver": ver, "attempts": attempt,
                    "conclusion": _clip_middle(str(conclusion or "").strip(), 600)}
            if ver and ver["ok"] and (diff or "").strip():
                _progress(f"✅ 「{desc[:32]}」实现并自测通过" + (f"（修复 {attempt - 1} 次后）" if attempt > 1 else ""))
                return last                                  # 绿了就收
            # 准备下一次的反馈：no-op（没改文件）和测试红是两码事，提示也不同
            if not (diff or "").strip():
                cur = _noop_retry_prompt(desc)               # 没改动：用更命令式提示逼它真动手
            else:
                cur = _repair_prompt(desc, (ver or {}).get("output", "")[-1500:])   # 红：带失败反馈再试
        _progress(f"❌ 「{desc[:32]}」试了 {_dev_attempts()} 次仍未过")
        return last

    async def _implement_parallel(descs, test_cmd):
        """并行隔离实现 descs（每个内部自修复重试），返回 (greens, lines)。lines=逐条 ✅/❌（标修复次数）。

        并发受 _dev_parallelism() 上限约束（信号量）：descs 很多时也只同时跑 N 个、其余排队，
        防一次性 fan-out 几十个 worktree+LLM 打爆中转站/磁盘——所有子任务仍都会被处理，只是不再挤在一起。
        """
        import asyncio
        cap = _dev_parallelism()
        _progress(f"⚙️ 并行隔离实现 {len(descs)} 个子任务中"
                  f"（各自起 worktree 实现+自测；最多 {cap} 个同时跑）…")
        sem = asyncio.Semaphore(cap)

        async def _bounded(t):
            async with sem:
                # 每块单独计量：并行子任务的用量也算得进来（usage_scope 绑的是可变 dict，
                # create_task 拷贝 context 时子任务拿到同一引用）。这是"哪块贵"的数据来源。
                from src.llm.client import usage_scope
                with usage_scope() as used:
                    r = await _implement_with_repair(t, test_cmd)
                if isinstance(r, dict):
                    r["tokens"] = int(used.get("total_tokens") or 0)
                return r

        results = await asyncio.gather(*[_bounded(t) for t in descs])
        lines, greens = [], []
        for r in results:
            att = r.get("attempts", 1)
            green = r.get("ver") and r["ver"]["ok"] and (r.get("diff") or "").strip()
            if green:
                lines.append(f"· {r['desc']}：✅ 通过" + (f"（修复 {att - 1} 次后）" if att > 1 else ""))
                greens.append(r)
            elif r.get("err"):
                lines.append(f"· {r['desc']}：❌ LLM 通道故障（{str(r['err'])[:80]}）"
                             + (f"（试了 {att} 次）" if att > 1 else ""))
            elif not (r.get("diff") or "").strip():
                lines.append(f"· {r['desc']}：{_noop_note(r.get('conclusion'))}"
                             + (f"（试了 {att} 次）" if att > 1 else ""))
            else:
                lines.append(f"· {r['desc']}：❌ 未过（试了 {att} 次）")
        return greens, lines

    async def _dependent_with_repair(branch, desc, title, test_cmd):
        """在 branch 之上接力实现一个（有依赖的 / resume 补跑的）子任务 + 自测；红了带失败反馈、换全新
        worktree 再试，最多 _dev_attempts() 次。绿则就地提交（推进 branch）后返回。
        返回 run_dependent_on_branch 的结果 dict（附 attempts）。desc=实现描述、title=展示名。"""
        import uuid
        from src.agents.worktree import run_dependent_on_branch
        mk = _make_writer(test_cmd)
        base = desc
        cur = base
        msg = f"dev_auto(dep): {title}"
        r = {"ok": False, "output": "未尝试", "attempts": 0}
        for attempt in range(1, _dev_attempts() + 1):
            _progress(f"🔗 依赖接力实现「{title}」" + (f"（第 {attempt} 次修复重试）" if attempt > 1 else "…"))
            wid = "wt-" + uuid.uuid4().hex[:8]
            r = await run_dependent_on_branch(repo_root, wid, branch, cur, mk(None), msg, test_cmd)
            r["attempts"] = attempt
            if r["ok"]:
                return r
            cur = _repair_prompt(base, (r.get("output") or "")[-1500:])
        return r

    async def _dev_parallel(args: dict) -> str:
        import asyncio
        import uuid
        from src.agents.worktree import apply_diffs_to_branch

        tasks = args.get("tasks") or args.get("descriptions") or []
        if isinstance(tasks, str):
            tasks = [tasks]
        tasks = [str(t).strip() for t in tasks if str(t).strip()][:5]   # 最多 5，防失控
        if not tasks:
            return "dev_parallel 需要 tasks（相互独立的子任务字符串列表）。"
        sel = str(args.get("test") or "").strip()
        from src.agents.test_detect import detect_test_cmd
        test_cmd = detect_test_cmd(repo_root, sel)          # 按仓库类型探测（pytest/npm/go/cargo/make）

        greens, lines = await _implement_parallel(tasks, test_cmd)
        if not greens:
            return f"并行 {len(tasks)} 个子任务：无通过测试的改动。\n" + "\n".join(lines)
        branch = "vorto/parallel-" + uuid.uuid4().hex[:8]
        _progress(f"📦 {len(greens)} 块通过 → 落分支 {branch} 并跑集成测试中…")
        res = await asyncio.to_thread(
            apply_diffs_to_branch, repo_root, branch,
            [(g["diff"], f"dev_parallel: {g['desc']}") for g in greens],
            test_cmd)                                    # 落完在集成分支上再跑一遍全量，抓"单独绿合起来红"
        head = f"并行 {len(tasks)} 个子任务：{len(greens)} 通过测试。"
        if not res["applied"]:
            return head + "落分支失败。\n" + "\n".join(lines)
        # 自测绿但落分支时与其它块**文本冲突被跳过**的块，必须如实点名——否则用户以为都进去了、
        # 实际悄悄丢了一块（与 dev_auto 的 #117 丢块诚实报告对齐；此前 dev_parallel 漏了这一半）。
        dropped = res.get("failed") or []
        dropped_note = ""
        if dropped:
            dropped_note = (f"\n⚠️ {len(dropped)} 块虽自测绿但**未能干净落分支**（已跳过，"
                            f"仅落地/验证实际应用的部分）；逐条原因如下：\n"
                            + "\n".join(f"  · {d.get('msg', '?')}：{(d.get('error') or '')[:120]}"
                                        for d in dropped))
        integ = res.get("integration")
        if integ and not integ["ok"]:                    # 各块单独绿、但合到一起红 → 如实说，别谎报全绿
            tail = integ["output"][-1200:]
            note = (f"⚠️ {len(res['applied'])} 块已落到 {branch}，但**集成后全量测试未过**"
                    f"（单独绿、合起来红，多为语义冲突/相互破坏）。失败尾部：\n{tail}\n"
                    f"分支已保留待修：git checkout {branch}，据失败修正后再集成。")
        elif integ and integ["ok"]:
            note = (f"✅ {len(res['applied'])} 块落到 {branch} 且**集成后全量测试通过**（git checkout 查看，未碰 main）。"
                    + _test_delta_note("\n".join(g["diff"] for g in greens)))
        else:                                            # 没跑集成测试（理论上 test_cmd 恒有，留兜底）
            note = f"{len(res['applied'])} 块落到 {branch}（git checkout 查看，未碰 main）。"
        return head + note + dropped_note + "\n" + "\n".join(lines)

    async def _open_pr_for_branch(branch: str, task: str, body: str, base: str) -> str:
        """dev_auto 集成绿后、经确认把分支 push 并开 PR。confirm 缺失/被拒/失败都给清楚说明、不抛。"""
        import asyncio
        if confirm is None:
            return ("\n（本环境未接确认门，未自动开 PR；分支已就绪，可用 open_pr 工具手动开。）")
        title = f"dev_auto: {task[:60]}"
        _emit_diff(f"待开 PR 的改动：{branch} → {base}", branch, base)
        if not await confirm(f"把 {branch} push 到远端并对 {base} 开 PR？\n  标题：{title}"):
            return f"\n（已取消开 PR；分支 {branch} 保留，可稍后手动 open_pr。）"
        _progress(f"🚀 push {branch} 并对 {base} 开{'（draft）' if draft_pr else ''} PR…")
        from src.agents.vcs import push_and_open_pr
        res = await asyncio.to_thread(push_and_open_pr, repo_root, branch, title, body[:4000],
                                      base, "origin", draft_pr)
        if res.get("ok") and res.get("url"):
            return f"\n🎉 已开 PR：{res['url']}"
        if res.get("pushed"):
            return f"\n（已 push {branch}，但开 PR 失败：{res.get('error')}。可手动 gh pr create。）"
        return f"\n（开 PR 失败：{res.get('error')}；分支 {branch} 保留。）"

    async def _run_review_gate(branch: str, base: str, test_cmd) -> tuple:
        """薄封装：把"依赖接力修复"作为 repair 注入 review.run_gate（挑刺→修→重审），返回 (note, blocked)。

        reviewer 按 review.PERSPECTIVES 造多份（同一套工具+证据铁律，各配一只聚焦镜头）并行
        对抗审查；视角集由 VORTOCODE_DEV_REVIEW_PERSPECTIVES 控制（默认全部）。
        """
        import uuid
        from src.agents import review as _review
        from src.agents.worktree import run_dependent_on_branch
        mk = _make_writer(test_cmd)

        def _make_reviewer(lens: str):
            """一只镜头一个 reviewer：在临时 worktree 检出分支，启动带工具的挑刺子 agent。
            放在 main_agent 侧避免 review 反向导入。"""

            async def _review_branch(_repo_root: str, _branch: str, _base: str, *, llm=None,
                                     test_cmd=None, guidelines: str = "", max_steps: int = 8) -> list:
                from src.agents.worktree import _git, _worktrees_dir, remove_worktree

                path = _worktrees_dir(_repo_root) / ("wt-review-" + uuid.uuid4().hex[:8])
                path.parent.mkdir(parents=True, exist_ok=True)
                if path.exists():
                    remove_worktree(_repo_root, path)
                add = _git(_repo_root, "worktree", "add", str(path), _branch, check=False)
                if add.returncode != 0:
                    return []
                try:
                    diff = _review._branch_diff(_repo_root, _base, _branch)
                    if not diff.strip():
                        return []
                    tools = build_read_tools(str(path)) + [build_test_tool(str(path), test_cmd)]
                    extra = (_review._REVIEWER_SYSTEM
                             + (f"\n\n{lens}" if lens else "")
                             + (f"\n\n【本仓库审查规范】\n{guidelines}" if guidelines else ""))
                    agent = MainAgent(tools, llm=llm, max_steps=max_steps, extra_system=extra,
                                      capabilities=capabilities)
                    prompt = (f"审查分支 {_branch}（相对 {_base}）的以下改动。只报 P0/P1、每条带验证证据、"
                              f"用 run_tests 复现你怀疑的问题，最后只输出 JSON 数组：\n\n```diff\n{diff}\n```")
                    try:
                        from src.agents.taint import merge_nested_taint
                        with merge_nested_taint():
                            reply = await agent.run_turn(prompt, mode="build")
                    except Exception:  # noqa: BLE001
                        return []
                    return _review.parse_findings(reply)
                finally:
                    remove_worktree(_repo_root, path)

            return _review_branch

        async def _repair(fix_desc: str) -> None:
            await run_dependent_on_branch(repo_root, "wt-" + uuid.uuid4().hex[:8], branch,
                                          fix_desc, mk(None), "dev_auto(review-fix)", test_cmd)

        reviewers = {name: _make_reviewer(_review.PERSPECTIVES[name])
                     for name in _review.dev_review_perspectives()}
        return await _review.run_gate(repo_root, branch, base, test_cmd=test_cmd,
                                      repair=_repair, reviewers=reviewers, progress=_progress)

    def _branch_exists(branch: str) -> bool:
        import subprocess
        return subprocess.run(["git", "-C", str(repo_root), "rev-parse", "--verify", "--quiet", branch],
                              capture_output=True, text=True).returncode == 0

    async def _execute_plan(dp, test_cmd, out: list) -> str:
        """从一个（部分或全新的）DevPlan 跑到完成——fresh（全 pending）与 resume（部分 landed）共用一套。

        每个块状态转换都 **write-ahead 落盘**（先标记再干活/先干活再落地），崩溃时计划文件如实反映进度、
        绝不超前标 landed；resume 据此跳过已 landed、只重跑未完成。out 累积展示文本，返回最终展示串。
        """
        import asyncio

        from src.llm.client import usage_scope
        import types
        import uuid
        from src.agents import dev_plan as _dp
        from src.agents.decompose import topo_order
        from src.agents.worktree import apply_diffs_to_branch, ensure_branch, verify_branch

        def _fmt_runtime(integ: dict) -> str:
            """把集成验证里的运行时验证结果渲染成逐条 ✅/❌（红的带输出尾部）。无运行时验证则空串。"""
            rt = integ.get("runtime") or []
            if not rt:
                return ""
            lines = ["\n运行时验证:"]
            for rc in rt:
                mark = "✅" if rc.get("ok") else "❌"
                lines.append(f"  {mark} {rc.get('name')}: {rc.get('cmd')}")
                if rc.get("screenshot_path"):
                    lines.append(f"     screenshot: {rc.get('screenshot_path')}"
                                 "（read_file 该路径可直接查看截图）")
                if not rc.get("ok"):
                    lines.append(f"     {(rc.get('output') or '')[-300:]}")
            return "\n".join(lines)

        branch = dp.branch

        def _save():
            _dp.save_plan(repo_root, dp)

        # 1) 独立块：全新时并行实现 + 批量落分支（建分支）；resume 时（分支已在）逐个在分支上接力补跑。
        todo_ind = dp.pending("independent")
        if todo_ind:
            exists = _branch_exists(branch)
            for b in todo_ind:
                b.status = "running"
            _save()                                          # write-ahead：先标 running 再实现
            if not exists:
                cap = _dev_parallelism()
                _progress(f"⚙️ 并行隔离实现 {len(todo_ind)} 个独立子任务中（最多 {cap} 个同时跑）…")
                sem = asyncio.Semaphore(cap)

                async def _impl(b):
                    async with sem:
                        # 每块单独计量（含全部重试与子 agent）：usage_scope 绑的是可变 dict，
                        # create_task 拷贝 context 时子任务拿到同一引用，并行也算得进来。
                        with usage_scope() as used:
                            r = await _implement_with_repair(b.desc, test_cmd)
                        return b, r, int(used.get("total_tokens") or 0)

                results = await asyncio.gather(*[_impl(b) for b in todo_ind])
                items = []
                for b, r, spent in results:
                    b.attempts = r.get("attempts", 1)
                    b.tokens = spent
                    green = r.get("ver") and r["ver"]["ok"] and (r.get("diff") or "").strip()
                    if green:
                        if r["ver"].get("skipped"):   # 文档档位：绿是"没跑测试"的绿，台账要记
                            b.note = "跳过测试（纯文档改动，跳过≠通过）"
                        items.append((r["diff"], f"dev_auto[{b.id}]: {b.desc}"))
                    else:
                        b.status = "failed"
                        if r.get("err"):               # 死因透传进台账：外伤（通道）别记成内科（no-op）
                            b.note = f"LLM 通道故障: {str(r['err'])[:120]}"
                        elif not (r.get("diff") or "").strip():
                            # 把子 agent 自己说的原因带出来——那通常就是"为什么没动手"的答案
                            # （找不到目标段落 / 认为已经满足 / 理解成了别的任务）。
                            b.note = _noop_note(r.get("conclusion"))
                        else:
                            b.note = "自测未过"
                _save()                                      # 落地前先记下哪些没绿
                apply_res = await asyncio.to_thread(apply_diffs_to_branch, repo_root, branch, items, None)
                # 只以 applied 为**白名单**判 landed——绝不靠"不在 failed 就是 landed"反推：
                # 整体 apply 失败（如 worktree add 挂了）会返回 applied=[]、failed=[{"msg":"(worktree add)"}]，
                # 此时绿块 msg 既不在 applied 也不在 failed，反推法会把它们全误标 landed → 污染计划、
                # dev_resume 跳过实际没落地的块（违反 write-ahead/不超前标记）。
                applied_msgs = set(apply_res.get("applied", []))
                # 逐块留下**真实原因**。此前这里只留 msg 集合、把 error 丢了，于是任何落分支失败
                # 都被硬写成"文本冲突"——真机 2026-07-26 撞到的其实是 `commit 失败: Author identity
                # unknown`（新机器没配 git user.name/email），报成文本冲突把人引向完全错的方向。
                failed_errs = {f.get("msg"): str(f.get("error") or "").strip()
                               for f in apply_res.get("failed", [])}
                for b, r, _spent in results:                 # results 是三元组（见上面 _impl）
                    if b.status == "failed":
                        continue
                    b.status, b.note = land_note(f"dev_auto[{b.id}]: {b.desc}",
                                                 applied_msgs, failed_errs)
                _save()                                      # 真提交后才标 landed（不超前）
            else:
                for b in todo_ind:                           # resume：分支已存在，逐个在其上补跑
                    with usage_scope() as used:              # 续跑同样烧钱，同样要计量
                        r = await _dependent_with_repair(branch, b.desc, b.title or b.id, test_cmd)
                    b.attempts = r.get("attempts", 1)
                    b.tokens = (b.tokens or 0) + int(used.get("total_tokens") or 0)
                    if r["ok"]:
                        b.status, b.note = "landed", ""
                    else:
                        b.status, b.note = "failed", _fail_note(r)
                    _save()

        ind = dp.independent()
        if ind:
            landed_n = sum(1 for b in ind if b.landed)
            out.append(f"\n【独立批】{landed_n}/{len(ind)} 落到分支：")
            for b in ind:
                if b.landed:
                    skipped = (b.note or "").startswith("跳过测试")
                    out.append(f"  · {b.desc}：✅" + ("（**已跳过测试：纯文档，跳过≠通过**）"
                                                     if skipped else ""))
                elif (b.note or "").startswith("自测绿但"):  # 自测绿但没落成 → 如实点名带原因（#123 诚实性）
                    out.append(f"  · {b.desc}：⚠️ {b.note}")
                else:
                    out.append(f"  · {b.desc}：❌ {b.note or '未过'}")

        # 2) 依赖块：拓扑序逐个在 branch 之上接力实现+自测，绿则就地提交（推进 tip 给下一个看）。
        todo_dep = dp.pending("dependent")
        if todo_dep:
            if not _branch_exists(branch):
                await asyncio.to_thread(ensure_branch, repo_root, branch, dp.base)  # 无独立基底也给依赖一个
            satisfied = set(dp.satisfied_ids) | dp.landed_ids()
            shims = [types.SimpleNamespace(id=b.id, dependencies=b.deps, block=b) for b in todo_dep]
            out.append("\n【依赖接力】按拓扑序在分支上逐个实现：")
            for shim in topo_order(shims, satisfied):
                b = shim.block
                disp = b.title or b.desc[:40] or b.id
                b.status = "running"
                _save()                                      # write-ahead
                with usage_scope() as used:                  # 接力块同样按块计量
                    r = await _dependent_with_repair(branch, b.desc, disp, test_cmd)
                b.attempts = r.get("attempts", 1)
                b.tokens = int(used.get("total_tokens") or 0)
                if r["ok"]:
                    b.status, b.note = "landed", ""
                    out.append(f"  · {disp}：✅ 已接力提交"
                               + (f"（修复 {b.attempts - 1} 次后）" if b.attempts > 1 else ""))
                else:
                    b.status, b.note = "failed", _fail_note(r)
                    out.append(f"  · {disp}：❌ 试了 {b.attempts} 次仍未过：{b.note}")
                _save()

        landed_ind = sum(1 for b in dp.independent() if b.landed)
        dep_done = sum(1 for b in dp.dependent() if b.landed)

        # 3) 最终集成验证：整条分支跑一遍全量
        if landed_ind == 0 and dep_done == 0:
            dp.status = "failed"
            _save()
            return "\n".join(out) + "\n\n没有任何子任务落地（都没过自测/或落分支时相互冲突被丢）；建议拆细或用 dev_isolated 逐个做。"
        _progress(f"🔍 对整条分支 {branch}（{landed_ind} 独立 + {dep_done} 依赖）跑最终集成测试"
                  f"（含运行时验证，如分支配了 .vortocode/verify.yaml）中…")
        # runtime=True：verify_branch 会在**目标分支的 worktree 内**读 verify.yaml 决定跑不跑运行时验证
        # ——从分支自己的配置读（本次改动的 verify.yaml 生效），坏配置判红、没配则只跑单测。
        integ = await asyncio.to_thread(
            verify_branch, repo_root, branch, test_cmd, "wt-verify-" + uuid.uuid4().hex[:8], True)
        dp.integration = integ
        if integ["ok"]:
            dp.status = "integrated"
            _save()
            passed = "**集成后全量测试 + 运行时验证通过**" if (integ.get("runtime")) else "**集成后全量测试通过**"
            done = (f"\n✅ 全部落到 {branch}（{landed_ind} 独立 + {dep_done} 依赖）且{passed}"
                    f"（未碰 main，git checkout {branch} 查看）。")
            done += _fmt_runtime(integ)
            # 诚实提示测试增量：从**整条分支相对 base 的实际 diff**算（独立批 + 依赖接力提交都覆盖）。
            changed = await asyncio.to_thread(_branch_changed_files, repo_root, dp.base, branch)
            if changed is not None:
                done += _test_delta_msg(sum(1 for p in changed if _is_test_path(p)))
            out.append(done)
            # PR 前对抗审查段：**仅在真要开 PR 时**跑（名副其实的"PR 前"，codex 审 #120 P1）。
            if dp.want_pr and _dev_review_enabled():
                note, blocked = await _run_review_gate(branch, dp.base, test_cmd)
                dp.review = {"note": note, "blocked": bool(blocked)}
                _save()
                out.append(note)
                if blocked:
                    return "\n".join(out)                    # 审查未过 → 不开 PR、分支保留待人工
            if dp.want_pr:                                    # 集成绿 + 审查过 → 经确认 push+开 PR
                pr_note = await _open_pr_for_branch(branch, dp.task, "\n".join(out), dp.base)
                out.append(pr_note)
                dp.pr = {"note": pr_note.strip()}
                dp.status = "done"
                _save()
        else:
            dp.status = "integration_failed"
            _save()
            rt = integ.get("runtime") or []
            if rt and any(not rc.get("ok") for rc in rt):
                # 单测过了、栽在运行时验证：别拿绿的单测输出当"失败尾部"误导，直接列运行时结果。
                detail = f"集成单测过了，但**运行时验证未过**。{_fmt_runtime(integ)}"
            else:
                detail = f"**集成后全量测试未过**。失败尾部：\n{integ['output'][-1000:]}"
            out.append(f"\n⚠️ 已落到 {branch}（{landed_ind} 独立 + {dep_done} 依赖），但{detail}\n"
                       f"分支保留待修：git checkout {branch}（可修完再 dev_resume({dp.plan_id})）。")
            if dp.want_pr:
                out.append("（集成验证未过，未自动开 PR——先把分支修绿再开。）")
        return "\n".join(out)

    async def _dev_auto(args: dict) -> str:
        """自动分解大任务 → 无依赖子任务并行隔离实现 → **有依赖的按拓扑序在同一分支上逐个接力实现**
        （检出该分支、看得见前面的改动、自测绿才提交、推进 tip 给下一个看）→ 最后对整条分支跑一遍
        集成测试。端到端把大任务做完，不再只做独立那一半就停。全程不碰 main/工作区。

        分解结果 + 逐块状态 **write-ahead 落盘**到 .vortocode/dev_plans/<id>.json（C1）：中断后可用
        dev_resume(plan_id) 从断点续跑；计划文件也是进度播报/IM /status 的数据源、可手改后重跑。
        给 open_pr=true 且接了确认门：集成绿后经确认把分支 push 并开 PR（"一句话→PR"闭环）。"""
        import uuid
        from src.agents import dev_plan as _dp
        from src.agents.decompose import decompose_for_parallel, describe_subtask

        task = str(args.get("task") or args.get("goal") or args.get("description") or "").strip()
        if not task:
            return "dev_auto 需要 task（要自动分解并实现的大任务）。"
        want_pr = _truthy(args.get("open_pr") or args.get("pr") or False)
        # 预检放在**分解之前**：环境不合格就别烧 LLM。真机 2026-07-26 的教训是
        # "实现绿、自测绿、最后 commit 挂在一条 git config 上"——那一整轮工作全白做。
        # heal=True：白名单内能自动处置的（如没配 git 提交身份）就地修好，别为一条
        # `git config` 拦住整条流水线。修了什么会经 _progress 如实说出来，不静默。
        if (blockers := preflight_dev(repo_root, want_pr=want_pr, on_heal=_progress)):
            return ("❌ 环境预检未过，未开跑（省下白做一轮的时间）：\n\n"
                    + "\n\n".join(f"· {b}" for b in blockers)
                    + "\n\n修好后重发这条任务即可。")
        # 调用方（如后台任务 worker）可**指定 plan_id**——这样它能在 dev_auto 返回后按这个确定的 id
        # load_plan 拿到本次的 branch，不必靠"全局最新 plan"猜（并发多任务时会串单，见 #128 评审）。
        pinned_pid = str(args.get("plan_id") or "").strip() or None
        base = _detect_base_branch(repo_root)               # PR base：dev_auto 出发时所在分支
        sel = str(args.get("test") or "").strip()
        from src.agents.test_detect import detect_test_cmd
        test_cmd = detect_test_cmd(repo_root, sel)          # 按仓库类型探测（pytest/npm/go/cargo/make）
        _progress("🧩 自动分解任务中…")
        # 阶段计量：分解与执行分开记，才答得了"钱花在哪个阶段"——那是模型分层
        # （规划用旗舰、执行用中档）唯一靠谱的依据。先量后动。
        from src.llm.client import usage_scope
        try:
            with usage_scope() as decompose_usage:
                plan = await decompose_for_parallel(task)
        except Exception as e:  # noqa: BLE001
            return f"(任务分解出错: {_exc_text(e)}；可改用 dev_parallel 手动给独立子任务)"
        _log_stage_usage("decompose", decompose_usage)
        descs, deferred = plan["descriptions"], plan["deferred"]
        if not descs and not deferred:
            return f"分解出 {plan['total']} 个子任务，但没拿到可实现的描述；建议用 dev_isolated 逐个做。"

        branch = "vorto/auto-" + uuid.uuid4().hex[:8]
        dp = _dp.DevPlan.new(task, branch, base, test_sel=sel, want_pr=want_pr, plan_id=pinned_pid)
        for i, d in enumerate(descs):
            dp.blocks.append(_dp.Block(id=f"ind-{i}", kind="independent", desc=d))
        for s in deferred:
            sid = str(getattr(s, "id", None) or ("dep-" + uuid.uuid4().hex[:6]))
            dp.blocks.append(_dp.Block(
                id=sid, kind="dependent", desc=describe_subtask(s),
                title=str(getattr(s, "title", "") or ""),
                deps=[str(x) for x in (getattr(s, "dependencies", None) or [])]))
        dp.satisfied_ids = [str(getattr(s, "id", None)) for s in plan["independent"]
                            if getattr(s, "id", None) is not None]
        _dp.save_plan(repo_root, dp)                         # write-ahead：分解完成即落文件

        out = [f"已把任务分解为 {plan['total']} 个子任务：{len(descs)} 个独立(并行) + {len(deferred)} 个有依赖(接力)。",
               f"（计划已存盘 plan_id={dp.plan_id}；中断后可 dev_resume 续跑）"]
        with usage_scope() as execute_usage:
            result = await _execute_plan(dp, test_cmd, out)
        _log_stage_usage("execute", execute_usage)
        return result

    async def _pr_fix(args: dict) -> str:
        """读一个 PR 的 review 评论 + CI 状态 → 在其分支上逐条修（自测）→ 绿则 push（确认门）。

        硬闸：分支必须匹配 vorto/*，绝不碰 main/master/其它分支。'人在合并口'之前的往返自动化。"""
        import asyncio
        from src.agents.vcs import failed_check_log_excerpts, pr_feedback, push_branch
        from src.agents.test_detect import detect_test_cmd

        ref = str(args.get("pr") or args.get("branch") or args.get("ref") or "").strip()
        if not ref:
            return "pr_fix 需要 pr（PR 号）或 branch（vorto/* 分支名）。"
        fb = await asyncio.to_thread(pr_feedback, repo_root, ref)
        if not fb.get("ok"):
            return f"读 PR 反馈失败：{fb.get('error')}"
        branch = fb.get("branch") or (ref if ref.startswith("vorto/") else "")
        if not branch.startswith("vorto/"):              # 硬闸：只修隔离流水线分支
            return (f"拒绝：pr_fix 只在 vorto/* 分支上修（PR 的 head 分支是 {branch or '未知'}）。"
                    f"绝不碰 main/其它分支。")
        comments, checks = fb.get("comments") or [], fb.get("failing_checks") or []
        if not comments and not checks:
            return f"PR #{fb.get('pr')}（{branch}）没有待办的 review 评论，CI 也没红——无需修。"
        # 把反馈拼成修复描述，喂到"在分支上接力实现+自测"的循环
        parts = ["按下面的 PR review 反馈与 CI 失败逐条修正（只改必要处、别引入无关改动）："]
        for c in comments[:20]:
            loc = f"（{c['path']}:{c['line']}）" if c.get("path") else ""
            parts.append(f"- [{c.get('author', '?')}]{loc} {c['body'][:300]}")
        for ck in checks[:10]:
            parts.append(f"- CI 失败：{ck['name']}（{ck.get('link', '')}）")
        log_result = await asyncio.to_thread(failed_check_log_excerpts, repo_root, checks)
        from src.agents.pr_doctor import classify_failed_checks, repair_templates
        classification = classify_failed_checks(checks, list(log_result.get("logs") or []))
        if classification.get("category") and classification.get("category") != "unknown":
            parts.append(
                f"- CI 类型判断：{classification.get('label')}；"
                f"建议：{classification.get('next_action')}"
            )
        for item in repair_templates(checks, list(log_result.get("logs") or []), classification)[:4]:
            slash = str(item.get("slash") or "")
            slash_part = f"；可执行动作：{slash}" if slash else ""
            parts.append(
                f"- 推荐修复模板：{item.get('title')}；"
                f"命令：{item.get('command')}{slash_part}；说明：{item.get('detail')}"
            )
        for item in (log_result.get("logs") or [])[:3]:
            loc = str(item.get("job_name") or "")
            if item.get("step_name"):
                loc = (loc + " > " if loc else "") + str(item.get("step_name"))
            if loc:
                parts.append(f"- CI 失败定位：{item.get('name') or 'check'} -> {loc}")
            excerpt = str(item.get("excerpt") or "").strip()
            if excerpt:
                parts.append(f"- CI 日志摘录：{item.get('name') or 'check'}\n{excerpt[:900]}")
        fix_desc = "\n".join(parts)
        sel = str(args.get("test") or "").strip()
        test_cmd = detect_test_cmd(repo_root, sel)
        _progress(f"🔧 按 PR #{fb.get('pr')} 的 {len(comments)} 条评论 / {len(checks)} 个失败检查在 {branch} 上修…")
        r = await _dependent_with_repair(branch, fix_desc, f"pr-fix #{fb.get('pr')}", test_cmd)
        if not r.get("ok"):
            return (f"❌ 按 PR 反馈修改后自测仍未过（试了 {r.get('attempts', 1)} 次）：{(r.get('output') or '')[-200:]}\n"
                    f"分支 {branch} 未推送。")
        # 绿了 → push（外向操作，走确认门）
        _emit_diff(f"pr_fix 待推送的改动：{branch}（对 origin/{branch}）", branch, f"origin/{branch}")
        if confirm is not None and not await confirm(
                f"已按 PR #{fb.get('pr')} 的反馈在 {branch} 上修好且自测通过，push 到远端更新 PR？"):
            return f"（已在本地 {branch} 修好并提交，但未 push——你取消了。）"
        _progress(f"🚀 push {branch} 更新 PR #{fb.get('pr')}…")
        pushed = await asyncio.to_thread(push_branch, repo_root, branch)
        if pushed["ok"]:
            return f"✅ 已按 PR #{fb.get('pr')} 的反馈修好、自测通过并 push 到 {branch}（PR 时间线可见新 commit）。"
        return f"已在 {branch} 本地修好，但 push 失败：{pushed['output']}"

    async def _dev_resume(args: dict) -> str:
        """从落盘的 dev_auto 计划断点续跑：已 landed 的块跳过，未完成的（pending/running/failed）重走，
        最后重跑集成验证 + （若原计划 open_pr）审查段与开 PR。不传 plan_id 则列出最近可续的计划。"""
        from src.agents import dev_plan as _dp
        from src.agents.test_detect import detect_test_cmd

        pid = str(args.get("plan_id") or args.get("id") or "").strip()
        if not pid:
            plans = _dp.list_plans(repo_root)
            if not plans:
                return "没有可续跑的计划（.vortocode/dev_plans/ 为空）。先用 dev_auto 起一个大任务。"
            lines = [f"· {p['plan_id']}：{p['status']} — {p['task'][:50]}" for p in plans[:10]]
            return "dev_resume 需要 plan_id。最近的计划：\n" + "\n".join(lines)
        dp = _dp.load_plan(repo_root, pid)
        if dp is None:
            return f"找不到计划 {pid}（.vortocode/dev_plans/{pid}.json 不存在或损坏）。dev_resume() 不带参可列出可续计划。"
        if dp.status == "done":
            return f"计划 {pid} 已完成（{dp.summary()}），无需续跑。"
        test_cmd = detect_test_cmd(repo_root, dp.test_sel)
        c = dp.counts()
        remaining = c["pending"] + c["running"] + c["failed"]
        _progress(f"↻ 续跑计划 {pid}（已 landed {c['landed']}，续跑未完成 {remaining} 块）…")
        out = [f"续跑计划 {pid}：{dp.task[:60]}",
               f"（已 landed {c['landed']} 块，续跑未完成的 {remaining} 块，不重做已落地部分）"]
        dp.status = "running"
        _dp.save_plan(repo_root, dp)
        return await _execute_plan(dp, test_cmd, out)

    return [
        Tool("dev_isolated",
             "在隔离 git worktree 里实现一个独立子任务 + 自测 + 跑测试验证；✅通过就自动落到一个"
             "vorto/<id> 新分支（绝不碰 main/工作区），❌带失败输出供修正。仅 build",
             {"description": "要在隔离工作区实现的子任务",
              "test": "可选，pytest 选择器，省略则跑全量 tests/"},
             _dev_isolated, read_only=False, required_capabilities=("host_process",)),
        Tool("dev_parallel",
             "并行实现：多个**相互独立**的子任务各起隔离 worktree 同时实现+自测+验证（互不冲突，"
             "红了带失败反馈自修复重试），绿块一并落到一个 vorto/parallel 新分支（不碰 main），"
             "**落分支后再跑一遍集成测试**抓'单独绿合起来红'，汇报各自 ✅/❌ 及集成结果。最多 5（仅 build）",
             {"tasks": "相互独立的子任务字符串列表",
              "test": "可选，pytest 选择器，省略则各自跑全量 tests/"},
             _dev_parallel, read_only=False, required_capabilities=("host_process",)),
        Tool("dev_auto",
             "把一个大任务**端到端**做完：自动分解→无依赖子任务并行隔离实现→**有依赖的按拓扑序"
             "在同一 vorto/auto 分支上逐个接力实现**（看得见前面的改动、自测绿才提交）→最后整条分支"
             "跑一遍集成测试。子任务红了都会带失败反馈自修复重试。不再只做独立那一半就停。绝不碰 main。"
             "给 open_pr=true 则集成通过后（经确认）把分支 push 并开 PR，一句话直达 PR。仅 build",
             {"task": "要自动分解并实现的大任务（自然语言）",
              "test": "可选，pytest 选择器",
              "open_pr": "可选，true 则集成绿后经确认 push 分支并开 PR"},
             _dev_auto, read_only=False, required_capabilities=("host_process",)),
        Tool("dev_resume",
             "从一个中断的 dev_auto 计划**断点续跑**：已落地(landed)的子任务块跳过、未完成的（含失败）重走，"
             "最后重跑集成验证（原计划要开 PR 的还会接着审查+开 PR）。10 个子任务断在第 7 个不用从头再来。"
             "不传 plan_id 则列出最近可续的计划。仅 build",
             {"plan_id": "要续跑的计划 id（dev_auto 起跑时会给出、存于 .vortocode/dev_plans/）；省略则列出可续计划"},
             _dev_resume, read_only=False, required_capabilities=("host_process",)),
        Tool("pr_fix",
             "读一个 PR 的 review 评论（含行级、已 resolved 的自动跳过）+ CI 失败检查，在其 **vorto/* 分支**上"
             "逐条修正 + 自测，绿了经确认 push 更新 PR。'人在合并口'之前的往返自动化。硬闸：只碰 vorto/* 分支。仅 build",
             {"pr": "PR 号（或用 branch 传 vorto/* 分支名）",
              "branch": "可选，vorto/* 分支名（与 pr 二选一）",
              "test": "可选，pytest 选择器"},
             _pr_fix, read_only=False, outward=True,
             required_capabilities=("host_process", "authenticated_outbound")),
    ]


def build_research_tools(repo_root: str, *, llm: Any = None,
                         max_steps: int = 12, max_parallel: int = 5, default_parallel: int = 2,
                         confirm: Any = None, on_progress: Any = None,
                         capabilities: Any = None) -> list[Tool]:
    """UI 无关的子 agent 委派工具（task / research_parallel）——给 Web/CLI 用。

    把一个大型只读调查甩给一个**只带 read_tools** 的隔离子 agent：它在独立上下文里
    读代码/搜仓库、返回简洁结论，**不挤占也不污染主 agent 的对话历史**（大调查不再把
    主上下文撑爆——配合对话压缩，是"扛大工程量"的另一条腿）。子 agent 无 task/写工具
    → 不会递归嵌套、绝不改文件。TUI 另有带进度回显的版本（self._chrome），此处是无 UI 版。
    llm 可注入（便于测试/共享客户端）；不传则子 agent 各自惰性建客户端（同 TUI）。

    **按名委派自定义角色**（`.vortocode/agents/*.md`，公司架构式分工）：可选 `agent` 参数
    指定角色——产品经理/评审/QA 等 read 型仍只读；`tools: dev` 型角色额外可用隔离 dev
    流水线真写代码（落 vorto/* 分支，绝不碰主区）。confirm/on_progress 只喂给 dev 面。
    """
    def _sub_for(agent_name: str):
        """按角色名装配子 agent；无角色名给默认只读研究员。返回 (sub, err)。"""
        if not agent_name:
            return MainAgent(build_read_tools(repo_root), llm=llm, max_steps=max_steps,
                             extra_system=(
                                 "你是只读研究子 agent：只用工具调研代码/仓库并返回**简洁结论**，绝不修改任何东西。"
                                 "读够信息就尽快收口，别把预算耗在重复读取上。"),
                             capabilities=capabilities), None
        from src.agents.subagents import registry_for
        reg = registry_for(repo_root)
        spec = reg.get(agent_name)
        if spec is None:
            avail = "、".join(reg.specs) or "（无——在 .vortocode/agents/ 放 <名>.md 定义角色）"
            return None, f"没有名为 {agent_name!r} 的子 agent。可用：{avail}"
        return build_subagent(repo_root, spec, llm=llm, confirm=confirm,
                              on_progress=on_progress, capabilities=capabilities), None

    async def _spawn(desc: str, agent_name: str = "") -> tuple[str, bool]:
        from src.agents.taint import is_tainted, merge_nested_taint
        sub, err = _sub_for(agent_name)
        if err:
            return err, is_tainted()
        # dev 型角色能产出写入（隔离流水线落 vorto/* 分支）。task 本身 read_only（plan 可用），
        # 不能让 dev 委派从 plan 门下偷渡——**过人闸**：无确认通道拒绝（fail-closed），
        # 有则问一次（headless 默认拒、--yes 放行；TUI/Web 弹确认），与 run_command 同一哲学。
        has_dev = any(t.startswith("dev_") for t in sub.tools)
        if has_dev:
            if confirm is None:
                return (f"角色 {agent_name} 是 dev 型（会经隔离流水线写代码），当前入口没有确认"
                        f"通道——已拒绝（fail-closed）。请在带确认的入口（TUI/Web/--yes）委派。",
                        is_tainted())
            if not await confirm(f"委派角色「{agent_name}」用隔离 dev 流水线实现：{desc[:120]}\n"
                                 f"（产出落 vorto/* 分支，不碰主工作区）"):
                return f"已取消：用户未放行 dev 型角色 {agent_name} 的委派。", is_tainted()
        mode = "build" if has_dev else "plan"
        with merge_nested_taint() as nested:
            try:
                result = (await sub.run_turn(desc, mode=mode)) or "(无结论)"
            except Exception as e:  # noqa: BLE001
                result = f"(子任务出错: {e})"
        return result, nested.child_tainted

    async def _task(args: dict) -> str:
        desc = str(args.get("description") or args.get("task") or "").strip()
        if not desc:
            return "task 需要 description（要委派给子 agent 的子任务）。"
        from src.agents.taint import mark_tainted
        result, child_tainted = await _spawn(desc, str(args.get("agent") or "").strip())
        if child_tainted:
            mark_tainted()
        return result

    async def _research_parallel(args: dict) -> str:
        import asyncio
        tasks = args.get("tasks") or args.get("descriptions") or []
        if isinstance(tasks, str):
            tasks = [tasks]
        cap = research_parallel_cap(args, default=default_parallel, maximum=max_parallel)
        tasks = [str(t).strip() for t in tasks if str(t).strip()][:cap]
        if not tasks:
            return "research_parallel 需要 tasks（字符串列表，每项一个独立子问题）。"
        agent_name = str(args.get("agent") or "").strip()
        spawned = await asyncio.gather(*[_spawn(t, agent_name) for t in tasks])
        if any(child_tainted for _, child_tainted in spawned):
            from src.agents.taint import mark_tainted
            mark_tainted()
        results = [result for result, _child_tainted in spawned]
        return "\n\n".join(f"【{t}】\n{r}" for t, r in zip(tasks, results))

    return [
        Tool("task",
             "把一个独立子任务委派给子 agent（隔离上下文、不污染主对话），返回它的结论；"
             "适合大型只读调查（读一堆文件/摸清某子系统）——别在主对话里逐个读，委派出去省上下文。"
             "可选 agent=<角色名> 用自定义角色（见系统提示【可用子 agent】；dev 型角色能用隔离流水线写代码）",
             {"description": "要委派给子 agent 的子任务",
              "agent": "可选：自定义角色名（.vortocode/agents/ 里定义；缺省=只读研究员）"},
             _task, read_only=True),
        Tool("research_parallel",
             "并行委派多个子 agent 同时处理**相互独立**的子问题，汇总各自结论（最多 5 个）；"
             "默认轻量最多 2 个；用户明确要求全面/多角度/并行深挖时，可传 max_parallel 和 reason 放宽。"
             "可选 agent=<角色名> 让全组用同一自定义角色",
             {"tasks": "独立子问题字符串列表",
              "max_parallel": "可选，并行子 agent 数；默认 2，需配合 reason 才能超过默认，硬上限 5",
              "reason": "可选；说明为什么需要超过默认并行度，如用户明确要求全面审查/多角度分析",
              "agent": "可选：自定义角色名（应用到本组全部子任务）"},
             _research_parallel, read_only=True),
    ]


_SUB_RULES = ("\n\n【子 agent 通用约束】你是被主 agent 委派的角色，只做角色职责内的事；"
              "完成后返回**简洁结论**（发现/建议/产出物指引），别复述过程。")
_DEV_RULES = ("你可以用 dev_isolated/dev_parallel 真正实现代码——它们在隔离 worktree 里做、"
              "自测绿才落 vorto/* 分支，绝不碰主工作区；除此之外你没有任何直接写文件的手段。")


def build_subagent(repo_root: str, spec: Any, *, llm: Any = None,
                   confirm: Any = None, on_progress: Any = None,
                   capabilities: Any = None) -> MainAgent:
    """按自定义角色定义装配一个子 agent。

    `src.agents.subagents` 只保留注册表/规格解析，避免反向导入 MainAgent 形成循环依赖。
    """
    from src.agents.permissions import load_permissions

    tools = build_read_tools(repo_root)
    extra = spec.system_prompt + _SUB_RULES
    if spec.tools == "dev":
        dev = [t for t in build_dev_tools(repo_root, on_progress=on_progress, confirm=confirm,
                                          capabilities=capabilities)
               if t.name in ("dev_isolated", "dev_parallel")]
        tools = tools + dev
        extra += _DEV_RULES
    # 项目级权限硬拦（.vortocode/permissions.yaml deny）必须继承，避免角色文件绕过项目规则。
    sub = MainAgent(tools, llm=llm, max_steps=spec.max_steps, extra_system=extra,
                    permissions=load_permissions(repo_root), capabilities=capabilities)
    if spec.model:
        try:
            sub.set_model(spec.model)
        except Exception:  # noqa: BLE001
            pass
    return sub
























def build_agent_tools(repo_root: str, *, confirm, on_progress: Optional[Callable[[str], None]] = None,
                      with_artifacts: bool = False, draft_pr: bool = False,
                      memory_source: str = "agent", memory_session_id=None,
                      capabilities: Any = None, with_web: bool = True,
                      # 出站投递面（send_image/send_file）。默认跟随 with_web 保持既有行为；
                      # 单独可控是因为它们与"读外网"是**两件事**：那两个要过确认门，无人值守
                      # 问不到人必拒——给了只会让模型反复撞一堵必然拒绝的墙。
                      with_im_media: Optional[bool] = None,
                      with_dev: bool = True, with_cron: bool = True,
                      on_diff: Optional[Callable[[str, str], None]] = None) -> list[Tool]:
    """标准主 agent 工具集（headless CLI 与 Web /agent 共用，保证二者"同源"、不漂移）。

    此前 cli._build_headless_agent 与 web._new_agent 各自手写同一串 build_*，极易漂移
    （工具清单/顺序/confirm 语义不一致）。收敛到这里一处装配：
      read（行段/grep/glob/git 只读/语义导航）+ research（只读子 agent 委派）+ web（fetch/search）
      + memory（跨会话长期记忆）+ skill（use_skill/save_skill）
      [+ artifact（发布/列制品，仅 with_artifacts）] + dev（隔离实现/并行，绿落 vorto 分支）
      + command（run_command）+ pr（open_pr）。
    confirm: async (message)->bool 确认门。**必须是 make_confirm_gate 包过的**（见 build_session）
      ——"要不要问、能不能免"由内核判，端只负责怎么问人。别再往这里塞裸 confirm。
    on_progress: dev 流水线进度回调（长任务边跑边播）。
    with_artifacts: 是否含制品工具（Web 有查看页故开；headless CLI 无浏览器故关）。
    with_web: 是否含联网工具。**无人值守会话必须传 False** —— web_fetch 是 read_only、不过确认门，
      而 GET 的 query string 就是一条外传通道；无人值守下系统提示可能被本地文件（repo.md /
      BACKLOG.md / HEARTBEAT.md）污染，一旦模型被诱导去 fetch 攻击者的 URL，就是零人工介入的
      静默外传。无人值守本来也不需要出网（领 BACKLOG 干活、跑评测都不用）。
    TUI 不走本工厂——它用富 UI 版写/dev/command 工具（着色 diff + ConfirmScreen），刻意不同源。
    注：调用方（CLI/Web）应把 `skill_catalog(repo_root)` 注入 extra_system，模型才知道有哪些技能可 use_skill。
    """
    tools = (build_read_tools(repo_root)
             + build_research_tools(repo_root, confirm=confirm, on_progress=on_progress,
                                    capabilities=capabilities)
             + build_memory_tools(repo_root, confirm, source=memory_source,
                                  session_id=memory_session_id)
             + build_skill_tools(repo_root, confirm))
    if with_web:
        tools += build_web_tools() + build_screenshot_tool(repo_root)
    if with_artifacts:
        from src.web.artifacts import build_artifact_tools    # 惰性导入：避免 agents 层在导入期硬依赖 web

        # 制品是**对外发布**（写盘 + 经 /artifact/<id> 提供服务）、删除不可逆 → 必须过确认门。
        # 此前这里根本没传 confirm，publish/delete 完全绕过了 gate（自审逮到）。
        # artifact 的 confirm 签名是 (preview, is_update)，这里适配成内核 gate 的 (message)。
        async def _art_publish(preview: dict, is_update: bool) -> bool:
            # "首次发布问、清白更新静默、污点更新仍问"的策略**统一在 build_artifact_tools._publish 里**
            # （工具边界，覆盖所有端）。这里只做"被调到就过内核 gate"——不再各写一遍污点判定。
            what = "更新" if is_update else "发布"
            return bool(await confirm(
                f"{what}制品「{preview.get('title') or preview.get('id')}」？"
                f"它会被写盘并经 /artifact/ 对外提供访问。"))

        async def _art_delete(preview: dict) -> bool:
            return bool(await confirm(
                f"删除制品「{preview.get('title') or preview.get('id')}」？此操作不可逆。"))

        tools += build_artifact_tools(repo_root, confirm=_art_publish,
                                      confirm_delete=_art_delete)
    if with_web if with_im_media is None else with_im_media:
        tools += build_im_media_tools(repo_root, confirm)
    if with_cron:
        # **无人值守必须传 False**：cron 作业能创建 cron 作业 = 自我复制驻留，等于 agent 可以
        # 自授「周期性无人值守执行」这项权限。研究员档同样不给（那是运维面，不是资料助理的活）。
        tools += build_cron_tools(repo_root, confirm)
    if with_dev:
        tools += (build_dev_tools(repo_root, on_progress=on_progress, confirm=confirm,
                                  draft_pr=draft_pr, capabilities=capabilities, on_diff=on_diff)
                  + build_pr_tool(repo_root, confirm))
    else:
        # 没有 dev 流水线的档（研究员）**必须另给写路径**：serve 侧刻意没有直写工具，
        # 写操作一律走隔离流水线——把流水线砍掉却不补，就等于连写个抓取脚本都做不到，
        # 那句"可以写代码来更好地帮助收集资料"就成了空话。
        # build_write_tools 是**根限定**的（`..` 越界拦死），而这一档的 repo_root 就是它自己的
        # 沙盒工作区，不是主项目——所以"在自己家里随便写"是安全的，无需逐次确认
        #（与一次性 worktree 里的子 agent 同一个道理：改动只落在自己的地盘）。
        tools += build_write_tools(repo_root)
    # run_command 与 dev 分开：研究员**要**能跑自己写的抓取/清洗脚本（沙箱内），
    # 但不该有改主项目代码、落分支、开 PR 的能力。把两者绑在一起会逼人二选一：
    # 要么给全套（权限过大），要么连脚本都跑不了（等于废了"写代码辅助收集资料"）。
    tools += build_command_tool(repo_root, confirm)
    return tools


# ── MainAgent 已搬到 src/agents/agent_loop.py ────────────────────────────────
# 因果调过来了：以前是"工具工厂要用 MainAgent，所以搬不走"；现在 MainAgent 自己在叶子位置，
# 依赖方向 main_agent（工具工厂）→ agent_loop（MainAgent）→ tools/*（无状态工具），不成环。
# 下面逐个再导出，`from src.agents.main_agent import MainAgent` 这类存量写法照常工作。
from src.agents.agent_loop import (  # noqa: F401,E402
    MainAgent,
    SYSTEM_TEMPLATE,
    DEV_SUBAGENT_ROLE,
    # 模块级常量也要一并再导出：测试直接 import 它们来断上下文预算/折叠策略
    # （漏了两个当场被 test_main_agent.py 的 ImportError 抓住）。这里是**照
    # agent_loop 的模块级名字全量对齐**补的，不是逐个撞红了再加。
    _CONTEXT_POLICY_PROFILES,
    _CONTEXT_WINDOW_FRACTION,
    _CONTEXT_MIN_WINDOW_TO_SCALE,
    _CONTEXT_BUDGET_HARD_CAP,
    _TRIM_LOW_WATERMARK,
    _MAX_COMPACT_FOCUS,
    _FOLD_MARK,
    _FOLD_KEEP_RECENT_TOOLS,
    _FORCE_FINISH_RULE,
    _SUMMARY_SYSTEM,
    _NUDGE,
    parse_tool_call,
    parse_tool_calls,
    native_default,
    _env_num,
    _env_int,
    _env_block,
    _clip_middle,
    _fmt_args,
    _tool_catalog,
    _tool_results_msg,
    _split_tool_results,
    _to_native_messages,
    _is_weak_final,
    _strip_fences,
    _first_json_object,
    _normalize_context_policy,
    _native_error_is_permanent,
)

# ── 工具工厂已搬到 src/agents/tools/（本文件曾 4300+ 行，工具工厂占了近一半）──────
# 只搬**不依赖 MainAgent** 的那些，所以依赖方向是单向的：main_agent → tools.*，不成环。
# build_dev_tools / build_research_tools / build_subagent 要用 MainAgent 起子 agent，
# 搬走就会成环，仍留在本文件。
#
# 这里逐个再导出：`from src.agents.main_agent import build_read_tools` 这类存量写法
# （src/ 与 tests/ 里几十处）继续照常工作，本次拆分对调用方零影响。
from src.agents.tools._common import (  # noqa: F401,E402
    _MAX_TOOL_RESULT_DEFAULT,
    _MAX_READ_FILE_DEFAULT,
    _env_limit,
    _max_tool_result,
    _max_read_file,
    _MAX_IMAGE_BYTES_DEFAULT,
    _max_image_bytes,
    _image_exts,
    _truthy,
)
from src.agents.tools.files import (  # noqa: F401,E402
    _glob_to_regex,
    _resolve_within,
    build_read_tools,
    build_write_tools,
    build_test_tool,
)
from src.agents.tools.web import (  # noqa: F401,E402
    build_web_tools,
    build_screenshot_tool,
)
from src.agents.tools.cron import (  # noqa: F401,E402
    build_cron_tools,
)
from src.agents.tools.media import (  # noqa: F401,E402
    build_im_media_tools,
)
from src.agents.tools.shell import (  # noqa: F401,E402
    build_command_tool,
    build_pr_tool,
)
from src.agents.tools.memory import (  # noqa: F401,E402
    _longterm_store,
    build_memory_tools,
)
from src.agents.tools.skills import (  # noqa: F401,E402
    LoadedSkill,
    SkillRegistry,
    _skill_registry_for,
    skill_catalog,
    build_skill_tools,
)
