"""排班工具：让 agent 自己建/开关/触发定时作业（.vortocode/cron.yaml）。

⚠ 三个写工具**必须显式** `read_only=False`：`Tool.read_only` 默认是 True，漏了就等于让
plan 模式改得动 cron.yaml、跑得动作业——绕过 plan/build 门。这是自测逮到的真洞。

无人值守档**不给**这套工具（见 gateway/session.py）：让一个没人盯着的回合给自己排下一班，
等于 agent 可以自授"周期性无人值守执行"。
"""

from __future__ import annotations

from typing import Callable, Optional

from src.agents.tool import Tool
from src.agents.tools._common import _truthy


def build_cron_tools(repo_root: str, confirm: Optional[Callable] = None) -> list[Tool]:
    """定时作业的观察 + 排班面（`.vortocode/cron.yaml`）。

    为什么要有（真机 2026-07-27）：用户问"能不能设置定时任务"，agent 答"我目前没有设置定时
    任务的能力"，然后推荐 crontab / GitHub Actions / **IFTTT、Zapier**——而 VortoCode 自己的
    cron 子系统当时就跑在那台机器上（`VORTOCODE_CRON=1`，relay_duty 每天 02:00）。就工具而言
    它没说谎（确实一个 cron 工具都没有），但把主人推去用外部服务是实打实的错——**产品有的
    能力，agent 却不知道**。#218 当初刻意只开了只读 REST 面（"无人值守修改面先观察后设计"），
    观察够了，这里补上写面。

    四条边界（都不是随手定的）：
    - **写面一律过确认门**。IM 每回合强制污点 → 免确认失效 → 每次建/停都必须真人点头。
    - **只建 prompt 作业**，不建 command 作业：见 cron.py 编辑面纪律第 3 条。
    - **不给删除**。停用可逆、可见、可再启用；删除会把主人的 relay_duty 这类巡检静默抹掉。
    - **无人值守档不给这组工具**（build_agent_tools 的 with_cron=False）：cron 作业能创建
      cron 作业就是自我复制驻留——那是把"周期性无人值守执行"变成 agent 可自授的权限。
    """
    async def _ask(msg: str) -> bool:
        return bool(await confirm(msg)) if confirm is not None else False

    async def _list(_args: dict) -> str:
        from datetime import datetime as _dt

        from src.gateway.cron import CronState, load_jobs
        from src.web.routers.cron import _next_due

        jobs = load_jobs(repo_root)
        if not jobs:
            return ("cron.yaml 里还没有任何作业。用 cron_add 建一个（只建 prompt 作业）。"
                    "注意：调度循环要服务带 VORTOCODE_CRON=1 起才转。")
        import os as _os
        on = str(_os.getenv("VORTOCODE_CRON", "")).strip().lower() in ("1", "true", "yes", "on")
        state, now = CronState(repo_root), _dt.now()
        lines = [f"调度总开关：{'✅ 开' if on else '⛔ 关（作业不会自动跑，仍可 cron_run 手动触发）'}"]
        for j in jobs:
            last = state.last_run(j.name)
            nxt = _next_due(j.schedule, last, now) if j.enabled else None
            fails = state.failures(j.name)
            lines.append(
                f"- {j.name}（{j.kind}）{'' if j.enabled else ' ⛔已停用'}"
                f"{' 🌐可联网' if j.allow_web else ''}\n"
                f"    排期 {j.schedule.raw}"
                f" · 下次 {nxt.strftime('%m-%d %H:%M') if nxt else '—'}"
                f" · 上次 {last.strftime('%m-%d %H:%M') if last else '从未'}"
                f"{f' · 🔴连败 {fails}' if fails else ''}\n"
                f"    内容 {' '.join((j.command or j.prompt).split())[:100]}")
        return "\n".join(lines)

    async def _add(args: dict) -> str:
        from src.gateway.cron import CronEditError, add_job

        name = str(args.get("name") or "").strip()
        schedule = str(args.get("schedule") or "").strip()
        prompt = str(args.get("prompt") or "").strip()
        announce = str(args.get("announce") or "im").strip().lower()
        want_web = _truthy(args.get("allow_web", False))
        # 出网许可**单独问一次**，不许混在"要不要启用"里一起蒙过去：这两件事的风险不是一个量级，
        # 合成一句话就等于让人在不知情下顺手交出无人值守的外传通道。先问许可、再问启用；
        # 许可被拒就按不出网建（作业仍然有用，只是这一档能力没有），而不是整个作业不建。
        allow_web = False
        if want_web:
            allow_web = await _ask(
                f"作业「{name}」申请**出网许可**（web_search / web_fetch / 截图）。\n"
                f"注意：它在**无人盯屏**时运行，出网请求的 URL 本身就是一条数据外带通道——"
                f"若这台机器上的文件被人做过手脚、诱导它去访问某个地址，没有人会拦。\n"
                f"仅在你确实需要它联网（如查新闻/行情）时批准。要给吗？")
        try:                                       # 先校验再问人：别拿一个注定失败的操作烦主人
            block = add_job(repo_root, name=name, schedule=schedule, prompt=prompt,
                            announce=announce, enabled=False, model=str(args.get("model") or ""),
                            allow_web=allow_web,
                            budget=int(args.get("budget") or 0) if str(args.get("budget") or "").strip().isdigit() else 0)
        except CronEditError as e:
            return f"未创建：{e}"
        except (OSError, ValueError) as e:
            return f"未创建：写 cron.yaml 失败 {type(e).__name__}: {e}"
        # 先以**停用**态落盘，再问要不要启用：确认被拒时留下的是一条不会跑的记录，
        # 而不是一个已经在排期里的作业。fail-closed 的方向永远是"不跑"。
        net = "可联网" if allow_web else "**不能联网**"
        ok = await _ask(f"新建定时作业「{name}」并**启用**？它会在无人盯屏时按 {schedule} 自动跑（{net}）：\n"
                        f"{' '.join(prompt.split())[:300]}")
        if not ok:
            return (f"已写入作业 {name}，但保持**停用**（你拒绝了启用）。"
                    f"想跑再说一声，或 cron_run 手动跑一次试试。\n{block}")
        try:
            from src.gateway.cron import set_job_enabled
            set_job_enabled(repo_root, name, True)
        except Exception as e:  # noqa: BLE001
            return f"作业 {name} 已写入但启用失败：{type(e).__name__}: {e}（现为停用态）"
        # 回执必须反映**最终**状态：`block` 是落盘那一刻生成的（enabled: false，因为要先停用再问），
        # 直接贴出来就会出现"已启用"和 `enabled: false` 同框——工具自己跟自己矛盾。真机
        # 2026-07-27 模型信了更具体的那半，回报"已创建但默认停用"，用户于是又说一次"启用"。
        # 别让人去分辨工具话里哪半是真的。
        from src.gateway.cron import job_block
        note = ("" if allow_web else
                ("\n⚠️ 你拒绝了出网许可，该作业**不能联网**——若它的内容需要搜索/抓网页，"
                 "到点会如实报告缺工具。要改请人工编辑 cron.yaml 加 `allow_web: true`。"
                 if want_web else
                 "\n提示：该作业**不能联网**（无人值守默认不出网）。需要联网请在创建时申报 allow_web。"))
        return (f"✅ 定时作业 {name} 已创建并**已启用**（{schedule}）。无需再启用一次。\n"
                + (job_block(repo_root, name) or block) + note)

    async def _toggle(args: dict) -> str:
        from src.gateway.cron import CronEditError, set_job_enabled

        name = str(args.get("name") or "").strip()
        enabled = _truthy(args.get("enabled", True))
        if enabled and not await _ask(f"启用定时作业「{name}」？启用后它会在无人盯屏时自动跑。"):
            return f"未启用 {name}（你拒绝了）。"
        try:
            return "✅ " + set_job_enabled(repo_root, name, enabled)
        except CronEditError as e:
            return f"未改动：{e}"
        except (OSError, ValueError) as e:
            return f"未改动：写 cron.yaml 失败 {type(e).__name__}: {e}"

    async def _set_web(args: dict) -> str:
        from src.gateway.cron import CronEditError, load_jobs, set_job_web

        name = str(args.get("name") or "").strip()
        want = _truthy(args.get("allow_web", True))
        job = next((j for j in load_jobs(repo_root) if j.name == name), None)
        if job is None:
            return f"未改动：无此作业 {name}。用 cron_list 看现有作业。"
        if job.allow_web is want:
            return f"作业 {name} 的出网许可本来就是 {str(want).lower()}，无需改动。"
        if want and not await _ask(
                f"给已有作业「{name}」**出网许可**（web_search / web_fetch / 截图）？\n"
                f"它在**无人盯屏**时按 {job.schedule.raw} 运行，出网请求的 URL 本身就是一条数据"
                f"外带通道——若这台机器上的文件被人做过手脚、诱导它去访问某个地址，没有人会拦。\n"
                f"该作业到点会做的事：{' '.join((job.command or job.prompt).split())[:200]}"):
            return f"未改动 {name}（你拒绝了出网许可）。"
        try:
            return "✅ " + set_job_web(repo_root, name, want)
        except CronEditError as e:
            return f"未改动：{e}"
        except (OSError, ValueError) as e:
            return f"未改动：写 cron.yaml 失败 {type(e).__name__}: {e}"

    async def _run(args: dict) -> str:
        import asyncio as _aio

        from src.gateway.cron import check_trigger, run_job_by_name, track_trigger

        name = str(args.get("name") or "").strip()
        reason, _code, job = check_trigger(repo_root, name)
        if reason is not None:
            return f"未触发：{reason}"
        if not await _ask(f"立刻手动跑一次定时作业「{name}」（{job.kind}）？"):
            return f"未触发 {name}（你拒绝了）。"
        # 后台跑：夜跑评测这类作业可长达小时级，挂在回合上会把对话卡死。
        # **必须传 notify**：不传的话 `_announce` 只会 record_notice 写盘，人手机上收不到任何东西——
        # 真机 2026-07-27 就是这样，主人在钉钉等了半天，而这里的注释当时还写着"announce 推 IM"。
        from src.gateway.notices import make_notifier
        task = _aio.get_running_loop().create_task(
            run_job_by_name(repo_root, name, notify=make_notifier(repo_root)))
        track_trigger(name, task)
        return (f"✅ 已在后台触发 {name}。结果会按它的 announce 设置推给你，"
                f"也会落进 runs 台账；手动触发不占用它的正常排期。")

    return [
        Tool("cron_list", "列出本仓库的定时作业：排期/下次应跑/上次跑过/连败次数/内容，"
             "以及调度总开关是否打开。只读、无需确认",
             {}, _list, read_only=True),
        Tool("cron_add",
             "新建一个定时作业（写进 .vortocode/cron.yaml）。**只支持 prompt 作业**——要定时跑"
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
             _add, read_only=False),
        Tool("cron_toggle", "启用或停用一个已有定时作业（停用后调度器不跑它，可随时再启用）。"
             "本工具刻意不提供删除——停用可逆可见，误删主人的巡检作业不可逆。需确认",
             {"name": "作业名", "enabled": "true=启用 / false=停用"}, _toggle, read_only=False),
        # read_only=False 三处都必须显式写：Tool 的默认是 True，漏了就等于让 plan 模式改得动
        # cron.yaml、跑得动作业——绕过 plan/build 门。这是自测逮到的真洞，不是形式主义。
        Tool("cron_set_web", "给一个**已有**作业开/关联网许可（web_search/web_fetch/截图）。"
             "开时会单独向主人要一次授权。只改这一个开关——作业的 prompt/命令/排期都不会被动，"
             "要换内容请另建一个作业。需确认",
             {"name": "作业名", "allow_web": "true=给出网许可 / false=收回"},
             _set_web, read_only=False),
        Tool("cron_run", "立刻手动跑一次某个已有定时作业（不占用它的正常排期）。已停用的作业拒绝"
             "触发、同名作业在跑时拒绝重复触发。结果走通知台账，不在这里返回。需确认",
             {"name": "作业名"}, _run, read_only=False),
    ]
