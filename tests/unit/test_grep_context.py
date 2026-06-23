"""grep 的 context=±N 上下文行（build_read_tools）+ 一并修好被宣传却没生效的 dir 过滤。"""

import pytest

from src.agents.main_agent import build_read_tools


def _grep(tmp_path):
    return {t.name: t for t in build_read_tools(str(tmp_path))}["grep"]


@pytest.mark.asyncio
async def test_context_shows_surrounding_lines_and_marks_match(tmp_path):
    (tmp_path / "f.py").write_text(
        "alpha\nbeta\nTARGET one\ngamma\ndelta\nepsilon\nTARGET two\nzeta\n", encoding="utf-8")
    out = await _grep(tmp_path).handler({"pattern": "TARGET", "context": 1})
    assert "3 > TARGET one" in out                  # 命中行带 > 标记 + 行号
    assert "2   beta" in out and "4   gamma" in out  # 命中行前后各 1 行上下文
    assert "⋯" in out                               # 两处不相邻 → 窗口间有分隔
    assert "7 > TARGET two" in out


@pytest.mark.asyncio
async def test_adjacent_matches_merge_no_separator(tmp_path):
    (tmp_path / "f.py").write_text("a\nHIT one\nHIT two\nb\n", encoding="utf-8")
    out = await _grep(tmp_path).handler({"pattern": "HIT", "context": 1})
    assert "2 > HIT one" in out and "3 > HIT two" in out   # 两个命中都标 >
    assert "⋯" not in out                                 # 窗口相邻 → 合并、无分隔
    assert "1   a" in out and "4   b" in out               # 合并窗口含两侧上下文


@pytest.mark.asyncio
async def test_context_zero_is_compact_legacy_format(tmp_path):
    (tmp_path / "f.py").write_text("x\nNEEDLE\ny\n", encoding="utf-8")
    out = await _grep(tmp_path).handler({"pattern": "NEEDLE"})        # 不给 context → 旧紧凑格式
    assert out == "f.py:2: NEEDLE"
    assert ">" not in out                                            # 紧凑模式无 > 标记/上下文


@pytest.mark.asyncio
async def test_context_clamped_to_5(tmp_path):
    lines = [f"ctx{i}" for i in range(1, 10)] + ["MATCH"] + [f"ctx{i}" for i in range(11, 21)]
    (tmp_path / "big.py").write_text("\n".join(lines) + "\n", encoding="utf-8")
    out = await _grep(tmp_path).handler({"pattern": "MATCH", "context": 99})
    assert "ctx5" in out and "ctx15" in out          # ±5 窗口边界
    assert "ctx4" not in out and "ctx16" not in out  # 超过 5 的不显示（钳到 5，不会吐整文件）


@pytest.mark.asyncio
async def test_dir_filter_now_actually_applies(tmp_path):
    # 此前 grep 的 dir 参数被宣传却没生效 —— 修好：只搜指定子目录
    (tmp_path / "web").mkdir()
    (tmp_path / "web" / "page.html").write_text("MAGIC here\n", encoding="utf-8")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("MAGIC there\n", encoding="utf-8")
    only_web = await _grep(tmp_path).handler({"pattern": "MAGIC", "dir": "web"})
    assert "web/page.html" in only_web and "src/app.py" not in only_web
    # dir + context 也只在子目录内
    ctxed = await _grep(tmp_path).handler({"pattern": "MAGIC", "dir": "web", "context": 1})
    assert "web/page.html:" in ctxed and "src/app.py" not in ctxed


@pytest.mark.asyncio
async def test_no_match_with_context(tmp_path):
    (tmp_path / "f.py").write_text("nothing relevant\n", encoding="utf-8")
    out = await _grep(tmp_path).handler({"pattern": "ZZZ", "context": 2})
    assert "没有匹配" in out
