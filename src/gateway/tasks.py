"""后台任务 + write-ahead 台账（D1 v1）——把长流水线搬到后台、崩溃可恢复。

`TaskLedger`：每个任务一份 `.vortocode/tasks/<id>.json`，**write-ahead**——提交即写、每次状态转换
先写盘再干活/干完再落地。进程启动时扫描：仍是 running 的（上次崩在半路）改标 interrupted，
其 plan_id 可交给 C1 的 dev_resume 续跑。

`TaskRunner`：进程内 asyncio 任务池，并发上限 `VORTOCODE_BG_TASKS`（默认 2）。业务逻辑经**注入的
worker**（async(task, on_progress)->result_text）解耦——本模块不依赖 MainAgent，可用假 worker 确定性测试；
server / IM 各自注入"建 agent + 跑一回合"的 worker。状态变更经 on_update 回调推给订阅者（WS/IM）。

设计仿 web/session_store（原子 .tmp+replace）与 realtime 的后台回合（asyncio.create_task + 可取消）。
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional

_DIRNAME = "tasks"
_BAD = re.compile(r"[^A-Za-z0-9_-]")
_MAX_LOG = 200                                    # 进度日志只留尾部 N 行
_PROGRESS_SAVE_INTERVAL = 2.0                     # 进度写盘节流（秒）；终态/状态转换不受节流、必写

# 任务状态：queued（已提交排队）→ running（执行中，崩溃留在此态）→
#           done / failed / cancelled（终态）；启动扫描把残留 running 改 interrupted（可 resume 续跑）。
_TERMINAL = ("done", "failed", "cancelled")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def bg_concurrency() -> int:
    """后台任务并发上限（env VORTOCODE_BG_TASKS，默认 2，下限 1）。"""
    try:
        return max(1, int(os.getenv("VORTOCODE_BG_TASKS", "2")))
    except (TypeError, ValueError):
        return 2


def _clean_id(tid: str) -> Optional[str]:
    if not tid:
        return None
    s = _BAD.sub("_", str(tid)).strip("_")
    return s or None


@dataclass
class BackgroundTask:
    id: str
    kind: str                                    # "dev"（跑一句话 dev 流水线）等
    prompt: str
    status: str = "queued"
    plan_id: str = ""                            # 关联的 C1 dev_plan（可 dev_resume 续跑）
    branch: str = ""                            # 产出分支
    result: str = ""                            # 最终结论尾部
    error: str = ""
    log: List[str] = field(default_factory=list)   # 进度尾部
    created: str = ""
    updated: str = ""

    @staticmethod
    def new(kind: str, prompt: str, *, tid: Optional[str] = None) -> "BackgroundTask":
        now = _now()
        return BackgroundTask(id=tid or ("task-" + uuid.uuid4().hex[:10]), kind=kind,
                              prompt=prompt, created=now, updated=now)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @staticmethod
    def from_dict(d: Dict[str, Any]) -> "BackgroundTask":
        known = set(BackgroundTask.__dataclass_fields__)
        return BackgroundTask(**{k: v for k, v in d.items() if k in known})

    def summary(self) -> str:
        s = f"{self.id} · {self.status}"
        if self.branch:
            s += f" · {self.branch}"
        if self.plan_id:
            s += f" · plan={self.plan_id}"
        return s


# --------------------------------------------------------------------- 台账（原子落盘）
class TaskLedger:
    def __init__(self, repo_root: str):
        self.repo_root = str(repo_root)

    def _dir(self) -> Path:
        return Path(self.repo_root) / ".vortocode" / _DIRNAME

    def _path(self, tid: str) -> Path:
        return self._dir() / f"{tid}.json"

    def create(self, kind: str, prompt: str) -> BackgroundTask:
        task = BackgroundTask.new(kind, prompt)
        self.save(task)
        return task

    def save(self, task: BackgroundTask) -> bool:
        tid = _clean_id(task.id)
        if tid is None:
            return False
        task.id = tid
        task.updated = _now()
        task.log = task.log[-_MAX_LOG:]
        p = self._path(tid)
        try:
            from src.agents.dev_plan import ensure_state_gitignore
            ensure_state_gitignore(self.repo_root)       # .vortocode/ 自忽略：台账不污染目标仓库 git status
            p.parent.mkdir(parents=True, exist_ok=True)
            tmp = p.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(task.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.replace(p)
            return True
        except (OSError, TypeError, ValueError):
            return False

    def load(self, tid: str) -> Optional[BackgroundTask]:
        cid = _clean_id(tid)
        if cid is None:
            return None
        p = self._path(cid)
        if not p.is_file():
            return None
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if not isinstance(data, dict):
            return None
        try:
            return BackgroundTask.from_dict(data)
        except (TypeError, ValueError):
            return None

    def list(self) -> List[BackgroundTask]:
        d = self._dir()
        if not d.is_dir():
            return []
        out: List[tuple] = []
        for p in d.glob("*.json"):
            t = self.load(p.stem)
            if t is not None:
                try:
                    out.append((p.stat().st_mtime, t))
                except OSError:
                    out.append((0.0, t))
        out.sort(key=lambda x: x[0], reverse=True)
        return [t for _m, t in out]

    def recover_interrupted(self) -> List[BackgroundTask]:
        """启动时调用：把上次崩在半路（仍标 running）的任务改标 interrupted（可交 dev_resume 续跑）。"""
        recovered = []
        for t in self.list():
            if t.status == "running":
                t.status = "interrupted"
                self.save(t)
                recovered.append(t)
        return recovered


# --------------------------------------------------------------------- 运行时
Worker = Callable[[BackgroundTask, Callable[[str], None]], Awaitable[str]]


class TaskRunner:
    """进程内后台任务池：submit 即排队 + 后台跑；并发受 VORTOCODE_BG_TASKS 上限约束；可取消、可订阅。

    worker(task, on_progress)->result_text：业务逻辑（建 agent + 跑一回合），可在其中设 task.plan_id/branch。
    on_update(task)：每次状态/进度变更回调（推 WS/IM）；出错不影响任务本身。
    """
    def __init__(self, repo_root: str, worker: Worker, *,
                 max_concurrent: Optional[int] = None,
                 on_update: Optional[Callable[[BackgroundTask], None]] = None):
        self.repo_root = str(repo_root)
        self.ledger = TaskLedger(repo_root)
        self._worker = worker
        self._sem = asyncio.Semaphore(max_concurrent or bg_concurrency())
        self._running: Dict[str, asyncio.Task] = {}
        self._subs: set = set()
        if on_update is not None:
            self._subs.add(on_update)

    # -------- 订阅（WS/IM 推送）
    def subscribe(self, cb: Callable[[BackgroundTask], None]) -> Callable[[], None]:
        self._subs.add(cb)
        return lambda: self._subs.discard(cb)

    def _notify(self, task: BackgroundTask) -> None:
        for cb in list(self._subs):
            try:
                cb(task)
            except Exception:  # noqa: BLE001 —— 订阅者出错不拖垮任务
                pass

    # -------- 生命周期
    def recover(self) -> List[BackgroundTask]:
        return self.ledger.recover_interrupted()

    async def submit(self, prompt: str, kind: str = "dev") -> BackgroundTask:
        task = self.ledger.create(kind, prompt)          # write-ahead：排队即落盘
        self._notify(task)
        self._running[task.id] = asyncio.create_task(self._run(task))
        return task

    async def _run(self, task: BackgroundTask) -> None:
        last_save = [0.0]

        def _progress(msg: str) -> None:
            task.log.append(str(msg))
            now = time.monotonic()
            if now - last_save[0] >= _PROGRESS_SAVE_INTERVAL:   # 进度写盘节流，避免频繁 IO
                last_save[0] = now
                self.ledger.save(task)
            self._notify(task)

        try:
            async with self._sem:                        # 并发上限：超额排队等空位
                task.status = "running"
                self.ledger.save(task)                   # write-ahead：真开跑前先落 running
                self._notify(task)
                result = await self._worker(task, _progress)
                task.result = str(result or "")[-4000:]
                task.status = "done"
        except asyncio.CancelledError:
            task.status = "cancelled"
            self.ledger.save(task)
            self._notify(task)
            raise
        except Exception as e:  # noqa: BLE001
            task.status = "failed"
            task.error = str(e)[-1000:]
        finally:
            self._running.pop(task.id, None)
        self.ledger.save(task)                           # 终态落盘
        self._notify(task)

    def cancel(self, tid: str) -> bool:
        t = self._running.get(tid)
        if t is not None and not t.done():
            t.cancel()
            return True
        return False

    def get(self, tid: str) -> Optional[BackgroundTask]:
        return self.ledger.load(tid)

    def list(self) -> List[BackgroundTask]:
        return self.ledger.list()

    def is_active(self, tid: str) -> bool:
        t = self._running.get(tid)
        return t is not None and not t.done()
