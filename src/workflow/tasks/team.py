"""任务上下文（ADR-014）：一个大任务的根，主 agent 在里面协调职责分明的成员。

本模块只管确定性的部分，不调模型、不起执行：

- 根是 ``TaskLedger`` 里 ``kind="team"`` 的任务，小组状态记在 ``task.team``；成员每次派活是一条协作子任务
  （``CollaborationService``，``dispatch.source == "team"``），归属 ``team-<根 id>``。
- 不设额度（主人 2026-11-04 定）：用量只计数给主人看，不拦。防空转只靠无进展判定；每轮成员各有执行时限。
- 协调人每次醒来前后调用 ``begin_wake`` / ``end_wake``：没有新进展或同一错误反复出现就转「等你」，不靠模型自述。
- 协调人不能宣布完成：``submit`` 只把根交到「待你验收」，并要求每条验收标准都给证据。
- 根的状态不用 ``running``：启动恢复扫描会把残留的 ``running`` 改标 ``interrupted``；协调回合被重启打断的
  规则另由 ``recover_after_restart`` 处理（重跑一次，第二次即失败）。
"""
from __future__ import annotations

import uuid
from typing import Any, Callable, Dict, List, Optional

from src.workflow.tasks.collaboration import CollaborationService
from src.workflow.tasks.errors import CollaborationConflict
from src.workflow.tasks.tasks import BackgroundTask, TaskLedger, TASK_STATE_LOCK
from src.utils.state_dir import state_dir

KIND = "team"
MODES = ("open", "private")
LIVE = ("active", "waiting", "review")
CLOSED = ("done", "failed", "cancelled")

# Legacy teams retain their three faces, sourced from the canonical staff records.
from src.workflow.staff.roles import builtin_team_roles
ROLES: Dict[str, Dict[str, Any]] = builtin_team_roles()

# 成员的执行框架（ADR-014 S2）。vortocode = 进程内自有 agent；其余经 ACP 起外部框架，权限按角色逐次判定。
ENGINES: Dict[str, str] = {"vortocode": "VortoOne", "opencode": "OpenCode", "deepseek-harness": "DeepSeek Harness"}
# 还不能按成员换模型的框架（DeepSeek Harness 用它 ACP 档案里的默认模型）
_ENGINES_WITHOUT_MODEL = ("deepseek-harness",)

# 只剩并发：同时在跑的成员数（排队，不拒绝）。子任务数、轮数、时长、唤醒次数都不设上限，只计数。
DEFAULT_LIMITS = {"parallel": 2}
MAX_LIMITS = {"parallel": 4}
_USAGE_KEYS = ("tasks", "rounds", "steps", "timeout_seconds", "wakes")
STALL_WAKES = 3
_MAX_MEMBERS = 6
_MAX_INBOX = 20
_MAX_LOG = 200                    # 群聊记录要看得到整个过程（2026-11-04 起；之前只留 50 条）
_MAX_INSTRUCTION = 16000          # 交叉审查开发成员的改动时，要求里带着 diff
_LOCK = TASK_STATE_LOCK


def _now() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _text(value: object, limit: int = 2000) -> str:
    return str(value if value is not None else "").strip()[:limit]


def child_owner(root_id: str) -> str:
    return f"team-{root_id}"


def is_team_child(task: Any) -> bool:
    collaboration = getattr(task, "collaboration", None)
    dispatch = collaboration.get("dispatch") if isinstance(collaboration, dict) else None
    return isinstance(dispatch, dict) and dispatch.get("source") == KIND


def normalize_limits(value: Optional[dict]) -> dict:
    """只认并发 ``parallel``；旧版的额度键（tasks、rounds 等）不再生效，忽略。"""
    limits = dict(DEFAULT_LIMITS)
    for key, number in (value or {}).items():
        if key not in MAX_LIMITS:
            continue
        if type(number) is not int or not 1 <= number <= MAX_LIMITS[key]:
            raise CollaborationConflict(f"{key} 无效（1–{MAX_LIMITS[key]}）")
        limits[key] = number
    return limits


def _members(value: Any, mode: str, roles=None) -> List[dict]:
    roles = ROLES if roles is None else roles
    if not isinstance(value, list) or not 1 <= len(value) <= _MAX_MEMBERS:
        raise CollaborationConflict(f"成员需要 1–{_MAX_MEMBERS} 个")
    members, names = [], set()
    for item in value:
        if not isinstance(item, dict):
            raise CollaborationConflict("成员格式无效")
        name, role = _text(item.get("name"), 20), str(item.get("role") or "")
        if not name or name in names:
            raise CollaborationConflict("成员名不能为空或重复")
        if role not in roles:
            raise CollaborationConflict(f"未知角色：{role}（可用：{'、'.join(roles)}）")
        if mode == "private" and roles[role]["web"]:
            raise CollaborationConflict("private 任务上下文不能有联网成员：私人资料和出网成员不能放在一起")
        engine = str(item.get("engine") or "vortocode")
        if engine not in ENGINES:
            raise CollaborationConflict(f"未知执行框架：{engine}（可用：{'、'.join(ENGINES)}）")
        allowed_engines = roles[role].get("engines")
        if allowed_engines and engine not in allowed_engines:
            raise CollaborationConflict(f"{roles[role]['title']}成员只能由 {'、'.join(ENGINES[e] for e in allowed_engines)} 担任")
        model = _text(item.get("model"), 80)
        if model and engine in _ENGINES_WITHOUT_MODEL:
            raise CollaborationConflict(f"{ENGINES[engine]} 成员暂不支持指定模型")
        names.add(name)
        members.append({"name": name, "role": role, "model": model, "engine": engine})
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
    def _event(root: BackgroundTask, kind: str, text: str = "", *, limit: int = 300, **extra: Any) -> None:
        root.team["events"] = int(root.team.get("events", 0)) + 1
        log = root.team.setdefault("log", [])
        log.append({"kind": kind, "text": _text(text, limit), "seq": root.team["events"], "at": _now(), **extra})
        root.team["log"] = log[-_MAX_LOG:]

    # ── 建根（主人批准之后由调用方调用） ────────────────────────────────
    def create(self, owner: str, *, objective: str, acceptance: List[str], members: List[dict],
               mode: str = "open", limits: Optional[dict] = None, private_refs: Optional[List[str]] = None,
               tid: Optional[str] = None, project: Optional[dict] = None, department: str = '', reason: str = '',
               expected_roster: Optional[dict] = None) -> BackgroundTask:
        if not isinstance(owner, str) or not owner:
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
        from src.workflow.staff.registry import StaffRegistry
        registered = bool(reason or department or any(isinstance(x, str) or isinstance(x, dict) and 'member' in x for x in members))
        proposal = None
        if registered:
            from src.workflow.staff.team_roster import normalize
            proposal = normalize(self.repo_root, members, mode, department, reason)
            if expected_roster is not None and proposal != expected_roster:
                raise CollaborationConflict('确认期间岗位、成员或部门已变化，未组队')
            roster = proposal['members']
        else:
            roster = _members(members, mode, StaffRegistry(self.repo_root).team_roles())
        developers = [item for item in roster if item['role'] == 'developer'
                      or (item.get('role_snapshot') or {}).get('grants', {}).get('project')]
        if registered:
            from src.workflow.staff.team_roster import fixed_project
            fixed = fixed_project(roster)
            if fixed:
                fixed = {key: fixed[key] for key in ('name', 'repo_root')}
                if project and {key: project.get(key) for key in ('name', 'repo_root')} != fixed:
                    raise CollaborationConflict('团队不能覆盖开发岗位的固定项目')
                project = fixed
        if developers and mode != "private":
            raise CollaborationConflict("带开发成员的小组必须是 private 档位：项目代码不能和联网成员放在同一个小组")
        if developers and not (isinstance(project, dict) and project.get("name")
                               and str(project.get("repo_root") or "").startswith("/")):
            raise CollaborationConflict("带开发成员的小组要指定一个登记过的常驻项目")
        if project and not developers:
            project = None
        with _LOCK:
            if registered:
                from src.workflow.staff.team_roster import normalize
                if normalize(self.repo_root, members, mode, department, reason) != proposal:
                    raise CollaborationConflict('组队保存前岗位、成员或部门已变化')
            if registered and not department and sum(root.status in LIVE and not root.team.get('department') for root in self.list()) >= 3:
                raise CollaborationConflict('同时进行的临时团队最多 3 个')
            root = BackgroundTask.new(KIND, objective, tid=tid, owner_session=owner)
            if self.ledger.load(root.id) is not None:
                raise CollaborationConflict("任务上下文 ID 已存在")
            root.status = "active"
            root.team = {
                "version": 1, "mode": mode, "objective": objective,
                "acceptance": [_text(item, 300) for item in acceptance],
                "members": roster, "private_refs": refs,
                "project": {"name": _text(project["name"], 100), "repo_root": str(project["repo_root"])} if project else None,
                "limits": normalize_limits(limits), "used": {key: 0 for key in _USAGE_KEYS},
                "children": [], "coordinator": {"kind": "main_agent", "model": ""},
                "events": 0, "log": [], "inbox": [], "waiting_reason": "", "submission": None, "tainted": False,
                "wake": {"open": False, "cuts": 0, "events_at_start": 0, "idle": 0, "last_error": "", "same_error": 0,
                         "seen_results": {}},
            }
            if proposal is not None:
                root.team.update(staff_roster=True, department=department, reason=reason,
                    department_snapshot=proposal['department_snapshot'], notes=proposal['notes'], coordinator=proposal['coordinator'])
            self._event(root, "created", objective)
            return self._save(root)

    # ── 用量（只计数，不拦） ────────────────────────────────────────────
    @staticmethod
    def _charge(root: BackgroundTask, cost: Dict[str, int]) -> None:
        used = root.team.setdefault("used", {})
        for key, value in cost.items():
            used[key] = int(used.get(key, 0)) + value

    @staticmethod
    def _member(root: BackgroundTask, name: str) -> dict:
        member = next((item for item in root.team["members"] if item["name"] == name), None)
        if member is None:
            raise CollaborationConflict(f"没有名为 {name} 的成员")
        return member

    # ── 协调人的动作 ───────────────────────────────────────────────────
    def assign(self, root_id: str, member: str, instruction: str, expected: str, *,
               tainted: bool = False, materials: Optional[List[dict]] = None) -> BackgroundTask:
        """给成员派一件事：先记用量并落盘根，再建子任务。

        ``tainted``：要求里带着别的成员交回的外部内容（例如请审查员审研究员的结果），这条子任务一开始就是污点。"""
        if not instruction.strip() or not expected.strip():
            raise CollaborationConflict("派活要写清做什么和交回什么")
        if len(instruction) > _MAX_INSTRUCTION:
            raise CollaborationConflict('派活要求超过 16000 字符，未静默截断；请用交付文件引用或收窄要求')
        with _LOCK:
            root = self._live(root_id)
            entry = self._member(root, member)
            if root.team.get('staff_roster'):
                from src.workflow.staff.team_roster import identity, check
                specialist, assigned = identity(root, entry)
                check(self.repo_root, root, specialist, assigned)
                role = assigned
            else:
                role = ROLES[entry["role"]]
            if materials:
                if not isinstance(materials, list) or len(materials) > 8 or any(not isinstance(item, dict)
                        or item.get('task_id') not in root.team['children'] for item in materials):
                    raise CollaborationConflict('交接材料不属于本团队')
                from src.workflow.staff.registry import StaffRegistry
                if 'read_delivery' not in StaffRegistry(self.repo_root).role(entry['role'])['grants']['tools']:
                    raise CollaborationConflict('岗位没有交付读取权限，请主人先批准岗位升级')
                from src.workflow.tasks.task_outputs import TaskOutputs
                for item in materials:
                    TaskOutputs(self.repo_root).read(item['task_id'], item, limit=1)
            self._charge(root, {"tasks": 1, "rounds": 1, "steps": role["max_steps"],
                                "timeout_seconds": role["timeout_seconds"]})
            self._save(root)
            child = self.collaboration(root).create(
                _text(instruction, _MAX_INSTRUCTION), member, [_text(expected, 500)], prompt_limit=_MAX_INSTRUCTION,
                dispatch={"source": KIND, "root": root.id, "member": member, "role": entry["role"],
                          "model": entry["model"], "engine": entry.get("engine", "vortocode"),
                          "max_steps": role["max_steps"],
                          "timeout_seconds": role["timeout_seconds"]})
            if root.team.get('staff_roster'):
                child.collaboration['dispatch'].update(member=specialist['id'], member_revision=specialist['revision'],
                    role_revision=assigned['revision'], department=entry['department'], delivery='review',
                    staff_roster=True, notes=entry['notes'])
                if not self.ledger.save(child):
                    raise OSError('专员任务快照保存失败')
            if materials:
                child.collaboration['dispatch']['materials'] = materials
                if not self.ledger.save(child):
                    raise OSError('交接材料未能保存')
            if tainted:
                child.collaboration["tainted"] = True
                if not self.ledger.save(child):
                    raise OSError("子任务污点标记未能保存")
            root = self.get(root_id)
            root.team["children"].append(child.id)
            self._event(root, "assigned", f"{member}：{instruction}")
            self._save(root)
            return child

    def execution_identity(self, child_id):
        child = self.ledger.load(child_id)
        dispatch = child.collaboration.get('dispatch', {}) if child else {}
        root = self.get(dispatch.get('root', ''))
        if child_id not in root.team['children'] or child.owner_session != child_owner(root.id):
            raise CollaborationConflict('任务不属于本团队')
        from src.workflow.staff.team_roster import identity, check
        entry = self._member(root, child.collaboration['assignee'])
        member, role = identity(root, entry)
        check(self.repo_root, root, member, role)
        if (dispatch.get('member') != member['id'] or dispatch.get('member_revision') != member['revision']
                or dispatch.get('role_revision') != role['revision']):
            raise CollaborationConflict('成员子任务的版本快照已变化')
        return member, role

    def check_coordinator(self, root_id):
        root = self.get(root_id)
        if not root.team.get('staff_roster'):
            return
        if root.status != 'active' and not (root.status == 'waiting' and root.team.get('quota', {}).get('status') == 'waiting'):
            raise CollaborationConflict('团队已停止或不在协调阶段')
        from src.workflow.staff.registry import StaffRegistry
        registry = StaffRegistry(self.repo_root)
        if root.team.get('department') and registry.department(root.team['department'])['status'] != 'active':
            raise CollaborationConflict('部门已暂停，协调人不能继续')
        coordinator = root.team['coordinator']
        if coordinator['kind'] == 'staff_lead':
            if (registry.member(coordinator['member']) != coordinator['member_snapshot']
                    or registry.role(coordinator['role_snapshot']['id']) != coordinator['role_snapshot']):
                raise CollaborationConflict('部门协调人的岗位或版本已变化')

    def interrupt_coordinator(self, root_id):
        """Park a failed shared-pool continuation; never silently replay previous actions."""
        with _LOCK:
            root = self.get(root_id)
            if root.status == 'active' or root.status == 'waiting' and root.team.get('quota', {}).get('status') == 'waiting':
                root.status = 'waiting'
                root.team['waiting_reason'] = '协调执行已中断；请核对现有任务和执行结果后，显式回复继续。'
                root.team['wake']['open'] = False
                if root.team.get('quota', {}).get('status') == 'waiting':
                    root.team['quota']['status'] = 'interrupted'
                self._event(root, 'failed', root.team['waiting_reason'])
                self._save(root)
            return root

    def read_material(self, child_id, source_id, number, *, offset=0, part='result', limit=8000):
        """Host-bound reader: only explicit inputs or this child's own previous deliveries."""
        current = self.ledger.load(child_id)
        if type(number) is not int or number < 1:
            raise CollaborationConflict('交付轮次无效')
        if current is None or current.collaboration.get('dispatch', {}).get('source') != 'team':
            raise CollaborationConflict('没有绑定团队子任务')
        root = self.get(current.collaboration['dispatch']['root'])
        if child_id not in root.team['children'] or root.status not in {'active', 'waiting'}:
            raise CollaborationConflict('当前任务不在本团队执行范围')
        child = self.collaboration(root).get(child_id)
        if root.team.get('staff_roster'):
            _, role = self.execution_identity(child_id)
        else:
            from src.workflow.staff.registry import StaffRegistry
            role = StaffRegistry(self.repo_root).role(child.collaboration['dispatch']['role'])
        if 'read_delivery' not in role['grants']['tools']:
            raise CollaborationConflict('岗位未获交付读取能力；请主人批准岗位升级')
        materials = child.collaboration['dispatch'].get('materials', [])
        reference = next((x for x in materials if x.get('task_id') == source_id and x.get('round') == number
                          and x.get('part', 'result') == part), None)
        if source_id == child_id and part == 'result' and number < child.collaboration['round']:
            return self.collaboration(root).read_output(source_id, number, offset=offset, limit=limit)
        if reference is None or source_id not in root.team['children']:
            raise CollaborationConflict('该交付未明确交给当前任务，不能按任务 ID 越界读取')
        if root.team['mode'] == 'private' and role['grants']['web']:
            raise CollaborationConflict('私人交付不能交给联网成员')
        from src.workflow.tasks.task_outputs import TaskOutputs
        return TaskOutputs(self.repo_root).read(source_id, reference, offset=offset, limit=limit)

    def review(self, root_id: str, child_id: str, verdict: str, note: str) -> BackgroundTask:
        """接受或返工一条成员结果；返工即下一轮。"""
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
                entry = self._member(root, child.collaboration['assignee'])
                role = entry['role_snapshot'] if root.team.get('staff_roster') else ROLES[entry['role']]
                if root.team.get('staff_roster'):
                    self.execution_identity(child_id)
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
    def _result_snapshot(self, root: BackgroundTask) -> Dict[str, dict]:
        return {child.id: {"round": child.collaboration["round"], "status": child.status}
                for child in self.children(root)
                if child.status in {"done", "failed", "cancelled", "interrupted"}
                and child.collaboration.get("review") != "accepted"}

    def _initialize_seen_results(self, root: BackgroundTask) -> bool:
        if "seen_results" in root.team["wake"]:
            return False
        # Older completed wakes have no delivery cursor. Do not manufacture new
        # events out of their old terminal records on every upgrade/restart.
        # An interrupted wake still takes the explicit recovery/recheck path.
        legacy_completed = root.team["used"].get("wakes", 0) > 0 and not root.team["wake"]["open"]
        root.team["wake"]["seen_results"] = self._result_snapshot(root) if legacy_completed else {}
        return True

    def unseen_member_results(self, root_id: str) -> List[BackgroundTask]:
        """Return receipts not delivered in a saved coordinator turn, irrespective of acceptance."""
        with _LOCK:
            root = self.get(root_id)
            if self._initialize_seen_results(root):
                self._save(root)
            seen = root.team["wake"]["seen_results"]
            snapshot = self._result_snapshot(root)
            return [child for child in self.children(root)
                    if child.id in snapshot and seen.get(child.id) != snapshot[child.id]]

    def begin_wake(self, root_id: str) -> BackgroundTask:
        """协调人每次醒来前调用：记一次唤醒并标记回合进行中。"""
        with _LOCK:
            root = self._live(root_id)
            self._initialize_seen_results(root)
            self._charge(root, {"wakes": 1})
            wake = root.team["wake"]
            snapshot = self._result_snapshot(root)
            wake.update(open=True, events_at_start=root.team["events"],
                        result_snapshot={key: value for key, value in snapshot.items()
                                         if wake["seen_results"].get(key) != value})
            return self._save(root)

    def note_said(self, root_id: str, text: str, *, tainted: bool = False) -> None:
        """协调人一回合结束时对主人说的话，记进群聊（不算进展：在 end_wake 之后调用）。"""
        text = _text(text, 1500)
        if not text:
            return
        with _LOCK:
            root = self.get(root_id)
            self._event(root, "said", text, limit=1500, tainted=bool(tainted))
            self._save(root)

    def note_member_result(self, root_id: str, child: BackgroundTask) -> BackgroundTask:
        """成员这一轮的结果（交回 / 失败）。连续 STALL_WAKES 次失败就转「等你」：协调人每次都会重新派活，
        按「醒来有没有新动作」判不出这种空转（真机 2026-11-04：研究员连续多轮没交回，小组跑了四十分钟）。"""
        with _LOCK:
            root = self.get(root_id)
            wake = root.team["wake"]
            if child.status == "done":
                wake["member_failures"] = 0
            elif child.status in ("failed", "interrupted"):
                wake["member_failures"] = int(wake.get("member_failures", 0)) + 1
                if root.status == "active" and wake["member_failures"] >= STALL_WAKES:
                    reason = (f"成员连续 {STALL_WAKES} 次没有交回结果（最近一次：{child.collaboration.get('assignee')}"
                              f"：{_text(child.error, 120) or '没有说明'}）")
                    root.status, root.team["waiting_reason"] = "waiting", reason
                    wake["member_failures"] = 0
                    self._event(root, "stalled", reason)
            else:
                return root
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
            root.team["inbox"] = (root.team["inbox"] + [{"id": uuid.uuid4().hex, "from": "owner", "text": text}])[-_MAX_INBOX:]
            root.status, root.team["waiting_reason"] = "active", ""
            root.team["wake"].update(idle=0, same_error=0, last_error="")
            self._event(root, "owner_reply", text)
            return self._save(root)

    def checkout_inbox(self, root_id: str) -> List[dict]:
        """Snapshot owner messages without consuming them; identify older queued messages in place."""
        with _LOCK:
            root = self.get(root_id)
            seen, changed = set(), False
            for item in root.team["inbox"]:
                identity = item.get("id")
                if not isinstance(identity, str) or not identity or identity in seen:
                    identity = item["id"] = uuid.uuid4().hex
                    changed = True
                seen.add(identity)
            if changed:
                self._save(root)
            return [dict(item) for item in root.team["inbox"]]

    def acknowledge_inbox(self, root_id: str, message_ids: List[str], *, seen_results: Optional[dict] = None) -> None:
        """Acknowledge saved input snapshots atomically; later arrivals remain unseen."""
        with _LOCK:
            root = self.get(root_id)
            ids = set(message_ids)
            kept = [item for item in root.team["inbox"] if item.get("id") not in ids]
            changed = len(kept) != len(root.team["inbox"])
            if seen_results:
                seen = root.team["wake"].setdefault("seen_results", {})
                for key, value in seen_results.items():
                    previous = seen.get(key)
                    if previous is None or value["round"] >= previous["round"]:
                        seen[key] = dict(value)
                        changed = True
            if changed:
                root.team["inbox"] = kept
                self._save(root)

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
                root.team["inbox"] = (root.team["inbox"] + [{"id": uuid.uuid4().hex, "from": "owner", "text": _text(note, 2000)}])[-_MAX_INBOX:]
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
        slots = max(0, int(root.team.get("limits", {}).get("parallel", DEFAULT_LIMITS["parallel"])) - running)
        return [child for child in children if child.status == "queued"][:slots]

    def record_delivery(self, root_id: str, child_id: str, delivery: Dict[str, Any]) -> BackgroundTask:
        """开发成员这一轮的交付（分支、改动文件、验证结果），由执行层在落分支后写入。只记事实，不改状态。"""
        with _LOCK:
            root = self.get(root_id)
            if child_id not in root.team["children"]:
                raise CollaborationConflict("这条子任务不属于本任务上下文")
            child = self.ledger.load(child_id)
            if child is None:
                raise CollaborationConflict("子任务不存在")
            keep = ("branch", "round", "files", "verified", "verify_cmd", "verify_output", "error",
                    "preexisting_failures", "flaky")
            child.collaboration["delivery"] = {key: delivery[key] for key in keep if key in delivery}
            if not self.ledger.save(child):
                raise OSError("交付记录未能保存")
            return child

    def view(self, root: BackgroundTask) -> Dict[str, Any]:
        """由代码拼的结构化视图：主对话与 work_status 用它，不读协调人写的文字。"""
        children = self.children(root)
        return {"id": root.id, "objective": root.team["objective"], "status": root.status, "mode": root.team["mode"],
                'department': root.team.get('department', ''),
                'coordinator': {'kind': root.team['coordinator']['kind'],
                    'name': (root.team['coordinator'].get('member_snapshot') or {}).get('name', '协调人'),
                    'member': root.team['coordinator'].get('member', '')},
                "waiting_reason": root.team["waiting_reason"], "used": dict(root.team["used"]),
                "limits": dict(root.team["limits"]),
                "project": (root.team.get("project") or {}).get("name"),
                "members": [{"name": item["name"], "role": item["role"], "engine": item.get("engine", "vortocode")}
                            for item in root.team["members"]],
                "children": [{"id": child.id, "member": child.collaboration.get("assignee"), "status": child.status,
                              "review": child.collaboration.get("review"), "round": child.collaboration.get("round"),
                              "tainted": bool(child.collaboration.get("tainted")),
                              **({'output': child.collaboration['output']} if child.collaboration.get('output') else {}),
                              **({"delivery": child.collaboration["delivery"]} if child.collaboration.get("delivery") else {})}
                             for child in children],
                "tainted": bool(root.team.get("tainted")) or any(child.collaboration.get("tainted") for child in children)}


# ── 群聊视图（ADR-014 S3：主人像看群聊一样看小组） ─────────────────────────
_ROOT_SPEAKERS = {"ask_owner": "coordinator", "submitted": "coordinator", "said": "coordinator",
                  "owner_reply": "owner", "accepted": "owner", "returned": "owner",
                  "stalled": "system", "failed": "system", "stopped": "system"}


def member_states(service: "TeamService", root: BackgroundTask) -> List[Dict[str, Any]]:
    """成员名单：角色、执行框架、模型，以及现在在不在做、做了几轮、失败几次（都按记录算）。"""
    children = service.children(root)
    out = []
    for item in root.team["members"]:
        mine = [child for child in children if child.collaboration.get("assignee") == item["name"]]
        messages = [message for child in mine for message in child.collaboration.get("messages") or []]
        busy = next((child for child in mine if child.status in ("running", "queued")), None)
        out.append({"name": item["name"], "role": item["role"], "title": (item.get('role_snapshot') or ROLES.get(item['role'], {})).get('title', item['role']),
                    "engine": item.get("engine", "vortocode"), "model": item.get("model", ""),
                    "state": "working" if busy is not None and busy.status == "running"
                    else "queued" if busy is not None else "idle",
                    "current": busy.id if busy is not None else "",
                    "rounds": sum(1 for message in messages if message.get("kind") == "started"),
                    "delivered": sum(1 for message in messages if message.get("kind") == "submitted"),
                    "failures": sum(1 for message in messages if message.get("kind") in ("failed", "interrupted"))})
    return out


def transcript(service: "TeamService", root: BackgroundTask) -> List[Dict[str, Any]]:
    """把小组的经过排成一条群聊：协调人派活 / 接受 / 返工 / 问你 / 交付 / 说的话，成员交回或失败，你的回复与验收，
    系统事件。全部来自记录（成员子任务的消息带时间；小组事件 2026-11-04 起带时间，更早的只能排在最后）。
    含外部内容的标 ``tainted``，前端只当纯文本显示。"""
    roles = {item["name"]: item for item in root.team["members"]}
    rows: List[Dict[str, Any]] = [{"at": root.created, "speaker": "system", "kind": "created",
                                   "text": f"小组开工：{root.team['objective']}", "order": 0}]
    for child in service.children(root):
        member = child.collaboration.get("assignee") or ""
        meta = roles.get(member, {})
        tainted = bool(child.collaboration.get("tainted"))
        messages = list(child.collaboration.get("messages") or [])
        last_submitted = max((index for index, message in enumerate(messages) if message.get("kind") == "submitted"),
                             default=-1)
        for index, message in enumerate(messages):
            kind, body, at = message.get("kind"), str(message.get("body") or ""), message.get("created") or ""
            base = {"at": at, "child_id": child.id, "round": message.get("round"), "member": member,
                    "role": meta.get("role", ""), "engine": meta.get("engine", "vortocode"), "order": 1}
            if kind == "assigned":
                rows.append({**base, "speaker": "coordinator", "kind": "assigned", "mention": member, "text": body,
                             "tainted": tainted})
            elif kind == "submitted":
                full = child.result if index == last_submitted and child.status == "done" and child.result else body
                rows.append({**base, "speaker": "member", "kind": "result", "text": full, "tainted": tainted,
                             **({'output': message['output']} if message.get('output') else {})})
            elif kind in ("failed", "interrupted", "cancelled"):
                rows.append({**base, "speaker": "member", "kind": kind, "text": body})
            elif kind in ("accept", "rework"):
                rows.append({**base, "speaker": "coordinator", "kind": kind, "mention": member, "text": body,
                             "tainted": tainted})
    submission = root.team.get("submission") if isinstance(root.team.get("submission"), dict) else None
    for event in root.team.get("log") or []:
        speaker = _ROOT_SPEAKERS.get(event.get("kind"))
        if speaker is None:
            continue
        row = {"at": event.get("at") or "", "speaker": speaker, "kind": event["kind"], "text": event.get("text", ""),
               "seq": event.get("seq", 0), "order": 2,
               "tainted": bool(event.get("tainted")) or (event["kind"] == "submitted" and bool(root.team.get("tainted")))}
        if event["kind"] == "ask_owner":
            row["mention"] = "owner"
        if event["kind"] == "submitted" and submission:
            row["text"] = submission.get("summary") or row["text"]
            row["evidence"] = [{"criterion": item.get("criterion"), "text": item.get("text", ""),
                                "evidence": item.get("evidence", "")} for item in submission.get("evidence") or []]
        rows.append(row)
    dated = sorted((row for row in rows if row["at"]), key=lambda row: (row["at"], row["order"], row.get("seq", 0)))
    undated = sorted((row for row in rows if not row["at"]), key=lambda row: row.get("seq", 0))
    for row in dated + undated:
        row.pop("order", None)
        row.pop("seq", None)
    return dated + undated


# ── 协调记录（与主对话分开；只存协调人自己的上下文） ─────────────────────────
_MAX_HISTORY = 48


def _history_path(repo_root: str, root_id: str):
    from src.utils.ids import safe_id
    clean = safe_id(root_id)
    if clean is None:
        raise CollaborationConflict("任务上下文 ID 无效")
    return state_dir(repo_root) / "teams" / f"{clean}.json"


def load_history(repo_root: str, root_id: str) -> List[dict]:
    import json
    try:
        data = json.loads(_history_path(repo_root, root_id).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return [item for item in data if isinstance(item, dict)] if isinstance(data, list) else []


def save_history(repo_root: str, root_id: str, history: List[dict]) -> None:
    import json
    from src.utils.state_dir import ensure_state_gitignore
    path = _history_path(repo_root, root_id)
    ensure_state_gitignore(repo_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    kept = list(history[-_MAX_HISTORY:])
    while kept and kept[0].get("role") != "user":      # 不从一组工具调用的中间开始
        kept.pop(0)
    tmp.write_text(json.dumps(kept, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)
