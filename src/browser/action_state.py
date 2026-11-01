"""One pending browser dispatch, not a business receipt or retry queue.

Registered connections keep this beside their profile under its existing lease.
Anonymous windows only retain the marker in the current BrowserSession.
"""
import json
import os
from pathlib import Path
import re
import stat
import uuid


class BrowserActionState:
    def __init__(self, directory: Path | None = None):
        self.path = directory / ".vortocode-pending-action.json" if directory else None
        self.memory = None

    def read(self):
        if self.path is None:
            return self.memory
        if not self.path.exists() and not self.path.is_symlink():
            return self.memory
        info = self.path.lstat()
        if (not stat.S_ISREG(info.st_mode) or info.st_size > 1024
                or (hasattr(os, "getuid") and (info.st_uid != os.getuid() or info.st_mode & 0o077))):
            raise ValueError("网站待核对记录无效；不会自动清除")
        def unique(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError("网站待核对记录重复字段")
                result[key] = value
            return result
        raw = json.loads(self.path.read_bytes(), object_pairs_hook=unique)
        if (not isinstance(raw, dict) or set(raw) != {"version", "dispatch", "action"}
                or type(raw["version"]) is not int or raw["version"] != 1
                or not isinstance(raw["dispatch"], str) or not re.fullmatch(r"[a-f0-9]{32}", raw["dispatch"])
                or raw["action"] not in {"open", "click", "type", "scroll"}):
            raise ValueError("网站待核对记录损坏；不会自动清除")
        return raw

    def begin(self, action):
        if self.read() is not None or action not in {"open", "click", "type", "scroll"}:
            raise ValueError("上次网站操作尚待人工核对")
        marker = {"version": 1, "dispatch": uuid.uuid4().hex, "action": action}
        self.memory = marker  # Keep blocking if persistence or fsync fails.
        if self.path is not None:
            fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
            with os.fdopen(fd, "w") as stream:
                json.dump(marker, stream)
                stream.flush()
                os.fsync(stream.fileno())
            self._sync()
        return marker

    def complete(self, marker):
        if self.read() != marker:
            raise ValueError("网站待核对记录已变化；未清除")
        if self.path is not None:
            self.path.unlink()
            self._sync()
        self.memory = None

    def _sync(self):
        if self.path is not None and os.name != "nt":
            fd = os.open(self.path.parent, os.O_RDONLY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
