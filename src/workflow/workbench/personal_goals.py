"""Non-executing personal goal contracts, using the existing goal files and evidence.

Deadlines are tracking metadata. Explicit approval can authorize one reminder;
this module never submits tasks, runs verifiers, or calls a model.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone

from src.utils.state_dir import ensure_state_gitignore
from src.workflow.workbench.goals import Goal, GoalLedger


def _text(value: object, maximum: int, *, required: bool = False) -> str:
    if not isinstance(value, str) or len(value) > maximum or "\x00" in value:
        raise ValueError(f"文本必须是字符串，长度不超过 {maximum}")
    value = value.strip()
    if required and not value:
        raise ValueError("内容不能为空")
    return value


def _date(value: object) -> str:
    text = _text(value, 64)
    if not text:
        return ""
    try:
        date = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if date.tzinfo is None:
            raise ValueError
        return date.astimezone(timezone.utc).isoformat(timespec="seconds")
    except (ValueError, OverflowError):
        raise ValueError("时间需要包含时区的 ISO 日期") from None


def _fields(body: dict, allowed: set[str]) -> None:
    if set(body) - allowed:
        raise ValueError("请求包含不支持的字段")


def follow_up_preview(goal: Goal) -> str:
    """Exact bounded notification body, shown before approval and saved unchanged."""
    lines = ["📌 目标跟进提醒", goal.objective[:200], f"跟进时间：{goal.follow_up_at}",
             f"下一步：{goal.next_action[:500]}"]
    if goal.blocker:
        lines.append(f"阻塞：{goal.blocker[:500]}")
    lines.append("这是一次提醒，不代表任务执行完成或目标达成。")
    return "\n".join(lines)


class PersonalGoals:
    def __init__(self, root: str):
        self.ledger = GoalLedger(root, kind="personal")

    @contextmanager
    def _lock(self, goal_id: str):
        if not re.fullmatch(r"personal-[A-Za-z0-9_-]{1,64}", goal_id):
            raise ValueError("无效的私人目标 ID")
        directory = self.ledger._dir()
        if directory.is_symlink() or directory.parent.is_symlink():
            raise OSError("目标存储位置不可用")
        ensure_state_gitignore(self.ledger.repo_root)
        directory.mkdir(parents=True, exist_ok=True)
        lock_path = directory / f".{goal_id}.lock"
        if lock_path.is_symlink():
            raise OSError("目标锁不可用")
        fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        with os.fdopen(fd, "r+b") as handle:
            if sys.platform == "win32":
                import msvcrt
                if os.fstat(handle.fileno()).st_size == 0:
                    handle.write(b"\0")
                    handle.flush()
                handle.seek(0)
                try:
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                except OSError:
                    raise ValueError("目标正在更新，请刷新后重试") from None
            else:
                import fcntl
                try:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    raise ValueError("目标正在更新，请刷新后重试") from None
            # Closing the handle releases the lock. Never unlink its stable inode.
            yield

    def _load(self, goal_id: str) -> Goal:
        if not re.fullmatch(r"personal-[A-Za-z0-9_-]{1,64}", goal_id):
            raise KeyError(goal_id)
        goal = self.ledger.load(goal_id)
        if goal is None:
            raise KeyError(goal_id)
        if not isinstance(goal.follow_up, dict) or not isinstance(goal.follow_up_history, list):
            raise ValueError("提醒记录格式无效")
        return goal

    @staticmethod
    def _view(goal: Goal) -> dict:
        now = datetime.now(timezone.utc)
        if type(goal.revision) is not int or goal.revision < 1:
            raise ValueError("目标版本记录无效")
        due, follow_up = _date(goal.due_at), _date(goal.follow_up_at)
        active = goal.status != "achieved"
        result = goal.to_dict()
        result.update(
            follow_up_preview=follow_up_preview(goal),
            progress={"passed": sum(c.status == "passed" for c in goal.acceptance_criteria),
                      "failed": sum(c.status == "failed" for c in goal.acceptance_criteria),
                      "total": len(goal.acceptance_criteria)},
            overdue=bool(active and due and datetime.fromisoformat(due) <= now),
            follow_up_due=bool(active and follow_up and datetime.fromisoformat(follow_up) <= now),
        )
        return result

    def list(self) -> list[dict]:
        result = []
        directory = self.ledger._dir()
        if directory.is_symlink() or directory.parent.is_symlink():
            raise OSError("目标存储位置不可用")
        for path in directory.glob("personal-*.json"):
            try:
                result.append(self._view(self._load(path.stem)))
            except (KeyError, TypeError, ValueError):
                raise OSError("部分私人目标无法读取，请检查存储后重试") from None
        result.sort(key=lambda item: (item["updated"], item["id"]), reverse=True)
        return result

    def get(self, goal_id: str) -> dict:
        return self._view(self._load(goal_id))

    def _save(self, goal: Goal) -> dict:
        if not self.ledger.save(goal):
            raise OSError("目标未保存，请刷新确认后重试")
        return self._view(goal)

    def create(self, body: dict) -> dict:
        _fields(body, {"request_id", "objective", "acceptance_criteria", "next_action", "due_at", "follow_up_at"})
        request_id = body.get("request_id")
        if not isinstance(request_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", request_id):
            raise ValueError("需要有效的 request_id")
        objective = _text(body.get("objective"), 500, required=True)
        raw = body.get("acceptance_criteria")
        if not isinstance(raw, list) or not 1 <= len(raw) <= 20:
            raise ValueError("需要 1–20 条验收标准")
        criteria = [_text(item, 500, required=True) for item in raw]
        next_action = _text(body.get("next_action", ""), 1000, required=True)
        due, follow_up = _date(body.get("due_at", "")), _date(body.get("follow_up_at", ""))
        fingerprint = hashlib.sha256(json.dumps(
            [objective, criteria, next_action, due, follow_up], ensure_ascii=False,
        ).encode()).hexdigest()
        goal_id = f"personal-{request_id}"
        with self._lock(goal_id):
            path = self.ledger._path(goal_id)
            if path.exists() or path.is_symlink():
                old = self._load(goal_id)
                if old.request_fingerprint != fingerprint:
                    raise ValueError("创建请求已用于其他目标，请刷新核对")
                # Retried creation never overwrites later progress or evidence.
                return self._view(old)
            goal = Goal.new(objective, criteria, goal_id=goal_id)
            goal.kind, goal.status, goal.revision = "personal", "active", 1
            goal.request_fingerprint = fingerprint
            goal.next_action, goal.due_at, goal.follow_up_at = next_action, due, follow_up
            return self._save(goal)

    @staticmethod
    def _revision(goal: Goal, body: dict) -> None:
        revision = body.get("revision")
        if type(revision) is not int or revision != goal.revision:
            raise ValueError("目标已变化，请刷新后重试")

    @staticmethod
    def _invalidate_follow_up(goal: Goal) -> None:
        if goal.follow_up.get("status") == "scheduled":
            goal.follow_up.update(status="cancelled", reason="目标进展或验收已更新，需要重新批准提醒")
        elif goal.follow_up.get("status") == "sending":
            # Holding the lock means the old sending claim has no live sender.
            goal.follow_up.update(status="unknown", reason="此前发送中断，需核对通知结果")

    def configure_follow_up(self, goal_id: str, body: dict) -> dict:
        _fields(body, {"revision", "enabled", "confirmed", "acknowledge_uncertain"})
        enabled = body.get("enabled")
        if type(enabled) is not bool or (enabled and body.get("confirmed") is not True):
            raise ValueError("启用提醒需要用户明确批准")
        with self._lock(goal_id):
            goal = self._load(goal_id)
            self._revision(goal, body)
            old = goal.follow_up
            if enabled:
                if goal.status == "achieved" or not goal.follow_up_at:
                    raise ValueError("需要未达成的目标和已保存的下次跟进时间")
                _date(goal.follow_up_at)
                if old.get("status") in {"unknown", "sending"} and body.get("acknowledge_uncertain") is not True:
                    raise ValueError("此前通知结果不明，请核对后明确批准可能重复的提醒")
                if old.get("id"):
                    goal.follow_up_history = [*goal.follow_up_history, dict(old)][-20:]
                goal.follow_up = {"id": uuid.uuid4().hex, "status": "scheduled", "attempts": 0,
                                  "approved_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                                  "due_at": goal.follow_up_at, "text": follow_up_preview(goal)}
            else:
                if old.get("status") in {"accepted", "acknowledged", "unknown", "sending"}:
                    raise ValueError("该通知可能已经受理，暂停不能撤回；请先核对结果")
                goal.follow_up = {**old, "status": "cancelled", "reason": "用户暂停提醒"}
            goal.revision += 1
            return self._save(goal)

    def acknowledge_follow_up(self, goal_id: str, body: dict) -> dict:
        _fields(body, {"revision", "confirmed"})
        if body.get("confirmed") is not True:
            raise ValueError("需要用户明确确认已看到这条提醒")
        with self._lock(goal_id):
            goal = self._load(goal_id)
            self._revision(goal, body)
            state = goal.follow_up
            if not state.get("id") or type(state.get("attempts")) is not int or state["attempts"] < 1:
                raise ValueError("还没有这条提醒的通知尝试记录")
            state.update(status="acknowledged", user_acknowledged_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                         reason="用户明确确认已看到提醒，不代表目标已达成")
            goal.revision += 1
            return self._save(goal)

    def update(self, goal_id: str, body: dict) -> dict:
        _fields(body, {"revision", "next_action", "blocker", "due_at", "follow_up_at"})
        with self._lock(goal_id):
            goal = self._load(goal_id)
            self._revision(goal, body)
            if goal.status == "achieved":
                raise ValueError("已达成的目标不能修改进展；复验未通过会重新打开目标")
            for key in ("next_action", "blocker"):
                if key in body:
                    setattr(goal, key, _text(body[key], 1000, required=key == "next_action"))
            for key in ("due_at", "follow_up_at"):
                if key in body:
                    setattr(goal, key, _date(body[key]))
            failed = any(c.status == "failed" for c in goal.acceptance_criteria)
            goal.status = "blocked" if goal.blocker or failed else "active"
            if failed and not goal.blocker:
                goal.blocker = "仍有验收标准未通过，请复验"
            self._invalidate_follow_up(goal)
            goal.revision += 1
            return self._save(goal)

    def evidence(self, goal_id: str, criterion_id: str, body: dict) -> dict:
        _fields(body, {"revision", "summary", "passed", "confirmed"})
        if body.get("confirmed") is not True or type(body.get("passed")) is not bool:
            raise ValueError("需要用户明确确认验收结果")
        summary = _text(body.get("summary"), 2000, required=True)
        with self._lock(goal_id):
            goal = self._load(goal_id)
            self._revision(goal, body)
            if len(goal.evidence) >= 200:
                raise ValueError("目标验收记录已达上限，请建立后续目标")
            if not goal.criterion(criterion_id):
                raise ValueError("未知的验收标准")
            previous_next_action = goal.next_action
            goal.add_evidence(criterion_id=criterion_id, kind="personal_acceptance", summary=summary,
                              passed=body["passed"], source="user_confirmation")
            # Re-evaluate only explicit criterion decisions; no task completion receipts.
            generated_blocker = goal.blocker.startswith("验收未通过：") or goal.blocker == "仍有验收标准未通过，请复验"
            manual_blocker = "" if generated_blocker else goal.blocker
            goal.blocker = ""
            goal.evaluate()
            if goal.status != "achieved" and manual_blocker:
                goal.blocker, goal.status = manual_blocker, "blocked"
            if goal.status != "achieved":
                goal.next_action = ("处理未通过的事项并重新确认"
                                    if any(c.status == "failed" for c in goal.acceptance_criteria)
                                    else "继续推进并核对剩余验收标准"
                                    if previous_next_action == "处理未通过的事项并重新确认"
                                    else previous_next_action)
            self._invalidate_follow_up(goal)
            goal.revision += 1
            return self._save(goal)
