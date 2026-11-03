"""任务上下文（ADR-014）：一个大任务的根，主 agent 在里面协调职责分明的成员。

本模块只管确定性的部分，不调模型、不起执行：

- 根是 ``TaskLedger`` 里 ``kind="team"`` 的任务，小组状态记在 ``task.team``；成员每次派活是一条协作子任务
  （``CollaborationService``，``dispatch.source == "team"``），归属 ``team-<根 id>``。
- 额度记在根上：子任务数、轮数、步数、累计时限、协调人唤醒次数。先落盘预留再执行，预留不退款。
- 协调人每次醒来前后调用 ``begin_wake`` / ``end_wake``：没有新进展或同一错误反复出现就转「等你」，不靠模型自述。
- 协调人不能宣布完成：``submit`` 只把根交到「待你验收」，并要求每条验收标准都给证据。
- 根的状态不用 ``running``：启动恢复扫描会把残留的 ``running`` 改标 ``interrupted``；协调回合被重启打断的
  规则另由 ``recover_after_restart`` 处理（重跑一次，第二次即失败）。
"""
from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

from src.workflow.tasks.collaboration import CollaborationService
from src.workflow.tasks.errors import CollaborationConflict
from src.workflow.tasks.tasks import BackgroundTask, TaskLedger, TASK_STATE_LOCK

KIND = "team"
MODES = ("open", "private")
LIVE = ("active", "waiting", "review")
CLOSED = ("done", "failed", "cancelled")

# 第一个切片的两种成员角色。工具面是封闭枚举；每次派活按角色的单轮合同扣额度。
ROLES: Dict[str, Dict[str, Any]] = {
    "web-researcher": {"title": "研究员", "tools": "web_read", "web": True, "max_steps": 12, "timeout_seconds": 600},
    "reviewer": {"title": "审查员", "tools": "none", "web": False, "max_steps": 2, "timeout_seconds": 300},
}

DEFAULT_LIMITS = {"tasks": 8, "rounds": 12, "steps": 96, "timeout_seconds": 3600, "wakes": 16, "parallel": 2}
MAX_LIMITS = {"tasks": 32, "rounds": 48, "steps": 512, "timeout_seconds": 28800, "wakes": 64, "parallel": 4}
_USAGE_KEYS = ("tasks", "rounds", "steps", "timeout_seconds", "wakes")
STALL_WAKES = 3
_MAX_MEMBERS = 6
_MAX_INBOX = 20
_LOCK = TASK_STATE_LOCK


def _text(value: object, limit: int = 2000) -> str:
    return str(value if value is not None else "").strip()[:limit]


def child_owner(root_id: str) -> str:
    return f"team-{root_id}"


def is_team_child(task: Any) -> bool:
    collaboration = getattr(task, "collaboration", None)
    dispatch = collaboration.get("dispatch") if isinstance(collaboration, dict) else None
    return isinstance(dispatch, dict) and dispatch.get("source") == KIND


def _limits(value: Optional[dict]) -> dict:
    limits = dict(DEFAULT_LIMITS)
    for key, number in (value or {}).items():
        if key not in MAX_LIMITS or type(number) is not int or not 1 <= number <= MAX_LIMITS[key]:
            raise CollaborationConflict(f"额度 {key} 无效（1–{MAX_LIMITS.get(key, '?')}）")
        limits[key] = number
    return limits


def _members(value: Any, mode: str) -> List[dict]:
    if not isinstance(value, list) or not 1 <= len(value) <= _MAX_MEMBERS:
        raise CollaborationConflict(f"成员需要 1–{_MAX_MEMBERS} 个")
    members, names = [], set()
    for item in value:
        if not isinstance(item, dict):
            raise CollaborationConflict("成员格式无效")
        name, role = _text(item.get("name"), 20), str(item.get("role") or "")
        if not name or name in names:
            raise CollaborationConflict("成员名不能为空或重复")
        if role not in ROLES:
            raise CollaborationConflict(f"未知角色：{role}（可用：{'、'.join(ROLES)}）")
        if mode == "private" and ROLES[role]["web"]:
            raise CollaborationConflict("private 任务上下文不能有联网成员：私人资料和出网成员不能放在一起")
        names.add(name)
        members.append({"name": name, "role": role, "model": _text(item.get("model"), 80)})
    return members


class TeamService:
    """任务上下文的领域服务。所有变更在任务锁内先落盘，再返回。"""

    def __init__(self, repo_root: str, *, on_update: Optional[Callable[[BackgroundTask], None]] = None):
        self.repo_root = str(repo_root)
        self.ledger = TaskLedger(repo_root)
        self._on_update = on_update

    # ── 读取 ──────────────────────────────────────────────────────────
    def get(self, root_id: str) -> BackgroundTask:
        task = self.ledger.load(root_id)
        if task is None or task.kind != KIND or not isinstance(task.team, dict) or task.team.get("version") != 1:
            raise CollaborationConflict("任务上下文不存在")
        return task

    def list(self, owner: str = "") -> List[BackgroundTask]:
        return [task for task in self.ledger.list() if task.kind == KIND
                and (not owner or task.owner_session == owner)]

    def children(self, root: BackgroundTask) -> List[BackgroundTask]:
        out = []
        for child_id in root.team.get("children", []):
            child = self.ledger.load(child_id)
            if child is not None:
                out.append(child)
        return out

    def collaboration(self, root: BackgroundTask) -> CollaborationService:
        return CollaborationService(self.repo_root, child_owner(root.id))

    def _save(self, root: BackgroundTask) -> BackgroundTask:
        if not self.ledger.save(root):
            raise OSError("任务上下文未能保存；未执行")
        if self._on_update is not None:
            try:
                self._on_update(root)
            except Exception:  # noqa: BLE001 —— 通知是旁路，不影响已落盘的状态
                pass
        return root

    def _live(self, root_id: str, *, statuses=("active",)) -> BackgroundTask:
        root = self.get(root_id)
        if root.status not in statuses:
            raise CollaborationConflict(f"任务上下文当前是 {root.status}，不能这样操作")
        return root

    @staticmethod
    def _event(root: BackgroundTask, kind: str, text: str = "") -> None:
        root.team["events"] = int(root.team.get("events", 0)) + 1
        log = root.team.setdefault("log", [])
        log.append({"kind": kind, "text": _text(text, 300), "seq": root.team["events"]})
        root.team["log"] = log[-50:]

    # ── 建根（主人批准之后由调用方调用） ────────────────────────────────
    def create(self, owner: str, *, objective: str, acceptance: List[str], members: List[dict],
               mode: str = "open", limits: Optional[dict] = None, private_refs: Optional[List[str]] = None,
               tid: Optional[str] = None) -> BackgroundTask:
        if not owner:
            raise CollaborationConflict("任务上下文必须属于一个会话")
        objective = _text(objective, 2000)
        if not objective:
            raise CollaborationConflict("任务目标不能为空")
        if mode not in MODES:
            raise CollaborationConflict("开放档位只能是 open 或 private")
        if (not isinstance(acceptance, list) or not 1 <= len(acceptance) <= 10
                or any(not isinstance(item, str) or not item.strip() for item in acceptance)):
            raise CollaborationConflict("验收标准需要 1–10 条非空文字")
        refs = [str(ref) for ref in (private_refs or [])][:10]
        if refs and mode != "private":
            raise CollaborationConflict("只有 private 任务上下文可以带私人资料")
        roster = _members(members, mode)
        with _LOCK:
            root = BackgroundTask.new(KIND, objective, tid=tid, owner_session=owner)
            if self.ledger.load(root.id) is not None:
                raise CollaborationConflict("任务上下文 ID 已存在")
            root.status = "active"
            root.team = {
                "version": 1, "mode": mode, "objective": objective,
                "acceptance": [_text(item, 300) for item in acceptance],
                "members": roster, "private_refs": refs,
                "limits": _limits(limits), "used": {key: 0 for key in _USAGE_KEYS},
                "children": [], "coordinator": {"kind": "main_agent", "model": ""},
                "events": 0, "log": [], "inbox": [], "waiting_reason": "", "submission": None, "tainted": False,
                "wake": {"open": False, "cuts": 0, "events_at_start": 0, "idle": 0, "last_error": "", "same_error": 0},
            }
            self._event(root, "created", objective)
            return self._save(root)

    # ── 额度 ──────────────────────────────────────────────────────────
    @staticmethod
    def _charge(root: BackgroundTask, cost: Dict[str, int]) -> None:
        used, limits = root.team["used"], root.team["limits"]
        over = [key for key, value in cost.items() if used[key] + value > limits[key]]
        if over:
            raise CollaborationConflict(f"任务上下文额度不足（{'、'.join(over)}）；需要主人加预算")
        for key, value in cost.items():
            used[key] += value

    @staticmethod
    def _member(root: BackgroundTask, name: str) -> dict:
        member = next((item for item in root.team["members"] if item["name"] == name), None)
        if member is None:
            raise CollaborationConflict(f"没有名为 {name} 的成员")
        return member

    # ── 协调人的动作 ───────────────────────────────────────────────────
    def assign(self, root_id: str, member: str, instruction: str, expected: str) -> BackgroundTask:
        """给成员派一件事：预留额度并落盘根，再建子任务。子任务建失败时预留不退。"""
        if not instruction.strip() or not expected.strip():
            raise CollaborationConflict("派活要写清做什么和交回什么")
        with _LOCK:
            root = self._live(root_id)
            entry = self._member(root, member)
            role = ROLES[entry["role"]]
            self._charge(root, {"tasks": 1, "rounds": 1, "steps": role["max_steps"],
                                "timeout_seconds": role["timeout_seconds"]})
            self._save(root)
            child = self.collaboration(root).create(
                _text(instruction, 4000), member, [_text(expected, 500)],
                dispatch={"source": KIND, "root": root.id, "member": member, "role": entry["role"],
                          "model": entry["model"], "max_steps": role["max_steps"],
                          "timeout_seconds": role["timeout_seconds"]})
            root = self.get(root_id)
            root.team["children"].append(child.id)
            self._event(root, "assigned", f"{member}：{instruction}")
            self._save(root)
            return child

    def review(self, root_id: str, child_id: str, verdict: str, note: str) -> BackgroundTask:
        """接受或返工一条成员结果；返工预留下一轮额度。"""
        with _LOCK:
            root = self._live(root_id)
            if child_id not in root.team["children"]:
                raise CollaborationConflict("这条子任务不属于本任务上下文")
            service = self.collaboration(root)
            child = service.get(child_id)
            number = child.collaboration["round"]
            if verdict == "rework":
                if child.status == "done":
                    service.review(child_id, number, "rework", note)
                role = ROLES[self._member(root, child.collaboration["assignee"])["role"]]
                self._charge(root, {"rounds": 1, "steps": role["max_steps"], "timeout_seconds": role["timeout_seconds"]})
                self._save(root)
                child = service.followup(child_id, number, note)
            elif verdict == "accept":
                child = service.review(child_id, number, "accept", note)
            else:
                raise CollaborationConflict("结论只能是 accept 或 rework")
            root = self.get(root_id)
            root.team["tainted"] = bool(root.team.get("tainted")) or bool(child.collaboration.get("tainted"))
            self._event(root, verdict, f"{child_id}：{note}")
            self._save(root)
            return child

    def ask_owner(self, root_id: str, question: str) -> BackgroundTask:
        question = _text(question, 500)
        if not question:
            raise CollaborationConflict("问题不能为空")
        with _LOCK:
            root = self._live(root_id)
            root.status, root.team["waiting_reason"] = "waiting", question
            self._event(root, "ask_owner", question)
            return self._save(root)

    def submit(self, root_id: str, summary: str, evidence: List[dict]) -> BackgroundTask:
        """交主人验收：每条验收标准都要有证据。不能自己宣布完成。"""
        with _LOCK:
            root = self._live(root_id)
            criteria = root.team["acceptance"]
            by_index: Dict[int, str] = {}
            for item in evidence if isinstance(evidence, list) else []:
                index = item.get("criterion") if isinstance(item, dict) else None
                text = _text(item.get("evidence"), 1000) if isinstance(item, dict) else ""
                if type(index) is int and 0 <= index < len(criteria) and text:
                    by_index[index] = text
            missing = [criteria[i] for i in range(len(criteria)) if i not in by_index]
            if missing:
                raise CollaborationConflict(f"这些验收标准还没有证据：{'；'.join(missing)}")
            if not summary.strip():
                raise CollaborationConflict("交付需要一段总结")
            root.status = "review"
            root.team["submission"] = {"summary": _text(summary, 4000),
                                       "evidence": [{"criterion": i, "text": criteria[i], "evidence": by_index[i]}
                                                    for i in sorted(by_index)],
                                       "tainted": bool(root.team.get("tainted")) or any(
                                           child.collaboration.get("tainted") for child in self.children(root))}
            self._event(root, "submitted", summary)
            return self._save(root)

    def mark_tainted(self, root_id: str) -> BackgroundTask:
        """协调回合读过外部内容（成员结果带污点）：根从此带污点，主对话里不转述它写的文字。"""
        with _LOCK:
            root = self.get(root_id)
            if not root.team.get("tainted"):
                root.team["tainted"] = True
                self._save(root)
            return root

    # ── 协调人醒来 ─────────────────────────────────────────────────────
    def begin_wake(self, root_id: str) -> BackgroundTask:
        """协调人每次醒来前调用：扣一次唤醒额度并标记回合进行中。额度用完转「等你」。"""
        with _LOCK:
            root = self._live(root_id)
            try:
                self._charge(root, {"wakes": 1})
            except CollaborationConflict:
                root.status, root.team["waiting_reason"] = "waiting", "协调人唤醒次数用完；要继续需要加预算"
                self._event(root, "budget", root.team["waiting_reason"])
                self._save(root)
                raise
            wake = root.team["wake"]
            wake.update(open=True, events_at_start=root.team["events"])
            return self._save(root)

    def end_wake(self, root_id: str, *, error: str = "") -> BackgroundTask:
        """协调回合结束：按记录里的事实判断有没有进展，连续没进展或同一错误反复出现就转「等你」。"""
        with _LOCK:
            root = self.get(root_id)
            wake = root.team["wake"]
            progressed = root.team["events"] > wake["events_at_start"]
            wake["open"], wake["cuts"] = False, 0
            wake["idle"] = 0 if progressed else wake["idle"] + 1
            error = _text(error, 300)
            if error and error == wake["last_error"]:
                wake["same_error"] += 1
            else:
                wake["same_error"] = 1 if error else 0
            wake["last_error"] = error
            if root.status == "active" and (wake["idle"] >= STALL_WAKES or wake["same_error"] >= STALL_WAKES):
                reason = (f"连续 {STALL_WAKES} 次出现同一个错误：{error}" if wake["same_error"] >= STALL_WAKES
                          else f"协调人连续 {STALL_WAKES} 次醒来都没有新进展")
                root.status, root.team["waiting_reason"] = "waiting", reason
                self._event(root, "stalled", reason)
            return self._save(root)

    def recover_after_restart(self, root_id: str) -> str:
        """启动时对进行中的根调用。返回 ``rerun``（被打断一次，可重跑）/ ``failed`` / ``idle``。"""
        with _LOCK:
            root = self.get(root_id)
            wake = root.team["wake"]
            if root.status not in LIVE or not wake["open"]:
                return "idle"
            wake["cuts"] += 1
            if wake["cuts"] >= 2:
                root.status, root.error = "failed", "协调回合连续两次被重启打断；可能已有部分动作生效，请核对后决定是否重开"
                wake["open"] = False
                self._event(root, "failed", root.error)
                self._save(root)
                return "failed"
            self._save(root)
            return "rerun"

    # ── 主人的动作 ─────────────────────────────────────────────────────
    def owner_reply(self, root_id: str, text: str) -> BackgroundTask:
        text = _text(text, 2000)
        if not text:
            raise CollaborationConflict("回复不能为空")
        with _LOCK:
            root = self._live(root_id, statuses=("active", "waiting"))
            root.team["inbox"] = (root.team["inbox"] + [{"from": "owner", "text": text}])[-_MAX_INBOX:]
            root.status, root.team["waiting_reason"] = "active", ""
            root.team["wake"].update(idle=0, same_error=0, last_error="")
            self._event(root, "owner_reply", text)
            return self._save(root)

    def take_inbox(self, root_id: str) -> List[dict]:
        """协调人读走主人的留言（读一次就清）。"""
        with _LOCK:
            root = self.get(root_id)
            items, root.team["inbox"] = list(root.team["inbox"]), []
            self._save(root)
            return items

    def add_budget(self, root_id: str, extra: Dict[str, int]) -> BackgroundTask:
        """主人批准加预算后调用：上调额度并恢复进行中。"""
        with _LOCK:
            root = self._live(root_id, statuses=("active", "waiting"))
            limits = dict(root.team["limits"])
            for key, number in extra.items():
                if key not in _USAGE_KEYS or type(number) is not int or number < 1:
                    raise CollaborationConflict(f"加预算项无效：{key}")
                limits[key] = min(MAX_LIMITS[key], limits[key] + number)
            root.team["limits"] = limits
            root.status, root.team["waiting_reason"] = "active", ""
            root.team["wake"].update(idle=0, same_error=0, last_error="")
            self._event(root, "budget_added", ", ".join(f"{k}+{v}" for k, v in extra.items()))
            return self._save(root)

    def decide(self, root_id: str, accept: bool, note: str = "") -> BackgroundTask:
        """主人验收：通过即完成；退回则带着意见回到进行中。"""
        with _LOCK:
            root = self._live(root_id, statuses=("review",))
            if accept:
                root.status = "done"
                self._event(root, "accepted", note)
            else:
                if not note.strip():
                    raise CollaborationConflict("退回要写明原因")
                root.status, root.team["submission"] = "active", None
                root.team["inbox"] = (root.team["inbox"] + [{"from": "owner", "text": _text(note, 2000)}])[-_MAX_INBOX:]
                root.team["wake"].update(idle=0, same_error=0, last_error="")
                self._event(root, "returned", note)
            return self._save(root)

    def stop(self, root_id: str, reason: str = "主人停止") -> tuple[BackgroundTask, List[str]]:
        """停止整个任务上下文；返回仍在排队或执行的子任务，调用方负责请求取消。"""
        with _LOCK:
            root = self._live(root_id, statuses=LIVE)
            root.status, root.error = "cancelled", _text(reason, 300)
            root.team["wake"]["open"] = False
            self._event(root, "stopped", reason)
            self._save(root)
            pending = [child.id for child in self.children(root) if child.status in {"queued", "running", "blocked"}]
            return root, pending

    # ── 给执行器与展示用 ────────────────────────────────────────────────
    def runnable(self, root: BackgroundTask) -> List[BackgroundTask]:
        """可以开始执行的子任务：根在进行中，且同时在跑的不超过 parallel。"""
        if root.status != "active":
            return []
        children = self.children(root)
        running = sum(1 for child in children if child.status == "running")
        slots = max(0, root.team["limits"]["parallel"] - running)
        return [child for child in children if child.status == "queued"][:slots]

    def view(self, root: BackgroundTask) -> Dict[str, Any]:
        """由代码拼的结构化视图：主对话与 work_status 用它，不读协调人写的文字。"""
        children = self.children(root)
        return {"id": root.id, "objective": root.team["objective"], "status": root.status, "mode": root.team["mode"],
                "waiting_reason": root.team["waiting_reason"], "used": dict(root.team["used"]),
                "limits": dict(root.team["limits"]),
                "members": [{"name": item["name"], "role": item["role"]} for item in root.team["members"]],
                "children": [{"id": child.id, "member": child.collaboration.get("assignee"), "status": child.status,
                              "review": child.collaboration.get("review"), "round": child.collaboration.get("round"),
                              "tainted": bool(child.collaboration.get("tainted"))} for child in children],
                "tainted": bool(root.team.get("tainted")) or any(child.collaboration.get("tainted") for child in children)}
