"""通知台账 —— 无人值守产出里"人该看到"的那一路（cron 例行作业 / heartbeat 值班）。

例行产出**不进任何人类会话历史**（run lane 规则，见 `cron.py`）：需要人知道的事落这条 JSONL 台账
（`.vortocode/logs/notices.jsonl`，`logs/` 已在 `.vortocode` 自忽略清单内），再由 GET /api/notices
查询、WS `notice` 广播、IM 推 owner 三路投递。**台账是唯一有持久保证的一路**，另两路 best-effort。

放在 gateway 而不是留在 web 路由里：CLI（`vc cron run`）与常驻调度循环都要写它，
不该为了记一条通知去导入整个 FastAPI 层。`src/web/routers/tasks.py` 从这里再导出，保持旧入口可用。
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

MAX_NOTICES = 500

# 未送达补发：队列上限 / 每条最多重试几次 / 多久算过期（小时）。
# 三个都要有：没有上限会把队列涨爆；没有重试上限，一条永远送不出去的通知会每次恢复都刷一遍；
# 没有过期，第二天补发昨天的"作业跑完了"纯属噪音（人早就自己去看过了）。
MAX_UNDELIVERED = 50
MAX_ATTEMPTS = 3
STALE_HOURS = 12


def _path(repo_root: str) -> Path:
    return Path(repo_root) / ".vortocode" / "logs" / "notices.jsonl"


def _undelivered_path(repo_root: str) -> Path:
    return Path(repo_root) / ".vortocode" / "logs" / "undelivered.jsonl"


def record_notice(repo_root: str, text: str, *, source: str = "scheduler",
                  trigger: str = "") -> None:
    """往通知台账追加一条（JSONL，尾部截断到 MAX_NOTICES）。best-effort，出错不抛。

    `source` 是**这条通知属于谁**（`cron:<作业名>` / `delivery` / `self-heal:*`…），
    `trigger` 是**谁按的**（`scheduler` 定时到点 / `manual` 人手动触发）。两者分开记：
    2026-07-27 排查漏推时靠 source 认作业，而我在收口投递器时把它统一写成了 `scheduler`，
    等于把"哪个作业出的"这条线索抹掉了——按下去的手和产出的人本来就是两件事。
    """
    p = _path(repo_root)
    try:
        from src.agents.dev_plan import ensure_state_gitignore
        ensure_state_gitignore(repo_root)
        p.parent.mkdir(parents=True, exist_ok=True)
        payload: Dict[str, Any] = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "source": str(source)[:120], "text": str(text)[:2000]}
        if trigger:
            payload["trigger"] = str(trigger)[:32]
        entry = json.dumps(payload, ensure_ascii=False)
        lines = []
        if p.is_file():
            lines = p.read_text(encoding="utf-8").splitlines()[-(MAX_NOTICES - 1):]
        lines.append(entry)
        tmp = p.with_suffix(".jsonl.tmp")
        tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
        tmp.replace(p)
    except (OSError, TypeError, ValueError):
        pass


# --------------------------------------------------------------------- 未送达队列（补发用）
def _read_undelivered(repo_root: str) -> List[Dict[str, Any]]:
    p = _undelivered_path(repo_root)
    if not p.is_file():
        return []
    out: List[Dict[str, Any]] = []
    try:
        for ln in p.read_text(encoding="utf-8").splitlines():
            try:
                item = json.loads(ln)
            except ValueError:
                continue
            if isinstance(item, dict) and item.get("text"):
                out.append(item)
    except OSError:
        return []
    return out


def _write_undelivered(repo_root: str, items: List[Dict[str, Any]]) -> None:
    p = _undelivered_path(repo_root)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        if not items:
            p.unlink(missing_ok=True)
            return
        body = "\n".join(json.dumps(x, ensure_ascii=False) for x in items[-MAX_UNDELIVERED:])
        tmp = p.with_suffix(".jsonl.tmp")
        tmp.write_text(body + "\n", encoding="utf-8")
        tmp.replace(p)
    except (OSError, TypeError, ValueError):
        pass


def pending_undelivered_count(repo_root: str) -> int:
    """还没补发出去的条数（运维面/活性快照用）。"""
    return len(_read_undelivered(repo_root))


def queue_undelivered(repo_root: str, text: str, *, source: str = "") -> None:
    """把一条没推到 IM 的通知排进补发队列。best-effort。"""
    items = _read_undelivered(repo_root)
    items.append({"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                  "source": str(source)[:120], "text": str(text)[:2000], "attempts": 0})
    _write_undelivered(repo_root, items)


def _too_old(item: Dict[str, Any], now: datetime) -> bool:
    try:
        ts = datetime.fromisoformat(str(item.get("ts")))
    except (TypeError, ValueError):
        return False                      # 时间戳坏了不当过期处理，交给重试次数兜底
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return (now - ts).total_seconds() > STALE_HOURS * 3600


async def flush_undelivered(repo_root: str, send, *, now: datetime = None) -> Dict[str, int]:
    """桥恢复后把攒下的未送达通知**补推一条汇总**给主人。返回 {sent, dropped, kept}。

    为什么是汇总而不是逐条重放：断连一夜可能攒下十几条，逐条推等于早上被刷屏——
    人真正需要知道的是"这段时间你漏了什么、去哪看全文"，全文本来就在台账里。

    三道闸缺一不可（都是为了让这条自动化不变成新的噪音源）：
      · 过期（默认 12 小时）直接丢——第二天补发昨天的"作业跑完了"纯属噪音；
      · 每条最多试 MAX_ATTEMPTS 次——一条永远送不出去的通知不该每次恢复都刷一遍；
      · 补推本身失败 → 原样留在队列、attempts+1，下次恢复再试（绝不静默丢，那正是本批要治的病）。
    """
    now = now or datetime.now(timezone.utc)
    items = _read_undelivered(repo_root)
    if not items:
        return {"sent": 0, "dropped": 0, "kept": 0}

    fresh, dropped = [], 0
    for it in items:
        if _too_old(it, now) or int(it.get("attempts") or 0) >= MAX_ATTEMPTS:
            dropped += 1
            continue
        fresh.append(it)
    if not fresh:
        _write_undelivered(repo_root, [])
        return {"sent": 0, "dropped": dropped, "kept": 0}

    head = f"📬 补发｜断连期间有 {len(fresh)} 条通知没送到（全文都在台账 /api/notices）："
    lines = [head]
    for it in fresh[:10]:
        stamp = str(it.get("ts", ""))[11:16]         # 只取 HH:MM，够定位了
        lines.append(f"· {stamp} {str(it.get('text', ''))[:120]}")
    if len(fresh) > 10:
        lines.append(f"…另有 {len(fresh) - 10} 条，见台账")
    if dropped:
        lines.append(f"（另有 {dropped} 条太旧或重试超限，已丢弃）")

    try:
        await send("\n".join(lines))
    except Exception:  # noqa: BLE001 —— 补推又失败：原样留着，下次恢复再试
        for it in fresh:
            it["attempts"] = int(it.get("attempts") or 0) + 1
        _write_undelivered(repo_root, fresh)
        return {"sent": 0, "dropped": dropped, "kept": len(fresh)}

    _write_undelivered(repo_root, [])
    record_notice(repo_root, f"已补发 {len(fresh)} 条断连期间未送达的通知", source="delivery")
    return {"sent": len(fresh), "dropped": dropped, "kept": 0}


def make_notifier(repo_root: str, *, source: str = "scheduler", trigger: str = ""):
    """造三路通知投递器：①持久台账 ②WS 广播 ③IM 推 owner。每路 best-effort，一路挂不拖另两路。

    **凡是"跑完要让人知道"的路径都必须用它**，别只调 `record_notice`——那只写盘，人手机上什么
    都收不到。真机 2026-07-27 就栽在这儿：`cron_run` 工具手动触发时没传 notify，于是作业跑完
    结果只落台账，主人在钉钉等了半天没等到，而工具的注释还写着"announce 推 IM"。
    调度循环（scheduler_loop）传了、REST 触发路由传了，唯独我新加的那个工具漏了——
    **同一件事有三个调用方，第三个总会漏**，所以投递器上收到这里一份。

    从 web 路由上移到 gateway：agents 层的 cron 工具要用它，不该为了推一条通知去导入 FastAPI 层。
    `src/web/routers/tasks.py` 保留同名再导出，旧入口不变。
    """
    _default_source, _default_trigger = source, trigger

    async def _notify(text, *, source: str = "", trigger: str = "") -> None:
        # 归属可按次覆盖：同一个投递器既服务调度到点，也服务人手动触发（见 record_notice 的
        # source/trigger 之分）。不传就用造投递器时给的那套。
        src = source or _default_source
        trg = trigger or _default_trigger
        record_notice(repo_root, str(text), source=src, trigger=trg)   # ① 台账（唯一有持久保证）
        try:
            from src.web import task_events
            task_events.broadcast_notice(str(text))    # ② WS 广播给连着的客户端
        except Exception:  # noqa: BLE001
            pass
        delivered = False
        try:
            from src.gateway.im_runtime import notify_owner
            delivered = bool(await notify_owner(f"🔔 {text}"))   # ③ IM 推已配对 owner
        except Exception:  # noqa: BLE001
            pass
        if not delivered:
            # IM 那路没送到就把账记明白 **并排进补发队列**。三路里只有台账有持久保证，而
            # "送到了"和"没送到"在台账上曾长得一模一样——2026-07-28 早上排查时，唯一的证据
            # 是"手机没响"这个否定事实本身。留痕之外还要能自愈：桥恢复后由 flush_undelivered
            # 补推一条汇总，否则"补发"永远是人（那天是我）手动干的活。
            record_notice(repo_root,
                          f"⚠ 该通知未能推到 IM（无桥或发送失败），内容已在台账：{str(text)[:60]}",
                          source="delivery")
            queue_undelivered(repo_root, str(text), source=src)

    return _notify


def load_notices(repo_root: str, limit: int = 50) -> List[Dict[str, Any]]:
    """读通知台账尾部 N 条（新的在前）。无文件/坏行 → 尽量返回能解析的。"""
    p = _path(repo_root)
    if not p.is_file():
        return []
    out: List[Dict[str, Any]] = []
    try:
        for ln in p.read_text(encoding="utf-8").splitlines():
            try:
                out.append(json.loads(ln))
            except ValueError:
                continue
    except OSError:
        return []
    return list(reversed(out[-limit:]))
