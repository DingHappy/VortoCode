"""主 agent 的持久任务清单（plan/todo）——把大任务拆成可见、可续、可分解的步骤骨架。

这是"工程量大时怎么展开"的地基：计划存在 MainAgent.plan 上、注入系统提示，让 agent
跨步/跨上下文压缩始终看得见"做到哪了、下一步是什么"；UI 据 on_plan 回调渲染进度。
本模块只放纯函数（规整模型载荷 + 渲染），便于确定性测试、不依赖任何 UI。
"""
from __future__ import annotations

_GLYPH = {"pending": "○", "in_progress": "▸", "completed": "✓"}

# 状态同义词归一：模型用词五花八门，都收敛到三态
_DONE = {"done", "complete", "completed", "finished", "✓", "x", "已完成", "完成", "搞定"}
_DOING = {"doing", "in_progress", "in-progress", "inprogress", "active", "current",
          "wip", "ongoing", "进行中", "正在做", "处理中"}

_MAX_STEPS = 30
_MAX_LEN = 200


def _coerce_status(s) -> str:
    s = str(s or "").strip().lower()
    if s in _DONE:
        return "completed"
    if s in _DOING:
        return "in_progress"
    return "pending"


def normalize_plan(raw) -> list[dict]:
    """把模型给的任意形态规整成 [{step, status}]。

    容忍：字符串项、`{"steps":[...]}` 误套一层、字段名混用(step/content/task/title)、
    状态同义词；步骤去空白、截断长度、限制条数（防膨胀/防注入）。
    """
    if isinstance(raw, dict):
        raw = raw.get("steps") or raw.get("plan") or raw.get("todos") or raw.get("items") or []
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, (list, tuple)):
        return []
    out: list[dict] = []
    for item in raw:
        if isinstance(item, str):
            step, status = item, "pending"
        elif isinstance(item, dict):
            step = (item.get("step") or item.get("content") or item.get("task")
                    or item.get("title") or item.get("text") or "")
            status = _coerce_status(item.get("status"))
        else:
            continue
        step = " ".join(str(step).split()).strip()
        if step:
            out.append({"step": step[:_MAX_LEN], "status": status})
        if len(out) >= _MAX_STEPS:
            break
    return out


def render_plan(plan) -> str:
    """渲染成带状态字形的多行文本（回灌进 agent 上下文，也供纯文本 UI）。"""
    if not plan:
        return "（空计划）"
    return "\n".join(f"  {_GLYPH.get(p['status'], '○')} {p['step']}" for p in plan)


def plan_progress(plan) -> tuple[int, int]:
    """(已完成数, 总数)。"""
    return (sum(1 for p in plan if p.get("status") == "completed"), len(plan))
