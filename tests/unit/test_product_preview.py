"""批之前看得见内容——只给一句 summary 就让人点头，那个"批"字没有意义。

真机 2026-09-10：通知里只有 `等你批：5 个候选选题`，用户在钉钉上回了一句"我没看到文本"。
他说得对：他批的是自己没看过的东西。

渲染器**刻意不认识任何具体字段名**：工序定义（`.vortocode/pipelines/*.yaml`）和角色提示词
都是用户可改的，产出物长什么样跟着它们走。写死 `topics`/`article` 这类字段，用户改一次
流水线预览就开始漏内容——而漏的那部分恰恰是他要据以拍板的东西。
"""
import pytest

from src.gateway.products import MAX_PREVIEW_CHARS, render_payload


def test_it_shows_the_actual_items_not_just_a_count():
    out = render_payload({"topics": [
        {"topic": "MCP 生态爆发", "why_now": "社区实现井喷", "confidence": "high"},
        {"topic": "Rust 进内核", "confidence": "medium"},
    ]})
    assert "MCP 生态爆发" in out and "社区实现井喷" in out and "Rust 进内核" in out


def test_an_unknown_shape_still_renders():
    """**这条是本渲染器的立身之本。** 换一条流水线、换个角色，字段名全变，照样看得见。"""
    out = render_payload({"完全没见过的字段": [{"甲": 1, "乙": "二"}], "另一个": "值"})
    assert "完全没见过的字段" in out and "甲: 1" in out and "乙: 二" in out
    assert "另一个: 值" in out


def test_long_text_is_clipped_per_field_not_dropped():
    out = render_payload({"article": "长" * 500})
    assert "…" in out and "长长长" in out          # 截断但看得见开头，不是整段消失


def test_the_whole_preview_is_capped_and_says_how_much_is_left():
    """IM 一屏读不完就没人读。超了要说清还剩多少，而不是无声截断。"""
    out = render_payload({"items": [{"x": "y" * 100} for _ in range(200)]})
    assert len(out) <= MAX_PREVIEW_CHARS + 80
    assert "还有" in out


def test_long_lists_say_how_many_more():
    out = render_payload({"topics": [{"topic": f"第{i}条"} for i in range(20)]})
    assert "第1条" in out and "还有" in out


def test_deep_nesting_stops_instead_of_exploding():
    deep = {"a": {"b": {"c": {"d": {"e": {"f": "底"}}}}}}
    out = render_payload(deep)
    assert "层级过深" in out


@pytest.mark.parametrize("empty", [None, {}, [], ""])
def test_empty_payload_renders_nothing(empty):
    """没内容就别占一行——空壳预览比没有预览更让人以为"就这些"。"""
    assert render_payload(empty) == ""
