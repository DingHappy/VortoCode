"""L2.2 CodeFixLoop 测试（离线：注入 FakeLLM editor + 注入 gate）。

重点验证安全核心：
- 改动落盘后 gate 看到的是【已修改】内容；门控绿才纳入、红则【回滚】且工作区还原；
- 编辑无法干净应用时在门控前就拒绝；
- 绝不编辑测试文件；
- apply 只写新分支、提交特定文件。
"""

import json
import subprocess

import pytest

from src.orchestrator.self_analysis import Finding
from src.orchestrator.code_fix import CodeFixLoop, EditorAgent, CodeProposal, CodeFixResult


class FakeLLM:
    def __init__(self, content):
        self._content = content

    async def chat(self, messages, model=None, temperature=None, max_tokens=None, stream=False):
        return {"content": self._content}


BUGGY = "def add(a, b):\n    return a - b\n"   # bug：应为 a + b


def _editor(edits, rationale="fix"):
    return EditorAgent(llm_client=FakeLLM(json.dumps({"edits": edits, "rationale": rationale})))


def _bug_finding(file="src/calc.py"):
    return Finding(category="bug", severity="high", title="add 实现错误",
                   file=file, evidence="返回 a-b", suggestion="改为 a+b")


def _make_src(tmp_path):
    (tmp_path / "src").mkdir()
    f = tmp_path / "src" / "calc.py"
    f.write_text(BUGGY)
    return f


@pytest.mark.asyncio
async def test_valid_fix_passes_gate_and_is_accepted(tmp_path):
    f = _make_src(tmp_path)
    seen = {}

    async def gate():
        # 门控运行时应看到【已应用修复】的内容
        seen["content"] = f.read_text()
        return True, "all green"

    loop = CodeFixLoop(str(tmp_path),
                       editor=_editor([{"old_string": "return a - b", "new_string": "return a + b"}]),
                       gate=gate)
    result = await loop.propose([_bug_finding()])

    assert len(result.accepted) == 1
    p = result.accepted[0]
    assert "return a + b" in p.new_content
    assert "-    return a - b" in p.diff and "+    return a + b" in p.diff
    assert "return a + b" in seen["content"]      # gate 确实看到修改后的内容
    # dry-run：工作区已还原
    assert f.read_text() == BUGGY


@pytest.mark.asyncio
async def test_failing_gate_reverts_and_rejects(tmp_path):
    f = _make_src(tmp_path)

    async def gate():
        return False, "1 failed: test_add"

    loop = CodeFixLoop(str(tmp_path),
                       editor=_editor([{"old_string": "return a - b", "new_string": "return a + b"}]),
                       gate=gate)
    result = await loop.propose([_bug_finding()])

    assert result.accepted == []
    assert "门控未通过" in result.rejected[0].reason
    assert f.read_text() == BUGGY                 # 回滚：文件还原


@pytest.mark.asyncio
async def test_unapplicable_edit_rejected_before_gate(tmp_path):
    _make_src(tmp_path)
    gate_called = {"n": 0}

    async def gate():
        gate_called["n"] += 1
        return True, ""

    # old_string 不在文件里 -> apply_edits 失败 -> 门控前就拒绝
    loop = CodeFixLoop(str(tmp_path),
                       editor=_editor([{"old_string": "return a / b", "new_string": "return a + b"}]),
                       gate=gate)
    result = await loop.propose([_bug_finding()])

    assert result.accepted == []
    assert "无法干净应用" in result.rejected[0].reason
    assert gate_called["n"] == 0                   # 门控未被调用


@pytest.mark.asyncio
async def test_refuses_to_edit_test_files(tmp_path):
    called = {"editor": 0}

    class CountingEditor(EditorAgent):
        async def execute(self, task, **kwargs):
            called["editor"] += 1
            return await super().execute(task, **kwargs)

    loop = CodeFixLoop(str(tmp_path),
                       editor=CountingEditor(llm_client=FakeLLM("{}")))
    result = await loop.propose([_bug_finding(file="tests/unit/test_calc.py")])

    assert result.accepted == []
    assert "不编辑测试文件" in result.rejected[0].reason
    assert called["editor"] == 0                   # 编辑器根本没被调用


@pytest.mark.asyncio
async def test_non_fixable_category_is_ignored(tmp_path):
    _make_src(tmp_path)
    loop = CodeFixLoop(str(tmp_path), editor=_editor([]))
    # orphan-module / test-gap 不属于可修类别
    findings = [Finding(category="test-gap", severity="low", title="x", file="src/calc.py")]

    result = await loop.propose(findings)

    assert result.proposals == []


def test_apply_writes_accepted_fix_to_branch(tmp_path):
    def git(*a):
        subprocess.run(["git", *a], cwd=tmp_path, check=True, capture_output=True, text=True)

    git("init")
    git("config", "user.email", "t@t.com")
    git("config", "user.name", "t")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "calc.py").write_text(BUGGY)
    git("add", "-A")
    git("commit", "-m", "init")

    loop = CodeFixLoop(str(tmp_path))
    result = CodeFixResult(proposals=[CodeProposal(
        finding_title="add bug", file="src/calc.py",
        new_content="def add(a, b):\n    return a + b\n", accepted=True, reason="ok",
    )])

    branch = loop.apply(result, branch="l2fix/auto-x")

    assert branch == "l2fix/auto-x"
    cur = subprocess.run(["git", "branch", "--show-current"], cwd=tmp_path,
                         capture_output=True, text=True).stdout.strip()
    assert cur == "l2fix/auto-x"
    assert (tmp_path / "src" / "calc.py").read_text() == "def add(a, b):\n    return a + b\n"
    status = subprocess.run(["git", "status", "--porcelain"], cwd=tmp_path,
                            capture_output=True, text=True).stdout.strip()
    assert status == ""        # 已提交，干净
