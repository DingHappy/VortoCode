"""常驻助理的巡检与汇报：Desktop 关着也照常跟进派出去的活，有变化推到手机，早晚各报一次。

- 每分钟向项目运行时刷新已下发任务的状态（assistant_dispatch.refresh），状态一变就推送：
  新写好等你确认的任务单、完成（含分支）、失败、在等人回答。手机上只是提醒，确认下发仍在 Desktop。
- 早报 / 晚报（默认 09:00、21:00，本机时区）：今天聊了几件事、任务单各状态几件、哪些等你处理。
  直接从台账生成，不调模型。
- 推送：本进程持有 IM 桥就直接发；否则交给 ``VORTOCODE_OWNER_NOTIFY_URL`` 指向的同机服务。
  两者都没有就不启动——Desktop 本机的通用会话（IM 固定关、不配转发）不会冒出推送。
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional

from src.workflow.tasks import assistant_dispatch

logger = logging.getLogger(__name__)

INTERVAL_SECONDS = 60
_REPORT_GRACE = timedelta(hours=2)          # 错过整点（重启、断网）两小时内补发，再晚就不补
_NOTIFY_STATUSES = {"proposed", "waiting", "done", "failed", "send_failed"}

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
            await deliver(text)
            return True
        except Exception as error:  # noqa: BLE001 —— 推送是旁路
            logger.warning("助理汇报失败：%s", error)
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
    path = _state_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def status_message(item: Dict[str, Any]) -> str:
    title = f"{item.get('project')}：{item.get('title')}"
    status = item.get("status")
    if status == "proposed":
        return f"📝 助理写好了任务单，等你确认下发——{title}\n在 Desktop 助理的「动态」里确认（手机上回复不会下发）。"
    if status == "waiting":
        return f"⏸ {title} 在等人回答，到工作台的常驻电脑项目里处理。"
    if status == "done":
        branch = f"，交付分支 {item['branch']}" if item.get("branch") else ""
        result = str(item.get("result") or "").strip()
        return f"✅ {title} 完成{branch}。" + (f"\n{result[-300:]}" if result else "")
    return f"❌ {title} 失败：{str(item.get('error') or '没有错误信息')[:300]}"


def build_report(root: str, now: datetime, label: str) -> str:
    """早报 / 晚报正文：今天的对话、任务单状态、等你处理的。"""
    from src.gateway.turn_digest import recent_turns

    today = now.date()

    def local_day(value: str) -> Optional[Any]:
        try:
            return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(now.tzinfo).date()
        except (TypeError, ValueError):
            return None

    turns = [turn for turn in recent_turns(root, 200) if local_day(turn.get("at", "")) == today]
    items = [item for item in assistant_dispatch.list_dispatches(root) if item.get("status") != "cancelled"]
    active = [item for item in items if item.get("status") in ("proposed", "running", "waiting", "send_failed")
              or local_day(item.get("updated_at", "")) == today]
    lines = [f"🗒 {label}（{now:%m月%d日 %H:%M}）", f"今天聊了 {len(turns)} 件事。"]
    for turn in turns[:5]:
        lines.append(f"· {turn.get('title')}：{turn.get('outcome') or '—'}")
    names = {"proposed": "等你确认下发", "send_failed": "下发失败", "running": "在做", "waiting": "在等人回答",
             "done": "完成", "failed": "失败", "stopped": "已停下"}
    if active:
        lines.append("")
        lines.append("任务单：")
        for item in active[:10]:
            lines.append(f"· [{names.get(item.get('status'), item.get('status'))}] {item.get('project')}：{item.get('title')}")
    waiting = [item for item in items if item.get("status") in ("proposed", "waiting", "send_failed")]
    lines.append("")
    lines.append(f"等你处理：{len(waiting)} 件。" if waiting else "没有等你处理的事。")
    return "\n".join(lines)


_REPORT_SYSTEM = (
    "你是用户的私人助理「{name}」。{soul}下面是今天的工作素材（对话摘要、任务单状态、等用户处理的事）。"
    "写一份给用户手机上看的{label}：第一句先说要他处理的事（没有就说没有），再用两三句讲今天做成了什么、"
    "还在进行什么。不超过 8 行，口语、具体；只依据素材，不要编造素材里没有的结果。"
)


async def compose_report(root: str, now: datetime, label: str, *, llm: Any = None) -> str:
    """早报 / 晚报：数据由台账给，话由模型说（像助理在汇报，而不是一张统计表）。模型不可用就发数据版。"""
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


async def watch_once(root: str, now: datetime, *, notify: Notify, refresh=assistant_dispatch.refresh,
                     times: Optional[List[str]] = None, writer=None) -> None:
    """巡检一轮：刷新任务状态、推送变化、到点发报告。首次运行只记下现状，不把旧账推一遍。"""
    state = _load_state(root)
    first_run = state is None
    state = state or {"seen": {}, "reports": []}
    items = await refresh(root)
    seen: Dict[str, str] = state.setdefault("seen", {})
    for item in items:
        status = str(item.get("status") or "")
        if seen.get(item["id"]) == status:
            continue
        if not first_run and status in _NOTIFY_STATUSES:
            await notify(status_message(item))
        seen[item["id"]] = status
    sent: List[str] = state.setdefault("reports", [])
    for hhmm in times if times is not None else report_times():
        hour, minute = map(int, hhmm.split(":"))
        due = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        key = f"{due:%Y-%m-%d} {hhmm}"
        if due <= now < due + _REPORT_GRACE and key not in sent:
            label = "早报" if hour < 12 else "晚报"
            await notify(await (writer or compose_report)(root, now, label))
            sent.append(key)
    state["reports"] = sent[-20:]
    state["seen"] = {key: value for key, value in seen.items() if key in {item["id"] for item in items}}
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
    if os.getenv("VORTOCODE_HEARTBEAT", "").strip().lower() in ("1", "true", "yes", "on"):
        every = (os.getenv("VORTOCODE_HEARTBEAT_EVERY", "30m") or "30m").strip()
        items.append({"id": "heartbeat", "kind": "heartbeat", "title": "Heartbeat",
                      "schedule": f"每 {every}", "detail": "按 HEARTBEAT.md 检查单巡视，有情况才告诉你", "enabled": True})
    try:
        from src.scheduling.cron import CronState, load_jobs, next_due

        now = datetime.now()
        state = CronState(root)
        for job in load_jobs(root):
            last = state.last_run(job.name)
            due = next_due(job.schedule, last, now) if job.enabled else None
            items.append({"id": f"cron-{job.name}", "kind": "cron", "title": job.name,
                          "schedule": job.schedule.raw, "detail": (job.prompt or job.command or "")[:200],
                          "enabled": bool(job.enabled),
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
                         + (f"，上次 {item['last']}" if item.get("last") else "") for item in items)

    tools = [Tool("today_activity", "今天和用户聊过的事（每件一行：时间、标题、结果）。", {}, _today),
             Tool("schedule_list", "列出定时事项（早晚报、跟进、你交代的作业）与上次运行时间。", {}, _cron)]
    tools += [tool for tool in assistant_dispatch.build_dispatch_tools(root) if tool.name == "list_dispatches"]
    return tools
