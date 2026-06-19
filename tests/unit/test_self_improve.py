"""L2 自我改进回路测试（离线：注入 FakeLLM 生成器；门控用真 pytest 子进程）。

重点验证安全核心：生成的测试必须真的 pytest 通过才被纳入提案；失败的被拒；
apply 只写新分支、提交特定文件，绝不碰 main。LLM 生成环节用 FakeLLM 覆盖
（真实生成需配 key），其余编排/门控/apply 全部确定性。
"""

import json
import subprocess

import pytest

from src.orchestrator.self_analysis import Finding
from src.orchestrator.self_improve import (
    SelfImprovementLoop, TestWriterAgent, Proposal, ImprovementResult,
)


class FakeLLM:
    """模拟 LLMClient.chat：返回预设 content。"""

    def __init__(self, content):
        self._content = content

    async def chat(self, messages, model=None, temperature=None,
                   max_tokens=None, stream=False):
        return {"content": self._content}


def _repo_with_test_gap(root):
    """构造一个含【测试缺口】的临时仓库：feature 被 feature_user 使用但测试到不了。"""
    src = root / "src"
    tests = root / "tests"
    src.mkdir()
    tests.mkdir()
    (src / "__init__.py").write_text("")
    (src / "feature.py").write_text("def feat():\n    return 9\n")
    # feature_user 让 feature 非孤儿（有导入方），但 feature_user 本身无人导入、测试到不了
    (src / "feature_user.py").write_text("from src.feature import feat\n\ndef use():\n    return feat()\n")
    (tests / "__init__.py").write_text("")


@pytest.mark.asyncio
async def test_passing_generated_test_is_accepted(tmp_path):
    _repo_with_test_gap(tmp_path)
    good_test = json.dumps({
        "content": "from src.feature import feat\n\n\ndef test_feat():\n    assert feat() == 9\n"
    })
    loop = SelfImprovementLoop(str(tmp_path), writer=TestWriterAgent(llm_client=FakeLLM(good_test)))

    result = await loop.propose()   # 用默认真 pytest runner 门控

    assert len(result.accepted) == 1
    p = result.accepted[0]
    assert p.module == "src.feature"
    assert p.test_path == "tests/unit/test_feature.py"
    # dry-run：没写任何文件
    assert not (tmp_path / "tests" / "unit" / "test_feature.py").exists()


@pytest.mark.asyncio
async def test_failing_generated_test_is_rejected(tmp_path):
    _repo_with_test_gap(tmp_path)
    bad_test = json.dumps({
        "content": "from src.feature import feat\n\n\ndef test_feat():\n    assert feat() == 0\n"  # 故意写错
    })
    loop = SelfImprovementLoop(str(tmp_path), writer=TestWriterAgent(llm_client=FakeLLM(bad_test)))

    result = await loop.propose()

    assert result.accepted == []
    assert len(result.rejected) == 1
    assert "门控未通过" in result.rejected[0].reason


@pytest.mark.asyncio
async def test_no_test_gap_yields_no_proposals():
    loop = SelfImprovementLoop(".", writer=TestWriterAgent(llm_client=FakeLLM("{}")))
    # 显式传入不含 test-gap 的 findings
    findings = [Finding(category="orphan-module", severity="medium", title="x", file="src/x.py")]

    result = await loop.propose(findings=findings)

    assert result.proposals == []


@pytest.mark.asyncio
async def test_max_fixes_caps_targets_and_uses_runner():
    calls = []

    async def fake_runner(test_path, content):
        calls.append(test_path)
        return True, "ok"

    writer = TestWriterAgent(llm_client=FakeLLM(json.dumps({"content": "def test_x():\n    assert 1 == 1\n"})))
    loop = SelfImprovementLoop(".", writer=writer, runner=fake_runner, max_fixes=2)
    findings = [
        Finding(category="test-gap", severity="low", title=f"m{i}", file=f"src/m{i}.py")
        for i in range(5)
    ]

    result = await loop.propose(findings=findings)

    assert len(result.proposals) == 2          # 被 max_fixes 截断
    assert len(calls) == 2                       # runner 被调用 2 次
    assert all(p.accepted for p in result.proposals)


def test_apply_writes_to_new_branch_not_main(tmp_path):
    # 初始化一个真 git 仓库
    def git(*a):
        subprocess.run(["git", *a], cwd=tmp_path, check=True, capture_output=True, text=True)

    git("init")
    git("config", "user.email", "t@t.com")
    git("config", "user.name", "t")
    (tmp_path / "README.md").write_text("x\n")
    git("add", "README.md")
    git("commit", "-m", "init")

    loop = SelfImprovementLoop(str(tmp_path))
    result = ImprovementResult(proposals=[Proposal(
        finding_title="t", module="src.feature",
        test_path="tests/unit/test_feature.py",
        content="def test_ok():\n    assert True\n", accepted=True, reason="ok",
    )])

    branch = loop.apply(result, branch="l2/auto-x")

    assert branch == "l2/auto-x"
    # 当前在新分支上，且测试文件已提交（status 干净）
    cur = subprocess.run(["git", "branch", "--show-current"], cwd=tmp_path,
                         capture_output=True, text=True).stdout.strip()
    assert cur == "l2/auto-x"
    assert (tmp_path / "tests" / "unit" / "test_feature.py").exists()
    status = subprocess.run(["git", "status", "--porcelain", "tests/unit/test_feature.py"],
                            cwd=tmp_path, capture_output=True, text=True).stdout
    assert status.strip() == ""   # 已提交，无残留
