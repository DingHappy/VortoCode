"""Isolated development tool assembly; independent of client/session assembly."""
from __future__ import annotations

from typing import Any, Callable, Optional

from src.agents.agent_loop import MainAgent, DEV_SUBAGENT_ROLE, _clip_middle
from src.agents.tools._common import _truthy
from src.agents.tool import Tool
from src.agents.tools.files import build_read_tools, build_write_tools, build_test_tool
from src.utils.exc_utils import _exc_text
from src.utils.async_ops import await_thread

from src.agents.dev_policy import (
    _dev_review_enabled,
    _dev_attempts,
    _dev_parallelism,
    preflight_dev,
    land_note,
    _repair_prompt,
    _noop_retry_prompt,
    _missing_tests_retry_prompt,
    _dropped_tests_retry_prompt,
    _fail_note,
    _noop_note,
    _log_stage_usage,
    _detect_base_branch,
    _is_test_path,
    _count_test_files,
    _dropped_tests,
    _dropped_tests_note,
    _wants_new_tests,
    _branch_changed_files,
    _test_delta_msg,
    _test_delta_note,
    _diff_change_counts,
)

def build_dev_tools(repo_root: str, on_progress: Optional[Callable[[str], None]] = None,
                    confirm: Optional[Callable] = None, draft_pr: bool = False,
                    capabilities: Any = None,
                    on_diff: Optional[Callable[[str, str], None]] = None,
                    agent_factory=MainAgent, question_factory=None) -> list[Tool]:
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
            except OSError:
                raise
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
                # ok=False：台账/UI 据此记 failed。没有它，一条以 ❌ 开头的结果会被记成 succeeded。
                return {"ok": False, "text": (
                    f"❌ 隔离实现失败：LLM 通道故障（{err[:160]}），试了 {attempts} 次。"
                    f"多为中转站/网络抖动——确认中转站健康后重试即可，不必改任务描述。")}
            return {"ok": False, "text": (
                f"❌ 隔离实现未产生任何改动（试了 {attempts} 次，子 agent 始终没真正修改文件）。"
                f"请把任务描述写得更具体、可执行（明确要改哪个文件、加什么）后再调 dev_isolated。")}
        additions, deletions = _diff_change_counts(diff)
        change_size = f"+{additions}/-{deletions} 行"
        if ver and ver["ok"]:
            import subprocess
            base_result = await await_thread(
                subprocess.run, ["git", "-C", repo_root, "rev-parse", "HEAD"],
                capture_output=True, text=True, timeout=20)
            base_oid = base_result.stdout.strip() if base_result.returncode == 0 else ""
            slug = re.sub(r"[^a-z0-9]+", "-", desc.lower()).strip("-")[:28] or "iso"
            branch = f"vorto/{slug}-{uuid.uuid4().hex[:8]}"
            res = await await_thread(apply_diff_to_branch, repo_root, branch, diff, f"dev_isolated: {desc}")
            # 文档档位跳过了测试：结论里必须写明，不能和"测试通过"混为一谈（跳过 ≠ 通过）
            verdict = ("已隔离实现（**纯文档改动，已跳过测试——跳过≠通过**）"
                       if ver.get("skipped") else "已隔离实现且测试通过")
            if res["ok"]:
                delivery_note = ""
                try:
                    from src.gateway.isolated_deliveries import record_isolated_delivery
                    await await_thread(
                        record_isolated_delivery, repo_root, branch=branch, description=desc,
                        base_oid=base_oid, verification=ver, attempts=attempts)
                except Exception as error:  # noqa: BLE001
                    delivery_note = f"（Desktop 交付记录未保存：{_exc_text(error)}；分支仍可用）"
                return (f"✅ {verdict}{fixed}，落到新分支 {branch}（{change_size}，"
                        f"git checkout {branch} 查看，未碰 main）。"
                        + _test_delta_note(diff) + _dropped_tests_note(diff, desc) + delivery_note)
            return f"✅ {verdict}{fixed}，但落分支失败：{res['error']}。diff {change_size}。"
        tail = (ver or {}).get("output", "")[-1000:]
        return {"ok": False, "text": (
            f"❌ 隔离实现完成但测试未过（试了 {attempts} 次）。失败输出尾部：\n{tail}\n"
            f"据此修正后重试（再调 dev_isolated）。diff {change_size}，未落地。")}

    def _make_writer(test_cmd, question=None):
        """造一个'隔离实现子 agent'工厂：worktree 里 read+write+run_tests、自测到通过再交。"""
        # 仓库记忆是这里最值钱的落点：子 agent 每次都在全新 worktree 里从零开始，
        # 构建怪癖/测试命令/已知坑本来每次重踩——现在开局就带着。
        # 走 dev_subagent_system 统一拼装（别再各处各拼一份，那样加东西必漏）。
        from src.agents.repo_memory import dev_subagent_system
        extra = dev_subagent_system(repo_root, DEV_SUBAGENT_ROLE)

        from src.llm.providers import client_for_role
        impl_llm = client_for_role("implement")   # 没配 = None = 沿用默认，行为与从前一致

        def _mk(_desc):
            def _b(wt):
                tools = build_read_tools(wt) + build_write_tools(wt) + [build_test_tool(wt, test_cmd)]
                kwargs = {}
                if question is not None:
                    from src.agents.permissions import load_permissions
                    kwargs["permissions"] = load_permissions(repo_root)
                    if question.tool is not None:
                        tools.append(question.tool)
                agent = agent_factory(
                    tools,
                    llm=impl_llm,
                    max_steps=16,
                    extra_system=extra,
                    capabilities=capabilities,
                    raise_llm_errors=True, **kwargs)
                if question is not None:
                    agent.max_steps = min(agent.max_steps, 16)
                    agent.build_max_steps = min(agent.build_max_steps, 16)
                    agent.build_auto_continues = 0
                    agent._untrusted_input = question.tainted
                return agent
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
        # 绿但"没补要求的测试"时会继续重试，而后面几轮可能更差（红/无改动）——把第一份绿的留住，
        # 免得为了追测试反而把已经能用的实现丢了。
        best_green: Optional[dict] = None
        best_green_clean = False       # 留住的那份是不是"既没删测试也没漏测试"的干净绿
        for attempt in range(1, _dev_attempts() + 1):
            if attempt > 1:
                if last.get("err"):
                    # 通道故障（LLM 502/超时）≠ agent 没干活：如实播报死因，并给瞬时故障一点恢复窗口
                    _progress(f"↻ 「{desc[:32]}」上次 LLM 通道故障（{str(last['err'])[:80]}），"
                              f"第 {attempt} 次重试…")
                    await asyncio.sleep(min(10.0, 3.0 * (attempt - 1)))
                else:
                    # 死因按**证据**分三种，别一律说成"红/无改动"：真机 2026-09-17 的那轮里，
                    # 每次失败都被播成"未达标（红/无改动）"，而账本最后给出的死因是 TimeoutError
                    # ——人照着"未达标"去改任务描述，改一晚上也没用，因为根本不是任务的问题。
                    prev_ver = last.get("ver")
                    if prev_ver and not prev_ver.get("ok"):
                        why = "自测未过（红）"
                    elif not str(last.get("diff") or "").strip():
                        why = "没有产出任何改动"
                    else:
                        why = "未达标"
                    _progress(f"↻ 「{desc[:32]}」上次{why}，第 {attempt} 次换全新 worktree 重试…")
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
                # 任务明确要求补测试、改动里却一个测试文件都没有 → 还没做完。在**本次调用内**再试一轮，
                # 别把半成品落成 vorto/* 分支、指望主 agent 看见那句 ⚠ 警告自己再调一次 dev_isolated
                # （真机 2026-09-19 打包版冒烟就是这么走的：两次调用、两条分支，第一条是死的）。
                # 删掉既有测试让套件变绿，是比"没补测试"更坏的一种——所有信号都会说绿
                # （真机 2026-09-20：子 agent 把 test_add_and_pending 删了原地改成 test_remove，
                # 测试 1 passed、测试增量提示还报"含 1 个测试文件"，没有任何东西报警）。
                dropped = _dropped_tests(desc, diff)
                needs_tests = _wants_new_tests(desc) and _count_test_files(diff) == 0
                clean = not dropped and not needs_tests
                if best_green is None or (clean and not best_green_clean):
                    best_green, best_green_clean = last, clean
                if clean or attempt >= _dev_attempts():
                    _progress(f"✅ 「{desc[:32]}」实现并自测通过"
                              + (f"（修复 {attempt - 1} 次后）" if attempt > 1 else ""))
                    return last                              # 绿且干净就收（或已用完次数）
                if dropped:
                    _progress(f"↻ 「{desc[:32]}」删掉了既有测试（{'、'.join(dropped[:2])}），"
                              f"第 {attempt + 1} 次重做…")
                    cur = _dropped_tests_retry_prompt(desc, dropped)
                else:
                    _progress(f"↻ 「{desc[:32]}」实现绿了但没补要求的测试，第 {attempt + 1} 次补测试…")
                    cur = _missing_tests_retry_prompt(desc)
                continue
            # 准备下一次的反馈：no-op（没改文件）和测试红是两码事，提示也不同
            if not (diff or "").strip():
                cur = _noop_retry_prompt(desc)               # 没改动：用更命令式提示逼它真动手
            else:
                cur = _repair_prompt(desc, (ver or {}).get("output", "")[-1500:])   # 红：带失败反馈再试
        if best_green is not None:
            # 为补测试多试的那几轮都没成，但先前那份绿的实现照样有效——交它，并保留 ⚠ 测试增量提示。
            _progress(f"✅ 「{desc[:32]}」实现并自测通过（未补上要求的测试）")
            return best_green
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

    async def _dependent_with_repair(branch, desc, title, test_cmd, question=None):
        """在 branch 之上接力实现一个（有依赖的 / resume 补跑的）子任务 + 自测；红了带失败反馈、换全新
        worktree 再试，最多 _dev_attempts() 次。绿则就地提交（推进 branch）后返回。
        返回 run_dependent_on_branch 的结果 dict（附 attempts）。desc=实现描述、title=展示名。"""
        import uuid
        from src.agents.worktree import run_dependent_on_branch
        mk = _make_writer(test_cmd, question)
        base = desc + (question.context if question is not None else "")
        cur = base
        msg = f"dev_auto(dep): {title}"
        r = {"ok": False, "output": "未尝试", "attempts": 0}
        attempts = min(_dev_attempts(), 2) if question is not None else _dev_attempts()
        for attempt in range(1, attempts + 1):
            _progress(f"🔗 依赖接力实现「{title}」" + (f"（第 {attempt} 次修复重试）" if attempt > 1 else "…"))
            wid = "wt-" + uuid.uuid4().hex[:8]
            r = await run_dependent_on_branch(repo_root, wid, branch, cur, mk(None), msg, test_cmd)
            r["attempts"] = attempt
            if r["ok"]:
                return r
            cur = _repair_prompt(base, (r.get("output") or "")[-1500:])
        return r

    async def _dev_parallel(args: dict) -> str:
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
        res = await await_thread(
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
        if confirm is None:
            return ("\n（本环境未接确认门，未自动开 PR；分支已就绪，可用 open_pr 工具手动开。）")
        title = f"dev_auto: {task[:60]}"
        _emit_diff(f"待开 PR 的改动：{branch} → {base}", branch, base)
        if not await confirm(f"把 {branch} push 到远端并对 {base} 开 PR？\n  标题：{title}"):
            return f"\n（已取消开 PR；分支 {branch} 保留，可稍后手动 open_pr。）"
        _progress(f"🚀 push {branch} 并对 {base} 开{'（draft）' if draft_pr else ''} PR…")
        from src.agents.vcs import push_and_open_pr
        res = await await_thread(push_and_open_pr, repo_root, branch, title, body[:4000],
                                      base, "origin", draft_pr)
        if res.get("ok") and res.get("url"):
            return f"\n🎉 已开 PR：{res['url']}"
        if res.get("pushed"):
            return f"\n（已 push {branch}，但开 PR 失败：{res.get('error')}。可手动 gh pr create。）"
        return f"\n（开 PR 失败：{res.get('error')}；分支 {branch} 保留。）"

    async def _run_review_gate(branch: str, base: str, test_cmd, on_incomplete=None) -> tuple:
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
                add = _git(_repo_root, "worktree", "add", "--detach", str(path), _branch, check=False)
                if add.returncode != 0:
                    raise RuntimeError("无法创建审查 worktree")
                try:
                    diff = _review._branch_diff(_repo_root, _base, _branch)
                    if not diff.strip():
                        return []
                    tools = build_read_tools(str(path)) + [build_test_tool(str(path), test_cmd)]
                    extra = (_review._REVIEWER_SYSTEM
                             + (f"\n\n{lens}" if lens else "")
                             + (f"\n\n【本仓库审查规范】\n{guidelines}" if guidelines else ""))
                    # 审查段是**异厂商最有价值**的地方，理由是独立性不是强弱：同一个模型自己审
                    # 自己写的实现，会系统性漏掉自己的盲区（实现时认为对的，审时还是认为对）。
                    # 显式注入的 llm（测试/调用方）优先，其次才看角色表。
                    from src.llm.providers import client_for_role
                    agent = agent_factory(tools, llm=llm or client_for_role("review"),
                                      max_steps=max_steps, extra_system=extra,
                                      capabilities=capabilities, raise_llm_errors=True)
                    prompt = (f"审查分支 {_branch}（相对 {_base}）的以下改动。只报 P0/P1、每条带验证证据、"
                              f"用 run_tests 复现你怀疑的问题，最后只输出 JSON 数组：\n\n```diff\n{diff}\n```")
                    from src.agents.taint import merge_nested_taint
                    with merge_nested_taint():
                        reply = await agent.run_turn(prompt, mode="build")
                    return _review.parse_findings(reply)
                finally:
                    remove_worktree(_repo_root, path)

            return _review_branch

        async def _repair(fix_desc: str) -> None:
            result = await run_dependent_on_branch(
                repo_root, "wt-" + uuid.uuid4().hex[:8], branch,
                fix_desc, mk(None), "dev_auto(review-fix)", test_cmd)
            if not result.get("ok"):
                raise RuntimeError(result.get("output") or "审查修复未成功落地")

        reviewers = {name: _make_reviewer(_review.PERSPECTIVES[name])
                     for name in _review.dev_review_perspectives()}
        return await _review.run_gate(repo_root, branch, base, test_cmd=test_cmd,
                                      repair=_repair, reviewers=reviewers, progress=_progress,
                                      on_incomplete=on_incomplete)

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
            _dp.save_checkpoint(repo_root, dp)

        # 1) 独立块：全新时并行实现 + 批量落分支（建分支）；resume 时（分支已在）逐个在分支上接力补跑。
        todo_ind = dp.pending("independent")
        if todo_ind:
            exists = _branch_exists(branch)
            if question_factory is not None and not exists:
                await await_thread(ensure_branch, repo_root, branch, dp.base)
                exists = True
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
                apply_res = await await_thread(apply_diffs_to_branch, repo_root, branch, items, None)
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
                    question = question_factory(dp, b) if question_factory is not None else None
                    with usage_scope() as used:              # 续跑同样烧钱，同样要计量
                        r = await _dependent_with_repair(branch, b.desc, b.title or b.id, test_cmd, question)
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
                await await_thread(ensure_branch, repo_root, branch, dp.base)  # 无独立基底也给依赖一个
            satisfied = set(dp.satisfied_ids) | dp.landed_ids()
            shims = [types.SimpleNamespace(id=b.id, dependencies=b.deps, block=b) for b in todo_dep]
            out.append("\n【依赖接力】按拓扑序在分支上逐个实现：")
            for shim in topo_order(shims, satisfied):
                b = shim.block
                disp = b.title or b.desc[:40] or b.id
                b.status = "running"
                _save()                                      # write-ahead
                question = question_factory(dp, b) if question_factory is not None else None
                with usage_scope() as used:                  # 接力块同样按块计量
                    r = await _dependent_with_repair(branch, b.desc, disp, test_cmd, question)
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
        integ = await await_thread(
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
            changed = await await_thread(_branch_changed_files, repo_root, dp.base, branch)
            if changed is not None:
                done += _test_delta_msg(sum(1 for p in changed if _is_test_path(p)))
            out.append(done)
            # PR 前对抗审查段：**仅在真要开 PR 时**跑（名副其实的"PR 前"，codex 审 #120 P1）。
            if dp.want_pr and _dev_review_enabled():
                # "哪个视角没看成"要落进台账，别只活在进度文案里——没跑成的审查最容易被当成跑过了。
                incomplete: dict = {}
                note, blocked = await _run_review_gate(branch, dp.base, test_cmd,
                                                       on_incomplete=incomplete.update)
                dp.review = {"note": note, "blocked": bool(blocked), "incomplete": incomplete}
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
        _dp.save_checkpoint(repo_root, dp)                   # write-ahead：分解完成即落文件

        out = [f"已把任务分解为 {plan['total']} 个子任务：{len(descs)} 个独立(并行) + {len(deferred)} 个有依赖(接力)。",
               f"（计划已存盘 plan_id={dp.plan_id}；中断后可 dev_resume 续跑）"]
        if plan.get("dropped_verify_only"):
            # 滤掉了就**说一声**——悄悄吞掉是今天一整天在修的那个毛病，别自己犯。
            out.append(f"（已滤掉 {len(plan['dropped_verify_only'])} 个「只跑验证不改文件」的子任务："
                       + "、".join(plan["dropped_verify_only"][:3])
                       + "；流水线跑完所有块后本来就会做集成验证）")
        with usage_scope() as execute_usage:
            result = await _execute_plan(dp, test_cmd, out)
        _log_stage_usage("execute", execute_usage)
        return result

    async def _pr_fix(args: dict) -> str:
        """读一个 PR 的 review 评论 + CI 状态 → 在其分支上逐条修（自测）→ 绿则 push（确认门）。

        硬闸：分支必须匹配 vorto/*，绝不碰 main/master/其它分支。'人在合并口'之前的往返自动化。"""
        from src.agents.vcs import failed_check_log_excerpts, pr_feedback, push_branch
        from src.agents.test_detect import detect_test_cmd

        ref = str(args.get("pr") or args.get("branch") or args.get("ref") or "").strip()
        if not ref:
            return "pr_fix 需要 pr（PR 号）或 branch（vorto/* 分支名）。"
        fb = await await_thread(pr_feedback, repo_root, ref)
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
        log_result = await await_thread(failed_check_log_excerpts, repo_root, checks)
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
        pushed = await await_thread(push_branch, repo_root, branch)
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
        from src.gateway.tasks import TaskLedger
        if any(task.plan_id == pid and task.status == "blocked" and task.development
               for task in TaskLedger(repo_root).list()):
            raise ValueError("开发计划仍等待回答；请回答或取消问题后再恢复")
        if not isinstance(dp.branch, str) or not dp.branch.startswith("vorto/"):
            raise ValueError("开发恢复仅允许 vorto/ 隔离分支；未执行")
        if dp.status == "done":
            return f"计划 {pid} 已完成（{dp.summary()}），无需续跑。"
        test_cmd = detect_test_cmd(repo_root, dp.test_sel)
        c = dp.counts()
        remaining = c["pending"] + c["running"] + c["failed"]
        _progress(f"↻ 续跑计划 {pid}（已 landed {c['landed']}，续跑未完成 {remaining} 块）…")
        out = [f"续跑计划 {pid}：{dp.task[:60]}",
               f"（已 landed {c['landed']} 块，续跑未完成的 {remaining} 块，不重做已落地部分）"]
        dp.status = "running"
        _dp.save_checkpoint(repo_root, dp)
        return await _execute_plan(dp, test_cmd, out)

    return [
        Tool("dev_isolated",
             "在隔离 git worktree 里实现一个独立子任务 + 自测 + 跑测试验证；✅通过就自动落到一个"
             "vorto/<id> 新分支（绝不碰 main/工作区），❌带失败输出供修正。仅 build",
             {"description": "要在隔离工作区实现的子任务",
              "test": "可选，pytest 文件/节点路径或 test_函数名；省略则跑全量 tests/"},
             _dev_isolated, read_only=False, required_capabilities=("host_process",)),
        Tool("dev_parallel",
             "并行实现：多个**相互独立**的子任务各起隔离 worktree 同时实现+自测+验证（互不冲突，"
             "红了带失败反馈自修复重试），绿块一并落到一个 vorto/parallel 新分支（不碰 main），"
             "**落分支后再跑一遍集成测试**抓'单独绿合起来红'，汇报各自 ✅/❌ 及集成结果。最多 5（仅 build）",
             {"tasks": "相互独立的子任务字符串列表",
              "test": "可选，pytest 文件/节点路径或 test_函数名；省略则各自跑全量 tests/"},
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
