"""常驻助理的巡检登记表：每一项巡检报名字、多久跑一次、怎么跑；框架负责计时、隔离失败、记下上次结果。

以前每加一项提醒就往 `assistant_watch.watch_once` 里再插一段 try/except，各自记时间戳，
哪项坏了只在日志里有一行，Desktop 的「定时」页也看不到。现在：
- 新巡检只要在 `PATROLS` 里登记一项；
- 一项失败（普通异常）只记下来，其余照跑；取消（CancelledError）照常往外传，不吞；
- 每项的上次运行、上次成功、连续失败次数记在 `.vortocode/dispatches/patrols.json`，
  「定时」页据此显示「连续失败 N 次」。

任务单跟进和早晚报不在这里：它们共用同一份任务单快照和「首次运行只记现状」的语义，仍由 watch_once 主体负责。
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

Notify = Callable[..., Awaitable[bool]]


@dataclass
class PatrolContext:
    root: str
    now: datetime
    notify: Notify
    #: 助理巡检的共享状态（watch.json），用到它的巡检直接读写，框架统一保存
    state: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Patrol:
    name: str
    title: str
    detail: str
    run: Callable[[PatrolContext], Awaitable[None]]
    #: 多久跑一次；None = 每轮巡检（每分钟）都跑，由巡检自己判断该不该动
    every: Optional[timedelta] = None
    #: 「定时」页上显不显示（只在这台机器上真有事可做时显示）
    visible: Callable[[str], bool] = lambda _root: True
    #: 「定时」页上的频率说明；None = 按 every 写
    schedule: Optional[Callable[[str], str]] = None


def _path(root: str) -> Path:
    return Path(root) / ".vortocode" / "dispatches" / "patrols.json"


def load_records(root: str) -> Dict[str, Dict[str, Any]]:
    try:
        data = json.loads(_path(root).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _save_records(root: str, records: Dict[str, Dict[str, Any]]) -> None:
    path = _path(root)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(".patrols.json.tmp")
        tmp.write_text(json.dumps(records, ensure_ascii=False, sort_keys=True), encoding="utf-8")
        os.replace(tmp, path)
    except OSError as error:
        logger.warning("巡检记录写不进去：%s", type(error).__name__)


def _parse(value: Any) -> Optional[datetime]:
    try:
        moment = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    return moment if moment.tzinfo else moment.astimezone()


async def run_patrols(patrols: List[Patrol], ctx: PatrolContext) -> None:
    """按登记顺序跑一遍到点的巡检。每项的结果单独记；一项失败不挡后面的。"""
    records = load_records(ctx.root)
    for patrol in patrols:
        record = dict(records.get(patrol.name) or {})
        last = _parse(record.get("last_run"))
        if patrol.every and last is not None and timedelta(0) <= ctx.now - last < patrol.every:
            continue
        record["last_run"] = ctx.now.isoformat()
        try:
            await patrol.run(ctx)
        except Exception as error:  # noqa: BLE001 —— 一项巡检坏了不挡其余；取消（BaseException）照常往外传
            record["failures"] = int(record.get("failures") or 0) + 1
            record["last_error"] = type(error).__name__
            logger.warning("巡检「%s」失败：%s", patrol.title, type(error).__name__)
        else:
            record["failures"] = 0
            record.pop("last_error", None)
            record["last_ok"] = ctx.now.isoformat()
        records[patrol.name] = record
        _save_records(ctx.root, records)


def _every_text(patrol: Patrol) -> str:
    if patrol.every is None:
        return "每分钟"
    minutes = int(patrol.every.total_seconds() // 60)
    return f"每 {minutes} 分钟" if minutes < 60 else f"每 {minutes // 60} 小时"


def overview(patrols: List[Patrol], root: str) -> List[Dict[str, Any]]:
    """「定时」页的条目：只列这台机器上真有事可做的巡检，带上次运行和连续失败次数。"""
    records = load_records(root)
    items = []
    for patrol in patrols:
        try:
            if not patrol.visible(root):
                continue
            schedule = patrol.schedule(root) if patrol.schedule else _every_text(patrol)
        except Exception:  # noqa: BLE001 —— 某项的说明读不出来，不影响其余几项
            continue
        record = records.get(patrol.name) or {}
        last = _parse(record.get("last_run"))
        items.append({"id": f"patrol-{patrol.name}", "kind": "watch", "title": patrol.title, "schedule": schedule,
                      "detail": patrol.detail, "enabled": True,
                      "last": last.isoformat(timespec="minutes") if last else None,
                      "failures": int(record.get("failures") or 0)})
    return items
