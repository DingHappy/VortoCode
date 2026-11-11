"""排班工具：让 agent 自己建/开关/触发定时作业（.vortoone/cron.yaml）。

⚠ 三个写工具**必须显式** `read_only=False`：`Tool.read_only` 默认是 True，漏了就等于让
plan 模式改得动 cron.yaml、跑得动作业——绕过 plan/build 门。这是自测逮到的真洞。

无人值守档**不给**这套工具（见 gateway/session.py）：让一个没人盯着的回合给自己排下一班，
等于 agent 可以自授"周期性无人值守执行"。
"""

from __future__ import annotations

from src.agents.tool import PreparedToolCall




from typing import Callable, Optional

from src.agents.tool import Tool
from src.agents.tools._common import _truthy


def _scheduler_on() -> bool:
    import os
    return str(os.getenv("VORTOONE_CRON", "")).strip().lower() in ("1", "true", "yes", "on")


# 只用 Desktop 时本机引擎固定关掉调度（VORTOONE_CRON=0）：作业写得进去、显示「已启用」，却永远不会到点跑。
# 建/启用时必须当面说清，不然用户等到第二天才发现提醒没来。
_SCHEDULER_OFF = ("\n⚠️ 这台机器上的调度**没有打开**（只用 Desktop 时就是这样）：作业不会自动跑，到点也没人执行。"
                  "可以用 cron_run 手动跑一次；要按时自动跑，需要一台一直开着、以常驻方式运行的电脑"
                  "（服务带 VORTOONE_CRON=1 启动）。")


def build_cron_tools(repo_root: str, confirm: Optional[Callable] = None) -> list[Tool]:
    """定时作业的观察 + 排班面（`.vortoone/cron.yaml`）。

    为什么要有（真机 2026-07-27）：用户问"能不能设置定时任务"，agent 答"我目前没有设置定时
    任务的能力"，然后推荐 crontab / GitHub Actions / **IFTTT、Zapier**——而 VortoOne 自己的
    cron 子系统当时就跑在那台机器上（`VORTOONE_CRON=1`，relay_duty 每天 02:00）。就工具而言
    它没说谎（确实一个 cron 工具都没有），但把主人推去用外部服务是实打实的错——**产品有的
    能力，agent 却不知道**。#218 当初刻意只开了只读 REST 面（"无人值守修改面先观察后设计"），
    观察够了，这里补上写面。

    四条边界（都不是随手定的）：
    - **扩大自动执行面的写操作过确认门**（建、启用、手动触发）。IM 每回合强制污点 → 免确认失效。
      停用不问（让人容易关掉捣乱的作业），但会推一条通知给主人：2026-10-12 一次验收会话停掉了
      常驻助理的早报，之后六天没人发现。
    - **只建 prompt 作业**，不建 command 作业：见 cron.py 编辑面纪律第 3 条。
    - **不给删除**。停用可逆、可见、可再启用；删除会把主人的 relay_duty 这类巡检静默抹掉。
    - **无人值守档不给这组工具**（build_agent_tools 的 with_cron=False）：cron 作业能创建
      cron 作业就是自我复制驻留——那是把"周期性无人值守执行"变成 agent 可自授的权限。
    """

    async def _list(_args: dict) -> str:
        from datetime import datetime as _dt

        from src.scheduling.cron import CronState, load_jobs
        from src.scheduling.cron import next_due

        jobs = load_jobs(repo_root)
        if not jobs:
            return ("cron.yaml 里还没有任何作业。用 cron_add 建一个（只建 prompt 作业）。"
                    "注意：调度循环要服务带 VORTOONE_CRON=1 起才转。")
        on = _scheduler_on()
        state, now = CronState(repo_root), _dt.now()
        lines = [f"调度总开关：{'✅ 开' if on else '⛔ 关（作业不会自动跑，仍可 cron_run 手动触发）'}"]
        for j in jobs:
            last = state.last_run(j.name)
            nxt = next_due(j.schedule, last, now) if j.enabled else None
            fails = state.failures(j.name)
            lines.append(
                f"- {j.name}（{j.kind}）{'' if j.enabled else ' ⛔已停用'}"
                f"{' 🌐可联网' if j.allow_web else ''}\n"
                f"    排期 {j.schedule.raw}"
                f" · 下次 {nxt.strftime('%m-%d %H:%M') if nxt else '—'}"
                f" · 上次定时运行 {last.strftime('%m-%d %H:%M') if last else '未记录（不代表从未执行）'}"
                f"{f' · 🔴连败 {fails}' if fails else ''}\n"
                f"    内容 {' '.join((j.command or j.prompt).split())[:100]}")
        return "\n".join(lines)

    async def _add(args: dict) -> object:
        from src.scheduling.cron import CronEditError, add_job, preview_new_job
        name = str(args.get("name") or "").strip()
        schedule = str(args.get("schedule") or "").strip()
        prompt = str(args.get("prompt") or "").strip()
        want_web = _truthy(args.get("allow_web", False))
        values = {"name": name, "schedule": schedule, "prompt": prompt,
                  "announce": str(args.get("announce") or "im").strip().lower(),
                  "model": str(args.get("model") or ""),
                  "budget": int(args.get("budget") or 0) if str(args.get("budget") or "").strip().isdigit() else 0}
        try:
            preview_new_job(repo_root, **values)
        except (CronEditError, OSError, ValueError) as error:
            return f"未创建：{error}"
        def creation(allow_web):
            async def apply():
                try:
                    block = add_job(repo_root, **values, enabled=True, allow_web=allow_web)
                except (CronEditError, OSError, ValueError) as error:
                    return f"未创建：{error}"
                note = "" if allow_web else "\n提示：该作业不能联网（无人值守默认不出网）。"
                return (f"✅ 定时作业 {name} 已创建并已启用（{schedule}）。无需再启用一次。\n"
                        + block + note + ("" if _scheduler_on() else _SCHEDULER_OFF))
            net = "可联网" if allow_web else "不能联网"
            return PreparedToolCall(apply,
                f"新建定时作业「{name}」并启用？它会在无人盯屏时按 {schedule} 自动跑（{net}）：\n{prompt}"
                + ("" if _scheduler_on() else _SCHEDULER_OFF), "execute",
                denied_result=f"未创建 {name}（你拒绝了创建和启用）。")
        if not want_web:
            return creation(False)
        async def with_web():
            return creation(True)
        return PreparedToolCall(with_web,
            f"作业「{name}」申请出网许可：它会在无人盯屏时联网，URL 可能成为数据外带通道。\n任务原文：{prompt}",
            "execute", denied_result=creation(False))

    async def _toggle(args: dict) -> object:
        from src.scheduling.cron import CronEditError, set_job_enabled

        name = str(args.get("name") or "").strip()
        enabled = _truthy(args.get("enabled", True))
        async def apply():
            try:
                done = "✅ " + set_job_enabled(repo_root, name, enabled) + ("" if not enabled or _scheduler_on() else _SCHEDULER_OFF)
            except CronEditError as e:
                return f"未改动：{e}"
            except (OSError, ValueError) as e:
                return f"未改动：写 cron.yaml 失败 {type(e).__name__}: {e}"
            if not enabled:
                from src.scheduling.notices import make_notifier
                try:
                    await make_notifier(repo_root, source=f"cron:{name}:disabled")(
                        f"⏸ 定时作业「{name}」已被停用，重新启用前不会再自动跑。不是你的意思的话，回复「启用 {name}」。")
                except Exception:  # noqa: BLE001 —— 通知是旁路，停用本身已经生效
                    pass
            return done
        return PreparedToolCall(apply, f"{'启用' if enabled else '停用'}定时作业「{name}」？", 'execute', protective=not enabled, denied_result=f'未启用 {name}（你拒绝了）。')

    async def _set_prompt(args: dict) -> object:
        from src.scheduling.cron import CronEditError, job_block, load_jobs, set_job_prompt

        name = str(args.get("name") or "").strip()
        new = str(args.get("prompt") or "").strip()
        job = next((j for j in load_jobs(repo_root) if j.name == name), None)
        if job is None:
            return f"未改动：无此作业 {name}。用 cron_list 看现有作业。"
        if not new:
            return "未改动：prompt 不能为空。"
        if job.kind == "command":
            return (f"未改动：{name} 是 command 作业（跑确定性命令），本工具只改 prompt 作业的内容。"
                    f"改命令是另一个风险类别，只能人工编辑 .vortoone/cron.yaml。")
        if " ".join(new.split()) == " ".join((job.prompt or "").split()):
            return f"作业 {name} 的内容与你给的完全一致，无需改动。"

        # **改前改后都摆出来**：改一个已被信任的作业，社工面比新建高得多——人对
        # "改一下 daily-tech-news" 的警惕远低于"新建一个陌生作业"。唯一的补偿就是让他
        # 看见具体差异，而不是只看见一个熟悉的名字。启用态也要说明白：改完下次到点就生效。
        state = (f"⚠️ 它当前是**启用**的（{job.schedule.raw}），改完下次到点就按新内容跑"
                 if job.enabled else f"它当前是停用的（{job.schedule.raw}）")
        async def apply():
            try:
                note = set_job_prompt(repo_root, name, new)
            except CronEditError as e:
                return f"未改动：{e}"
            except (OSError, ValueError) as e:
                return f"未改动：写 cron.yaml 失败 {type(e).__name__}: {e}"
            # 回执按**盘上实际内容**重新渲染，不复述我以为写进去的东西
            # （2026-07-27 的教训：cron_add 的回执自相矛盾，模型信了更具体的那半边）。
            return f"✅ {note}。当前该作业在 cron.yaml 里是：\n{job_block(repo_root, name)}"
        return PreparedToolCall(apply, f"改定时作业「{name}」的内容？\n{state}\n\n── 改前 ──\n{' '.join((job.prompt or '').split())[:400]}\n\n── 改后 ──\n{' '.join(new.split())[:400]}", 'execute', denied_result=f'未改动 {name}（你拒绝了）。')

    async def _set_web(args: dict) -> object:
        from src.scheduling.cron import CronEditError, load_jobs, set_job_web

        name = str(args.get("name") or "").strip()
        want = _truthy(args.get("allow_web", True))
        job = next((j for j in load_jobs(repo_root) if j.name == name), None)
        if job is None:
            return f"未改动：无此作业 {name}。用 cron_list 看现有作业。"
        if job.allow_web is want:
            return f"作业 {name} 的出网许可本来就是 {str(want).lower()}，无需改动。"
        async def apply():
            try:
                return "✅ " + set_job_web(repo_root, name, want)
            except CronEditError as e:
                return f"未改动：{e}"
            except (OSError, ValueError) as e:
                return f"未改动：写 cron.yaml 失败 {type(e).__name__}: {e}"
        return PreparedToolCall(apply, f"{'给予' if want else '撤销'}已有作业「{name}」**出网许可**（web_search / web_fetch / 截图）？\n它在**无人盯屏**时按 {job.schedule.raw} 运行，出网请求的 URL 本身就是一条数据外带通道——若这台机器上的文件被人做过手脚、诱导它去访问某个地址，没有人会拦。\n该作业到点会做的事：{' '.join((job.command or job.prompt).split())[:200]}", 'execute', protective=not want, denied_result=f'未改动 {name}（你拒绝了出网许可）。')

    async def _run(args: dict) -> object:
        import asyncio as _aio

        from src.scheduling.cron import check_trigger, run_job_by_name, track_trigger

        name = str(args.get("name") or "").strip()
        reason, _code, job = check_trigger(repo_root, name)
        if reason is not None:
            return f"未触发：{reason}"
        async def apply():
            from src.scheduling.notices import make_notifier
            task = _aio.get_running_loop().create_task(
                run_job_by_name(repo_root, name, notify=make_notifier(repo_root)))
            track_trigger(name, task)
            return (f"✅ 已在后台触发 {name}。结果会按它的 announce 设置推给你，"
                    f"也会落进 runs 台账；手动触发不占用它的正常排期。")
        return PreparedToolCall(apply, f'立刻手动跑一次定时作业「{name}」（{job.kind}）？', 'execute', denied_result=f'未触发 {name}（你拒绝了）。')

    return [
        Tool("cron_list", "列出本仓库的定时作业：排期/下次应跑/上次跑过/连败次数/内容，"
             "以及调度总开关是否打开。只读、无需确认",
             {}, _list, read_only=True, operation_kind='read', confirm=confirm),
        Tool.prepared("cron_add",
             "新建一个定时作业（写进 .vortoone/cron.yaml）。**只支持 prompt 作业**——要定时跑"
             "命令，就把命令写进 prompt 交给无人值守 agent 执行。作业先以停用态落盘、经主人确认"
             "后才启用。重名会被拒（改已有作业请人工编辑 cron.yaml）。需确认",
             {"name": "作业名（字母/数字/下划线/连字符，≤64）",
              "schedule": "排期：'at HH:MM' 每天该时刻 / 'every 30m'|'every 2h' 固定间隔 / 5 段 cron 'm h dom mon dow'",
              "prompt": "到点要做的事（自然语言，交给一个全新的无人值守 agent 执行）",
              "announce": "可选：im=结果推给主人（默认）/ silent=只落台账",
              "allow_web": "可选，默认 false。作业到点时要联网（搜索/抓网页/截图）才传 true——"
                           "会**单独**向主人要一次授权；查新闻、查行情这类必须传，"
                           "纯本地活儿（跑测试、整理文件）不要传",
              "model": "可选：指定模型", "budget": "可选：本作业 token 预算上限"},
             _add, read_only=False, required_args=('name', 'schedule', 'prompt'), argument_schema={'name': {'type': 'string'},
             'schedule': {'type': 'string'},
             'prompt': {'type': 'string'},
             'allow_web': {'type': 'boolean'},
             'budget': {'type': 'integer', 'minimum': 0},
             'announce': {'type': 'string', 'enum': ['im', 'silent']}}, operation_kind='write', possible_operations=('execute',), confirm=confirm),
        Tool.prepared("cron_toggle", "启用或停用一个已有定时作业（停用后调度器不跑它，可随时再启用）。"
             "本工具刻意不提供删除——停用可逆可见，误删主人的巡检作业不可逆。需确认",
             {"name": "作业名", "enabled": "true=启用 / false=停用"}, _toggle, read_only=False, required_args=('name', 'enabled'), argument_schema={'name': {'type': 'string'}, 'enabled': {'type': 'boolean'}}, operation_kind='write', possible_operations=('execute',), protective=True, confirm=confirm),
        # read_only=False 三处都必须显式写：Tool 的默认是 True，漏了就等于让 plan 模式改得动
        # cron.yaml、跑得动作业——绕过 plan/build 门。这是自测逮到的真洞，不是形式主义。
        Tool.prepared("cron_set_web", "给一个**已有**作业开/关联网许可（web_search/web_fetch/截图）。"
             "开时会单独向主人要一次授权。只改这一个开关——作业的 prompt/命令/排期都不会被动，"
             "要换内容请另建一个作业。需确认",
             {"name": "作业名", "allow_web": "true=给出网许可 / false=收回"},
             _set_web, read_only=False, required_args=('name', 'allow_web'), argument_schema={'name': {'type': 'string'}, 'allow_web': {'type': 'boolean'}}, operation_kind='write', possible_operations=('execute',), protective=True, confirm=confirm),
        Tool.prepared("cron_set_prompt",
             "改一个**已有 prompt 作业**要做的事（整块替换它的 prompt，排期/开关/出网许可都不动）。"
             "确认时会把改前改后摆给你看。command 作业（跑确定性命令的）拒绝修改。需确认",
             {"name": "作业名", "prompt": "这个作业到点要做的事（完整新内容，不是补丁）"},
             _set_prompt, read_only=False, required_args=('name', 'prompt'), argument_schema={'name': {'type': 'string'}, 'prompt': {'type': 'string'}}, operation_kind='write', possible_operations=('execute',), confirm=confirm),
        Tool.prepared("cron_run", "立刻手动跑一次某个已有定时作业（不占用它的正常排期）。已停用的作业拒绝"
             "触发、同名作业在跑时拒绝重复触发。结果走通知台账，不在这里返回。需确认",
             {"name": "作业名"}, _run, read_only=False, required_args=('name',), argument_schema={'name': {'type': 'string'}}, operation_kind='execute', confirm=confirm),
    ]
