"""Account-scoped IM receipts in the native SessionStore, under the channel lease.

This is a bounded admission journal, not a prompt queue or task ledger. A claimed
turn whose process disappeared becomes interrupted; it is never executed here.
The caller must hold the existing channel account lease throughout this lifetime.
"""
from __future__ import annotations

import hashlib
import json
import re

from src.gateway import session_store
from src.gateway.process_lease import channel_lease

MAX_RECEIPTS = 2048
MAX_BYTES = 2 * 1024 * 1024
STATES = {"claimed", "settled", "interrupted", "rejected"}


def fingerprint(value) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":")).encode()).hexdigest()


class InputStoreError(RuntimeError):
    pass


class InputStore:
    def __init__(self, channel: str, account: str, *, project_root=None):
        self.root = channel_lease(channel, account).path.parent / "inputs" / fingerprint([channel, account])
        self.key = "sid-im-inputs"
        self.path = self.root / ".vortocode" / "web_sessions" / "im-inputs.json"
        self.blocked = False
        if project_root is not None:
            from pathlib import Path
            project = Path(project_root).resolve()
            if self.root.resolve() == project or project in self.root.resolve().parents:
                raise InputStoreError("IM input storage must remain outside the project")
        initialized = self.root.exists()
        if self.root.is_symlink():
            raise InputStoreError("IM input storage identity is invalid")
        try:
            saved = session_store.load_session(str(self.root), self.key, strict=True, max_bytes=MAX_BYTES)
            if initialized and saved is None:
                raise ValueError("Missing initialized input journal")
            envelope = (saved or {}).get("im_inputs", {"version": 1, "receipts": {}})
            if saved is not None and "im_inputs" not in saved:
                raise ValueError("Missing input envelope")
            self._validate(envelope)
            self.receipts = envelope["receipts"]
            self.session = {"prompt_queue": [], "prompt_queue_paused": True}
            self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
            self.digest = self._digest()
            self.recovered = sum(row["state"] == "claimed" for row in self.receipts.values())
            for row in self.receipts.values():
                if row["state"] == "claimed":
                    row["state"] = "interrupted"
            self.flush()
        except (OSError, ValueError, TypeError) as exc:
            raise InputStoreError("IM input journal is missing or invalid; manual reconciliation required") from exc

    @staticmethod
    def _validate(envelope):
        if not isinstance(envelope, dict) or set(envelope) != {"version", "receipts"} \
                or type(envelope["version"]) is not int or envelope["version"] != 1:
            raise ValueError("Invalid receipt schema")
        rows = envelope["receipts"]
        if not isinstance(rows, dict) or len(rows) > MAX_RECEIPTS:
            raise ValueError("Invalid receipt capacity")
        for key, row in rows.items():
            if not isinstance(key, str) or re.fullmatch(r"[a-f0-9]{64}", key) is None \
                    or not isinstance(row, dict) or set(row) != {"payload", "target", "state"} \
                    or row["state"] not in STATES \
                    or any(not isinstance(row[field], str) or re.fullmatch(r"[a-f0-9]{64}", row[field]) is None
                           for field in ("payload", "target")):
                raise ValueError("Invalid receipt")

    def _digest(self):
        if self.path.is_symlink() or (self.path.exists() and self.path.stat().st_size > MAX_BYTES):
            raise InputStoreError("IM input journal identity changed")
        return hashlib.sha256(self.path.read_bytes()).hexdigest() if self.path.exists() else None

    def flush(self):
        if self.blocked:
            raise InputStoreError("IM input journal is blocked")
        try:
            if self._digest() != self.digest:
                raise InputStoreError("IM input journal changed outside its owner")
            ok = session_store.save_session(str(self.root), self.key, [], [], [],
                im_inputs={"version": 1, "receipts": self.receipts}, managed_im=True,
                prompt_queue=self.session["prompt_queue"],
                prompt_queue_paused=self.session["prompt_queue_paused"], durable=True)
            if not ok:
                raise InputStoreError("IM input journal write failed")
            self.digest = self._digest()
        except (OSError, ValueError, InputStoreError) as exc:
            self.blocked = True
            raise InputStoreError("IM input journal unavailable; new inputs paused") from exc

    def identify(self, event, channel, account, text):
        ids = [event.source_conversation_id, event.source_event_id]
        if any(not isinstance(value, str) or not 1 <= len(value) <= 512
               or any(ord(char) < 32 for char in value) for value in ids):
            raise InputStoreError("Native input identity is required")
        return (fingerprint([channel, account, event.kind, *ids]),
                fingerprint([str(event.sender_id), text, bool(event.images), bool(event.files),
                             bool(event.unsupported)]))

    def seen(self, key, payload):
        self.check()
        row = self.receipts.get(key)
        if row is not None and row["payload"] != payload:
            self.blocked = True
            raise InputStoreError("Native input identity has conflicting content")
        return row is not None

    def check(self):
        try:
            valid = not self.blocked and self._digest() == self.digest
        except (OSError, InputStoreError):
            valid = False
        if not valid:
            self.blocked = True
            raise InputStoreError("IM input journal unavailable")

    def record(self, key, payload, target, state):
        self.check()
        if self.full:
            raise InputStoreError("IM input journal is full; manual reconciliation required")
        self.receipts[key] = {"payload": payload, "target": target, "state": state}
        self.flush()

    @property
    def full(self):
        return len(self.receipts) >= MAX_RECEIPTS

    def settle(self, key, state):
        previous = self.receipts[key]["state"]
        self.receipts[key]["state"] = state
        try:
            self.flush()
        except InputStoreError:
            self.receipts[key]["state"] = previous
            raise
