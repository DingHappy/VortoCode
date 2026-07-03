"""dev_auto 的 PR 前对抗审查段（C2）——集成绿之后、开 PR 之前的一道"专职挑刺"关。

借鉴 Codex GitHub review / Claude ultrareview：**只报 P0/P1**（防评论疲劳）、审查指令从目标仓库
就近 AGENTS.md 的 `## Review guidelines` 读、**每条 finding 必须带验证证据**（跑过的命令+输出，或
能触发的具体输入）——空口断言的降级为不拦截。confirmed（P0/P1 且有证据）会喂回一轮自修复，修不掉
就如实拦下 PR、保留分支。把 codex 外审反复抓到真 bug 的经验内化进流水线。

与 main_agent 的循环依赖用**函数内惰性 import** 打破（main_agent 调本模块的 review_branch，
本模块建 reviewer 子 agent 时才 import main_agent 的工具工厂）。
"""

from __future__ import annotations

import json
import re
import subprocess
import uuid
from pathlib import Path
from typing import List, Optional

_SEVERITIES = ("P0", "P1")


# --------------------------------------------------------------- Review guidelines（就近 AGENTS.md）
def _extract_section(md: str, title: str) -> str:
    """从 markdown 里抽出 `## <title>`（或更深级别）到下一个同/更高级标题之间的正文。"""
    lines = (md or "").splitlines()
    out: List[str] = []
    grabbing = False
    grab_level = 0
    for ln in lines:
        m = re.match(r"^(#{1,6})\s+(.*)$", ln)
        if m:
            level, heading = len(m.group(1)), m.group(2).strip()
            if not grabbing and title.lower() in heading.lower():
                grabbing, grab_level = True, level
                continue
            if grabbing and level <= grab_level:      # 遇到同级/更高级标题 → 小节结束
                break
        if grabbing:
            out.append(ln)
    return "\n".join(out).strip()


def load_review_guidelines(repo_root: str) -> str:
    """读目标仓库 AGENTS.md / CLAUDE.md 的 `## Review guidelines` 小节（无则空串）。"""
    for name in ("AGENTS.md", "CLAUDE.md", "VORTO.md"):
        p = Path(repo_root) / name
        if p.is_file():
            try:
                sec = _extract_section(p.read_text(encoding="utf-8"), "Review guidelines")
            except Exception:  # noqa: BLE001
                sec = ""
            if sec:
                return sec
    return ""


# --------------------------------------------------------------- findings 解析 / 过滤（纯函数）
def parse_findings(text: str) -> List[dict]:
    """从 reviewer 回复里宽容地抽出 JSON 数组的 findings；解析不出就当无发现（[]，fail-open）。"""
    if not text:
        return []
    for cand in _json_array_candidates(text):
        try:
            data = json.loads(cand)
        except Exception:  # noqa: BLE001
            continue
        if isinstance(data, list):
            return [d for d in data if isinstance(d, dict)]
    return []


def _json_array_candidates(text: str) -> List[str]:
    # 优先最外层 [ ... ]（贪婪），再退化到每个 ```json 代码块
    cands = []
    greedy = re.search(r"\[.*\]", text, re.S)
    if greedy:
        cands.append(greedy.group(0))
    cands += re.findall(r"```(?:json)?\s*(\[.*?\])\s*```", text, re.S)
    return cands


def confirmed_findings(findings: List[dict]) -> List[dict]:
    """confirmed = severity ∈ {P0,P1} **且** evidence 非空。evidence 为空的只当噪音、不拦 PR。"""
    out = []
    for f in findings or []:
        sev = str(f.get("severity", "")).upper().strip()
        ev = str(f.get("evidence", "")).strip()
        if sev in _SEVERITIES and ev:
            out.append(f)
    return out


def format_findings(findings: List[dict]) -> str:
    lines = []
    for f in findings:
        sev = str(f.get("severity", "?")).upper()
        loc = str(f.get("file", "?"))
        issue = str(f.get("issue", "")).strip()
        ev = str(f.get("evidence", "")).strip()
        lines.append(f"  · [{sev}] {loc}：{issue}\n    证据：{ev[:200]}")
    return "\n".join(lines)


# --------------------------------------------------------------- 分支 diff
def _branch_diff(repo_root: str, base: str, branch: str, limit: int = 8000) -> str:
    try:
        r = subprocess.run(["git", "-C", str(repo_root), "diff", f"{base}...{branch}"],
                           capture_output=True, text=True, timeout=30)
        d = r.stdout or ""
    except Exception as e:  # noqa: BLE001
        return f"(取 diff 失败: {e})"
    return d[:limit] + ("\n…(diff 已截断)" if len(d) > limit else "")


_REVIEWER_SYSTEM = (
    "你是一个**专挑刺**的代码审查子 agent，只审查给你的这个分支相对 base 的改动。铁律：\n"
    "1. **只报 P0/P1**：P0=会导致错误结果/崩溃/数据丢失/安全漏洞的严重缺陷；P1=重要的正确性问题。"
    "风格、命名、可读性、微优化、'建议'一律**不报**（防评论疲劳）。\n"
    "2. **每条发现必须带验证证据**：你实际用 run_tests 跑出来的失败、或一个能触发该问题的具体输入/"
    "调用序列。空口'可能有问题'不算证据、不要报。你可以用 read_file/grep 看上下文、用 run_tests 复现。\n"
    "3. 没有够格的 P0/P1 就**输出空数组 []**——宁可不报，也不要凑数。\n"
    "最终**只输出一个 JSON 数组**，每项形如 "
    '{"severity":"P0","file":"路径","issue":"一句话问题","evidence":"你验证到的证据"}。'
)


async def review_branch(repo_root: str, branch: str, base: str, *, llm=None,
                        test_cmd: Optional[list] = None, guidelines: str = "",
                        max_steps: int = 8) -> List[dict]:
    """在临时 worktree 检出 branch，让 reviewer 子 agent 挑刺，返回 findings（list[dict]）。

    出任何错都返回 []（fail-open：审查是开 PR 前的**顾问级**预检，人在合并口是最终兜底，
    reviewer 抽风不该拦住绿的集成）。
    """
    from src.agents.main_agent import (MainAgent, build_read_tools,  # 惰性：破 review↔main_agent 循环
                                       build_test_tool)
    from src.agents.worktree import _git, _worktrees_dir, remove_worktree

    path = _worktrees_dir(repo_root) / ("wt-review-" + uuid.uuid4().hex[:8])
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        remove_worktree(repo_root, path)
    add = _git(repo_root, "worktree", "add", str(path), branch, check=False)
    if add.returncode != 0:
        return []
    try:
        diff = _branch_diff(repo_root, base, branch)
        if not diff.strip():
            return []
        tools = build_read_tools(str(path)) + [build_test_tool(str(path), test_cmd)]
        extra = _REVIEWER_SYSTEM + (f"\n\n【本仓库审查规范】\n{guidelines}" if guidelines else "")
        agent = MainAgent(tools, llm=llm, max_steps=max_steps, extra_system=extra)
        prompt = (f"审查分支 {branch}（相对 {base}）的以下改动。只报 P0/P1、每条带验证证据、"
                  f"用 run_tests 复现你怀疑的问题，最后只输出 JSON 数组：\n\n```diff\n{diff}\n```")
        try:
            reply = await agent.run_turn(prompt, mode="build")
        except Exception:  # noqa: BLE001
            return []
        return parse_findings(reply)
    finally:
        remove_worktree(repo_root, path)


# --------------------------------------------------------------- 审查关（orchestration，可注入以便测试）
async def run_gate(repo_root: str, branch: str, base: str, *, test_cmd=None, repair,
                   reviewer=None, progress=None) -> tuple:
    """审查 → confirmed 喂一轮修复（repair(fix_desc)）→ 重审一次。返回 (note, blocked)。

    reviewer/repair 都可注入（测试用假的，生产由 dev_auto 注入真 review_branch + 依赖接力修复）。
    **fail-open**：审查/重审出错不拦（人在合并口兜底）；只有"确实还有 confirmed"或"修复出错"才 blocked。
    """
    reviewer = reviewer or review_branch
    log = progress or (lambda _m: None)
    guidelines = load_review_guidelines(repo_root)

    log("🔍 PR 前对抗审查：reviewer 子 agent 挑刺中…")
    try:
        findings = await reviewer(repo_root, branch, base, test_cmd=test_cmd, guidelines=guidelines)
    except Exception as e:  # noqa: BLE001
        return (f"\n（审查未能完成：{e}；未拦截，以人工 PR 审核为准。）", False)
    confirmed = confirmed_findings(findings)
    if not confirmed:
        return ("\n🔍 PR 前审查通过：无 P0/P1。", False)

    log(f"🔧 审查发现 {len(confirmed)} 条 P0/P1，喂回一轮自修复…")
    fix_desc = ("修复以下审查发现的严重问题（P0/P1），改完务必自测通过；只动相关文件：\n"
                + format_findings(confirmed))
    try:
        await repair(fix_desc)
    except Exception as e:  # noqa: BLE001
        return (f"\n⚠️ 审查发现 {len(confirmed)} 条 P0/P1，自修复出错（{e}）；**未开 PR**，分支保留：\n"
                + format_findings(confirmed), True)

    log("🔍 重审修复后的分支…")
    try:
        confirmed2 = confirmed_findings(
            await reviewer(repo_root, branch, base, test_cmd=test_cmd, guidelines=guidelines))
    except Exception:  # noqa: BLE001
        confirmed2 = []
    if not confirmed2:
        return (f"\n🔍 PR 前审查发现 {len(confirmed)} 条 P0/P1，已自修复并复审通过。", False)
    return (f"\n⚠️ 审查仍有 {len(confirmed2)} 条 P0/P1 未修掉，**未开 PR**，分支保留待人工处理：\n"
            + format_findings(confirmed2), True)
