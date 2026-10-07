"""Lifetime ownership on one host; never delete a held lock's inode."""
from __future__ import annotations

import os
import stat
from pathlib import Path


class ProcessLease:
    def __init__(self, path):
        self.path = Path(path)
        self._fd = None

    def acquire(self):
        if self._fd is not None:
            return self
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.path, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode):
                raise RuntimeError("Runtime ownership lock must be a regular file")
            if os.name == "nt":
                import msvcrt
                if info.st_size == 0:
                    os.write(fd, b"0")
                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except Exception as exc:
            os.close(fd)
            raise RuntimeError("Another Runtime owns this workspace or IM channel") from exc
        self._fd = fd
        return self

    def release(self):
        if self._fd is not None:
            os.close(self._fd)
            self._fd = None

    def __enter__(self):
        return self.acquire()

    def __exit__(self, *_):
        self.release()


def channel_lease(channel: str, identity: str) -> ProcessLease:
    """Channel names and credentials never become arbitrary filesystem paths."""
    import hashlib
    if channel not in {"telegram", "dingtalk"}:
        raise ValueError("Unknown IM channel")
    directory = Path(os.getenv("VORTOCODE_IM_LOCK_DIR") or
                     Path.home() / ".vortocode" / "channel-locks")
    digest = hashlib.sha256(str(identity).encode()).hexdigest()
    return ProcessLease(directory / f"{channel}-{digest}.lock")
