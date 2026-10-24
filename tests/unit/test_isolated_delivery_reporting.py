"""Real temporary Git/test evidence reaches the parent, distinct from the requested command."""
from copy import deepcopy
import json
import subprocess

import pytest

from src.agents.capabilities import SessionCapabilities
from src.agents.dev_tools import build_dev_tools
from src.agents.agent_loop import MainAgent
from src.workflow.review.isolated_deliveries import list_isolated_deliveries, verification_report


def _git(root, *args):
    return subprocess.check_output(["git", "-C", str(root), *args], text=True).strip()


@pytest.mark.parametrize("native", [False, True])
async def test_actual_verifier_and_commit_reach_the_parent_model(tmp_path, monkeypatch, native):
    monkeypatch.setenv("VORTOCODE_SANDBOX", "off")
    monkeypatch.setenv("VORTOCODE_DEV_ATTEMPTS", "1")
    _git(tmp_path, "init", "-q", "-b", "main")
    _git(tmp_path, "config", "user.name", "Test")
    _git(tmp_path, "config", "user.email", "test@example.invalid")
    (tmp_path / "pricing.py").write_text("def total(price, percent):\n    return price * (1 + percent / 100)\n")
    (tmp_path / "test_pricing.py").write_text(
        "from pricing import total\ndef test_discount():\n    assert total(80, 10) == 72\n")
    _git(tmp_path, "add", "pricing.py", "test_pricing.py")
    _git(tmp_path, "commit", "-qm", "base")
    baseline = _git(tmp_path, "rev-parse", "HEAD")

    class Writer:
        def __init__(self, tools, **kwargs):
            self.write = next(tool for tool in tools if tool.name == "write_file")

        async def run_turn(self, *args, **kwargs):
            await self.write.handler({"path": "pricing.py", "content":
                                      "def total(price, percent):\n    return price * (1 - percent / 100)\n"})
            return "合成实现结论，声称运行了 unittest"  # Must not replace verifier evidence.

    tools = [tool for tool in build_dev_tools(str(tmp_path), agent_factory=Writer) if tool.name == "dev_isolated"]
    requests = []

    class ParentLLM:
        async def chat(self, messages, **kwargs):
            requests.append(deepcopy(messages))
            if len(requests) == 1:
                args = {"description": "修复折扣，要求 python3 -m unittest -q", "test": "test_pricing.py"}
                if native:
                    return {"content": "", "tool_calls": [{"id": "delivery", "name": "dev_isolated", "arguments": json.dumps(args)}]}
                return {"content": json.dumps({"tool": "dev_isolated", "args": args})}
            return {"content": "已读取实际交付记录"}

    parent = MainAgent(tools, llm=ParentLLM(), native=native,
                       capabilities=SessionCapabilities.for_profile("local", str(tmp_path)))
    await parent.run_turn("修复折扣，要求 python3 -m unittest -q", mode="build")
    [delivery] = list_isolated_deliveries(str(tmp_path))
    evidence = delivery["verification"]
    assert evidence["ok"] and "pytest" in evidence["cmd"]
    model_input = str(requests[-1])
    assert evidence["cmd"] in model_input
    assert delivery["head_oid"] in model_input and baseline in model_input
    assert "1 passed" in model_input
    assert "不得把请求中的命令写成已执行" in model_input
    assert "合成实现结论，声称运行了 unittest" not in model_input
    assert _git(tmp_path, "rev-parse", "HEAD") == baseline
    assert _git(tmp_path, "diff", "--name-only") == ""
    assert _git(tmp_path, "diff", "--name-only", f"{baseline}..{delivery['head_oid']}") == "pricing.py"


@pytest.mark.parametrize("verification, expected", [
    ({"ok": True, "skipped": True, "output": "docs only"}, "已跳过，不能计为测试通过"),
    ({"ok": False, "cmd": "npm test", "output": "1 failed"}, "结果：未通过"),
    ({"ok": True}, "未记录，不能确认执行命令"),
])
def test_report_preserves_skipped_failed_and_missing_evidence(verification, expected):
    assert expected in verification_report(verification)


def test_report_redacts_before_truncating_long_secret_values():
    secret = "synthetic-sensitive-value" * 100
    report = verification_report({"ok": True, "cmd": "pytest -q", "output": f"API_KEY={secret}\n1 passed"})
    assert "synthetic-sensitive-value" not in report
    assert "[REDACTED]" in report and "1 passed" in report
    assert len(verification_report({"cmd": "x" * 5000, "output": "line\n" * 5000})) < 2200


@pytest.mark.parametrize("record_fails", [False, True])
async def test_verification_survives_failed_landing_or_recording(tmp_path, monkeypatch, record_fails):
    from src.agents import worktree
    from src.workflow.review import isolated_deliveries
    monkeypatch.setenv("VORTOCODE_DEV_ATTEMPTS", "1")

    async def implement(*args, **kwargs):
        return ("diff --git a/pricing.py b/pricing.py\n-old\n+new\n", "wrong claimed command",
                {"ok": True, "cmd": "actual-test --check", "output": "synthetic-verifier-pass"})

    def land(*args):
        return {"ok": record_fails, "error": "synthetic commit failure"}

    def record(*args, **kwargs):
        raise OSError("synthetic disk failure")

    monkeypatch.setattr(worktree, "run_isolated_task", implement)
    monkeypatch.setattr(worktree, "apply_diff_to_branch", land)
    monkeypatch.setattr(isolated_deliveries, "record_isolated_delivery", record)
    tool = next(tool for tool in build_dev_tools(str(tmp_path)) if tool.name == "dev_isolated")
    result = await tool.handler({"description": "修复折扣"})
    text = result if isinstance(result, str) else result["text"]
    assert "actual-test --check" in text and "synthetic-verifier-pass" in text
    assert "wrong claimed command" not in text and "交付提交：" not in text
    if record_fails:
        assert "交付记录未保存" in text  # Branch remains available; no invented commit.
    else:
        assert result["ok"] is False and "未交付分支" in text
