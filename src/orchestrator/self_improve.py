"""L2 自我改进回路（第一刀：测试缺口 → 自动生成测试，PR/分支门控）。

为什么先做"补测试"这一类：它是唯一【纯增量】的修复——只新增测试文件、绝不改既有
代码，所以不可能弄坏生产逻辑；而且有【客观适应度信号】（真的 pytest 通过/失败，
不是 LLM 自评），这正是让自改进区别于"自主进化炒作"的关键。

安全边界（与演进后的核心理念一致：框架可"提议"对自身的修改，人在合并口）：
- 只新增测试，绝不编辑既有代码。
- 生成的测试必须真的 pytest 通过才纳入提案（门控）。
- 绝不碰 main、绝不自动合并：默认 dry-run 只产出提案；apply 仅写新分支供人审。
- 自动生成的测试可能平凡或断言有误，质量由人在合并口把关——这是有意为之。

更难的"改 bug / 坏味道 / 循环依赖"需要外科手术式编辑既有文件 + 更强的人判，留给 L2.2。
"""

import asyncio
import logging
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any, Awaitable, Callable, List, Optional, Tuple

from pydantic import BaseModel, Field

from ..agents.base import Agent, AgentConfig, AgentResult

logger = logging.getLogger(__name__)


class Proposal(BaseModel):
    """对单个 finding 的一条改进提案。"""
    finding_title: str
    module: str
    test_path: str           # 拟新增的测试文件相对路径
    content: str = ""
    accepted: bool = False
    reason: str = ""         # 通过说明或被拒原因


class ImprovementResult(BaseModel):
    """一次自改进的产出。"""
    proposals: List[Proposal] = Field(default_factory=list)
    branch: Optional[str] = None
    summary: str = ""

    @property
    def accepted(self) -> List[Proposal]:
        return [p for p in self.proposals if p.accepted]

    @property
    def rejected(self) -> List[Proposal]:
        return [p for p in self.proposals if not p.accepted]


class TestWriterAgent(Agent):
    """为给定模块生成 pytest 单元测试（复用 Agent 的 LLM 调用与推理链捕获）。"""

    __test__ = False  # 类名以 Test 开头，显式告诉 pytest 不要把它当测试类收集

    SYSTEM = (
        "你是资深 Python 测试工程师。为给定模块编写 pytest 单元测试。"
        "要求：用 `from <module> import ...` 或 `import <module>` 导入被测模块；"
        "只测公开、确定性的行为；不要联网、不要文件/进程副作用、不要 sleep；"
        "断言要有实际意义，不要写 `assert True` 这类平凡断言。"
        "返回 JSON：{\"content\":\"<完整的测试文件源码>\"}。只返回 JSON。"
    )

    def __init__(self, config: Optional[AgentConfig] = None, llm_client: Optional[Any] = None):
        super().__init__(config or AgentConfig(role="test-writer", temperature=0.2,
                                               max_tokens=4096), llm_client=llm_client)

    async def execute(self, task: str, **kwargs) -> AgentResult:
        ctx = self._merge_context(kwargs)
        module = ctx.get("module", "")
        source = (ctx.get("source", "") or "")[:6000]
        test_path = ctx.get("test_path", "")
        prompt = (
            f"为模块 `{module}` 写 pytest 单元测试。\n\n"
            f"模块源码（可能截断）：\n```python\n{source}\n```\n\n"
            f"返回 JSON：{{\"content\": \"...完整测试文件...\"}}"
        )
        data = await self._complete_json(prompt, system=self.SYSTEM, default={})
        content = data.get("content", "") if isinstance(data, dict) else ""
        return AgentResult(
            success=bool(content),
            output={"content": content, "path": test_path},
            reasoning=self._last_reasoning,
            metadata={"role": "test-writer", "module": module},
        )


# runner 约定：async (test_path: str, content: str) -> (passed: bool, output: str)
Runner = Callable[[str, str], Awaitable[Tuple[bool, str]]]


class SelfImprovementLoop:
    """L1 找问题 → 生成修复 → 真测试门控 → 提案（可选写分支）。"""

    def __init__(self, repo_root: str = ".", writer: Optional[Any] = None,
                 runner: Optional[Runner] = None, max_fixes: int = 3):
        self.root = Path(repo_root).resolve()
        self.writer = writer or TestWriterAgent()
        self.runner = runner            # None -> 用默认真 pytest runner
        self.max_fixes = max_fixes

    async def propose(self, findings: Optional[List[Any]] = None) -> ImprovementResult:
        if findings is None:
            from .self_analysis import analyze_self
            report = await analyze_self(str(self.root))
            findings = report.findings

        targets = [f for f in findings if getattr(f, "category", "") == "test-gap"][: self.max_fixes]
        proposals: List[Proposal] = []

        for f in targets:
            module = self._module_of(f.file)
            test_path = f"tests/unit/test_{module.split('.')[-1]}.py"
            src_path = self.root / f.file
            source = src_path.read_text(encoding="utf-8") if src_path.exists() else ""

            res = await self.writer.execute(
                "为该模块写测试",
                context={"module": module, "source": source, "test_path": test_path},
            )
            content = ""
            if res and isinstance(res.output, dict):
                content = res.output.get("content", "") or ""

            if not content.strip():
                proposals.append(Proposal(finding_title=f.title, module=module,
                                          test_path=test_path, accepted=False,
                                          reason="生成器未产出测试内容"))
                continue

            passed, out = await self._gate(test_path, content)
            proposals.append(Proposal(
                finding_title=f.title, module=module, test_path=test_path,
                content=content, accepted=passed,
                reason="测试通过（待人审质量）" if passed else f"门控未通过: {out.strip()[:300]}",
            ))

        acc = sum(1 for p in proposals if p.accepted)
        return ImprovementResult(
            proposals=proposals,
            summary=f"共 {len(proposals)} 条提案，{acc} 条通过门控、{len(proposals) - acc} 条被拒",
        )

    async def _gate(self, test_path: str, content: str) -> Tuple[bool, str]:
        """把候选测试落到临时位置，真实跑 pytest，跑完删除。"""
        if self.runner is not None:
            return await self.runner(test_path, content)
        return await self._default_runner(test_path, content)

    async def _default_runner(self, test_path: str, content: str) -> Tuple[bool, str]:
        # 放到 tests/unit/ 下、用临时名跑，保证与既有测试相同的 import 解析；跑完即删
        tmp_dir = self.root / "tests" / "unit"
        tmp_dir.mkdir(parents=True, exist_ok=True)
        tmp = tmp_dir / f"test__l2tmp_{uuid.uuid4().hex[:8]}.py"
        tmp.write_text(content, encoding="utf-8")
        try:
            import os
            env = {**os.environ, "PYTHONPATH": str(self.root), "OPENAI_API_KEY": ""}
            proc = await asyncio.create_subprocess_exec(
                sys.executable, "-m", "pytest", str(tmp), "-q", "-p", "no:cacheprovider",
                cwd=str(self.root), env=env,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
            )
            out, _ = await asyncio.wait_for(proc.communicate(), timeout=120)
            return proc.returncode == 0, out.decode("utf-8", "replace")
        except Exception as e:
            return False, f"运行测试出错: {e}"
        finally:
            try:
                tmp.unlink()
            except OSError:
                pass

    def apply(self, result: ImprovementResult, branch: Optional[str] = None) -> Optional[str]:
        """把【已通过门控】的测试写到一个新分支并提交（绝不碰 main、绝不合并）。

        只 add/commit 这些新测试文件，不动工作区里其它未提交改动。返回分支名。
        """
        accepted = result.accepted
        if not accepted:
            return None

        branch = branch or f"l2/auto-tests-{uuid.uuid4().hex[:8]}"
        self._git("checkout", "-b", branch)

        written: List[str] = []
        for p in accepted:
            dest = self.root / p.test_path
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(p.content, encoding="utf-8")
            written.append(p.test_path)

        self._git("add", *written)
        modules = ", ".join(p.module for p in accepted)
        self._git("commit", "-m", f"test: L2 自动补测 {modules}\n\n由 self-improve 生成，待人工审查测试质量后合并。", "--", *written)
        result.branch = branch
        return branch

    def _git(self, *args: str) -> None:
        subprocess.run(["git", *args], cwd=str(self.root), check=True,
                       capture_output=True, text=True)

    def _module_of(self, rel_file: str) -> str:
        parts = Path(rel_file).with_suffix("").parts
        return ".".join(parts)


def render_result(result: ImprovementResult) -> str:
    lines = ["", "=" * 64, "  L2 自改进提案（默认 dry-run，只读不改 main）", "=" * 64,
             result.summary, "-" * 64]
    for i, p in enumerate(result.proposals, 1):
        mark = "✅ 纳入" if p.accepted else "❌ 跳过"
        lines.append(f"{i}. {mark}  {p.module}  ->  {p.test_path}")
        lines.append(f"   针对: {p.finding_title}")
        lines.append(f"   原因: {p.reason}")
    lines.append("-" * 64)
    if result.branch:
        lines.append(f"已写入分支: {result.branch}（请 review 测试质量后再合并；切勿直接合并未审的自动测试）")
    else:
        lines.append("dry-run：未写任何文件。加 --apply 可把通过门控的测试写到一个新分支供 review。")
    lines.append("=" * 64)
    return "\n".join(lines)
