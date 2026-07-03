"""dev_auto PR 前对抗审查段（C2）的测试。

纯 helper（抽小节/解析 findings/confirmed 过滤/读 AGENTS.md）确定性测；run_gate 编排用**注入的
假 reviewer + 假 repair** 测（无 LLM）：钉住"无发现放行 / confirmed 喂修复后复审通过放行 / 修不掉拦
PR / 无证据不拦 / fail-open"。
"""

import pytest

from src.agents import review
from src.agents.main_agent import _dev_review_enabled


# ------------------------------------------------------------ 纯 helper
def test_extract_section_grabs_until_next_heading():
    md = "# 顶\n## Review guidelines\n规则一\n规则二\n## 别的\n无关"
    assert review._extract_section(md, "Review guidelines") == "规则一\n规则二"


def test_extract_section_absent_returns_empty():
    assert review._extract_section("# 只有标题\n正文", "Review guidelines") == ""


def test_parse_findings_bare_array():
    out = review.parse_findings('[{"severity":"P0","file":"a.py","issue":"x","evidence":"跑了 t"}]')
    assert len(out) == 1 and out[0]["severity"] == "P0"


def test_parse_findings_with_prose_and_codefence():
    txt = "我看了下：\n```json\n[{\"severity\":\"P1\",\"evidence\":\"e\"}]\n```\n就这些。"
    assert review.parse_findings(txt) == [{"severity": "P1", "evidence": "e"}]


def test_parse_findings_garbage_is_empty():
    assert review.parse_findings("没有发现，一切正常。") == []
    assert review.parse_findings("") == []


def test_confirmed_requires_severity_and_evidence():
    findings = [
        {"severity": "P0", "evidence": "复现了"},      # ✓
        {"severity": "P1", "evidence": ""},            # ✗ 无证据
        {"severity": "P2", "evidence": "e"},           # ✗ 非 P0/P1
        {"severity": "p1", "evidence": "e"},           # ✓ 大小写不敏感
    ]
    conf = review.confirmed_findings(findings)
    assert len(conf) == 2


def test_load_review_guidelines_from_agents_md(tmp_path):
    (tmp_path / "AGENTS.md").write_text(
        "# 项目\n## Review guidelines\n- 边界必查\n- 并发必查\n## 其它\n略", encoding="utf-8")
    g = review.load_review_guidelines(str(tmp_path))
    assert "边界必查" in g and "并发必查" in g and "略" not in g


def test_load_review_guidelines_absent(tmp_path):
    assert review.load_review_guidelines(str(tmp_path)) == ""


# ------------------------------------------------------------ run_gate 编排（假 reviewer/repair）
def _reviewer(*rounds):
    """造一个每次调用依次返回 rounds[i] 的假 reviewer，并记录调用次数。"""
    calls = {"n": 0}

    async def _r(repo_root, branch, base, *, test_cmd=None, guidelines=""):
        i = min(calls["n"], len(rounds) - 1)
        calls["n"] += 1
        return rounds[i]

    return _r, calls


def _repair_recorder():
    got = {"n": 0, "desc": None}

    async def _repair(fix_desc):
        got["n"] += 1
        got["desc"] = fix_desc

    return _repair, got


_P0 = {"severity": "P0", "file": "a.py", "issue": "越界", "evidence": "跑 t 复现"}


@pytest.mark.asyncio
async def test_gate_no_findings_passes_without_repair(tmp_path):
    reviewer, rc = _reviewer([])
    repair, got = _repair_recorder()
    note, blocked = await review.run_gate(str(tmp_path), "vorto/x", "main",
                                          repair=repair, reviewer=reviewer)
    assert not blocked and "审查通过" in note
    assert got["n"] == 0 and rc["n"] == 1                     # 无发现：没修、只审一次


@pytest.mark.asyncio
async def test_gate_confirmed_then_fixed_passes(tmp_path):
    reviewer, rc = _reviewer([_P0], [])                       # 首审有 P0，复审干净
    repair, got = _repair_recorder()
    note, blocked = await review.run_gate(str(tmp_path), "vorto/x", "main",
                                          repair=repair, reviewer=reviewer)
    assert not blocked and "已自修复并复审通过" in note
    assert got["n"] == 1 and rc["n"] == 2                     # 修一轮 + 审两次
    assert "越界" in got["desc"]                              # 修复描述带上了 finding


@pytest.mark.asyncio
async def test_gate_unfixable_blocks_pr(tmp_path):
    reviewer, _ = _reviewer([_P0], [_P0])                     # 修完还在
    repair, _ = _repair_recorder()
    note, blocked = await review.run_gate(str(tmp_path), "vorto/x", "main",
                                          repair=repair, reviewer=reviewer)
    assert blocked and "未开 PR" in note and "未修掉" in note


@pytest.mark.asyncio
async def test_gate_evidence_less_finding_does_not_block(tmp_path):
    reviewer, _ = _reviewer([{"severity": "P0", "issue": "疑似", "evidence": ""}])
    repair, got = _repair_recorder()
    note, blocked = await review.run_gate(str(tmp_path), "vorto/x", "main",
                                          repair=repair, reviewer=reviewer)
    assert not blocked and got["n"] == 0                      # 无证据 → 不算 confirmed → 不拦


@pytest.mark.asyncio
async def test_gate_reviewer_error_is_fail_open(tmp_path):
    async def _boom(*a, **k):
        raise RuntimeError("relay 挂了")
    note, blocked = await review.run_gate(str(tmp_path), "vorto/x", "main",
                                          repair=(lambda *_a: None), reviewer=_boom)
    assert not blocked and "未能完成" in note                 # fail-open：审查抽风不拦绿集成


@pytest.mark.asyncio
async def test_gate_repair_error_blocks(tmp_path):
    reviewer, _ = _reviewer([_P0])

    async def _bad_repair(_desc):
        raise RuntimeError("worktree 崩了")
    note, blocked = await review.run_gate(str(tmp_path), "vorto/x", "main",
                                          repair=_bad_repair, reviewer=reviewer)
    assert blocked and "自修复出错" in note


# ------------------------------------------------------------ 开关
def test_dev_review_enabled_default_on(monkeypatch):
    monkeypatch.delenv("VORTOCODE_DEV_REVIEW", raising=False)
    assert _dev_review_enabled() is True
    for v in ("0", "false", "no", "off"):
        monkeypatch.setenv("VORTOCODE_DEV_REVIEW", v)
        assert _dev_review_enabled() is False
    monkeypatch.setenv("VORTOCODE_DEV_REVIEW", "1")
    assert _dev_review_enabled() is True
