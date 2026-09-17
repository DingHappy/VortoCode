"""流水线的跨进程互斥与持久写入。锁随进程退出释放，不用 PID/超时猜执行者是否存活。"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path

from src.utils.ids import safe_id
from src.utils.state_dir import ensure_state_gitignore


class PipelineBusy(RuntimeError):
    pass


@contextmanager
def pipeline_lock(repo_root: str, run_id: str, *, kind: str = "operation"):
    if not safe_id(run_id):
        raise ValueError("无效的流水线运行 id")
    ensure_state_gitignore(repo_root)
    directory = Path(repo_root).resolve() / ".vortocode" / "pipeline_runs" / ".locks"
    directory.mkdir(parents=True, exist_ok=True)
    # 不删除锁文件：删除后另一个进程可能锁住新 inode，形成两个执行者。
    with (directory / f"{run_id}.{kind}.lock").open("a+b") as handle:
        if sys.platform == "win32":
            import msvcrt
            if os.fstat(handle.fileno()).st_size == 0:
                handle.write(b"\0")
                handle.flush()
            handle.seek(0)
            try:
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as exc:
                raise PipelineBusy("这条流水线正在处理，请稍后刷新") from exc
        else:
            import fcntl
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise PipelineBusy("这条流水线正在处理，请稍后刷新") from exc
        try:
            yield
        finally:
            if sys.platform == "win32":
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def write_json(path: Path, data: dict) -> None:
    """同目录临时文件 + fsync + replace；异常原样交给调用方，不宣称保存成功。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(name, path)
        if os.name != "nt":
            directory = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
    finally:
        Path(name).unlink(missing_ok=True)
