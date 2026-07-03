"""heartbeat — 便宜的周期性"值班"（D3）。

照抄 OpenClaw 的成本控制：隔离会话跑（不污染主上下文）、只带轻上下文（HEARTBEAT.md）、可指定便宜
模型、activeHours 限时段、应答含 `HEARTBEAT_OK` 就直接丢弃不打扰用户。自我迭代闭环的落地形态：
心跳醒来 → 从 `.vortocode/BACKLOG.md` 领一条活 → 提交后台流水线（产出 draft PR）→ IM 通知。

领活铁律：一次最多领一条、领了在条目上标在跑、产出只到 draft PR 为止、**永不自动合并**。
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Awaitable, Callable, List, Optional

_OK_TOKEN = "HEARTBEAT_OK"
_HEARTBEAT_MD = "HEARTBEAT.md"
_BACKLOG_MD = "BACKLOG.md"
_UNCHECKED = re.compile(r"^\s*[-*]\s*\[\s\]\s*(.+?)\s*$")     # `- [ ] 待办`
_CLAIMED = re.compile(r"^\s*[-*]\s*\[~\]\s*(.+?)\s*$")        # `- [~] 在跑`（本工具标记）


# --------------------------------------------------------------------- 配置 / 时段
def heartbeat_every_seconds() -> int:
    """心跳间隔（env VORTOCODE_HEARTBEAT_EVERY，如 '30m' / '2h' / '900'，默认 30m）。"""
    raw = (os.getenv("VORTOCODE_HEARTBEAT_EVERY", "30m") or "30m").strip().lower()
    m = re.fullmatch(r"(\d+)\s*([smh]?)", raw)
    if not m:
        return 1800
    n = int(m.group(1))
    return n * {"s": 1, "m": 60, "h": 3600, "": 60}[m.group(2)]


def parse_active_hours(spec: Optional[str]) -> Optional[tuple]:
    """'9-23' → (9, 23)；None/空/非法 → None（不限时段）。"""
    if not spec:
        return None
    m = re.fullmatch(r"\s*(\d{1,2})\s*-\s*(\d{1,2})\s*", str(spec))
    if not m:
        return None
    a, b = int(m.group(1)), int(m.group(2))
    if not (0 <= a <= 23 and 0 <= b <= 23):
        return None
    return (a, b)


def in_active_hours(hour: int, window: Optional[tuple]) -> bool:
    """hour 是否落在 activeHours 窗口内。window=None → 恒真。支持跨午夜（如 22-6）。"""
    if window is None:
        return True
    a, b = window
    if a <= b:
        return a <= hour <= b
    return hour >= a or hour <= b                      # 跨午夜


def is_ok_response(text: str) -> bool:
    """应答表示"没事"（含 HEARTBEAT_OK）→ 直接丢弃不打扰。"""
    return _OK_TOKEN in (text or "")


# --------------------------------------------------------------------- backlog 领活
def load_backlog_items(repo_root: str) -> List[str]:
    """`.vortocode/BACKLOG.md` 里所有未开始（`- [ ]`）的条目文本。"""
    p = Path(repo_root) / ".vortocode" / _BACKLOG_MD
    if not p.is_file():
        return []
    items = []
    for line in p.read_text(encoding="utf-8").splitlines():
        m = _UNCHECKED.match(line)
        if m:
            items.append(m.group(1).strip())
    return items


def claim_backlog_item(repo_root: str) -> Optional[str]:
    """领第一条未开始的 backlog 条目：把 `- [ ]` 改成 `- [~]`（标在跑），返回其文本。无则 None。

    一次只领一条（铁律）；标记落盘后即便崩溃也不会被重复领。原子写。
    """
    p = Path(repo_root) / ".vortocode" / _BACKLOG_MD
    if not p.is_file():
        return None
    lines = p.read_text(encoding="utf-8").splitlines()
    for i, line in enumerate(lines):
        m = _UNCHECKED.match(line)
        if m:
            item = m.group(1).strip()
            lines[i] = line.replace("[ ]", "[~]", 1)
            try:
                tmp = p.with_suffix(".md.tmp")
                tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
                tmp.replace(p)
            except OSError:
                return None
            return item
    return None


# --------------------------------------------------------------------- 值班一次
def heartbeat_prompt(repo_root: str) -> str:
    """构造"值班"提示：轻上下文只带 HEARTBEAT.md + 明确的 HEARTBEAT_OK 约定。"""
    p = Path(repo_root) / ".vortocode" / _HEARTBEAT_MD
    checklist = p.read_text(encoding="utf-8").strip() if p.is_file() else "（无 HEARTBEAT.md 清单）"
    return (
        "你是 VortoCode 的后台值班 agent（周期性心跳）。下面是值班清单，逐条快速看一眼：\n\n"
        f"{checklist}\n\n"
        f"如果没有任何需要现在处理的事，**只回复 `{_OK_TOKEN}`**（不要多说，省得打扰用户）。"
        "如果确有该处理的事，简明说清是什么、建议怎么做（不要现在就动手改代码）。")


async def run_heartbeat(
    repo_root: str, *,
    submit: Optional[Callable[[str], Awaitable]] = None,
    notify: Optional[Callable[[str], Awaitable]] = None,
    run_session: Optional[Callable[..., Awaitable[str]]] = None,
    hour: Optional[int] = None,
    active_hours: Optional[str] = None,
    model: Optional[str] = None,
) -> dict:
    """跑一次心跳。返回 {action, detail}：
      - action=skipped：不在 activeHours 窗口
      - action=claimed：领了一条 backlog 并 submit 后台任务（产出 draft PR）
      - action=ok：值班无事（HEARTBEAT_OK，已丢弃不打扰）
      - action=surfaced：值班有事，已 notify 用户
    submit/notify/run_session 可注入（测试/接线）；model 缺省用 env VORTOCODE_HEARTBEAT_MODEL。
    """
    window = parse_active_hours(active_hours or os.getenv("VORTOCODE_HEARTBEAT_HOURS"))
    if hour is None:
        from datetime import datetime
        hour = datetime.now().hour
    if not in_active_hours(hour, window):
        return {"action": "skipped", "detail": f"当前 {hour} 点不在 activeHours {window}"}

    # 1) 优先领一条 backlog 活 → 提交后台流水线（自我迭代闭环）。只在能 submit 时才领，避免领了没处跑。
    if submit is not None:
        item = claim_backlog_item(repo_root)
        if item is not None:
            await submit(item)
            if notify is not None:
                await notify(f"🫀 心跳领了一条 backlog：{item[:80]} → 已提交后台流水线（产出 draft PR，不自动合并）")
            return {"action": "claimed", "detail": item}

    # 2) 无 backlog → 跑一次值班（隔离、轻上下文、便宜模型）
    if run_session is None:
        from src.gateway.session import run_isolated_session
        run_session = run_isolated_session
    model = model or os.getenv("VORTOCODE_HEARTBEAT_MODEL") or None
    resp = await run_session(repo_root, heartbeat_prompt(repo_root),
                             mode="plan", model=model, light=True)
    if is_ok_response(resp):
        return {"action": "ok", "detail": ""}         # 没事 → 丢弃不打扰
    if notify is not None:
        await notify(f"🫀 心跳发现可能要处理的事：\n{resp.strip()[:800]}")
    return {"action": "surfaced", "detail": resp.strip()[:800]}
