"""主 agent 持久任务清单的纯函数（src/agents/plan.py）单测。"""

from src.agents.plan import normalize_plan, plan_progress, render_plan


def test_normalize_strings_become_pending():
    p = normalize_plan(["读代码", "写测试"])
    assert p == [{"step": "读代码", "status": "pending"}, {"step": "写测试", "status": "pending"}]


def test_normalize_status_synonyms():
    p = normalize_plan([
        {"step": "a", "status": "done"},
        {"step": "b", "status": "in-progress"},
        {"step": "c", "status": "正在做"},
        {"step": "d", "status": "todo"},
        {"step": "e"},                      # 无 status → pending
    ])
    assert [x["status"] for x in p] == ["completed", "in_progress", "in_progress", "pending", "pending"]


def test_normalize_tolerates_field_names_and_nesting():
    assert normalize_plan({"steps": [{"content": "x"}]}) == [{"step": "x", "status": "pending"}]
    assert normalize_plan("只有一句") == [{"step": "只有一句", "status": "pending"}]
    assert normalize_plan({"task": "y"}) == []        # 没有 steps/plan/todos/items 键 → 空
    assert normalize_plan(123) == []                  # 非列表 → 空


def test_normalize_drops_empty_and_caps():
    p = normalize_plan(["", "   ", "实数"] + [f"s{i}" for i in range(50)])
    assert p[0]["step"] == "实数"                     # 空白项被丢
    assert len(p) <= 30                               # 截断防膨胀


def test_render_and_progress():
    plan = [{"step": "a", "status": "completed"},
            {"step": "b", "status": "in_progress"},
            {"step": "c", "status": "pending"}]
    assert plan_progress(plan) == (1, 3)
    r = render_plan(plan)
    assert "✓ a" in r and "▸ b" in r and "○ c" in r
    assert render_plan([]) == "（空计划）"
