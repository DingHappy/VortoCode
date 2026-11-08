"""Local device ownership and one pending dispatch marker, never a task store."""
import hashlib
import json
import os
from pathlib import Path
import uuid

from src.devices.android import AndroidError, private_path, unique_object
from src.gateway.process_lease import ProcessLease
from src.utils.state_dir import home_state_dir


class AndroidState:
    def __init__(self, workspace, binding, *, root=None):
        self.root = root or home_state_dir() / "android-control"
        canonical, project = self.root.resolve(), Path(workspace).resolve()
        if canonical == project or project in canonical.parents:
            raise AndroidError("Android 控制状态必须位于工作区外")
        # A shared device lease across local Appium endpoints, sessions and apps.
        self.key = hashlib.sha256(("android:" + binding.udid).encode()).hexdigest()
        self.directory = self.root / self.key
        self.pending = self.directory / "pending.json"
        self.binding = binding
        self.lease = None

    def acquire(self):
        private_path(self.root.parent, directory=True, create=True)
        private_path(self.root, directory=True, create=True)
        private_path(self.directory, directory=True, create=True)
        if self.lease is None:
            self.lease = ProcessLease(self.directory / "owner.lock").acquire()

    def has_pending(self):
        if not self.pending.exists() and not self.pending.is_symlink():
            return False
        private_path(self.pending)
        if self.pending.stat().st_size > 1024:
            raise AndroidError("Android 待核对记录损坏")
        raw = json.loads(self.pending.read_bytes(), object_pairs_hook=unique_object)
        if (not isinstance(raw, dict) or set(raw) != {"version", "binding", "dispatch", "application", "generation"} or type(raw["version"]) is not int
                or raw["version"] != 1 or not isinstance(raw["binding"], str) or len(raw["binding"]) != 64
                or not isinstance(raw["dispatch"], str) or len(raw["dispatch"]) != 32
                or not isinstance(raw["application"], str) or not 1 <= len(raw["application"]) <= 255
                or not isinstance(raw["generation"], str) or not 1 <= len(raw["generation"]) <= 128):
            raise AndroidError("Android 待核对记录损坏；不会自动清理")
        return raw  # Also blocks after a different session/app takes this device.

    def begin(self):
        if self.lease is None or self.has_pending():
            raise AndroidError("Android 上次操作待人工核对")
        fd = os.open(self.pending, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
        with os.fdopen(fd, "w") as stream:
            json.dump({"version": 1, "binding": self.binding.fingerprint, "dispatch": uuid.uuid4().hex,
                       "application": self.binding.package, "generation": self.binding.generation}, stream)
            stream.flush(); os.fsync(stream.fileno())
        self._sync()

    def complete(self):
        if self.lease is None:
            raise AndroidError("Android 所有权已释放")
        if self.has_pending():
            self.pending.unlink()
            self._sync()

    def _sync(self):
        if os.name != "nt":
            fd = os.open(self.directory, os.O_RDONLY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)

    def release(self):
        if self.lease is not None:
            self.lease.release()
            self.lease = None
