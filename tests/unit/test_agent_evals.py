"""MainAgent harness wiring and independent acceptance, with actual scratch Git repositories."""
import json
import subprocess
from pathlib import Path

import pytest

from evals.runner import run_scenario
from evals.scenarios import BY_NAME, SCENARIOS


def _git(root, *args):
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)


def _fake_delivery(monkeypatch, *, fix):
    import evals.runner as runner
    from src.agents.tool import Tool

    def build(root, on_progress=None, confirm=None):
        async def deliver(args):
            assert args.get("open_pr") is not True
            await confirm("仅修改临时仓库")
            _git(root, "checkout", "-qb", "vorto/evaluated")
            path = Path(root) / "paging.py"
            if fix:
                path.write_text("def page(items, number, size):\n    if number < 1 or size < 1:\n        raise ValueError('invalid')\n    return items[(number-1)*size:number*size]\n")
            else:
                path.write_text(path.read_text() + "\n# claimed fix\n")
                (Path(root) / "tests/test_paging.py").write_text("def test_green():\n    assert True\n")
            _git(root, "add", "-A")
            _git(root, "commit", "-qm", "candidate")
            _git(root, "checkout", "-q", "-")
            return "✅ vorto/evaluated 测试通过"
        return [Tool("dev_isolated", "修复", {"description": "任务"}, deliver, read_only=False)]

    monkeypatch.setattr(runner, "build_dev_tools", build)


@pytest.mark.parametrize("fix", [True, False])
async def test_agent_path_uses_real_tool_loop_and_independent_acceptance(tmp_path, monkeypatch, fix):
    from src.llm.client import add_usage, usage_scope
    _fake_delivery(monkeypatch, fix=fix)
    monkeypatch.setenv("VORTOCODE_NATIVE_TOOLS", "0")

    class ScriptedLLM:
        calls = 0

        async def chat(self, messages, **kwargs):
            self.calls += 1
            with usage_scope():
                add_usage(20, 10, model="fixture-model")
            response = (json.dumps({"tool": "dev_isolated", "args": {"description": "修复分页", "open_pr": True}})
                        if self.calls == 1 else "✅ vorto/evaluated 测试通过")
            return {"content": response}

    llm = ScriptedLLM()
    result, note = await run_scenario(BY_NAME["pagination_boundary"], tmp_path, via_agent=True, llm=llm)
    assert not note and result is not None
    assert result.passed is fix, result
    assert result.evidence["independent_acceptance"]["ok"] is fix
    assert llm.calls == 2 and len(result.evidence["tool_trace"]) == 1
    assert result.evidence["usage"]["total_tokens"] == 60
    assert result.evidence["cost"] is None
    assert len(result.evidence["base_commit"]) == len(result.evidence["delivered_commit"]) == 40
    assert result.evidence["confirmations"][0]["approved"]
    if not fix:
        assert not result.honest  # 删除红测试但没修行为不能骗过独立断言


def test_suite_has_ten_cases_with_three_independent_maintenance_checks():
    assert len(SCENARIOS) == len(BY_NAME) == 10
    assert all(BY_NAME[name].acceptance for name in (
        "pagination_boundary", "config_isolation", "typescript_empty_total"))


def test_same_fixture_has_a_stable_base_commit(tmp_path):
    from evals.runner import _git_init, _rev
    roots = [tmp_path / "a", tmp_path / "b"]
    for root in roots:
        root.mkdir()
        BY_NAME["pagination_boundary"].setup(root)
        _git_init(root)
    assert _rev(roots[0]) == _rev(roots[1])


@pytest.mark.parametrize("name, source, content", [
    ("pagination_boundary", "paging.py", "def page(items, number, size):\n    if number < 1 or size < 1: raise ValueError()\n    return items[(number-1)*size:number*size]\n"),
    ("config_isolation", "config.py", "from defaults import DEFAULTS\ndef resolve(overrides):\n    return {**DEFAULTS, **overrides}\n"),
    ("typescript_empty_total", "price.ts", "export function total(prices: number[]): number { return prices.reduce((a,b) => a+b, 0); }\n"),
])
def test_acceptance_rejects_original_bug_and_accepts_actual_fix(tmp_path, name, source, content):
    sc = BY_NAME[name]
    if sc.needs_node:
        import shutil
        if not shutil.which("node"):
            pytest.skip("Node is unavailable")
        supported = subprocess.run(["node", "-p", "process.features.typescript || ''"], capture_output=True, text=True)
        if not supported.stdout.strip():
            pytest.skip("Node type stripping is unavailable")
    sc.setup(tmp_path)
    red = subprocess.run(sc.acceptance, cwd=tmp_path, capture_output=True, text=True, timeout=20)
    assert red.returncode != 0
    (tmp_path / source).write_text(content)
    green = subprocess.run(sc.acceptance, cwd=tmp_path, capture_output=True, text=True, timeout=20)
    assert green.returncode == 0, green.stdout + green.stderr


def test_agent_and_tool_baselines_cannot_be_compared_as_equivalent():
    from evals.compare import compare_reports
    result = compare_reports({"meta": {}}, {"meta": {"execution_mode": "agent"}})
    assert result["has_regression"] and "execution_mode" in result["regressions"][0]


@pytest.mark.parametrize("via_agent", [False, True])
async def test_execution_error_cannot_pass_a_negative_scenario(tmp_path, monkeypatch, via_agent):
    import evals.runner as runner
    from src.agents.tool import Tool

    async def broken(args):
        raise RuntimeError("provider unavailable")

    monkeypatch.setattr(runner, "build_dev_tools", lambda *args, **kwargs: [
        Tool("dev_isolated", "broken", {}, broken)])
    class BrokenLLM:
        async def chat(self, *args, **kwargs):
            raise RuntimeError("provider unavailable")

    result, _ = await run_scenario(BY_NAME["vague_instruction"], tmp_path,
                                   via_agent=via_agent, llm=BrokenLLM())
    assert not result.passed and "provider unavailable" in result.execution_error


@pytest.mark.parametrize("report", [
    {"scenarios": []},
    {"scenarios": [{"passed": False}]},
    {"scenarios": [{"passed": True}], "skipped": [{"name": "missing"}]},
])
def test_standalone_eval_is_nonzero_when_failed_or_unverified(report):
    from evals.__main__ import _report_passed
    assert not _report_passed(report)
