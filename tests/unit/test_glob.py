"""glob 文件查找工具（build_read_tools，对标 CC 的 Glob）+ glob→正则译器。"""
import pytest

from src.agents.main_agent import _glob_to_regex, build_read_tools


# ---- glob→正则译器（纯函数）：** 跨目录、* 不跨 / ----
def test_glob_regex_doublestar_any_depth():
    rx = _glob_to_regex("src/**/*.ts")
    assert rx.match("src/a.ts")                 # ** 含零层目录
    assert rx.match("src/x/y/z.ts")             # 任意深度
    assert not rx.match("lib/a.ts")             # 前缀不符


def test_glob_regex_leading_doublestar_matches_toplevel():
    rx = _glob_to_regex("**/*.py")
    assert rx.match("a.py") and rx.match("x/y.py")


def test_glob_regex_single_star_no_cross_slash():
    rx = _glob_to_regex("src/*.ts")
    assert rx.match("src/a.ts")
    assert not rx.match("src/sub/a.ts")         # * 不跨 /


def test_glob_regex_question_mark():
    rx = _glob_to_regex("a?.ts")
    assert rx.match("ab.ts") and not rx.match("abc.ts") and not rx.match("a/.ts")


def _repo(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "sub").mkdir()
    (tmp_path / "src" / "a.ts").write_text("x", encoding="utf-8")
    (tmp_path / "src" / "sub" / "b.ts").write_text("y", encoding="utf-8")
    (tmp_path / "src" / "c.py").write_text("z", encoding="utf-8")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_a.py").write_text("t", encoding="utf-8")
    (tmp_path / "top.ts").write_text("top", encoding="utf-8")
    (tmp_path / "data.json").write_text("{}", encoding="utf-8")     # 非文本扩展名也应被 glob 找到
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "junk.ts").write_text("skip", encoding="utf-8")
    return {t.name: t for t in build_read_tools(str(tmp_path))}


@pytest.mark.asyncio
async def test_glob_basename_pattern_any_depth(tmp_path):
    out = await _repo(tmp_path)["glob"].handler({"pattern": "*.ts"})
    assert "src/a.ts" in out and "src/sub/b.ts" in out and "top.ts" in out
    assert "junk.ts" not in out                  # node_modules 跳过
    assert "c.py" not in out


@pytest.mark.asyncio
async def test_glob_slash_doublestar(tmp_path):
    out = await _repo(tmp_path)["glob"].handler({"pattern": "src/**/*.ts"})
    assert "src/a.ts" in out and "src/sub/b.ts" in out
    assert "top.ts" not in out                    # 不在 src 下


@pytest.mark.asyncio
async def test_glob_finds_non_text_extension(tmp_path):
    out = await _repo(tmp_path)["glob"].handler({"pattern": "*.json"})
    assert "data.json" in out                     # glob 不限文本扩展名（区别于 list_files）


@pytest.mark.asyncio
async def test_glob_dir_scope(tmp_path):
    out = await _repo(tmp_path)["glob"].handler({"pattern": "*.py", "dir": "tests"})
    assert "tests/test_a.py" in out and "src/c.py" not in out


@pytest.mark.asyncio
async def test_glob_no_match_and_empty(tmp_path):
    by = _repo(tmp_path)
    assert "没有匹配" in await by["glob"].handler({"pattern": "*.rs"})
    assert "需要 pattern" in await by["glob"].handler({"pattern": "  "})


@pytest.mark.asyncio
async def test_glob_wired_read_only(tmp_path):
    by = _repo(tmp_path)
    assert "glob" in by and by["glob"].read_only is True     # 只读、plan 也可用
