"""L2.2 自我改进：修改既有代码（bug / 坏味道），全量测试门控。

相比 L2（只新增测试、纯增量），这里【会编辑既有源文件】，风险更高，故门控与边界更严：
- 只编辑 finding 指名的【那一个源文件】，绝不编辑测试文件（杜绝"改弱测试骗过门控"）；
- 用外科编辑原语 apply_edits（精确匹配 / 唯一 / 原子 / AST 校验）落地，要么干净命中要么拒绝；
- 门控 = 把改动落盘后【全量测试必须保持绿】，绿才纳入、红则【自动回滚】；
- 绝不碰 main、绝不自动合并：默认 dry-run 只产出 diff 提案，--apply 写新分支供人审。

findings 来自 self_analysis 的 LLM 深审层（category=bug/code-smell，带具体 file）。
"""

import asyncio
import logging
import os
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any, Awaitable, Callable, List, Optional, Tuple

from pydantic import BaseModel, Field

from ..agents.base import Agent, AgentConfig, AgentResult
from ..editor.surgical import Edit, apply_edits, render_diff

logger = logging.getLogger(__name__)

FIXABLE_CATEGORIES = {"bug", "code-smell"}


class CodeProposal(BaseModel):
    """对单个 finding 的一条代码修复提案。"""
    finding_title: str
    file: str
    diff: str = ""
    new_content: str = ""
    rationale: str = ""          # editor 给的修改理由
    accepted: bool = False
    reason: str = ""             # 通过说明或被拒原因


class CodeFixResult(BaseModel):
    proposals: List[CodeProposal] = Field(default_factory=list)
    branch: Optional[str] = None
    summary: str = ""

    @property
    def accepted(self) -> List[CodeProposal]:
        return [p for p in self.proposals if p.accepted]

    @property
    def rejected(self) -> List[CodeProposal]:
        return [p for p in self.proposals if not p.accepted]


class EditorAgent(Agent):
    """根据 finding 对既有文件产出最小化的外科修改（一组精确替换）。"""

    SYSTEM = (
        "你是资深工程师，只做最小化的外科手术式修改来修复指定问题。"
        "返回 JSON：{\"edits\":[{\"old_string\":\"...\",\"new_string\":\"...\"}],\"rationale\":\"...\"}。"
        "硬性要求："
        "old_string 必须是文件中【逐字符精确、含缩进、且唯一】的现有片段；"
        "new_string 是替换后的片段；改动尽量小、只针对该问题；"
        "绝不顺手重构无关代码、绝不改动测试。只返回 JSON。"
    )

    def __init__(self, config: Optional[AgentConfig] = None, llm_client: Optional[Any] = None):
        super().__init__(config or AgentConfig(role="editor", temperature=0.1,
                                               max_tokens=4096), llm_client=llm_client)

    async def execute(self, task: str, **kwargs) -> AgentResult:
        ctx = self._merge_context(kwargs)
        file = ctx.get("file", "")
        content = (ctx.get("content", "") or "")[:8000]
        finding = ctx.get("finding", "")
        prompt = (
            f"文件 `{file}` 存在问题：\n{finding}\n\n"
            f"当前完整内容：\n```python\n{content}\n```\n\n"
            f"产出最小化的外科修改来修复它，按系统提示的 JSON 返回。"
        )
        data = await self._complete_json(prompt, system=self.SYSTEM, default={})
        edits = data.get("edits", []) if isinstance(data, dict) else []
        rationale = data.get("rationale", "") if isinstance(data, dict) else ""
        return AgentResult(
            success=bool(edits),
            output={"edits": edits, "rationale": rationale},
            reasoning=self._last_reasoning,
            metadata={"role": "editor", "file": file},
        )


# gate 约定：async () -> (passed: bool, output: str)
Gate = Callable[[], Awaitable[Tuple[bool, str]]]


def _is_test_file(rel: str) -> bool:
    p = Path(rel)
    return p.name.startswith("test_") or "tests" in p.parts


class CodeFixLoop:
    """L1/深审找问题 → 外科修改 → 全量测试门控 → 提案（可选写分支）。"""

    def __init__(self, repo_root: str = ".", editor: Optional[Any] = None,
                 gate: Optional[Gate] = None, max_fixes: int = 3):
        self.root = Path(repo_root).resolve()
        self.editor = editor or EditorAgent()
        self.gate = gate
        self.max_fixes = max_fixes

    async def propose(self, findings: List[Any]) -> CodeFixResult:
        # 每个文件最多修一处（避免同文件多提案的 new_content 互相覆盖）
        targets: List[Any] = []
        seen_files = set()
        for f in findings:
            if getattr(f, "category", "") not in FIXABLE_CATEGORIES:
                continue
            rel = getattr(f, "file", "")
            if not rel or rel in seen_files:
                continue
            seen_files.add(rel)
            targets.append(f)
            if len(targets) >= self.max_fixes:
                break

        proposals: List[CodeProposal] = []
        for f in targets:
            proposals.append(await self._fix_one(f))

        acc = sum(1 for p in proposals if p.accepted)
        return CodeFixResult(
            proposals=proposals,
            summary=f"共 {len(proposals)} 条修复提案，{acc} 条通过全量门控、{len(proposals) - acc} 条被拒",
        )

    async def _fix_one(self, f: Any) -> CodeProposal:
        rel = f.file
        base = CodeProposal(finding_title=getattr(f, "title", ""), file=rel)

        if _is_test_file(rel):
            base.reason = "拒绝：不编辑测试文件（避免改弱测试骗过门控）"
            return base
        path = self.root / rel
        if not path.exists():
            base.reason = "拒绝：目标文件不存在"
            return base

        content = path.read_text(encoding="utf-8")
        finding_text = (f"{getattr(f, 'title', '')} | 依据:{getattr(f, 'evidence', '')} "
                        f"| 建议:{getattr(f, 'suggestion', '')}")
        res = await self.editor.execute("修复该文件", context={
            "file": rel, "content": content, "finding": finding_text,
        })
        out = res.output if res and isinstance(res.output, dict) else {}
        base.rationale = out.get("rationale", "")

        edits = self._parse_edits(out.get("edits", []))
        if not edits:
            base.reason = "拒绝：编辑器未产出有效编辑"
            return base

        applied = apply_edits(content, edits)
        if not applied.ok:
            base.reason = f"拒绝：编辑无法干净应用（{applied.error}）"
            return base

        passed, gout = await self._gate_with_content(rel, applied.content)
        base.diff = render_diff(content, applied.content, rel)
        base.new_content = applied.content
        base.accepted = passed
        base.reason = ("全量测试通过（待人审）" if passed
                       else f"门控未通过，已回滚: {gout.strip()[-300:]}")
        return base

    @staticmethod
    def _parse_edits(raw: Any) -> List[Edit]:
        edits = []
        if not isinstance(raw, list):
            return edits
        for e in raw:
            if isinstance(e, dict) and "old_string" in e and "new_string" in e:
                edits.append(Edit(
                    old_string=e["old_string"],
                    new_string=e["new_string"],
                    replace_all=bool(e.get("replace_all", False)),
                ))
        return edits

    async def _gate_with_content(self, rel: str, new_content: str) -> Tuple[bool, str]:
        """把改动落盘 → 跑门控 → 无论绿红都还原工作区（dry-run 不留痕）。"""
        path = self.root / rel
        snapshot = path.read_text(encoding="utf-8")
        path.write_text(new_content, encoding="utf-8")
        try:
            gate = self.gate or self._default_gate
            return await gate()
        finally:
            path.write_text(snapshot, encoding="utf-8")

    async def _default_gate(self) -> Tuple[bool, str]:
        env = {**os.environ, "PYTHONPATH": str(self.root),
               "OPENAI_API_KEY": "", "AUTODEV_API_TOKEN": ""}
        try:
            proc = await asyncio.create_subprocess_exec(
                sys.executable, "-m", "pytest", "tests/", "-q", "-p", "no:cacheprovider",
                cwd=str(self.root), env=env,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
            )
            out, _ = await asyncio.wait_for(proc.communicate(), timeout=600)
            return proc.returncode == 0, out.decode("utf-8", "replace")
        except Exception as e:
            return False, f"门控运行出错: {e}"

    def apply(self, result: CodeFixResult, branch: Optional[str] = None) -> Optional[str]:
        """把通过门控的修复写到新分支并提交（绝不碰 main、绝不合并）。"""
        accepted = result.accepted
        if not accepted:
            return None
        branch = branch or f"l2fix/auto-{uuid.uuid4().hex[:8]}"
        self._git("checkout", "-b", branch)

        written = []
        for p in accepted:
            (self.root / p.file).write_text(p.new_content, encoding="utf-8")
            written.append(p.file)
        self._git("add", *written)
        files = ", ".join(p.file for p in accepted)
        self._git("commit", "-m",
                  f"fix: L2.2 自动修复 {files}\n\n由 self-fix 生成，已通过全量测试门控；待人工审查后合并。",
                  "--", *written)
        result.branch = branch
        return branch

    def _git(self, *args: str) -> None:
        subprocess.run(["git", *args], cwd=str(self.root), check=True,
                       capture_output=True, text=True)


def render_result(result: CodeFixResult) -> str:
    lines = ["", "=" * 64, "  L2.2 代码修复提案（默认 dry-run，只读不改 main）", "=" * 64,
             result.summary, "-" * 64]
    for i, p in enumerate(result.proposals, 1):
        mark = "✅ 纳入" if p.accepted else "❌ 跳过"
        lines.append(f"{i}. {mark}  {p.file}")
        lines.append(f"   针对: {p.finding_title}")
        if p.rationale:
            lines.append(f"   修改理由: {p.rationale}")
        lines.append(f"   结果: {p.reason}")
        if p.accepted and p.diff:
            lines.append("   diff:")
            for dl in p.diff.splitlines():
                lines.append(f"     {dl}")
    lines.append("-" * 64)
    if result.branch:
        lines.append(f"已写入分支: {result.branch}（请 review 后再合并；切勿直接合并未审的自动修复）")
    else:
        lines.append("dry-run：未写任何文件。加 --apply 可把通过门控的修复写到新分支供 review。")
    lines.append("=" * 64)
    return "\n".join(lines)
