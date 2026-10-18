"""常驻助理的巡检与汇报：Desktop 关着也照常跟进派出去的活，有变化推到手机，早晚各报一次。

- 每分钟向项目运行时刷新已下发任务的状态（assistant_dispatch.refresh），状态一变就推送：
  新写好等你确认的任务单、完成（含分支）、失败、在等人回答。手机上只是提醒，确认下发仍在 Desktop。
- 早报 / 晚报（默认 09:00、21:00，本机时区）：今天聊了几件事、任务单各状态几件、哪些等你处理。
  模型整理台账素材，不可用时发数据版；投递失败复用已保存正文。
- 推送：本进程持有 IM 桥就直接发；否则交给 ``VORTOCODE_OWNER_NOTIFY_URL`` 指向的同机服务。
  两者都没有就不启动——Desktop 本机的通用会话（IM 固定关、不配转发）不会冒出推送。
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional

from src.workflow.tasks import assistant_dispatch

logger = logging.getLogger(__name__)

INTERVAL_SECONDS = 60
_REPORT_GRACE = timedelta(hours=2)          # 错过整点（重启、断网）两小时内补发，再晚就不补
_NOTIFY_STATUSES = {"proposed", "waiting", "done", "failed", "send_failed", "submission_unknown"}

# True = 发送或入补发队列已受理；不代表用户已读。
Notify = Callable[[str], Awaitable[bool]]


def notify_url() -> str:
    return os.getenv("VORTOCODE_OWNER_NOTIFY_URL", "").strip().rstrip("/")


def watch_enabled() -> bool:
    """本进程自己持有 IM，或配了转发地址，才有地方汇报。Desktop 的本机 sidecar 两者都固定关掉。"""
    return bool(notify_url() or os.getenv("VORTOCODE_IM", "").strip())


def report_times() -> List[str]:
    raw = os.getenv("VORTOCODE_ASSISTANT_REPORT_TIMES", "09:00,21:00")
    times = []
    for part in raw.split(","):
        part = part.strip()
        try:
            datetime.strptime(part, "%H:%M")
        except ValueError:
            continue
        times.append(part)
    return times


def owner_reporter(root: str) -> Notify:
    """汇报给主人：记通知台账，本进程有 IM 桥就直接推，没有就交给同机持有 IM 的服务。"""
    from src.scheduling.notices import make_notifier

    deliver = make_notifier(root, source="assistant")

    async def report(text: str) -> bool:
        try:
            return bool(await deliver(text))
        except Exception as error:  # noqa: BLE001 —— 推送是旁路
            logger.warning("助理汇报失败：%s", type(error).__name__)
            return False
    return report


def _state_path(root: str) -> Path:
    return Path(root) / ".vortocode" / "dispatches" / "watch.json"


def _load_state(root: str) -> Optional[Dict[str, Any]]:
    try:
        data = json.loads(_state_path(root).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _save_state(root: str, state: Dict[str, Any]) -> None:
    from src.utils.state_dir import ensure_state_gitignore

    ensure_state_gitignore(root)
    path = _state_path(root)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    # 早晚报草稿可能含私人摘要；临时文件和最终文件均仅当前用户可读写。
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                     prefix="watch-", suffix=".tmp", delete=False) as stream:
        tmp = Path(stream.name)
        try:
            json.dump(state, stream, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise
    try:
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


_DIGEST_LIMIT = 300
# 开发流水线报告里的流程提示：对看通知的人没用
_BOILERPLATE = ("已把任务分解为", "（计划已存盘")
_SENTENCE_ENDS = "。！？；\n"


def _clip_head(text: str, limit: int) -> str:
    """从头截到 limit 以内，尽量停在句末，不切半句；截了就补「…」。"""
    if len(text) <= limit:
        return text
    head = text[:limit]
    cut = max(head.rfind(ch) for ch in _SENTENCE_ENDS)
    return (head[:cut + 1] if cut >= limit // 3 else head).rstrip() + "…"


def result_digest(result: str, limit: int = _DIGEST_LIMIT) -> str:
    """任务结果压成通知正文：从末尾往前取**整段**（结论和验收在后面），不在半句中间切。

    以前取 ``result[-300:]``，开头会是「.py 新增…」这种半截话。省略了内容就提示去 Desktop 看全文。
    """
    text = result.replace("**", "").strip()
    paragraphs = [part.strip() for part in text.split("\n\n") if part.strip()]
    paragraphs = [part for part in paragraphs if not part.startswith(_BOILERPLATE)]
    kept: List[str] = []
    used = 0
    for part in reversed(paragraphs):
        cost = len(part) + (2 if kept else 0)
        if used + cost > limit:
            break
        kept.insert(0, part)
        used += cost
    if not kept and paragraphs:                 # 最后一段本身就超长：取它的开头
        kept = [_clip_head(paragraphs[-1], limit)]
    digest = "\n\n".join(kept)
    if digest and digest != "\n\n".join(paragraphs):
        digest += "\n（完整结果在 Desktop 助理的「动态」里）"
    return digest


def status_message(item: Dict[str, Any]) -> str:
    title = f"{item.get('project')}：{item.get('title')}"
    status = item.get("status")
    if status == "proposed":
        return f"📝 助理写好了任务单，等你确认下发——{title}\n在 Desktop 助理的「动态」里确认（手机上回复不会下发）。"
    if status == "waiting":
        return f"⏸ {title} 在等人回答，到工作台的常驻电脑项目里处理。"
    if status == "done":
        branch = f"，交付分支 {item['branch']}" if item.get("branch") else ""
        digest = result_digest(str(item.get("result") or ""))
        return f"✅ {title} 执行结束，等待审查{branch}。" + (f"\n{digest}" if digest else "")
    if status == "submission_unknown":
        return f"⚠ {title} 提交结果待核对，请在 Desktop 的任务卡核对状态。"
    return f"❌ {title} 失败：{_clip_head(str(item.get('error') or '没有错误信息').strip(), _DIGEST_LIMIT)}"


STATUS_NAMES = {"proposed": "等你确认下发", "send_failed": "下发失败", "running": "在做", "waiting": "在等人回答",
                "done": "执行结束，待审查", "failed": "失败", "stopped": "已停下",
                "sending": "正在下发", "submission_unknown": "提交结果待核对"}


def report_material(root: str, now: datetime):
    """今天的对话、今天相关的任务单、等你处理的事（attention_items，按紧急程度排好）。"""
    from src.gateway.turn_digest import recent_turns

    today = now.date()

    def local_day(value: str) -> Optional[Any]:
        try:
            return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(now.tzinfo).date()
        except (TypeError, ValueError):
            return None

    turns = [turn for turn in recent_turns(root, 200) if local_day(turn.get("at", "")) == today]
    items = [item for item in assistant_dispatch.list_dispatches(root) if item.get("status") != "cancelled"]
    active = [item for item in items if item.get("status") in ("proposed", "running", "waiting", "send_failed", "sending", "submission_unknown")
              or local_day(item.get("updated_at", "")) == today]
    # 「等你处理」用全入口共用的那一份（决策队列 + 任务单），不再只数任务单
    from src.workflow.tasks.attention import attention_items
    waiting = attention_items(root)
    return turns, active, waiting


def build_report(root: str, now: datetime, label: str) -> str:
    """早报 / 晚报正文：今天的对话、任务单状态、等你处理的。"""
    turns, active, waiting = report_material(root, now)
    lines = [f"🗒 {label}（{now:%m月%d日 %H:%M}）"]
    if turns:
        lines.append(f"今天聊了 {len(turns)} 件事。")
    for turn in turns[:5]:
        lines.append(f"· {turn.get('title')}：{turn.get('outcome') or '—'}")
    names = STATUS_NAMES
    if active:
        lines.append("")
        lines.append("任务单：")
        for item in active[:10]:
            freshness = " · 当前无法核实" if item.get("sync_state") == "unreachable" else ""
            lines.append(f"· [{names.get(item.get('status'), item.get('status'))}] {item.get('project')}：{item.get('title')}{freshness}")
    lines.append("")
    lines.append(f"等你处理：{len(waiting)} 件。" if waiting else "没有等你处理的事。")
    for item in waiting[:5]:
        lines.append(f"· {item.get('title')}")
    return "\n".join(lines)


def quiet_report(root: str, now: datetime, label: str) -> Optional[str]:
    """今天什么都没发生时的报告：早报一行（顺带说明助理还在），晚报不发（空串）。有事返回 None。"""
    turns, active, waiting = report_material(root, now)
    if turns or active or waiting:
        return None
    return f"🗒 {label}：今天没有需要你处理的事。" if label == "早报" else ""


_REPORT_SYSTEM = (
    "你是用户的私人助理「{name}」。{soul}下面是今天的工作素材（对话摘要、任务单状态、等用户处理的事）。"
    "写一份给用户手机上扫一眼的{label}：第一句先说要用户处理的事（没有就说没有），再用一两句讲今天做成了什么、"
    "还在进行什么。最多 5 行，每行不超过 40 个字；素材里没有的类别直接不提，不写「暂无」；"
    "不用标题、表情符号和加粗。口语、具体；只依据素材，不要编造素材里没有的结果。"
)


NEWS_ENV = "VORTOCODE_ASSISTANT_NEWS_ROOT"      # 有 signals 采集产出的项目目录；不设 = 早报不带新闻
_NEWS_MAX_AGE = timedelta(hours=24)
_NEWS_LIMIT = 6


def news_section(now: datetime) -> str:
    """早报末尾的新闻：最新一份 signals（24 小时内），确定性渲染、零模型调用。没有或过期就不带。"""
    root = os.getenv(NEWS_ENV, "").strip()
    if not root:
        return ""
    try:
        from src.workflow.pipeline.collect_cli import render_digest
        from src.workflow.pipeline.products import ProductStore

        product = ProductStore(root).latest("signals")
        if product is None:
            return ""
        created = datetime.fromisoformat(str(product.created).replace("Z", "+00:00"))
        if created.tzinfo is None:
            created = created.astimezone()
        if now - created > _NEWS_MAX_AGE:
            return ""
        return render_digest(product, limit=_NEWS_LIMIT)
    except Exception as error:  # noqa: BLE001 —— 新闻是附带的，读不到不影响早报
        logger.warning("早报新闻读取失败：%s", type(error).__name__)
        return ""


async def compose_report(root: str, now: datetime, label: str, *, llm: Any = None) -> str:
    """早报 / 晚报正文；早报末尾附上运维摘要（有运维心跳时）和新闻。

    新闻标题是外部内容：只原样附在模型写完的正文后面，**不进模型上下文**，免得被当成指令。
    """
    body = await _compose_body(root, now, label, llm=llm)
    if not body or label != "早报":
        return body
    from src.scheduling.ops_heartbeats import brief_line
    # 运维摘要只在配了运维心跳的常驻机器上有（没有报到记录就是空串）；同样不进模型上下文
    extras = [part for part in (brief_line(root, now), news_section(now)) if part]
    return "\n\n".join([body, *extras])


async def _compose_body(root: str, now: datetime, label: str, *, llm: Any = None) -> str:
    """早报 / 晚报：数据由台账给，话由模型说（像助理在汇报，而不是一张统计表）。模型不可用就发数据版。

    什么都没发生的日子不调模型：早报一行，晚报不发（返回空串，调用方当作已发）。
    """
    quiet = quiet_report(root, now, label)
    if quiet is not None:
        return quiet
    facts = build_report(root, now, label)
    try:
        from src.memory.assistant_profile import load_profile

        profile = load_profile(root)
        if llm is None:
            from src.llm.client import LLMClient
            llm = LLMClient()
        system = _REPORT_SYSTEM.format(name=profile["name"], label=label,
                                       soul=f"你的说话方式：{profile['soul']}。" if profile["soul"] else "")
        response = await asyncio.wait_for(
            llm.chat([{"role": "system", "content": system}, {"role": "user", "content": facts}],
                     temperature=0.4, max_tokens=1500), 90)
        text = str((response or {}).get("content") or "").strip()
    except Exception as error:  # noqa: BLE001 —— 写不出来就发数据版，汇报不能断
        logger.warning("早晚报改写失败，发数据版：%s", error)
        return facts
    return f"🗒 {label}\n{text}" if text else facts


async def _notify_accepted(notify: Notify, text: str) -> bool:
    try:
        accepted = bool(await notify(text))
    except Exception as error:  # noqa: BLE001 —— 一条失败不能挡住其它任务；取消继续向外传播
        logger.warning("助理通知未受理：%s", type(error).__name__)
        return False
    if not accepted:
        logger.warning("助理通知未受理，留待下轮巡检")
    return accepted


async def watch_once(root: str, now: datetime, *, notify: Notify, refresh=assistant_dispatch.refresh,
                     times: Optional[List[str]] = None, writer=None) -> None:
    """巡检一轮：刷新任务状态、推送变化、到点发报告。首次运行只记下现状，不把旧账推一遍。"""
    from src.workflow.workbench.goal_followup import poll_goal_followups
    try:  # 家里其它机器的定时活（备份同步、恢复演练）该报没报：每次断档推一次
        from src.scheduling.ops_heartbeats import overdue
        for message in overdue(root, now):
            await _notify_accepted(notify, message)
    except Exception as error:  # noqa: BLE001 —— 心跳检查失败不挡任务巡检
        logger.warning("运维心跳检查失败：%s", type(error).__name__)
    try:
        await poll_goal_followups(root, now, notify=notify)
    except Exception as error:  # Goals and dispatch refresh fail independently; cancellation propagates.
        logger.warning("个人目标巡检失败：%s", type(error).__name__)
    state = _load_state(root)
    first_run = state is None
    state = state or {"seen": {}, "reports": []}
    items = await refresh(root)
    item_ids = {item["id"] for item in items}
    seen: Dict[str, str] = {key: value for key, value in state.setdefault("seen", {}).items()
                            if key in item_ids}
    state["seen"] = seen
    if first_run:
        seen.update({item["id"]: str(item.get("status") or "") for item in items})
    # 首次快照整体保存，不能因首条通知失败，把下一轮误当成首次而吞掉重试。
    _save_state(root, state)
    if not first_run:
        for item in items:
            status = str(item.get("status") or "")
            if seen.get(item["id"]) == status:
                continue
            if status in _NOTIFY_STATUSES and not await _notify_accepted(notify, status_message(item)):
                continue
            seen[item["id"]] = status
            # 后续项失败或取消时，已受理的项不能被整轮重放。
            _save_state(root, state)
    sent: List[str] = state.setdefault("reports", [])
    due_reports = {}
    for hhmm in times if times is not None else report_times():
        hour, minute = map(int, hhmm.split(":"))
        due = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        key = f"{due:%Y-%m-%d} {hhmm}"
        if due <= now < due + _REPORT_GRACE and key not in sent:
            due_reports[key] = "早报" if hour < 12 else "晚报"
    # 只缓存当前补发窗口内的正文，重试不再次付费生成；过期/取消配置后删除。
    drafts: Dict[str, str] = {key: text for key, text in state.get("report_drafts", {}).items()
                              if key in due_reports and isinstance(text, str)}
    state["report_drafts"] = drafts
    _save_state(root, state)
    for key, label in due_reports.items():
        if key not in drafts:
            try:
                drafts[key] = await (writer or compose_report)(root, now, label)
            except Exception as error:  # noqa: BLE001 —— 一份报告失败不挡住其它时间槽
                logger.warning("助理报告生成失败：%s", type(error).__name__)
                continue
            _save_state(root, state)
        if not drafts[key] or await _notify_accepted(notify, drafts[key]):   # 空串：今天不必报
            sent.append(key)
            del drafts[key]
            state["reports"] = sent[-20:]
            _save_state(root, state)
    state["reports"] = sent[-20:]
    _save_state(root, state)


async def watch_loop(root: str, stop: asyncio.Event, *, notify: Optional[Notify] = None,
                     interval: float = INTERVAL_SECONDS) -> None:
    notify = notify or owner_reporter(root)
    while not stop.is_set():
        try:
            await watch_once(root, datetime.now().astimezone(), notify=notify)
        except Exception as error:  # noqa: BLE001 —— 巡检失败不拖垮服务，下一轮再来
            logger.warning("助理巡检失败：%s", error)
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval)
        except asyncio.TimeoutError:
            pass


def schedule_overview(root: str) -> List[Dict[str, Any]]:
    """右侧「定时」页：助理名下按时间自己会做的事。只读配置，不触发任何作业。"""
    items: List[Dict[str, Any]] = []
    if watch_enabled():
        for hhmm in report_times():
            label = "早报" if int(hhmm.split(":")[0]) < 12 else "晚报"
            items.append({"id": f"report-{hhmm}", "kind": "report", "title": label,
                          "schedule": f"每天 {hhmm}", "detail": "今天聊过的事、任务单状态、等你处理的", "enabled": True})
        items.append({"id": "follow-up", "kind": "watch", "title": "跟进派出去的活",
                      "schedule": f"每 {INTERVAL_SECONDS // 60} 分钟", "detail": "状态有变化就推到手机", "enabled": True})
    try:
        from src.gateway.workspace_scope import GENERAL, current_workspace_scope
        from src.workflow.workbench.personal_goals import PersonalGoals
        configured = current_workspace_scope() == GENERAL and watch_enabled()
        for goal in PersonalGoals(root).list():
            reminder = goal["follow_up"]
            if reminder.get("status") == "scheduled":
                items.append({"id": goal["id"], "kind": "goal", "title": f"目标提醒：{goal['objective'][:100]}",
                              "schedule": f"一次 · {reminder['due_at']}", "enabled": True,
                              "detail": "已批准的目标提醒；在目标卡中核对正文、暂停和通知结果。",
                              "scheduler_enabled": configured,
                              "next": reminder.get("next_attempt_at") or reminder["due_at"]})
    except (OSError, ValueError, KeyError, TypeError):
        logger.warning("个人目标提醒概览暂不可用")
    if os.getenv("VORTOCODE_HEARTBEAT", "").strip().lower() in ("1", "true", "yes", "on"):
        every = (os.getenv("VORTOCODE_HEARTBEAT_EVERY", "30m") or "30m").strip()
        items.append({"id": "heartbeat", "kind": "heartbeat", "title": "Heartbeat",
                      "schedule": f"每 {every}", "detail": "按 HEARTBEAT.md 检查单巡视，有情况才告诉你", "enabled": True})
    try:
        from src.scheduling.cron import CronState, next_due
        from src.scheduling.schedule_cards import config_snapshot

        now = datetime.now()
        state = CronState(root)
        jobs, revision = config_snapshot(root)
        scheduler_enabled = os.getenv("VORTOCODE_CRON", "").strip().lower() in ("1", "true", "yes", "on")
        for job in jobs:
            last = state.last_run(job.name)
            due = next_due(job.schedule, last, now) if job.enabled else None
            items.append({"id": f"cron-{job.name}", "kind": "cron", "title": job.name,
                          "schedule": job.schedule.raw, "detail": (job.prompt or job.command or "")[:200],
                          "enabled": bool(job.enabled), "revision": revision,
                          "scheduler_enabled": scheduler_enabled, "failures": state.failures(job.name),
                          "next": due.isoformat(timespec="minutes") if due else None,
                          "last": last.isoformat(timespec="minutes") if last else None})
    except Exception:  # noqa: BLE001 —— 作业表坏了不影响其余几项
        pass
    return items


def build_status_tools(root: str) -> list:
    """不联网的定时事项可用的只读状态工具：今天的动态、任务单、定时作业列表。

    全部只读、只读本机台账（任务单进度会问同机的项目运行时），没有任何出站通道——
    与「不联网的作业可以带关于你与记忆」同一条边界。联网作业不给。"""
    from src.agents.tool import Tool
    from src.gateway.turn_digest import recent_turns

    async def _today(_args: dict) -> str:
        now = datetime.now().astimezone()
        rows = []
        for turn in recent_turns(root, 100):
            try:
                at = datetime.fromisoformat(str(turn.get("at")).replace("Z", "+00:00")).astimezone(now.tzinfo)
            except (TypeError, ValueError):
                continue
            if at.date() == now.date():
                rows.append(f"- {at:%H:%M} {turn.get('title')}：{turn.get('outcome') or '—'}")
        return "今天聊过的事：\n" + "\n".join(rows) if rows else "今天还没有聊过什么。"

    async def _cron(_args: dict) -> str:
        items = schedule_overview(root)
        if not items:
            return "没有定时事项。"
        return "\n".join(f"- {item['title']}：{item['schedule']}{'' if item['enabled'] else '（已停用）'}"
                         + (("；未配置助理通知，提醒不会自动触发" if item.get("kind") == "goal" else "；调度总开关未开启，不会自动执行")
                            if item.get("scheduler_enabled") is False else "")
                         + (f"，上次定时执行 {item['last']}" if item.get("last") else "") for item in items)

    async def _history(args: dict) -> str:
        from src.scheduling.cron import load_jobs
        from src.scheduling.schedule_cards import recent_runs

        name = str(args.get("name") or "").strip()
        if name not in {job.name for job in load_jobs(root)}:
            return "没有这个定时作业；先用 schedule_list 查看名称。"
        rows = recent_runs(root, name)
        if not rows:
            return f"{name}：没有找到保留的执行记录，不能据此判断从未执行。"
        return json.dumps({"job": name, "retained_only": True, "runs": rows}, ensure_ascii=False)

    tools = [Tool("today_activity", "今天和用户聊过的事（每件一行：时间、标题、结果）。", {}, _today, untrusted_source=True),
             Tool("schedule_list", "列出定时事项（早晚报、跟进、你交代的作业）与上次运行时间。", {}, _cron, untrusted_source=True),
             Tool("schedule_history", "读取指定定时作业最近保留的执行记录、成功/失败及错误；空记录不代表从未执行。",
                  {"name": "定时作业的精确名称"}, _history, untrusted_source=True)]
    tools += [tool for tool in assistant_dispatch.build_dispatch_tools(root) if tool.name == "list_dispatches"]
    return tools
