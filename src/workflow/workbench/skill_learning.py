"""Operator-initiated skill proposals from recorded, reviewed dispatch deliveries.

Uses the original dispatch transaction and PersonalSkills lock/storage. No model,
remote refresh, scheduler, new task ledger or automatic instruction activation.
"""
from __future__ import annotations

import difflib
import hashlib
import json
import re
import uuid
from datetime import datetime, timezone
from itertools import islice

from src.memory.write_policy import redact_secret_like, sanitize_persistent_summary
from src.workflow.tasks import assistant_dispatch as dispatch
from src.workflow.workbench.personal_skills import MAX_SKILLS, PersonalSkills, SkillConflict

_LIMIT = 256 * 1024
_OID = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})")
_ACTOR = "instance_operator"  # Instance authentication does not identify separate people.


def _digest(value) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _dated(value) -> bool:
    try:
        return isinstance(value, str) and datetime.fromisoformat(value.replace("Z", "+00:00")).tzinfo is not None
    except ValueError:
        return False


def _source(item: dict) -> dict | None:
    evidence = item.get("delivery_evidence")
    if (item.get("status") != "done" or item.get("sync_state") != "current"
            or item.get("error") or item.get("delivery_error") or item.get("tainted")
            or item.get("recovery_task") or not item.get("remote_task_id") or not isinstance(evidence, dict)):
        return None
    head = evidence.get("head")
    total, accepted = evidence.get("total_hunks"), evidence.get("accepted_hunks")
    if (not isinstance(head, str) or not _OID.fullmatch(head)
            or evidence.get("verification") != "passed" or evidence.get("verified_head") != head
            or evidence.get("coverage_known") is not True or type(total) is not int
            or type(accepted) is not int or total < 0 or accepted != total
            or not _dated(evidence.get("verified_at")) or not _dated(evidence.get("checked_at"))):
        return None
    if (not all(isinstance(item.get(key), str) for key in ("id", "title", "goal", "project", "branch"))
            or not isinstance(item.get("criteria"), list)
            or len(item["criteria"]) > 10 or any(not isinstance(value, str) for value in item["criteria"])):
        return None
    # Include raw source/result identity in the hash; never copy logs/results into instructions.
    identity = {key: item.get(key) for key in ("id", "remote_task_id", "request_id", "title", "goal",
                "project", "branch", "criteria", "result", "error", "status", "tainted", "session", "attempt",
                "submission_target", "submission_runtime_root")}
    identity["evidence"] = {key: evidence.get(key) for key in ("head", "verification", "verified_head",
                        "verified_at", "coverage_known", "total_hunks", "accepted_hunks")}
    def safe(text, limit):
        return sanitize_persistent_summary(text, limit)[0]
    return {"dispatch_id": item["id"], "revision": _digest(identity), "title": safe(item["title"], 80),
            "project": safe(item["project"], 80), "goal": safe(item["goal"], 2000),
            "criteria": [safe(value, 400) for value in item["criteria"]], "head": head,
            "checked_at": evidence["checked_at"], "verified_at": evidence["verified_at"]}


class SkillLearning:
    def __init__(self, skills: PersonalSkills):
        self.skills = skills
        self.root = str(skills.root)

    def _record(self, skill_id: str) -> dict | None:
        try:
            record = json.loads(self.skills._read(skill_id, "proposal.json", _LIMIT))
        except FileNotFoundError:
            return None
        if (not isinstance(record, dict) or record.get("version") != 1 or record.get("skill_id") != skill_id
                or record.get("status") not in {"pending", "applying", "accepted", "rejected"}
                or not isinstance(record.get("source"), dict) or not isinstance(record.get("draft"), dict)
                or not isinstance(record.get("history"), list) or len(record["history"]) > 20
                or record.get("revision") != _digest({key: value for key, value in record.items() if key != "revision"})):
            raise ValueError("技能提案记录无效，请人工核对")
        return record

    def _write(self, record: dict) -> None:
        record["revision"] = _digest({key: value for key, value in record.items() if key != "revision"})
        raw = json.dumps(record, ensure_ascii=False).encode()
        if len(raw) > _LIMIT:
            raise ValueError("技能提案超过大小限制")
        self.skills._write(record["skill_id"], "proposal.json", raw)

    def _current(self, skill_id: str) -> tuple[str | None, bytes]:
        try:
            raw = self.skills._read(skill_id)
            return hashlib.sha256(raw).hexdigest(), raw
        except FileNotFoundError:
            return None, b""

    def _find(self, dispatch_id: str, revision: str) -> dict:
        source = next((_source(item) for item in dispatch.list_dispatches(self.root) if item["id"] == dispatch_id), None)
        if source is None or source["revision"] != revision:
            raise SkillConflict("任务交付证据已变化或不满足条件，请刷新；未保存技能")
        return source

    def list(self) -> dict:
        sources = [source for item in dispatch.list_dispatches(self.root) if (source := _source(item)) is not None]
        self.skills._path("check")
        paths = list(islice(self.skills.base.iterdir(), MAX_SKILLS + 1)) if self.skills.base.exists() else []
        proposals, issues = [], []
        for path in paths[:MAX_SKILLS]:
            try:
                record = self._record(path.name)
                if record:
                    proposals.append({key: record[key] for key in ("skill_id", "id", "status", "created_at", "source")})
            except (OSError, ValueError, KeyError):
                issues.append("部分技能提案无法读取，请人工核对；未自动修复或重试。")
        return {"sources": sources[:20], "proposals": sorted(proposals, key=lambda item: item["created_at"], reverse=True),
                "issues": list(dict.fromkeys(issues)), "truncated": len(sources) > 20 or len(paths) > MAX_SKILLS}

    def preview(self, skill_id: str) -> dict:
        record = self._record(skill_id)
        if record is None:
            raise FileNotFoundError
        revision, raw = self._current(skill_id)
        stale = revision != record["draft"].get("revision")
        try:
            self._find(record["source"]["dispatch_id"], record["source"]["revision"])
            source_stale = False
        except (dispatch.DispatchError, SkillConflict):
            source_stale = True
        diff = ""
        if not stale:
            after = self.skills.encode(record["draft"]).decode()
            diff = "".join(difflib.unified_diff(raw.decode().splitlines(keepends=True), after.splitlines(keepends=True),
                                               fromfile="当前版本", tofile="待审草稿"))
        diff, redactions = redact_secret_like(diff)
        return {**record, "stale": stale, "source_stale": source_stale, "diff": diff, "diff_redacted": bool(redactions),
                "current_revision": revision, "current_enabled": self.skills.get(skill_id)["enabled"] if revision else False}

    def propose(self, skill_id: str, body: dict) -> dict:
        if set(body) != {"dispatch_id", "source_revision", "draft"} or not isinstance(body.get("draft"), dict):
            raise ValueError("需要任务来源、来源版本和完整技能草稿")
        draft = body["draft"]
        raw = self.skills.encode(draft)
        if redact_secret_like(raw.decode())[1]:
            raise ValueError("草稿包含疑似凭据，请移除后再保存")
        # Same lock order for proposal and acceptance; no cross-ledger source race.
        with dispatch._transaction(self.root), self.skills._lock(skill_id):
            source = self._find(body["dispatch_id"], body["source_revision"])
            if self._current(skill_id)[0] != draft.get("revision"):
                raise SkillConflict("技能基准版本已变化，请刷新并重新比较")
            if len(list(islice(self.skills.base.iterdir(), MAX_SKILLS + 1))) > MAX_SKILLS:
                raise ValueError("个人技能数量已达上限")
            previous = self._record(skill_id)
            fingerprint = _digest({"source": source["revision"], "draft": draft})
            if previous and previous.get("fingerprint") == fingerprint:
                return self.preview(skill_id)  # Duplicate click or unknown HTTP response: same proposal.
            if previous and previous["status"] in {"pending", "applying"}:
                raise SkillConflict("此技能已有待处理提案，请先审阅或拒绝")
            history = previous["history"] + [self._summary(previous)] if previous else []
            if any(row.get("source_revision") == source["revision"] for row in history):
                raise SkillConflict("此任务来源已经处理过，不重复生成；请使用新的交付证据")
            record = {"version": 1, "skill_id": skill_id, "id": uuid.uuid4().hex, "status": "pending",
                      "source": source, "draft": draft, "fingerprint": fingerprint, "history": history[-20:],
                      "created_at": _now(), "proposed_by": _ACTOR, "reviewed_by": None, "reviewed_at": None,
                      "target_revision": hashlib.sha256(raw).hexdigest()}
            self._write(record)
            return self.preview(skill_id)

    @staticmethod
    def _summary(record: dict) -> dict:
        return {"id": record["id"], "source_revision": record["source"]["revision"], "status": record["status"],
                "created_at": record["created_at"], "proposed_by": record["proposed_by"],
                "reviewed_by": record["reviewed_by"], "reviewed_at": record["reviewed_at"],
                "target_revision": record["target_revision"]}

    def decide(self, skill_id: str, body: dict) -> dict:
        if set(body) != {"id", "revision", "action"} or body.get("action") not in {"accept", "reject", "reconcile"}:
            raise ValueError("需要明确的提案、审阅版本和决定")
        with dispatch._transaction(self.root), self.skills._lock(skill_id):
            record = self._record(skill_id)
            if record is None:
                raise FileNotFoundError
            if record["id"] != body["id"] or record["revision"] != body["revision"]:
                raise SkillConflict("提案已变化，请刷新并重新审阅")
            if record["status"] not in {"pending", "applying"}:
                raise SkillConflict("提案已经处理，不重复应用")
            action = body["action"]
            if action == "reject":
                record.update(status="rejected", reviewed_by=_ACTOR, reviewed_at=_now())
                self._write(record)  # Does not restore/modify the current skill, even after an interrupted acceptance.
            elif action == "reconcile":
                if (record["status"] != "applying" or self._current(skill_id)[0] != record["target_revision"]
                        or self.skills.get(skill_id)["enabled"]):
                    raise SkillConflict("尚未发现提案的完整目标版本，请核对当前技能；未重试写入")
                record.update(status="accepted", reconciled_at=_now())
                self._write(record)  # Only acknowledges the version already written; never enables it.
            else:
                if record["status"] != "pending":
                    raise SkillConflict("上次保存结果待核对，请检查当前版本；不会自动重试")
                self._find(record["source"]["dispatch_id"], record["source"]["revision"])
                if self._current(skill_id)[0] != record["draft"].get("revision"):
                    raise SkillConflict("技能基准版本已变化，请重新起草")
                # Journal intent first. Any later I/O failure leaves an explicit reconciliation state.
                record.update(status="applying", reviewed_by=_ACTOR, reviewed_at=_now())
                self._write(record)
                self.skills._save_locked(skill_id, record["draft"], self.skills.encode(record["draft"]))
                record["status"] = "accepted"
                self._write(record)
            return self.preview(skill_id)
