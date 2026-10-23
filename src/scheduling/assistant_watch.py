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
import re
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional

from src.workflow.tasks import assistant_dispatch
from src.scheduling.patrols import Patrol, PatrolContext, overview as patrol_overview, run_patrols

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

    async def report(text: str, confirm_id: str = "", timed: bool = False) -> bool:
        try:
            return bool(await deliver(text, confirm_id=confirm_id, timed=timed))
        except Exception as error:  # noqa: BLE001 —— 推送是旁路
            logger.warning("助理汇报失败：%s", type(error).__name__)
            return False
    report.accepts_confirm = True  # type: ignore[attr-defined]  # 任务单通知可带「批准 / 拒绝」按钮
    report.accepts_timed = True  # type: ignore[attr-defined]    # 主人定时的提醒不受安静时段影响
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


def dispatch_pr_ready(item: Dict[str, Any]) -> bool:
    return (item.get("status") == "done" and not item.get("error") and not item.get("pr_url")
            and str(item.get("branch") or "").startswith("vorto/"))


def status_message(item: Dict[str, Any]) -> str:
    title = f"{item.get('project')}：{item.get('title')}"
    status = item.get("status")
    if status == "proposed":
        if item.get("retry_note"):        # 助理复盘失败后起草的下一次：把换的做法摆出来，你看了再批
            title += f"（第 {item.get('attempt') or 2} 次）\n这次换个做法：{_clip_head(str(item['retry_note']), 400)}"
        if item.get("tainted"):
            return (f"📝 助理写好了任务单，等你确认下发——{title}\n这张是在读过网页等外部内容后写的，"
                    "为安全起见只能在 Desktop 助理的「动态」里确认。")
        return (f"📝 助理写好了任务单，等你确认下发——{title}\n点「批准」就交给常驻电脑去做，「拒绝」撤回；"
                "也可以在 Desktop 助理的「动态」里确认。回复文字不会下发。")
    if status == "waiting":
        question = item.get("question") if isinstance(item.get("question"), dict) else None
        if not question:
            return f"⏸ {title} 在等人回答，到工作台的常驻电脑项目里处理。"
        options = question.get("options") or []
        text = f"❓ {title} 做到一半要问你：\n{_clip_head(str(question.get('question') or ''), 600)}"
        if options:
            text += "\n建议：" + "\n".join(f"{index}. {option}" for index, option in enumerate(options, 1))
        return text + "\n直接回我答案（选项回编号也行），我转给它接着做；24 小时内有效。"
    if status == "done":
        branch = f"，交付分支 {item['branch']}" if item.get("branch") else ""
        digest = result_digest(str(item.get("result") or ""))
        evidence = item.get("delivery_evidence") if isinstance(item.get("delivery_evidence"), dict) else {}
        checked = {"passed": "自动验证通过", "failed": "⚠ 自动验证没通过", "stale": "验证结果已过期（之后又有提交）"}.get(
            str(evidence.get("verification") or ""), "还没有这次提交的验证结果")
        text = f"✅ {title} 做完了，等待审查{branch}；{checked}。" + (f"\n{digest}" if digest else "")
        text += _acceptance_lines(item)
        if dispatch_pr_ready(item):
            text += "\n点「批准」开一个 draft PR 给你审（合并仍由你在 GitHub 上点），「拒绝」先放着。"
        return text
    if status == "submission_unknown":
        return f"⚠ {title} 提交结果待核对，请在 Desktop 的任务卡核对状态。"
    retry = ("\n要再试，回我「改一改再派」并说说想怎么改，我带上这次的错误重写任务单。"
             if int(item.get("attempt") or 1) < assistant_dispatch.MAX_ATTEMPTS else
             f"\n这件事已经试了 {assistant_dispatch.MAX_ATTEMPTS} 次，建议换个做法或拆小再派。")
    outcome = item.get("outcome") if isinstance(item.get("outcome"), dict) else {}
    if outcome.get("kind") == "diagnosis":
        retry = f"\n我的判断：{_clip_head(str(outcome.get('diagnosis') or ''), 300)}"
        if outcome.get("draft_id"):
            retry += f"\n已拟好第 {int(item.get('attempt') or 1) + 1} 次的任务单（换个做法：{_clip_head(str(outcome.get('change') or ''), 200)}），马上推给你确认。"
        elif outcome.get("capped"):
            retry += f"\n这件事已经试了 {assistant_dispatch.MAX_ATTEMPTS} 次，建议换个做法或拆小再派。"
        else:
            retry += "\n换个做法也难成，需要你拿主意：回我想怎么改，我重写任务单。"
    return f"❌ {title} 失败：{_clip_head(str(item.get('error') or '没有错误信息').strip(), _DIGEST_LIMIT)}" + retry


def goal_message(item: Dict[str, Any]) -> str:
    link = item.get("goal_link") or {}
    text = (f"🎯 「{item.get('title')}」是为目标「{_clip_head(str(link.get('objective') or ''), 80)}」做的，"
            f"对应验收标准：{_clip_head(str(link.get('criterion') or ''), 120)}")
    outcome = item.get("outcome") if isinstance(item.get("outcome"), dict) else {}
    rows = outcome.get("criteria") if outcome.get("kind") == "acceptance" else None
    if rows:
        text += f"\n（助理对照任务验收标准：{sum(row.get('verdict') == 'met' for row in rows)}/{len(rows)} 条对上）"
    return text + "\n做完了算不算达成这一条？点「批准」记为通过（等于你亲自确认），「拒绝」先不算。"


_MARKS = {"met": "✓", "unmet": "✗", "unknown": "?"}


def _acceptance_lines(item: Dict[str, Any]) -> str:
    """做完时助理对照验收标准的复盘：逐条标对上 / 没对上 / 看不出。没写验收标准或没复盘就不写。"""
    outcome = item.get("outcome") if isinstance(item.get("outcome"), dict) else {}
    rows = outcome.get("criteria") if outcome.get("kind") == "acceptance" else None
    if not rows:
        return ""
    met = sum(row.get("verdict") == "met" for row in rows)
    lines = [f"\n验收对照（助理看输出判断，不代表人工验收）：{met}/{len(rows)} 条对上"]
    for row in rows[:6]:
        evidence = f"——{_clip_head(str(row.get('evidence') or ''), 80)}" if row.get("evidence") else ""
        lines.append(f"{_MARKS.get(str(row.get('verdict')), '?')} {_clip_head(str(row.get('text') or ''), 80)}{evidence}")
    return "\n".join(lines)


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


def _before_today(value: Any, now: datetime) -> bool:
    try:
        moment = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return False
    moment = moment if moment.tzinfo else moment.astimezone()
    return moment.astimezone(now.tzinfo).date() < now.date()


def _only_old_proposals(active: List[Dict[str, Any]], waiting: List[Dict[str, Any]], now: datetime) -> bool:
    """今天唯一的「事」是早就写好、今天没动过的待确认任务单：早报已经提过、按钮也在，晚报不必再说一遍。"""
    if not active or any(item.get("status") != "proposed" or not _before_today(item.get("updated_at"), now)
                         for item in active):
        return False
    return all(item.get("kind") == "dispatch" and str(item.get("id", "")).endswith(":proposed")
               and _before_today(item.get("created"), now) for item in waiting)


def quiet_report(root: str, now: datetime, label: str) -> Optional[str]:
    """今天什么都没发生时的报告：早报一行（顺带说明助理还在），晚报不发（空串）。有事返回 None。

    只剩早就写好、今天没动过的待确认任务单时，早报照常提（带按钮），晚报不再重复
    （真机 2026-10-21：同一张单子两天推了 6 次，晚报整份只有这一句）。"""
    turns, active, waiting = report_material(root, now)
    if label != "早报" and not turns and _only_old_proposals(active, waiting, now):
        return ""
    if turns or active or waiting:
        return None
    return f"🗒 {label}：今天没有需要你处理的事。" if label == "早报" else ""


_REPORT_SYSTEM = (
    "你是用户的私人助理「{name}」。{soul}下面是今天的工作素材（对话摘要、任务单状态、等用户处理的事）。"
    "写一份给用户手机上扫一眼的{label}：第一句先说要用户处理的事（没有就说没有），再用一两句讲今天做成了什么、"
    "还在进行什么。最多 5 行，每行不超过 40 个字；素材里没有的类别直接不提，不写「暂无」；"
    "不用标题、表情符号和加粗。口语、具体；只依据素材，不要编造素材里没有的结果。"
    "同一件事在素材里可能出现好几次（聊过、任务单、等你处理），只说一次；不写内部编号。"
    "直接对用户说事，不要提「素材」这个词，也不要写「没有别的可说」这类话（没有就不写）。"
)


def _drop_meta_lines(text: str) -> str:
    """模型偶尔把内部用词说出来（真机 2026-10-22 早报：「素材里没有别的进展可说」）：这种行整行去掉。"""
    return "\n".join(line for line in text.splitlines() if "素材" not in line).strip()

# 任务单 / 后台任务 / 目标的内部编号：对手机上的人没有意义，写进早晚报只是噪音（真机 2026-10-20 晚报）
_INTERNAL_ID = re.compile(r"[ \t]*[（(]?\b(?:dsp|bg-task|task|personal|cdx|dpr|dgl|run|plan)-[0-9a-f]{6,}\b[）)]?")


def _scrub_ids(text: str) -> str:
    return re.sub(r"[ \t]{2,}", " ", _INTERNAL_ID.sub("", text))


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
    if body and label == "晚报" and now.weekday() == 6:
        # 周日晚报附一段本周派活成绩（确定性数字，不进模型上下文）；这周没下发过就不带
        try:
            from src.workflow.tasks.dispatch_stats import dispatch_stats, stats_text
            stats = dispatch_stats(root, now, 7)
            if stats["sent"]:
                body += "\n\n" + stats_text(stats, title="📊 本周派活")
        except Exception as error:  # noqa: BLE001 —— 统计是附带的
            logger.warning("派活统计失败：%s", type(error).__name__)
    if label == "晚报":
        # 今天 Codex 各会话做成了什么（确定性数字和 Codex 自己的话，不进模型上下文）；
        # 别的都没发生、但 Codex 干了一天活的日子，晚报也发这一段
        from src.scheduling.codex_watch import day_summary
        codex_day = day_summary(root, now)
        if codex_day:
            body = f"{body or '🗒 晚报'}\n\n{codex_day}"
    if not body or label != "早报":
        return body
    from src.scheduling.ops_heartbeats import brief_line
    # 运维摘要只在配了运维心跳的常驻机器上有（没有报到记录就是空串）；同样不进模型上下文
    from src.scheduling.codex_watch import brief as codex_brief
    # 插件的今日段落（如日历）：外部内容，同样只附在正文后面
    from src.plugins import morning_briefs
    calendar = await morning_briefs(now)
    # 昨天的模型用量（只在早报带一行；数字来自本机台账，不进模型上下文）
    from src.llm.usage_ledger import summary_line
    usage = summary_line(now) if label == "早报" else ""
    extras = [part for part in (*calendar, codex_brief(root, now), brief_line(root, now), usage, news_section(now))
              if part]
    return "\n\n".join([body, *extras])


async def _compose_body(root: str, now: datetime, label: str, *, llm: Any = None) -> str:
    """早报 / 晚报：数据由台账给，话由模型说（像助理在汇报，而不是一张统计表）。模型不可用就发数据版。

    什么都没发生的日子不调模型：早报一行，晚报不发（返回空串，调用方当作已发）。
    """
    quiet = quiet_report(root, now, label)
    if quiet is not None:
        return quiet
    facts = _scrub_ids(build_report(root, now, label))
    try:
        from src.memory.assistant_profile import load_profile

        profile = load_profile(root)
        if llm is None:
            from src.llm.client import LLMClient
            llm = LLMClient()
        system = _REPORT_SYSTEM.format(name=profile["name"], label=label,
                                       soul=f"你的说话方式：{profile['soul']}。" if profile["soul"] else "")
        from src.llm.usage_ledger import usage_source
        with usage_source("早晚报"):
            response = await asyncio.wait_for(
                llm.chat([{"role": "system", "content": system}, {"role": "user", "content": facts}],
                         temperature=0.4, max_tokens=1500), 90)
        text = _drop_meta_lines(str((response or {}).get("content") or ""))
    except Exception as error:  # noqa: BLE001 —— 写不出来就发数据版，汇报不能断
        logger.warning("早晚报改写失败，发数据版：%s", error)
        return facts
    return f"🗒 {label}\n{_scrub_ids(text)}" if text else facts


async def _notify_accepted(notify: Notify, text: str, confirm_id: str = "") -> bool:
    try:
        if confirm_id and getattr(notify, "accepts_confirm", False):
            accepted = bool(await notify(text, confirm_id=confirm_id))  # type: ignore[call-arg]
        else:
            accepted = bool(await notify(text))
    except Exception as error:  # noqa: BLE001 —— 一条失败不能挡住其它任务；取消继续向外传播
        logger.warning("助理通知未受理：%s", type(error).__name__)
        return False
    if not accepted:
        logger.warning("助理通知未受理，留待下轮巡检")
    return accepted


_REMINDED_KEEP = timedelta(days=2)
_UNTIMED_PER_ROUND = 3


async def _usage_runaway(now: datetime, state: Dict[str, Any], notify: Notify) -> None:
    """模型用量当天暴涨（后台任务可能在反复调用）：一天只提醒一次；没推出去下一轮再试。"""
    from src.llm.usage_ledger import runaway
    day = now.date().isoformat()
    if state.get("usage_alerted") == day:
        return
    try:
        text = runaway(now)
    except Exception as error:  # noqa: BLE001 —— 台账读不了不挡巡检
        logger.warning("用量检查失败：%s", type(error).__name__)
        return
    if text and await _notify_accepted(notify, text):
        state["usage_alerted"] = day


async def _plugin_reminders(now: datetime, state: Dict[str, Any], notify: Notify) -> None:
    """插件的提醒（日程快开始了、新邮件）：同一件只推一次；日程是主人自己定的时间，不受安静时段影响。"""
    from src.plugins import due_reminders
    reminded: Dict[str, str] = {}
    for key, value in (state.get("reminded") or {}).items():
        when = _when(value)
        if when is not None and now - when < _REMINDED_KEEP:
            reminded[key] = value
    state["reminded"] = reminded
    untimed = 0
    for key, text, timed in await due_reminders(now):
        if key in reminded:
            continue
        if not timed:                    # 新邮件这类：一轮最多推几条，多出来的下一轮再推
            if untimed >= _UNTIMED_PER_ROUND:
                continue
            untimed += 1
        try:
            if timed and getattr(notify, "accepts_timed", False):
                accepted = bool(await notify(text, timed=True))  # type: ignore[call-arg]
            else:
                accepted = bool(await notify(text))
        except Exception as error:  # noqa: BLE001
            logger.warning("插件提醒未受理：%s", type(error).__name__)
            continue
        if accepted:
            reminded[key] = now.isoformat()


# —— 巡检登记表：每一项各自计时、各自失败；新提醒在这里登记一项即可（见 patrols.py） ——

async def _patrol_ops(ctx: PatrolContext) -> None:
    """家里其它机器的定时活（备份同步、恢复演练）该报没报：每次断档推一次。"""
    from src.scheduling.ops_heartbeats import overdue
    for message in overdue(ctx.root, ctx.now):
        await _notify_accepted(ctx.notify, message)


async def _patrol_goals(ctx: PatrolContext) -> None:
    from src.workflow.workbench.goal_followup import poll_goal_followups
    await poll_goal_followups(ctx.root, ctx.now, notify=ctx.notify)


async def _patrol_codex(ctx: PatrolContext) -> None:
    """定时监管 Mac 上的 Codex：卡住 / 连续失败 / 做完后判断下一步 / 定时进度（节奏由它自己的配置管）。"""
    from src.scheduling.codex_supervisor import supervise_once
    await supervise_once(ctx.root, ctx.now, notify=ctx.notify)


async def _patrol_usage(ctx: PatrolContext) -> None:
    await _usage_runaway(ctx.now, ctx.state, ctx.notify)


async def _patrol_plugins(ctx: PatrolContext) -> None:
    await _plugin_reminders(ctx.now, ctx.state, ctx.notify)


def _has_ops(root: str) -> bool:
    from src.scheduling.ops_heartbeats import sources
    return bool(sources(root))


def _has_codex(root: str) -> bool:
    from src.scheduling.codex_watch import load_state
    return bool(load_state(root).get("received_at"))


def _codex_schedule(root: str) -> str:
    from src.scheduling.codex_supervisor import load_config
    return f"每 {load_config(root)['interval_minutes']} 分钟"


def _has_reminder_plugins(_root: str) -> bool:
    from src.plugins import enabled_plugins
    return any(plugin.reminders is not None for plugin in enabled_plugins())


PATROLS: List[Patrol] = [
    Patrol("ops", "运维心跳", "家里其它机器的定时活（备份、恢复演练）报失败或该报没报时推一条",
           _patrol_ops, visible=_has_ops),
    Patrol("goals", "个人目标提醒", "到点推你批准过的目标提醒", _patrol_goals, visible=lambda _root: False),
    Patrol("codex", "监管 Codex", "看着 Mac 上的 Codex：卡住、出错、要你定方向时找你，定时汇报进度",
           _patrol_codex, visible=_has_codex, schedule=_codex_schedule),
    Patrol("usage", "模型用量报警", "当天用量超过 200 万 token 且是前 7 天日均的 3 倍时提醒一次", _patrol_usage),
    Patrol("plugins", "日程与新邮件提醒", "日程开始前、刚收到的私人邮件，推到手机",
           _patrol_plugins, visible=_has_reminder_plugins),
]


async def watch_once(root: str, now: datetime, *, notify: Notify, refresh=assistant_dispatch.refresh,
                     times: Optional[List[str]] = None, writer=None, review=None) -> None:
    """巡检一轮：先跑登记的各项巡检，再刷新任务状态、推送变化、到点发报告。首次运行只记下现状，不把旧账推一遍。"""
    state = _load_state(root)
    first_run = state is None
    state = state or {"seen": {}, "reports": []}
    await run_patrols(PATROLS, PatrolContext(root=root, now=now, notify=notify, state=state))
    _save_state(root, state)
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
            if review is not None and status in {"done", "failed"}:
                # 结束了先复盘：做完对照验收标准，失败诊断原因、值得再试就起草下一次（下发仍要你批）
                from src.scheduling.dispatch_review import follow_up
                item = await follow_up(root, item, review)
            # 等确认的任务单带「批准 / 拒绝」按钮（读过外部内容的不带，只能在 Desktop 确认）
            confirm_id = item["id"] if status == "proposed" and not item.get("tainted") else ""
            if status == "done" and dispatch_pr_ready(item):
                confirm_id = assistant_dispatch.pr_confirm_id(item["id"])
            if status in _NOTIFY_STATUSES and not await _notify_accepted(notify, status_message(item), confirm_id):
                continue
            if status == "done" and assistant_dispatch.goal_confirm_ready(item):
                # 挂在个人目标上的活：做完问你算不算达成那条验收标准（只问一次；没送到的在任务卡上还能点）
                await _notify_accepted(notify, goal_message(item), assistant_dispatch.goal_confirm_id(item["id"]))
            seen[item["id"]] = status
            # 后续项失败或取消时，已受理的项不能被整轮重放。
            _save_state(root, state)
    if not first_run:
        await _nudge_overdue(root, now, items, state, notify)
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
        text, confirm_id, covered = drafts[key], "", ""
        pending = [item for item in items if item.get("status") == "proposed"]
        if text and len(pending) == 1 and not pending[0].get("tainted"):
            # 只有一张单子等你确认：报告直接带它的「批准 / 拒绝」，这张单子就不再另推「写好一天了」
            only = pending[0]
            text += f"\n\n下面的按钮对应等你确认的任务单：{only.get('project')}：{only.get('title')}"
            confirm_id = covered = only["id"]
        if not text or await _notify_accepted(notify, text, confirm_id):   # 空串：今天不必报
            if covered and getattr(notify, "accepts_confirm", False):
                state.setdefault("nudged", {})[covered] = "pending"
            sent.append(key)
            del drafts[key]
            state["reports"] = sent[-20:]
            _save_state(root, state)
    state["reports"] = sent[-20:]
    _save_state(root, state)


LONG_RUNNING = timedelta(hours=3)            # 下发后这么久还没结束：提醒一次，可能卡住
WAITING_REMIND = timedelta(hours=6)          # 任务的问题这么久没人答：再提醒一次（问题一般 24 小时过期）
PROPOSED_REMIND = timedelta(hours=24)        # 写好的任务单这么久没人确认：再提醒一次
PROPOSED_EXPIRE = timedelta(days=7)          # 一周没确认：自动撤回（还没执行过，撤回不丢东西）


def _when(value: Any) -> Optional[datetime]:
    try:
        moment = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    return moment if moment.tzinfo else moment.astimezone()


async def _nudge_overdue(root: str, now: datetime, items: List[Dict[str, Any]], state: Dict[str, Any],
                         notify: Notify) -> None:
    """没人管的单子：跑太久的提醒一次；等确认太久的提醒一次，一周后自动撤回。"""
    now = now if now.tzinfo else now.astimezone()
    ids = {item["id"] for item in items}
    nudged: Dict[str, str] = {key: value for key, value in (state.get("nudged") or {}).items() if key in ids}
    state["nudged"] = nudged
    for item in items:
        title = f"{item.get('project')}：{item.get('title')}"
        status = item.get("status")
        if status == "running":
            # 你回答过问题它才接着做：从接着做的那一刻重新计时
            started = str(item.get("resumed_at") or item.get("sent_at") or "")
            since = _when(started)
            if since and now - since >= LONG_RUNNING and nudged.get(item["id"]) != f"long:{started}":
                hours = int((now - since).total_seconds() // 3600)
                where = assistant_dispatch.progress_line(item)
                if await _notify_accepted(notify, f"🐢 {title} 已经做了 {hours} 小时还没结束，可能卡住了。"
                                                  + (f"现在：{where}。" if where else "")
                                                  + "到 Desktop 的任务卡或常驻电脑项目里看一眼。"):
                    nudged[item["id"]] = f"long:{started}"
        elif status == "waiting" and isinstance(item.get("question"), dict):
            question = item["question"]
            since = _when(question.get("seen_at"))
            key = f"ask:{question.get('id')}"
            if since and now - since >= WAITING_REMIND and nudged.get(item["id"]) != key:
                if await _notify_accepted(notify, f"⏳ {title} 还在等你回答（问了 {int((now - since).total_seconds() // 3600)} 小时）："
                                                  f"{_clip_head(str(question.get('question') or ''), 200)}\n"
                                                  "直接回我答案就行；问题发出 24 小时后就过期、答不上了。"):
                    nudged[item["id"]] = key
        elif status == "proposed":
            since = _when(item.get("created_at"))
            if not since:
                continue
            if now - since >= PROPOSED_EXPIRE:
                try:
                    assistant_dispatch.cancel(root, item["id"])
                except assistant_dispatch.DispatchError:
                    continue
                await _notify_accepted(notify, f"🗑 {title} 写好一周了一直没确认，已自动撤回（还没执行过）。要做的话跟我说一声重写。")
                nudged[item["id"]] = "expired"
            elif now - since >= PROPOSED_REMIND and nudged.get(item["id"]) != "pending":
                confirm_id = "" if item.get("tainted") else item["id"]
                if await _notify_accepted(notify, f"📝 {title} 写好一天了还在等你确认。{'点「批准」下发，「拒绝」撤回；' if confirm_id else ''}"
                                                  "一周不确认会自动撤回。", confirm_id):
                    nudged[item["id"]] = "pending"


async def watch_loop(root: str, stop: asyncio.Event, *, notify: Optional[Notify] = None,
                     interval: float = INTERVAL_SECONDS) -> None:
    notify = notify or owner_reporter(root)
    while not stop.is_set():
        try:
            from src.scheduling.dispatch_review import review_outcome
            await watch_once(root, datetime.now().astimezone(), notify=notify, review=review_outcome)
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
        items += patrol_overview(PATROLS, root)
        for item in items:
            if item["id"] == "patrol-codex":       # 监管 Codex 的说明按它当前的配置写
                from src.scheduling.codex_supervisor import describe_config, load_config
                item["detail"] = describe_config(load_config(root))
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

    async def _notices(args: dict) -> str:
        from src.scheduling.notices import load_notices
        try:
            limit = max(1, min(20, int(args.get("limit") or 8)))
        except (TypeError, ValueError):
            limit = 8
        rows = []
        for notice in load_notices(root, limit):
            try:
                at = datetime.fromisoformat(str(notice.get("ts"))).astimezone()
                when = f"{at:%m-%d %H:%M}"
            except (TypeError, ValueError):
                when = "?"
            rows.append(f"[{when}]\n{str(notice.get('text') or '')[:800]}")
        return "最近推给用户的通知（新的在前）：\n\n" + "\n\n".join(rows) if rows else "最近没有推过通知。"

    tools = [Tool("recent_notices",
                  "你最近主动推给用户手机的通知（Codex 巡视汇报、提醒、任务单状态、早晚报），新的在前。"
                  "用户的话像是在回复一条通知（「就按这个做」「让它补测试」「第二个怎么回事」）时先查它，弄清在回哪条。",
                  {"limit": "条数，可选，默认 8，最多 20"}, _notices, untrusted_source=True),
             Tool("today_activity", "今天和用户聊过的事（每件一行：时间、标题、结果）。", {}, _today, untrusted_source=True),
             Tool("schedule_list", "列出定时事项（早晚报、跟进、你交代的作业）与上次运行时间。", {}, _cron, untrusted_source=True),
             Tool("schedule_history", "读取指定定时作业最近保留的执行记录、成功/失败及错误；空记录不代表从未执行。",
                  {"name": "定时作业的精确名称"}, _history, untrusted_source=True)]
    tools += [tool for tool in assistant_dispatch.build_dispatch_tools(root) if tool.name == "list_dispatches"]
    return tools
