"""测试命令自动探测（src/agents/test_detect.py）—— 让隔离 dev 流水线不再写死 pytest。"""

import sys

import pytest

from src.agents.test_detect import detect_test_cmd, is_pytest_cmd


def test_node_with_test_script(tmp_path):
    (tmp_path / "package.json").write_text('{"scripts": {"test": "jest"}}', encoding="utf-8")
    assert detect_test_cmd(str(tmp_path)) == ["npm", "test", "--silent"]


def test_node_pnpm_and_yarn(tmp_path):
    (tmp_path / "package.json").write_text('{"scripts": {"test": "vitest"}}', encoding="utf-8")
    (tmp_path / "pnpm-lock.yaml").write_text("", encoding="utf-8")
    assert detect_test_cmd(str(tmp_path)) == ["pnpm", "test"]
    (tmp_path / "pnpm-lock.yaml").unlink()
    (tmp_path / "yarn.lock").write_text("", encoding="utf-8")
    assert detect_test_cmd(str(tmp_path)) == ["yarn", "test"]


def test_node_without_test_script_falls_through(tmp_path):
    # 没有 test 脚本 → 不能用 npm test（会 missing script），落到 pytest 兜底
    (tmp_path / "package.json").write_text('{"scripts": {"build": "tsc"}}', encoding="utf-8")
    assert detect_test_cmd(str(tmp_path)) == [sys.executable, "-m", "pytest", "-q", "tests/"]


def test_rust_and_go(tmp_path):
    (tmp_path / "Cargo.toml").write_text("[package]\n", encoding="utf-8")
    assert detect_test_cmd(str(tmp_path)) == ["cargo", "test"]
    (tmp_path / "Cargo.toml").unlink()
    (tmp_path / "go.mod").write_text("module x\n", encoding="utf-8")
    assert detect_test_cmd(str(tmp_path)) == ["go", "test", "./..."]


def test_python_markers_and_selector(tmp_path):
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
    assert detect_test_cmd(str(tmp_path)) == [sys.executable, "-m", "pytest", "-q", "tests/"]
    # selector 只在 pytest 生效（文件级 narrow）
    assert detect_test_cmd(str(tmp_path), "tests/test_x.py") == \
        [sys.executable, "-m", "pytest", "-q", "tests/test_x.py"]


def test_python_tests_dir_only(tmp_path):
    (tmp_path / "tests").mkdir()
    assert detect_test_cmd(str(tmp_path))[:3] == [sys.executable, "-m", "pytest"]


def test_makefile_test_target(tmp_path):
    (tmp_path / "Makefile").write_text("build:\n\tgcc x.c\ntest:\n\t./run_tests\n", encoding="utf-8")
    assert detect_test_cmd(str(tmp_path)) == ["make", "test"]


def test_empty_repo_falls_back_to_pytest(tmp_path):
    assert detect_test_cmd(str(tmp_path)) == [sys.executable, "-m", "pytest", "-q", "tests/"]


def test_node_beats_python_when_both(tmp_path):
    # 同时有 package.json(test) 和 pyproject → 按顺序 Node 优先（前端为主的仓库）
    (tmp_path / "package.json").write_text('{"scripts": {"test": "jest"}}', encoding="utf-8")
    (tmp_path / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
    assert detect_test_cmd(str(tmp_path)) == ["npm", "test", "--silent"]


def test_is_pytest_cmd():
    assert is_pytest_cmd([sys.executable, "-m", "pytest", "-q"])
    assert not is_pytest_cmd(["go", "test", "./..."])
    assert not is_pytest_cmd(["npm", "test"])
    assert not is_pytest_cmd([])


@pytest.mark.asyncio
async def test_build_test_tool_selector_ignored_for_nonpython(tmp_path):
    # build_test_tool：非 pytest 的 default_cmd，给了 selector 也忽略、跑整套
    from src.agents.main_agent import build_test_tool
    tool = build_test_tool(str(tmp_path), default_cmd=["go", "test", "./..."])
    out = await tool.handler({"test": "foo_test.go"})
    assert "go test ./..." in out and "pytest" not in out


@pytest.mark.asyncio
async def test_build_test_tool_selector_applies_for_pytest(tmp_path):
    from src.agents.main_agent import build_test_tool
    tool = build_test_tool(str(tmp_path), default_cmd=[sys.executable, "-m", "pytest", "-q"])
    out = await tool.handler({"test": "tests/test_x.py"})
    assert "tests/test_x.py" in out                          # pytest：selector 生效


@pytest.mark.asyncio
async def test_dev_tools_use_detected_cmd_for_go(monkeypatch, tmp_path):
    # 端到端：Go 仓库 → dev_parallel 把探测到的 `go test ./...` 传给隔离实现
    import src.agents.worktree as wt
    from src.agents.main_agent import build_dev_tools
    (tmp_path / "go.mod").write_text("module x\n", encoding="utf-8")
    seen = {}

    async def cap_isolated(repo, wid, desc, build, test_cmd=None):
        seen["cmd"] = test_cmd
        return ("", "无改动", None)                            # 无 diff → 不落分支，快速返回
    monkeypatch.setattr(wt, "run_isolated_task", cap_isolated)
    tool = {t.name: t for t in build_dev_tools(str(tmp_path))}["dev_parallel"]
    await tool.handler({"tasks": ["实现点啥"]})
    assert seen["cmd"] == ["go", "test", "./..."]            # 不再写死 pytest
