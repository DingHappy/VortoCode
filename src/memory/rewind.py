"""agent 编辑的 checkpoint / rewind——复活 session_store 里此前零调用的 edits 表。

TUI 主 agent 的写工具（edit_file/write_file/rename_symbol）**直接改用户主工作区**（worktree
只隔离 dev 流水线；Web/CLI/IM 的写全走隔离流水线、不直写主工作区，见 build_agent_tools），
此前改错了只能靠用户自己 git 收拾。这里把每次工具写入记成一条 edits 行（写前全文 + 写后
全文 + 回合分组），`/rewind` 按回合逆序还原——"人只在需求确认与合并两个关口介入"的前提是
中间敢放手，敢放手的前提是收得回来。

记录与还原的约定必须同源，故收在本模块一处：
- metadata.turn    —— 回合 id（TUI 每次派发 run_turn 生成一个），rewind 的最小单位；
- metadata.created —— 该文件是本次写入新建的（rewind = 删除，而非回写空串）；
- 还原前核对「当前内容 == 记录的 new_content」，对不上（用户/外部手改过）**跳过不动**——
  宁可少撤，绝不覆盖手改；
- 还原成功的记录随即删除（LIFO 消费：再 /rewind 一次撤的就是上一个回合）。

边界：只覆盖工具写入；run_command 等 shell 副作用不在记录面内（与工具面能力一致，不假装全能）。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional


def _resolve_within(base: str, rel: str) -> Optional[Path]:
    """把 rel 安全解析到 base 内；越界返回 None。**实现在 src/utils/paths，全仓唯一一份。**

    这里原先是"独立一份同契约实现"，注释还写着"契约与 src/web/auth.resolve_within 相同"——
    **那句话是假的**：它捕了 OSError 而 web 那份没捕。两份各自演化，谁也不知道自己和另一份
    已经不一样了。安全规则上收到内核一处，正是为了不出现这种事。
    """
    from src.utils.paths import resolve_within

    return resolve_within(base, rel)


def _meta(edit: Dict[str, Any]) -> Dict[str, Any]:
    try:
        m = json.loads(edit.get("metadata") or "{}")
    except (TypeError, ValueError):
        m = {}
    return m if isinstance(m, dict) else {}


def record_edit(store, session_id: str, rel: str, old_content: Optional[str],
                new_content: str, *, turn_id: str, tool: str) -> Optional[str]:
    """把一次工具写入记进 edits 表。old_content=None 表示文件是本次新建。

    失败静默返回 None——记录是旁路，绝不能反过来弄坏编辑本身。
    """
    if not (store and session_id):
        return None
    try:
        return store.add_edit(
            session_id, rel,
            old_content if old_content is not None else "",
            new_content,
            metadata={"turn": turn_id, "tool": tool, "created": old_content is None})
    except Exception:  # noqa: BLE001
        return None


def group_turns(store, session_id: str) -> List[Dict[str, Any]]:
    """会话的编辑记录按回合分组，新 → 旧。每组 {turn, at, files, edits}。"""
    try:
        rows = store.get_edits(session_id)          # created_at ASC
    except Exception:  # noqa: BLE001
        return []
    groups: List[Dict[str, Any]] = []
    by_turn: Dict[str, Dict[str, Any]] = {}
    for e in rows:
        turn = str(_meta(e).get("turn") or e.get("id"))
        g = by_turn.get(turn)
        if g is None:
            g = {"turn": turn, "at": str(e.get("created_at") or ""), "files": [], "edits": []}
            by_turn[turn] = g
            groups.append(g)
        g["edits"].append(e)
        f = str(e.get("file") or "")
        if f and f not in g["files"]:
            g["files"].append(f)
    groups.reverse()
    return groups


def rewind_turns(store, session_id: str, repo_root: str, n: int = 1) -> Dict[str, Any]:
    """撤销最近 n 个回合的工具写入。返回 {reverted, skipped, turns}。

    全局按记录时间**逆序**还原（同文件多次编辑靠 new_content 校验自然成链：撤最新一笔后当前
    内容恰等于上一笔的 new_content）。校验不过/越界/IO 失败的记录跳过且**不消费**，原因进
    skipped；还原成功的记录删除。
    """
    groups = group_turns(store, session_id)
    picked = groups[: max(1, int(n))]
    reverted: List[str] = []
    skipped: List[tuple] = []
    consumed: List[str] = []
    edits = [e for g in picked for e in g["edits"]]
    for e in reversed(edits):
        rel = str(e.get("file") or "")
        p = _resolve_within(repo_root, rel)
        if p is None:
            skipped.append((rel, "路径越界，拒绝还原"))
            continue
        try:
            current = p.read_text(encoding="utf-8") if p.is_file() else None
        except Exception:  # noqa: BLE001
            current = None                          # 读不了（二进制/被删成目录…）→ 当作对不上
        if current != (e.get("new_content") or ""):
            skipped.append((rel, "写入后文件已被改动或不存在，跳过（不覆盖手改）"))
            continue
        meta = _meta(e)
        try:
            if meta.get("created"):
                p.unlink()
            else:
                p.write_text(e.get("old_content") or "", encoding="utf-8")
        except Exception as ex:  # noqa: BLE001
            skipped.append((rel, f"还原失败: {ex}"))
            continue
        reverted.append(rel)
        consumed.append(str(e.get("id")))
    if consumed:
        try:
            store.delete_edits(consumed)
        except Exception:  # noqa: BLE001
            pass
    return {"reverted": reverted, "skipped": skipped, "turns": [g["turn"] for g in picked]}
