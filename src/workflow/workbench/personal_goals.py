"""Non-executing personal goal contracts, using the existing goal files and evidence.

Deadlines are tracking metadata. This module does not schedule, submit tasks,
run verifiers, call a model, or treat execution receipts as user acceptance.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys
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
            goal.revision += 1
            return self._save(goal)
