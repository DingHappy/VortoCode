"""角色化 Agent 实现（真实 LLM 驱动）

主线复用的开发闭环角色（IterativeDevLoop：dev_loop / self_analysis / TUI /improve）：
- DeveloperAgent: 任务 → 生成代码并真实写入工作区
- ReviewerAgent : 代码 → 真实裁决（可 approve / request_changes）
- TesterAgent   : 代码 → 生成并真实运行 pytest，诚实报告通过/失败

约定：execute(task, context=<dict>, **kwargs)。context 在编排流程中贯穿传递，
内含 workspace 路径与各前序角色的产出（context["artifacts"][role]）。

注：ProductAgent / ArchitectAgent（需求→架构的前置阶段）随 5 角色批处理流水线
一并退役删除（2026-07 路线 A）——它们只服务已退役的 web/workspaces 批处理入口。
"""

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..base import Agent, AgentConfig, AgentResult

logger = logging.getLogger(__name__)


def _safe_join(workspace: Path, rel_path: str) -> Optional[Path]:
    """把相对路径安全地拼到工作区下，拒绝绝对路径与越界（..）写入。"""
    if not rel_path:
        return None
    rel_path = rel_path.strip().lstrip("/")
    target = (workspace / rel_path).resolve()
    try:
        target.relative_to(workspace.resolve())
    except ValueError:
        logger.warning("拒绝越界写入: %s", rel_path)
        return None
    return target


def _write_files(workspace: Path, files: List[Dict[str, Any]]) -> List[str]:
    """把 [{path, content}] 写入工作区，返回成功写入的相对路径列表。"""
    written: List[str] = []
    for f in files or []:
        if not isinstance(f, dict):
            continue
        rel = f.get("path") or f.get("filename") or ""
        content = f.get("content", "")
        target = _safe_join(workspace, rel)
        if target is None:
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content if isinstance(content, str) else str(content), encoding="utf-8")
        written.append(str(target.relative_to(workspace.resolve())))
    return written


class DeveloperAgent(Agent):
    """开发工程师 Agent - 代码生成（真实写盘）"""

    SYSTEM = (
        "你是资深全栈工程师。根据任务与架构编写可运行、完整的代码文件。"
        "返回 JSON：{\"files\":[{\"path\":\"相对路径\",\"content\":\"完整文件内容\"}],"
        "\"notes\":\"实现说明\",\"run_command\":\"如何运行\"}。"
        "路径必须是工作区内相对路径，content 必须是完整文件内容，不要省略。"
        "只返回 JSON，不要 markdown 围栏。"
    )

    def __init__(self, config: Optional[AgentConfig] = None, llm_client: Optional[Any] = None):
        default_config = AgentConfig(
            role="developer",
            name="Developer Agent",
            description="开发工程师，负责编写代码",
            capabilities=["code_generation", "testing"],
            tools=["read_file", "write_file", "execute_command"],
            temperature=0.2,
            max_tokens=8192,
        )
        super().__init__(config or default_config, llm_client=llm_client)

    async def execute(self, task: str, **kwargs) -> AgentResult:
        try:
            ctx = self._merge_context(kwargs)
            workspace = self._resolve_workspace(ctx)
            artifacts = ctx.get("artifacts", {}) or {}
            arch = ctx.get("architecture") or artifacts.get("architect", {})
            spec = ctx.get("spec") or artifacts.get("product", {})
            on_token = ctx.get("on_token")  # 提供时按 token 流式回调（用于实时展示）

            result = await self._implement_task(task, spec, arch, workspace, on_token=on_token)
            return AgentResult(
                success=bool(result.get("files_created")),
                output=result,
                files_created=result.get("files_created", []),
                files_modified=result.get("files_modified", []),
                error=None if result.get("files_created") else "未生成任何文件",
                reasoning=self._last_reasoning,
                metadata={"role": "developer", "workspace": str(workspace)},
            )
        except Exception as e:
            logger.exception("DeveloperAgent failed")
            return AgentResult(success=False, error=str(e))

    async def _implement_task(
        self, task: str, spec: Dict[str, Any], arch: Dict[str, Any], workspace: Path,
        on_token: Optional[Any] = None,
    ) -> Dict[str, Any]:
        context_bits = []
        if spec:
            context_bits.append(f"需求规格：{spec}")
        if arch:
            context_bits.append(f"架构方案：{arch}")
        context_text = ("\n".join(context_bits) + "\n") if context_bits else ""

        prompt = f"""{context_text}请实现以下任务，产出完整可运行的代码文件：
{task}

要求：
- 每个文件给出完整内容（不要用占位符省略）。
- 路径为工作区内相对路径，如 "src/app.py"、"main.py"。
- 如适用，请同时给出最小可运行入口。"""

        data = await self._complete_json(prompt, system=self.SYSTEM, default={"files": []},
                                         on_token=on_token)
        files = data.get("files", []) if isinstance(data, dict) else []
        written = _write_files(workspace, files)

        return {
            "files_created": written,
            "files_modified": [],
            "notes": data.get("notes", "") if isinstance(data, dict) else "",
            "run_command": data.get("run_command", "") if isinstance(data, dict) else "",
            "workspace": str(workspace),
        }


class ReviewerAgent(Agent):
    """代码审核员 Agent（真实裁决）"""

    SYSTEM = (
        "你是严格但务实的代码审查员。审查给定代码的正确性、安全性与可维护性。"
        "返回 JSON：{\"verdict\":\"approve|request_changes\",\"summary\":\"总体评价\","
        "\"findings\":[{\"severity\":\"high|medium|low\",\"file\":\"\",\"message\":\"\",\"suggestion\":\"\"}]}。"
        "发现实质问题时必须 request_changes，不要无脑通过。只返回 JSON。"
    )

    def __init__(self, config: Optional[AgentConfig] = None, llm_client: Optional[Any] = None):
        default_config = AgentConfig(
            role="reviewer",
            name="Reviewer Agent",
            description="代码审核员，负责代码审查",
            capabilities=["code_review", "security_analysis"],
            tools=["read_file", "lint_check"],
            temperature=0.2,
        )
        super().__init__(config or default_config, llm_client=llm_client)

    async def execute(self, task: str, **kwargs) -> AgentResult:
        try:
            ctx = self._merge_context(kwargs)
            code = ctx.get("diff") or ctx.get("code") or ""
            if not code:
                code = self._gather_workspace_code(ctx)
            review = await self._review_code(code, ctx.get("spec", {}))
            return AgentResult(success=True, output=review, reasoning=self._last_reasoning, metadata={"role": "reviewer"})
        except Exception as e:
            logger.exception("ReviewerAgent failed")
            return AgentResult(success=False, error=str(e))

    def _gather_workspace_code(self, ctx: Dict[str, Any]) -> str:
        ws = ctx.get("workspace")
        if not ws:
            return ""
        ws = Path(ws)
        if not ws.exists():
            return ""
        chunks = []
        for p in sorted(ws.rglob("*")):
            if p.is_file() and p.suffix in {".py", ".js", ".ts", ".html", ".css", ".go", ".rs", ".java"}:
                try:
                    chunks.append(f"# ===== {p.relative_to(ws)} =====\n{p.read_text(encoding='utf-8')}")
                except Exception:
                    continue
        return "\n\n".join(chunks)[:24000]  # 控制 token

    async def _review_code(self, code: str, spec: Dict[str, Any]) -> Dict[str, Any]:
        if not code.strip():
            return {"verdict": "request_changes", "findings": [],
                    "summary": "没有可供审查的代码"}
        prompt = f"""请审查以下代码：

{code}

按系统提示的 JSON 结构返回裁决。"""
        # 解析失败时不默认通过：保守地要求人工复核
        default = {"verdict": "request_changes", "findings": [],
                   "summary": "审查结果解析失败，需人工复核"}
        return await self._complete_json(prompt, system=self.SYSTEM, default=default)


class TesterAgent(Agent):
    """测试工程师 Agent（真实运行 pytest）"""

    SYSTEM = (
        "你是测试工程师。为给定 Python 代码编写可直接运行的 pytest 测试。"
        "返回 JSON：{\"files\":[{\"path\":\"tests/test_xxx.py\",\"content\":\"完整测试代码\"}]}。"
        "测试需 import 被测模块并覆盖关键路径。只返回 JSON，不要 markdown 围栏。"
    )

    def __init__(self, config: Optional[AgentConfig] = None, llm_client: Optional[Any] = None):
        default_config = AgentConfig(
            role="tester",
            name="Tester Agent",
            description="测试工程师，负责自动测试",
            capabilities=["test_generation", "test_execution"],
            tools=["execute_command", "browser"],
            temperature=0.2,
        )
        super().__init__(config or default_config, llm_client=llm_client)

    async def execute(self, task: str, **kwargs) -> AgentResult:
        try:
            ctx = self._merge_context(kwargs)
            test_result = await self._run_tests(ctx)
            return AgentResult(
                success=bool(test_result.get("passed")),
                output=test_result,
                error=None if test_result.get("passed") else test_result.get("reason"),
                reasoning=self._last_reasoning,
                metadata={"role": "tester"},
            )
        except Exception as e:
            logger.exception("TesterAgent failed")
            return AgentResult(success=False, error=str(e))

    async def _run_tests(self, ctx: Dict[str, Any]) -> Dict[str, Any]:
        ws = ctx.get("workspace")
        if not ws:
            return {"passed": False, "total": 0, "reason": "未提供工作区，无法运行测试"}
        workspace = Path(ws)

        py_files = [p for p in workspace.rglob("*.py")
                    if p.is_file() and not p.name.startswith("test_")]
        if not py_files:
            return {"passed": False, "total": 0, "reason": "工作区无 Python 代码可测"}

        # 若工作区尚无测试，则用 LLM 生成
        has_tests = any(p.name.startswith("test_") or "test" in p.parts for p in workspace.rglob("*.py"))
        generated: List[str] = []
        if not has_tests:
            code = "\n\n".join(
                f"# ===== {p.relative_to(workspace)} =====\n{p.read_text(encoding='utf-8')}"
                for p in py_files
            )[:20000]
            data = await self._complete_json(
                f"为以下代码编写 pytest 测试：\n\n{code}",
                system=self.SYSTEM, default={"files": []},
            )
            files = data.get("files", []) if isinstance(data, dict) else []
            generated = _write_files(workspace, files)
            if not generated:
                return {"passed": False, "total": 0, "reason": "未能生成测试"}

        return await self._pytest(workspace, generated)

    async def _pytest(self, workspace: Path, generated: List[str]) -> Dict[str, Any]:
        """运行 pytest（经统一 runner：Docker 可用则容器内隔离，否则宿主机）并解析结果。"""
        import re as _re
        from ...sandbox.runner import run_pytest

        r = await run_pytest(str(workspace), timeout=120)
        if r.error and not r.stdout:
            return {"passed": False, "total": 0, "reason": r.error,
                    "generated_tests": generated, "runtime": r.runtime}

        output = r.stdout
        return_code = r.exit_code

        def _count(pattern: str) -> int:
            m = _re.search(pattern, output)
            return int(m.group(1)) if m else 0

        passed_count = _count(r"(\d+) passed")
        failed_count = _count(r"(\d+) failed")
        error_count = _count(r"(\d+) error")
        total = passed_count + failed_count + error_count

        return {
            "passed": return_code == 0 and failed_count == 0 and error_count == 0 and total > 0,
            "total": total,
            "passed_count": passed_count,
            "failed_count": failed_count,
            "error_count": error_count,
            "return_code": return_code,
            "generated_tests": generated,
            "runtime": r.runtime,          # docker | host
            "isolated": r.isolated,
            "output": output[-4000:],
        }
