"""通知台账 —— 无人值守产出里"人该看到"的那一路（cron 例行作业 / heartbeat 值班）。

例行产出**不进任何人类会话历史**（run lane 规则，见 `cron.py`）：需要人知道的事落这条 JSONL 台账
（`.vortocode/logs/notices.jsonl`，`logs/` 已在 `.vortocode` 自忽略清单内），再由 GET /api/notices
查询、WS `notice` 广播、IM 推 owner 三路投递。**台账是唯一有持久保证的一路**，另两路 best-effort。

放在 gateway 而不是留在 web 路由里：CLI（`vc cron run`）与常驻调度循环都要写它，
不该为了记一条通知去导入整个 FastAPI 层。`src/web/routers/tasks.py` 从这里再导出，保持旧入口可用。
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

# 实时广播给已连接的客户端（WS）。由传输层启动时注册（见 web/routers/tasks.py）——调度层不
# 反向依赖 Web。没注册（如命令行单独跑 cron）就只走台账与 IM 两路：那时本来也没有客户端在连。
_notice_broadcaster: Optional[Callable[[str], None]] = None


def set_notice_broadcaster(broadcaster: Optional[Callable[[str], None]]) -> None:
    global _notice_broadcaster
    _notice_broadcaster = broadcaster


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
        from src.utils.state_dir import ensure_state_gitignore
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


def _write_undelivered(repo_root: str, items: List[Dict[str, Any]]) -> bool:
    p = _undelivered_path(repo_root)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        if not items:
            p.unlink(missing_ok=True)
            return True
        body = "\n".join(json.dumps(x, ensure_ascii=False) for x in items[-MAX_UNDELIVERED:])
        tmp = p.with_suffix(".jsonl.tmp")
        tmp.write_text(body + "\n", encoding="utf-8")
        tmp.replace(p)
        return True
    except (OSError, TypeError, ValueError):
        return False


def pending_undelivered_count(repo_root: str) -> int:
    """还没补发出去的条数（运维面/活性快照用）。"""
    return len(_read_undelivered(repo_root))


def queue_undelivered(repo_root: str, text: str, *, source: str = "") -> bool:
    """把一条没推到 IM 的通知排进补发队列；返回是否保存成功，不代表送达。"""
    items = _read_undelivered(repo_root)
    items.append({"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                  "source": str(source)[:120], "text": str(text)[:2000], "attempts": 0})
    return _write_undelivered(repo_root, items)


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


# ── 安静时段与重复合并（只管「推不推手机」，台账与 Desktop 实时广播照旧）──────────────
QUIET_ENV = "VORTOCODE_QUIET_HOURS"          # 例：23:00-08:00（本机时区，可跨夜）；不设 = 不启用
DEDUP_ENV = "VORTOCODE_NOTICE_DEDUP_MIN"     # 同一来源、同样内容多少分钟内只推一次；默认 60，0 = 关
MAX_HELD = 100


def _held_path(repo_root: str) -> Path:
    return Path(repo_root) / ".vortocode" / "logs" / "quiet_held.jsonl"


def _dedup_path(repo_root: str) -> Path:
    return Path(repo_root) / ".vortocode" / "logs" / "notice_dedup.json"


def quiet_window() -> Optional[tuple]:
    """解析安静时段为 (起, 止) 的「当天第几分钟」；格式不对或首尾相同 = 不启用（宁可照常推，也别静默吞）。"""
    raw = os.getenv(QUIET_ENV, "").strip()
    if not raw or "-" not in raw:
        return None
    try:
        start, end = (_minutes(part) for part in raw.split("-", 1))
    except ValueError:
        return None
    return None if start == end else (start, end)


def _minutes(text: str) -> int:
    hour, _, minute = text.strip().partition(":")
    h, m = int(hour), int(minute or 0)
    if not (0 <= h <= 23 and 0 <= m <= 59):
        raise ValueError(text)
    return h * 60 + m


def in_quiet_hours(now: Optional[datetime] = None) -> bool:
    window = quiet_window()
    if window is None:
        return False
    local = (now or datetime.now(timezone.utc)).astimezone()
    current = local.hour * 60 + local.minute
    start, end = window
    return start <= current < end if start < end else (current >= start or current < end)


def _read_jsonl(path: Path) -> List[Dict[str, Any]]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    out = []
    for line in lines:
        try:
            item = json.loads(line)
        except ValueError:
            continue
        if isinstance(item, dict):
            out.append(item)
    return out


def _write_jsonl(path: Path, items: List[Dict[str, Any]]) -> bool:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text("".join(json.dumps(i, ensure_ascii=False) + "\n" for i in items), encoding="utf-8")
        tmp.replace(path)
        return True
    except OSError:
        return False


def held_quiet_count(repo_root: str) -> int:
    return len(_read_jsonl(_held_path(repo_root)))


def hold_for_quiet(repo_root: str, text: str, *, source: str = "") -> bool:
    """安静时段内先攒着，时段结束推一条汇总；超过上限丢最旧的（全文始终在台账）。"""
    items = _read_jsonl(_held_path(repo_root))
    items.append({"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                  "source": str(source)[:120], "text": str(text)[:2000]})
    return _write_jsonl(_held_path(repo_root), items[-MAX_HELD:])


async def flush_quiet_digest(repo_root: str, send, *, now: Optional[datetime] = None) -> int:
    """时段结束后把攒下的通知合成一条推出去；返回推出去的条数（仍在时段内 / 没有攒的 → 0）。

    推送失败就原样留着，下个 tick 再试——绝不静默丢。
    """
    if in_quiet_hours(now):
        return 0
    items = _read_jsonl(_held_path(repo_root))
    if not items:
        return 0
    lines = [f"🌙 安静时段里有 {len(items)} 条通知（全文在台账 /api/notices）："]
    for it in items[:10]:
        stamp = ""
        try:
            stamp = datetime.fromisoformat(str(it.get("ts"))).astimezone().strftime("%H:%M") + " "
        except (TypeError, ValueError):
            pass
        lines.append(f"· {stamp}{str(it.get('text', ''))[:120]}")
    if len(items) > 10:
        lines.append(f"…另有 {len(items) - 10} 条，见台账")
    try:
        delivered = await send("\n".join(lines))
    except Exception:  # noqa: BLE001
        return 0
    if delivered is False:
        return 0
    _write_jsonl(_held_path(repo_root), [])
    record_notice(repo_root, f"安静时段结束，已汇总推送 {len(items)} 条通知", source="delivery")
    return len(items)


def _dedup_window_seconds() -> float:
    try:
        minutes = float(os.getenv(DEDUP_ENV, "60") or 60)
    except ValueError:
        minutes = 60.0
    return max(0.0, minutes) * 60


def _dedup_key(source: str, text: str) -> str:
    import hashlib
    normalized = "".join(str(text).split())          # 空白差异不算两件事（中文通知里空白本无意义）
    return hashlib.sha256(f"{source}\0{normalized}".encode("utf-8")).hexdigest()[:24]


def _recently_pushed(repo_root: str, key: str, now: float) -> bool:
    window = _dedup_window_seconds()
    if window <= 0:
        return False
    try:
        seen = json.loads(_dedup_path(repo_root).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    stamp = seen.get(key) if isinstance(seen, dict) else None
    return isinstance(stamp, (int, float)) and now - float(stamp) < window


def _mark_pushed(repo_root: str, key: str, now: float) -> None:
    path = _dedup_path(repo_root)
    try:
        seen = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(seen, dict):
            seen = {}
    except (OSError, ValueError):
        seen = {}
    horizon = now - max(_dedup_window_seconds(), 3600)
    seen = {k: v for k, v in seen.items() if isinstance(v, (int, float)) and v >= horizon}
    seen[key] = now
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(seen), encoding="utf-8")
    except OSError:
        pass


def make_notifier(repo_root: str, *, source: str = "scheduler", trigger: str = "", relay: bool = True):
    """造三路通知投递器：①持久台账 ②WS 广播 ③IM 推 owner。每路 best-effort，一路挂不拖另两路。

    返回 True 表示 IM/转发已受理，或已保存到本地补发队列；不代表用户已读。
    只有台账或 WS 成功不算受理：它们不会在桥恢复后主动补推。

    **凡是"跑完要让人知道"的路径都必须用它**，别只调 `record_notice`——那只写盘，人手机上什么
    都收不到。真机 2026-07-27 就栽在这儿：`cron_run` 工具手动触发时没传 notify，于是作业跑完
    结果只落台账，主人在钉钉等了半天没等到，而工具的注释还写着"announce 推 IM"。
    调度循环（scheduler_loop）传了、REST 触发路由传了，唯独我新加的那个工具漏了——
    **同一件事有三个调用方，第三个总会漏**，所以投递器上收到这里一份。

    从 web 路由上移到 gateway：agents 层的 cron 工具要用它，不该为了推一条通知去导入 FastAPI 层。
    `src/web/routers/tasks.py` 保留同名再导出，旧入口不变。
    """
    _default_source, _default_trigger = source, trigger

    async def _notify(text, *, source: str = "", trigger: str = "", urgent: bool = False,
                      confirm_id: str = "") -> bool:
        # 归属可按次覆盖：同一个投递器既服务调度到点，也服务人手动触发（见 record_notice 的
        # source/trigger 之分）。不传就用造投递器时给的那套。
        # urgent=True（如投递探针）不受安静时段与重复合并影响：它要测的就是「此刻能不能送到」。
        import time
        src = source or _default_source
        trg = trigger or _default_trigger
        record_notice(repo_root, str(text), source=src, trigger=trg)   # ① 台账（唯一有持久保证）
        try:
            if _notice_broadcaster is not None:
                _notice_broadcaster(str(text))         # ② WS 广播给连着的客户端
        except Exception:  # noqa: BLE001
            pass
        stamp = time.time()
        key = _dedup_key(src, str(text))
        if not urgent and _recently_pushed(repo_root, key, stamp):
            return True                                # 同样的事刚推过：台账/Desktop 照记，手机不再响
        if not urgent and in_quiet_hours():
            return hold_for_quiet(repo_root, str(text), source=src)   # 攒到时段结束推一条汇总
        delivered = False
        try:
            from src.gateway.im_runtime import notify_owner

            async def _send(message: str) -> bool:
                return bool(await notify_owner(message, relay=relay))

            await flush_quiet_digest(repo_root, _send)  # 先把时段里攒的补上，顺序才对
            # ③ IM 推已配对 owner：本进程有桥就直接发，没有就交给同机持有 IM 的服务（relay=False 时只本地）
            if confirm_id:   # 带「批准 / 拒绝」按钮（如助理任务单）；没有桥就退回纯文字
                from src.gateway.im_runtime import notify_owner_confirm
                delivered = bool(await notify_owner_confirm(f"🔔 {text}", confirm_id, relay=relay))
            else:
                delivered = bool(await notify_owner(f"🔔 {text}", relay=relay))
        except Exception:  # noqa: BLE001
            pass
        if delivered and not urgent:
            _mark_pushed(repo_root, key, stamp)
        if not delivered:
            # IM 那路没送到就把账记明白 **并排进补发队列**。三路里只有台账有持久保证，而
            # "送到了"和"没送到"在台账上曾长得一模一样——2026-07-28 早上排查时，唯一的证据
            # 是"手机没响"这个否定事实本身。留痕之外还要能自愈：桥恢复后由 flush_undelivered
            # 补推一条汇总，否则"补发"永远是人（那天是我）手动干的活。
            record_notice(repo_root,
                          f"⚠ 该通知未能推到 IM（无桥或发送失败），内容已在台账：{str(text)[:60]}",
                          source="delivery")
            return queue_undelivered(repo_root, str(text), source=src)
        return True

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
