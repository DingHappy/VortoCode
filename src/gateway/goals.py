"""First-class development goals with an evidence-based completion gate.

A background task finishing only proves that an execution attempt returned.  A
goal is stricter: every acceptance criterion must have explicit passing evidence
before the goal can become ``achieved``.  Goal state is persisted under
``.vortocode/goals`` and links to the existing task/dev-plan runtime rather than
introducing a second autonomous executor.
"""
from __future__ import annotations

import json
import re
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

_DIRNAME = "goals"
_BAD = re.compile(r"[^A-Za-z0-9_-]")
_GOAL_STATUSES = {"draft", "active", "blocked", "achieved", "failed"}
_CRITERION_STATUSES = {"pending", "passed", "failed"}
_VERIFIER_KINDS = {"test", "build", "lint", "file"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _clean_id(value: str) -> Optional[str]:
    if not value:
        return None
    cleaned = _BAD.sub("_", str(value)).strip("_")
    return cleaned or None


@dataclass
class CriterionVerifier:
    kind: str
    command: str = ""
    path: str = ""
    contains: str = ""
    timeout: int = 300


@dataclass
class AcceptanceCriterion:
    id: str
    text: str
    status: str = "pending"
    evidence_ids: List[str] = field(default_factory=list)
    verifier: Optional[CriterionVerifier] = None


@dataclass
class GoalEvidence:
    id: str
    criterion_id: str
    kind: str
    summary: str
    passed: bool
    source: str = "manual"
    created: str = ""


@dataclass
class Goal:
    id: str
    objective: str
    acceptance_criteria: List[AcceptanceCriterion] = field(default_factory=list)
    constraints: List[str] = field(default_factory=list)
    non_goals: List[str] = field(default_factory=list)
    status: str = "draft"
    plan_id: str = ""
    task_ids: List[str] = field(default_factory=list)
    branch: str = ""
    evidence: List[GoalEvidence] = field(default_factory=list)
    blocker: str = ""
    next_action: str = "确认目标并开始执行"
    created: str = ""
    updated: str = ""

    @staticmethod
    def new(
        objective: str,
        acceptance_criteria: List[str],
        *,
        constraints: Optional[List[str]] = None,
        non_goals: Optional[List[str]] = None,
        goal_id: Optional[str] = None,
    ) -> "Goal":
        now = _now()
        criteria = [
            AcceptanceCriterion(id=f"criterion-{index + 1}", text=str(text).strip())
            for index, text in enumerate(acceptance_criteria)
            if str(text).strip()
        ]
        return Goal(
            id=goal_id or ("goal-" + uuid.uuid4().hex[:10]),
            objective=str(objective).strip(),
            acceptance_criteria=criteria,
            constraints=[str(item).strip() for item in (constraints or []) if str(item).strip()],
            non_goals=[str(item).strip() for item in (non_goals or []) if str(item).strip()],
            created=now,
            updated=now,
        )

    def criterion(self, criterion_id: str) -> Optional[AcceptanceCriterion]:
        return next((item for item in self.acceptance_criteria if item.id == criterion_id), None)

    def contract_prompt(self) -> str:
        """Render a stable execution contract for ``dev_auto``/``dev_resume`` workers."""
        lines = ["请按下面的目标合同完成开发。", "", f"目标：{self.objective}", "", "验收标准："]
        lines.extend(f"- [{item.id}] {item.text}" for item in self.acceptance_criteria)
        verifier_lines = []
        for item in self.acceptance_criteria:
            verifier = item.verifier
            if verifier is None:
                continue
            detail = verifier.path if verifier.kind == "file" else verifier.command
            verifier_lines.append(f"- [{item.id}] {verifier.kind}: {detail}")
        if verifier_lines:
            lines.extend(["", "确定性验收器（实现后必须运行）：", *verifier_lines])
        if self.constraints:
            lines.extend(["", "约束：", *[f"- {item}" for item in self.constraints]])
        if self.non_goals:
            lines.extend(["", "非目标：", *[f"- {item}" for item in self.non_goals]])
        failed = [item for item in self.acceptance_criteria if item.status == "failed"]
        if failed:
            lines.extend(["", "上次未通过的标准：", *[f"- {item.text}" for item in failed]])
        if self.blocker:
            lines.extend(["", f"当前阻塞：{self.blocker}"])
        lines.extend([
            "",
            "完成实现与自动验证后如实返回结果；任务执行结束不等于目标验收通过，",
            "目标必须在每条验收标准都有明确证据后才能标记 achieved。",
        ])
        return "\n".join(lines)

    def add_evidence(
        self,
        *,
        criterion_id: str = "",
        kind: str,
        summary: str,
        passed: bool,
        source: str = "manual",
    ) -> GoalEvidence:
        criterion = self.criterion(criterion_id) if criterion_id else None
        if criterion_id and criterion is None:
            raise ValueError(f"未知验收标准 {criterion_id}")
        clean_summary = str(summary).strip()
        if not clean_summary:
            raise ValueError("证据摘要不能为空")
        clean_kind = str(kind or "manual").strip() or "manual"
        clean_source = str(source or "manual").strip() or "manual"
        existing = next((
            item for item in self.evidence
            if item.criterion_id == criterion_id
            and item.kind == clean_kind
            and item.summary == clean_summary
            and item.passed is bool(passed)
            and item.source == clean_source
        ), None)
        evidence = existing or GoalEvidence(
            id="evidence-" + uuid.uuid4().hex[:10],
            criterion_id=criterion_id,
            kind=clean_kind,
            summary=clean_summary,
            passed=bool(passed),
            source=clean_source,
            created=_now(),
        )
        if existing is None:
            self.evidence.append(evidence)
        if criterion is not None:
            if evidence.id not in criterion.evidence_ids:
                criterion.evidence_ids.append(evidence.id)
            criterion.status = "passed" if passed else "failed"
        return evidence

    def evaluate(self, *, default_active: bool = True) -> str:
        """Apply the completion gate and return the resulting status."""
        if self.acceptance_criteria and all(
            item.status == "passed" and item.evidence_ids for item in self.acceptance_criteria
        ):
            self.status = "achieved"
            self.blocker = ""
            self.next_action = "目标已通过全部验收标准"
            return self.status
        failed = [item for item in self.acceptance_criteria if item.status == "failed"]
        if failed:
            self.status = "blocked"
            if not self.blocker:
                self.blocker = "验收未通过：" + "；".join(item.text for item in failed[:3])
            self.next_action = "根据失败证据修正实现，然后重新执行并验收"
            return self.status
        if default_active and self.status != "draft":
            self.status = "active"
            self.blocker = ""
            pending = sum(1 for item in self.acceptance_criteria if item.status != "passed")
            self.next_action = f"为剩余 {pending} 条验收标准补充通过证据"
        return self.status

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @staticmethod
    def from_dict(data: Dict[str, Any]) -> "Goal":
        criterion_fields = set(AcceptanceCriterion.__dataclass_fields__)
        verifier_fields = set(CriterionVerifier.__dataclass_fields__)
        evidence_fields = set(GoalEvidence.__dataclass_fields__)
        criteria = []
        for item in (data.get("acceptance_criteria") or []):
            if not isinstance(item, dict):
                continue
            values = {key: value for key, value in item.items() if key in criterion_fields}
            verifier = values.get("verifier")
            if isinstance(verifier, dict):
                values["verifier"] = CriterionVerifier(**{
                    key: value for key, value in verifier.items() if key in verifier_fields
                })
            elif verifier is not None:
                values["verifier"] = None
            criteria.append(AcceptanceCriterion(**values))
        evidence = [
            GoalEvidence(**{key: value for key, value in item.items() if key in evidence_fields})
            for item in (data.get("evidence") or [])
            if isinstance(item, dict)
        ]
        known = {name for name in Goal.__dataclass_fields__ if name not in {"acceptance_criteria", "evidence"}}
        kwargs = {key: value for key, value in data.items() if key in known}
        goal = Goal(acceptance_criteria=criteria, evidence=evidence, **kwargs)
        if goal.status not in _GOAL_STATUSES:
            goal.status = "draft"
        for criterion in goal.acceptance_criteria:
            if criterion.status not in _CRITERION_STATUSES:
                criterion.status = "pending"
        return goal


class GoalLedger:
    """Atomic repository-local persistence for goal contracts and evidence."""

    def __init__(self, repo_root: str):
        self.repo_root = str(repo_root)

    def _dir(self) -> Path:
        return Path(self.repo_root) / ".vortocode" / _DIRNAME

    def _path(self, goal_id: str) -> Path:
        return self._dir() / f"{goal_id}.json"

    def create(
        self,
        objective: str,
        acceptance_criteria: List[str],
        *,
        constraints: Optional[List[str]] = None,
        non_goals: Optional[List[str]] = None,
    ) -> Goal:
        goal = Goal.new(
            objective,
            acceptance_criteria,
            constraints=constraints,
            non_goals=non_goals,
        )
        self.save(goal)
        return goal

    def save(self, goal: Goal) -> bool:
        goal_id = _clean_id(goal.id)
        if goal_id is None:
            return False
        goal.id = goal_id
        goal.updated = _now()
        path = self._path(goal_id)
        try:
            from src.agents.dev_plan import ensure_state_gitignore

            ensure_state_gitignore(self.repo_root)
            path.parent.mkdir(parents=True, exist_ok=True)
            temp = path.with_suffix(".json.tmp")
            temp.write_text(json.dumps(goal.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
            temp.replace(path)
            return True
        except (OSError, TypeError, ValueError):
            return False

    def load(self, goal_id: str) -> Optional[Goal]:
        clean = _clean_id(goal_id)
        if clean is None:
            return None
        path = self._path(clean)
        if not path.is_file():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return Goal.from_dict(data) if isinstance(data, dict) else None
        except (OSError, TypeError, ValueError):
            return None

    def list(self) -> List[Goal]:
        directory = self._dir()
        if not directory.is_dir():
            return []
        goals: List[tuple[float, Goal]] = []
        for path in directory.glob("*.json"):
            goal = self.load(path.stem)
            if goal is None:
                continue
            try:
                goals.append((path.stat().st_mtime, goal))
            except OSError:
                goals.append((0.0, goal))
        goals.sort(key=lambda item: item[0], reverse=True)
        return [goal for _mtime, goal in goals]

    def update_contract(
        self,
        goal_id: str,
        *,
        objective: str,
        acceptance_criteria: List[str],
        constraints: Optional[List[str]] = None,
        non_goals: Optional[List[str]] = None,
    ) -> Goal:
        """Edit an unstarted draft while preserving its identity and audit timestamps."""
        goal = self.load(goal_id)
        if goal is None:
            raise KeyError(goal_id)
        if goal.status != "draft" or goal.task_ids or goal.evidence:
            raise ValueError("只有尚未执行、没有证据的 draft 目标可以修改")
        objective = str(objective).strip()
        criteria = [str(item).strip() for item in acceptance_criteria if str(item).strip()]
        if not objective:
            raise ValueError("目标不能为空")
        if not criteria:
            raise ValueError("至少需要一条验收标准")
        goal.objective = objective
        goal.acceptance_criteria = [
            AcceptanceCriterion(id=f"criterion-{index + 1}", text=text)
            for index, text in enumerate(criteria)
        ]
        goal.constraints = [str(item).strip() for item in (constraints or []) if str(item).strip()]
        goal.non_goals = [str(item).strip() for item in (non_goals or []) if str(item).strip()]
        goal.blocker = ""
        goal.next_action = "确认目标合同并开始执行"
        self.save(goal)
        return goal

    def delete_draft(self, goal_id: str) -> bool:
        """Delete only a pristine draft; started/evidenced goals remain as an audit trail."""
        goal = self.load(goal_id)
        if goal is None:
            raise KeyError(goal_id)
        if goal.status != "draft" or goal.task_ids or goal.evidence:
            raise ValueError("只有尚未执行、没有证据的 draft 目标可以删除")
        clean = _clean_id(goal_id)
        if clean is None:
            return False
        try:
            self._path(clean).unlink()
            return True
        except OSError:
            return False

    def set_verifier(
        self,
        goal_id: str,
        criterion_id: str,
        verifier: Optional[Dict[str, Any]],
    ) -> Goal:
        """Configure a deterministic verifier while the goal contract is still a pristine draft."""
        goal = self.load(goal_id)
        if goal is None:
            raise KeyError(goal_id)
        if goal.status != "draft" or goal.task_ids or goal.evidence:
            raise ValueError("只有尚未执行、没有证据的 draft 目标可以修改验收器")
        criterion = goal.criterion(criterion_id)
        if criterion is None:
            raise ValueError(f"未知验收标准 {criterion_id}")
        if verifier is None or str(verifier.get("kind") or "manual").strip() == "manual":
            criterion.verifier = None
            self.save(goal)
            return goal
        kind = str(verifier.get("kind") or "").strip().lower()
        if kind not in _VERIFIER_KINDS:
            raise ValueError("验收器只支持 test / build / lint / file / manual")
        try:
            timeout = int(verifier.get("timeout") or 300)
        except (TypeError, ValueError):
            raise ValueError("验收器 timeout 必须是 1–900 秒") from None
        if not 1 <= timeout <= 900:
            raise ValueError("验收器 timeout 必须是 1–900 秒")
        command = str(verifier.get("command") or "").strip()
        path = str(verifier.get("path") or "").strip()
        contains = str(verifier.get("contains") or "")
        if kind == "file":
            if not path:
                raise ValueError("file 验收器需要仓库内相对路径")
            if len(path) > 500 or "\x00" in path or "\n" in path:
                raise ValueError("file 验收器路径无效")
        elif not command:
            raise ValueError(f"{kind} 验收器需要 command")
        if len(command) > 4_000 or len(contains) > 2_000:
            raise ValueError("验收器内容超过长度上限")
        criterion.verifier = CriterionVerifier(
            kind=kind,
            command=command,
            path=path,
            contains=contains,
            timeout=timeout,
        )
        self.save(goal)
        return goal

    def record_evidence(
        self,
        goal_id: str,
        criterion_id: str,
        *,
        kind: str,
        summary: str,
        passed: bool,
        source: str = "manual",
    ) -> Goal:
        goal = self.load(goal_id)
        if goal is None:
            raise KeyError(goal_id)
        goal.add_evidence(
            criterion_id=criterion_id,
            kind=kind,
            summary=summary,
            passed=passed,
            source=source,
        )
        goal.evaluate(default_active=True)
        self.save(goal)
        return goal


def evaluate_file_verifier(repo_root: str, verifier: CriterionVerifier) -> tuple[bool, str]:
    """Evaluate a repository-contained file existence/content verifier."""
    root = Path(repo_root).resolve()
    raw = Path(str(verifier.path or ""))
    if raw.is_absolute() or ".." in raw.parts:
        return False, f"文件验收路径越界：{verifier.path}"
    candidate = root / raw
    try:
        resolved = candidate.resolve()
        resolved.relative_to(root)
    except (OSError, ValueError):
        return False, f"文件验收路径越界或不可解析：{verifier.path}"
    if not resolved.is_file():
        return False, f"文件不存在：{verifier.path}"
    if verifier.contains:
        try:
            if resolved.stat().st_size > 1024 * 1024:
                return False, f"文件超过内容验收上限 1 MiB：{verifier.path}"
            content = resolved.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as error:
            return False, f"文件无法按 UTF-8 读取：{verifier.path}（{error}）"
        if verifier.contains not in content:
            return False, f"文件 {verifier.path} 不包含期望文本：{verifier.contains[:120]}"
        return True, f"文件 {verifier.path} 存在并包含期望文本"
    return True, f"文件存在：{verifier.path}"


def sync_goal_from_task(repo_root: str, task: Any) -> Optional[Goal]:
    """Project a linked task's lifecycle into its goal without bypassing acceptance."""
    goal_id = str(getattr(task, "goal_id", "") or "")
    if not goal_id:
        return None
    ledger = GoalLedger(repo_root)
    goal = ledger.load(goal_id)
    if goal is None:
        return None
    changed = False
    task_id = str(getattr(task, "id", "") or "")
    if task_id and task_id not in goal.task_ids:
        goal.task_ids.append(task_id)
        changed = True
    for attr in ("plan_id", "branch"):
        value = str(getattr(task, attr, "") or "")
        if value and getattr(goal, attr) != value:
            setattr(goal, attr, value)
            changed = True

    status = str(getattr(task, "status", "") or "")
    if status in {"queued", "running"} and goal.status not in {"active", "achieved"}:
        goal.status = "active"
        goal.blocker = ""
        goal.next_action = "后台开发任务正在执行"
        changed = True
    elif status in {"failed", "cancelled", "interrupted", "paused"}:
        detail = str(getattr(task, "error", "") or status)
        source = f"task:{task_id}:terminal"
        before = len(goal.evidence)
        goal.add_evidence(kind="execution", summary=detail, passed=False, source=source)
        changed = changed or len(goal.evidence) != before or goal.status != "blocked"
        goal.status = "blocked"
        goal.blocker = f"后台任务{status}：{detail}"
        goal.next_action = "修复阻塞后重新执行；已有计划可选择断点续跑"
    elif status == "done":
        plan = None
        plan_id = str(getattr(task, "plan_id", "") or "")
        if plan_id:
            try:
                from src.agents.dev_plan import load_plan

                plan = load_plan(repo_root, plan_id)
            except Exception:  # noqa: BLE001
                plan = None
        blocked = False
        if plan is not None and plan.status in {"failed", "integration_failed"}:
            summary = f"开发计划 {plan_id} 状态为 {plan.status}"
            before = len(goal.evidence)
            goal.add_evidence(
                kind="execution",
                summary=summary,
                passed=False,
                source=f"plan:{plan_id}:status",
            )
            changed = changed or len(goal.evidence) != before
            blocked = True
        if plan is not None and plan.integration is not None:
            integration_ok = bool(plan.integration.get("ok"))
            command = str(plan.integration.get("cmd") or "集成验证")
            summary = f"{command}：{'通过' if integration_ok else '未通过'}"
            before = len(goal.evidence)
            goal.add_evidence(
                kind="test",
                summary=summary,
                passed=integration_ok,
                source=f"plan:{plan_id}:integration",
            )
            changed = changed or len(goal.evidence) != before
            blocked = not integration_ok
        if plan is not None and plan.review is not None:
            review_ok = not bool(plan.review.get("blocked"))
            note = str(plan.review.get("note") or "开发审查")[-1000:]
            before = len(goal.evidence)
            goal.add_evidence(
                kind="review",
                summary=note,
                passed=review_ok,
                source=f"plan:{plan_id}:review",
            )
            changed = changed or len(goal.evidence) != before
            blocked = blocked or not review_ok
        if blocked:
            goal.status = "blocked"
            goal.blocker = "自动集成验证或开发审查未通过"
            goal.next_action = "修复失败项并重新执行目标"
        else:
            previous = (goal.status, goal.blocker, goal.next_action)
            if goal.status != "achieved":
                goal.status = "active"
            goal.evaluate(default_active=True)
            changed = changed or previous != (goal.status, goal.blocker, goal.next_action)

    if changed:
        ledger.save(goal)
    return goal
