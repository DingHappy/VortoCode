"""通用命令执行（src/agents/shell.py）单测。"""

from src.agents.shell import is_dangerous, run_command


def test_is_dangerous_flags_catastrophic():
    assert is_dangerous("rm -rf /")
    assert is_dangerous("rm -rf ~")
    assert is_dangerous("sudo mkfs.ext4 /dev/sda1")
    assert is_dangerous("git push origin main --force")
    assert is_dangerous("shutdown now")


def test_is_dangerous_allows_normal_dev_commands():
    for ok in ("pytest tests/unit/test_x.py -q", "ruff check src", "git log --oneline -5",
               "ls -la", "python -m build", "rm -rf build/ .pytest_cache"):
        assert is_dangerous(ok) == "", ok          # 正常命令不该被拦（含删 build/ 缓存）


def test_run_command_captures_output_and_code(tmp_path):
    ok = run_command(tmp_path, "echo hello")
    assert ok["ok"] is True and ok["code"] == 0 and "hello" in ok["output"]

    bad = run_command(tmp_path, "exit 3")
    assert bad["ok"] is False and bad["code"] == 3

    # 在 cwd 里跑：能看到该目录的文件
    (tmp_path / "marker.txt").write_text("x")
    ls = run_command(tmp_path, "ls")
    assert "marker.txt" in ls["output"]
