"""read_file 行段读取（build_read_tools）—— 接 find_definition/document_symbols 的行号读大文件深处。"""

import pytest

from src.agents.main_agent import build_read_tools


def _read_tool(root):
    return {t.name: t for t in build_read_tools(str(root))}["read_file"]


@pytest.mark.asyncio
async def test_small_file_no_range_raw(tmp_path):
    (tmp_path / "s.py").write_text("a = 1\nb = 2\n", encoding="utf-8")
    out = await _read_tool(tmp_path).handler({"path": "s.py"})
    assert out == "a = 1\nb = 2\n"                          # 小文件无 range：原样（向后兼容）


@pytest.mark.asyncio
async def test_range_returns_those_lines_with_header(tmp_path):
    (tmp_path / "m.py").write_text("\n".join(f"line{i}" for i in range(1, 51)) + "\n", encoding="utf-8")
    out = await _read_tool(tmp_path).handler({"path": "m.py", "start": "10", "end": "12"})
    assert "第 10–12 行" in out and "共 50 行" in out
    body = out.split("\n", 1)[1]
    assert body == "line10\nline11\nline12"                 # 正好 10–12 行、含端点、无行号污染（可复制）


@pytest.mark.asyncio
async def test_start_only_defaults_span(tmp_path):
    (tmp_path / "m.py").write_text("\n".join(f"L{i}" for i in range(1, 401)) + "\n", encoding="utf-8")
    out = await _read_tool(tmp_path).handler({"path": "m.py", "start": "50"})
    assert "第 50–170 行" in out                            # 默认约 120 行
    assert "L50" in out and "L170" in out and "L49" not in out


@pytest.mark.asyncio
async def test_start_clamped_to_file_end(tmp_path):
    (tmp_path / "m.py").write_text("a\nb\nc\n", encoding="utf-8")
    out = await _read_tool(tmp_path).handler({"path": "m.py", "start": "2", "end": "999"})
    assert "第 2–3 行" in out and out.strip().endswith("c")  # 越界结束被夹到文件末


@pytest.mark.asyncio
async def test_large_file_no_range_truncates_with_hint(tmp_path):
    (tmp_path / "big.py").write_text("x = 0  # pad\n" * 2000, encoding="utf-8")   # 远超 6000 字符
    out = await _read_tool(tmp_path).handler({"path": "big.py"})
    assert "过长" in out and "start/end" in out             # 提示用行段，别只看开头
    # 绝对上界——不拿"被测常量"自己当标尺（那样断言恒真，默认上限被改宽也发现不了）
    assert len(out) < 7000


@pytest.mark.asyncio
async def test_missing_file(tmp_path):
    out = await _read_tool(tmp_path).handler({"path": "nope.py"})
    assert "不存在" in out
