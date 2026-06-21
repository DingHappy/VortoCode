"""隔离实现地基：git worktree 生命周期 + 根限定写工具（src/agents/worktree.py, build_write_tools）。

用真实的临时 git 仓库验证：改动只落在一次性 worktree、产出 diff、无论成败都清理。
"""
import subprocess

import pytest

from src.agents import worktree
from src.agents.main_agent import build_write_tools


def _init_repo(path):
    def git(*a):
        subprocess.run(["git", "-C", str(path), *a], check=True, capture_output=True)
    git("init", "-q")
    git("config", "user.email", "t@t")
    git("config", "user.name", "t")
    (path / "a.txt").write_text("hello\n")
    git("add", "-A")
    git("commit", "-q", "-m", "init")


@pytest.mark.asyncio
async def test_in_worktree_isolates_and_collects_diff(tmp_path):
    _init_repo(tmp_path)

    async def work(wt):
        (wt / "new.py").write_text("print('hi')\n")        # 新文件
        (wt / "a.txt").write_text("changed\n")              # 改已有
        return "done"

    diff, result = await worktree.in_worktree(str(tmp_path), "wt-test", work)
    assert result == "done"
    assert "new.py" in diff and "print('hi')" in diff       # 新文件进 diff
    assert "changed" in diff                                 # 改动进 diff
    # 隔离：主工作区没被碰
    assert (tmp_path / "a.txt").read_text() == "hello\n"
    assert not (tmp_path / "new.py").exists()
    # worktree 已清理
    assert not (tmp_path / ".vortocode" / "worktrees" / "wt-test").exists()


@pytest.mark.asyncio
async def test_in_worktree_cleans_up_on_error(tmp_path):
    _init_repo(tmp_path)

    async def boom(wt):
        (wt / "x.py").write_text("x")
        raise RuntimeError("nope")

    with pytest.raises(RuntimeError):
        await worktree.in_worktree(str(tmp_path), "wt-err", boom)
    assert not (tmp_path / ".vortocode" / "worktrees" / "wt-err").exists()   # 出错也清理


@pytest.mark.asyncio
async def test_run_isolated_task_with_injected_agent(tmp_path):
    _init_repo(tmp_path)

    class FakeAgent:
        def __init__(self, root):
            self.root = root

        async def run_turn(self, desc, mode, emit):
            from pathlib import Path
            (Path(self.root) / "impl.py").write_text("# " + desc + "\n")
            return "实现完成"

    diff, conclusion, ver = await worktree.run_isolated_task(
        str(tmp_path), "wt-run", "加个模块", lambda root: FakeAgent(root))
    assert conclusion == "实现完成"
    assert "impl.py" in diff and "加个模块" in diff
    assert ver is None                                      # 没给 test_cmd → 不验证
    assert not (tmp_path / "impl.py").exists()              # 主工作区干净


@pytest.mark.asyncio
async def test_build_write_tools_edit_write_and_escape_guard(tmp_path):
    (tmp_path / "f.py").write_text("a=1\n")
    tools = {t.name: t for t in build_write_tools(str(tmp_path))}

    await tools["edit_file"].handler({"path": "f.py", "old": "a=1", "new": "a=2"})
    assert (tmp_path / "f.py").read_text() == "a=2\n"

    await tools["write_file"].handler({"path": "sub/new.py", "content": "x\n"})
    assert (tmp_path / "sub" / "new.py").read_text() == "x\n"

    r = await tools["write_file"].handler({"path": "../escape.py", "content": "x"})
    assert "越界" in r and not (tmp_path.parent / "escape.py").exists()   # 不写出 root 之外

    assert all(t.read_only is False for t in build_write_tools(str(tmp_path)))   # 是写工具


@pytest.mark.asyncio
async def test_run_isolated_task_with_real_main_agent(monkeypatch, tmp_path):
    # 真集成：真 MainAgent（只读+写工具）+ 假 LLM，在隔离 worktree 里写文件 → 产出 diff、清理
    import src.llm.client as llmmod
    from src.agents.main_agent import MainAgent, build_read_tools, build_write_tools
    _init_repo(tmp_path)

    class FakeLLM:
        def __init__(self, *a, **k):
            self.n = 0

        async def chat(self, messages, **k):
            self.n += 1
            if self.n == 1:                       # 第一步：写个文件
                return {"content": '{"tool":"write_file","args":{"path":"hello.py","content":"print(1)\\n"}}'}
            return {"content": "加了 hello.py。"}  # 第二步：收口

    monkeypatch.setattr(llmmod, "LLMClient", FakeLLM)

    def _build(wt):
        return MainAgent(build_read_tools(wt) + build_write_tools(wt), max_steps=6)

    diff, conclusion, ver = await worktree.run_isolated_task(str(tmp_path), "wt-real", "加 hello.py", _build)
    assert "hello.py" in diff and "print(1)" in diff      # 子 agent 的写改动进了 diff
    assert "加了 hello.py" in conclusion
    assert not (tmp_path / "hello.py").exists()            # 主工作区干净


@pytest.mark.asyncio
async def test_apply_diff_to_branch_creates_branch_without_touching_main(tmp_path):
    _init_repo(tmp_path)

    def git(*a):
        return subprocess.run(["git", "-C", str(tmp_path), *a], capture_output=True, text=True)

    cur = git("branch", "--show-current").stdout.strip()

    async def work(wt):
        (wt / "feat.py").write_text("x = 1\n")
        return None
    diff, _ = await worktree.in_worktree(str(tmp_path), "wt-diff", work)

    res = worktree.apply_diff_to_branch(str(tmp_path), "vorto/feat-x", diff, "add feat")
    assert res["ok"] is True and res["branch"] == "vorto/feat-x"
    assert "feat.py" in git("ls-tree", "-r", "--name-only", "vorto/feat-x").stdout   # 分支里有新文件
    assert not (tmp_path / "feat.py").exists()                                        # 主工作区没被碰
    assert git("branch", "--show-current").stdout.strip() == cur                      # 当前分支没切走
    wt_dir = tmp_path / ".vortocode" / "worktrees"
    assert not wt_dir.exists() or not any(wt_dir.iterdir())                           # 临时 worktree 已清


def test_apply_diff_to_branch_bad_diff_cleans_up(tmp_path):
    _init_repo(tmp_path)
    res = worktree.apply_diff_to_branch(str(tmp_path), "vorto/bad", "这不是合法 diff\n", "x")
    assert res["ok"] is False and "apply" in res["error"]
    branches = subprocess.run(["git", "-C", str(tmp_path), "branch", "--list", "vorto/bad"],
                              capture_output=True, text=True).stdout
    assert "vorto/bad" not in branches                                                # 残留空分支被删


def test_run_tests_pass_and_fail(tmp_path):
    import sys
    from src.agents.worktree import run_tests
    assert run_tests(tmp_path, [sys.executable, "-c", "print('hi')"])["ok"] is True
    r = run_tests(tmp_path, [sys.executable, "-c", "import sys; sys.stderr.write('boom'); sys.exit(1)"])
    assert r["ok"] is False and "boom" in r["output"]


@pytest.mark.asyncio
async def test_run_isolated_task_verifies_in_worktree(tmp_path):
    import sys
    _init_repo(tmp_path)

    class WriteAgent:
        def __init__(self, root):
            self.root = root

        async def run_turn(self, desc, mode, emit):
            from pathlib import Path
            (Path(self.root) / "m.py").write_text("ok\n")
            return "done"

    # 测试通过 → ok True
    _d, _c, ver = await worktree.run_isolated_task(
        str(tmp_path), "wt-vp", "x", lambda r: WriteAgent(r),
        test_cmd=[sys.executable, "-c", "import sys; sys.exit(0)"])
    assert ver and ver["ok"] is True
    # 测试失败 → ok False + 失败输出（供 agent 据此重试）
    _d, _c, ver2 = await worktree.run_isolated_task(
        str(tmp_path), "wt-vf", "x", lambda r: WriteAgent(r),
        test_cmd=[sys.executable, "-c", "import sys; sys.stderr.write('XFAIL'); sys.exit(1)"])
    assert ver2["ok"] is False and "XFAIL" in ver2["output"]
