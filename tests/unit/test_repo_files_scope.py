"""grep/list_files 全仓库范围（build_read_tools）—— 不止 src/tests .py，跳过噪音目录。"""

import pytest

from src.agents.main_agent import build_read_tools


def _make_repo(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("def go(): pass\n", encoding="utf-8")
    (tmp_path / "web").mkdir()
    (tmp_path / "web" / "page.html").write_text("<div>MAGIC_TOKEN</div>\n", encoding="utf-8")
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "guide.md").write_text("# 指南 MAGIC_TOKEN\n", encoding="utf-8")
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'  # MAGIC_TOKEN\n", encoding="utf-8")
    # 噪音目录：应被跳过
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "junk.js").write_text("MAGIC_TOKEN\n", encoding="utf-8")
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "config").write_text("MAGIC_TOKEN\n", encoding="utf-8")
    # 二进制/非文本扩展名：不收
    (tmp_path / "blob.bin").write_bytes(b"MAGIC_TOKEN\x00\x01")
    return {t.name: t for t in build_read_tools(str(tmp_path))}


@pytest.mark.asyncio
async def test_list_files_covers_repo_not_just_src(tmp_path):
    by = _make_repo(tmp_path)
    out = await by["list_files"].handler({})
    assert "src/app.py" in out and "web/page.html" in out
    assert "docs/guide.md" in out and "pyproject.toml" in out      # 非 .py、非 src 也列
    assert "node_modules" not in out and ".git" not in out         # 噪音目录跳过
    assert "blob.bin" not in out                                   # 非文本扩展名不收


@pytest.mark.asyncio
async def test_grep_searches_whole_repo(tmp_path):
    by = _make_repo(tmp_path)
    out = await by["grep"].handler({"pattern": "MAGIC_TOKEN"})
    assert "web/page.html" in out and "docs/guide.md" in out and "pyproject.toml" in out
    assert "node_modules" not in out and ".git/config" not in out  # 噪音目录不搜


@pytest.mark.asyncio
async def test_dir_filter_still_works(tmp_path):
    by = _make_repo(tmp_path)
    out = await by["list_files"].handler({"dir": "web"})
    assert "web/page.html" in out and "src/app.py" not in out      # 子目录前缀过滤照旧
