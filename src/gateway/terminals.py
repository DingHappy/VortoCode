"""Ephemeral PTY sessions for the Desktop integrated terminal.

Structured command runs remain the source of test/build evidence.  A terminal is
deliberately a separate, interactive surface: it owns a real pseudo-terminal,
accepts stdin and resize events, and is torn down with the local runtime.
"""
from __future__ import annotations

import codecs
import os
import re
import shutil
import signal
import struct
import subprocess
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Optional

_MAX_ACTIVE_TERMINALS = 6
_MAX_BUFFER_CHARS = 500_000
_MAX_INPUT_CHARS = 32_000


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _terminal_size(value: object, *, minimum: int, maximum: int, fallback: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = fallback
    return max(minimum, min(maximum, parsed))


@dataclass
class TerminalSnapshot:
    id: str
    status: str
    pid: int
    cwd: str
    shell: str
    cols: int
    rows: int
    code: Optional[int]
    created: str
    updated: str
    sandbox: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "status": self.status,
            "pid": self.pid,
            "cwd": self.cwd,
            "shell": self.shell,
            "cols": self.cols,
            "rows": self.rows,
            "code": self.code,
            "created": self.created,
            "updated": self.updated,
            "sandbox": self.sandbox,
        }


class _TerminalProcess:
    def __init__(self, terminal_id: str, repo_root: str, cols: int, rows: int) -> None:
        if os.name != "posix":
            raise RuntimeError("当前平台尚不支持本地 PTY")
        import fcntl
        import pty
        import termios

        from src.agents.sandbox import resolve_sandbox, sandboxed_exec_argv

        shell = os.environ.get("SHELL", "").strip()
        if not shell.startswith("/") or not os.access(shell, os.X_OK):
            shell = shutil.which("zsh") or shutil.which("bash") or "/bin/sh"

        master_fd, slave_fd = pty.openpty()
        fcntl.ioctl(slave_fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
        env = dict(os.environ)
        env.update({"TERM": "xterm-256color", "COLORTERM": "truecolor"})
        sandbox = resolve_sandbox()
        if not sandbox.allowed:
            os.close(master_fd)
            os.close(slave_fd)
            raise RuntimeError(sandbox.reason)
        argv = [shell, "-l"]
        if sandbox.isolated:
            argv = sandboxed_exec_argv(repo_root, argv, backend=sandbox.backend)
        try:
            process = subprocess.Popen(
                argv,
                cwd=repo_root,
                env=env,
                stdin=slave_fd,
                stdout=slave_fd,
                stderr=slave_fd,
                start_new_session=True,
                close_fds=True,
            )
        except Exception:
            os.close(master_fd)
            os.close(slave_fd)
            raise
        finally:
            try:
                os.close(slave_fd)
            except OSError:
                pass

        now = _now()
        self.id = terminal_id
        self.repo_root = repo_root
        self.shell = shell
        self.sandbox = sandbox.to_dict()
        self.cols = cols
        self.rows = rows
        self.process = process
        self.master_fd = master_fd
        self.created = now
        self.updated = now
        self._buffer = ""
        self._buffer_start = 0
        self._lock = threading.RLock()
        self._closed = False
        self._reader = threading.Thread(target=self._drain, name=f"pty-{terminal_id}", daemon=True)
        self._reader.start()

    def _drain(self) -> None:
        decoder = codecs.getincrementaldecoder("utf-8")("replace")
        try:
            while True:
                try:
                    payload = os.read(self.master_fd, 16_384)
                except OSError:
                    break
                if not payload:
                    break
                self._append(decoder.decode(payload))
            self._append(decoder.decode(b"", final=True))
        finally:
            with self._lock:
                self.updated = _now()

    def _append(self, text: str) -> None:
        if not text:
            return
        with self._lock:
            self._buffer += text
            if len(self._buffer) > _MAX_BUFFER_CHARS:
                removed = len(self._buffer) - _MAX_BUFFER_CHARS
                self._buffer = self._buffer[removed:]
                self._buffer_start += removed
            self.updated = _now()

    def snapshot(self) -> TerminalSnapshot:
        code = self.process.poll()
        with self._lock:
            return TerminalSnapshot(
                id=self.id,
                status="running" if code is None else "exited",
                pid=self.process.pid,
                cwd=self.repo_root,
                shell=self.shell,
                cols=self.cols,
                rows=self.rows,
                code=code,
                created=self.created,
                updated=self.updated,
                sandbox=self.sandbox,
            )

    def read(self, offset: int = 0) -> Dict[str, Any]:
        with self._lock:
            requested = max(0, int(offset or 0))
            dropped = requested < self._buffer_start
            start = max(requested, self._buffer_start)
            relative = min(len(self._buffer), start - self._buffer_start)
            output = self._buffer[relative:]
            next_offset = self._buffer_start + len(self._buffer)
        return {
            **self.snapshot().to_dict(),
            "output": output,
            "offset": next_offset,
            "dropped": dropped,
        }

    def write(self, data: str) -> None:
        if self.process.poll() is not None:
            raise RuntimeError("终端进程已经退出")
        text = str(data or "")
        if len(text) > _MAX_INPUT_CHARS:
            raise ValueError(f"单次终端输入不能超过 {_MAX_INPUT_CHARS} 字符")
        if not text:
            return
        os.write(self.master_fd, text.encode("utf-8", errors="replace"))

    def resize(self, cols: int, rows: int) -> None:
        import fcntl
        import termios

        with self._lock:
            self.cols = _terminal_size(cols, minimum=20, maximum=500, fallback=self.cols)
            self.rows = _terminal_size(rows, minimum=5, maximum=200, fallback=self.rows)
            size = struct.pack("HHHH", self.rows, self.cols, 0, 0)
            fcntl.ioctl(self.master_fd, termios.TIOCSWINSZ, size)
            self.updated = _now()

    def stop(self, timeout: float = 2.0) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
        if self.process.poll() is None:
            try:
                os.killpg(os.getpgid(self.process.pid), signal.SIGTERM)
                self.process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(os.getpgid(self.process.pid), signal.SIGKILL)
                except (OSError, ProcessLookupError):
                    pass
            except (OSError, ProcessLookupError):
                pass
        try:
            os.close(self.master_fd)
        except OSError:
            pass
        self.updated = _now()


class TerminalManager:
    """Own the runtime's bounded set of interactive local PTYs."""

    def __init__(self, repo_root: str) -> None:
        self.repo_root = str(repo_root)
        self._items: Dict[str, _TerminalProcess] = {}
        self._lock = threading.RLock()

    @staticmethod
    def _clean_id(terminal_id: str) -> str:
        value = str(terminal_id or "")
        return value if re.fullmatch(r"term-[A-Za-z0-9_-]+", value) else ""

    def create(self, *, cols: int = 100, rows: int = 28) -> Dict[str, Any]:
        with self._lock:
            active = sum(item.process.poll() is None for item in self._items.values())
            if active >= _MAX_ACTIVE_TERMINALS:
                raise ValueError(f"交互终端已达上限 {_MAX_ACTIVE_TERMINALS} 个")
            terminal_id = "term-" + uuid.uuid4().hex[:10]
            item = _TerminalProcess(
                terminal_id,
                self.repo_root,
                _terminal_size(cols, minimum=20, maximum=500, fallback=100),
                _terminal_size(rows, minimum=5, maximum=200, fallback=28),
            )
            self._items[terminal_id] = item
        return item.read(0)

    def list(self) -> list[Dict[str, Any]]:
        with self._lock:
            items = list(self._items.values())
        return [item.snapshot().to_dict() for item in reversed(items)]

    def get(self, terminal_id: str) -> Optional[_TerminalProcess]:
        clean = self._clean_id(terminal_id)
        with self._lock:
            return self._items.get(clean) if clean else None

    def read(self, terminal_id: str, offset: int = 0) -> Optional[Dict[str, Any]]:
        item = self.get(terminal_id)
        return item.read(offset) if item else None

    def write(self, terminal_id: str, data: str) -> Optional[Dict[str, Any]]:
        item = self.get(terminal_id)
        if item is None:
            return None
        item.write(data)
        return item.snapshot().to_dict()

    def resize(self, terminal_id: str, cols: int, rows: int) -> Optional[Dict[str, Any]]:
        item = self.get(terminal_id)
        if item is None:
            return None
        item.resize(cols, rows)
        return item.snapshot().to_dict()

    def stop(self, terminal_id: str) -> Optional[Dict[str, Any]]:
        item = self.get(terminal_id)
        if item is None:
            return None
        item.stop()
        return item.snapshot().to_dict()

    def shutdown(self) -> None:
        with self._lock:
            items = list(self._items.values())
        for item in items:
            item.stop()
