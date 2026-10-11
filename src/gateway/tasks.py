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
import logging
import os
import time
import threading
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional
from src.utils.async_ops import cancel_requested, request_cancel
from src.utils.ids import safe_id

_DIRNAME = "tasks"
_MAX_LOG = 200                                    # 进度日志只留尾部 N 行
_PROGRESS_SAVE_INTERVAL = 2.0                     # 进度写盘节流（秒）；终态/状态转换不受节流、必写
MAX_SCAN_ENTRIES = 512
MAX_SCAN_RECORD_BYTES = 256 * 1024

# 任务状态：queued（已提交排队）→ running（执行中，崩溃留在此态）→
#           done / failed / cancelled（终态）；启动扫描把残留 running 改 interrupted（可 resume 续跑）。
_TERMINAL = ("done", "failed", "cancelled", "paused", "interrupted")
TASK_STATE_LOCK = threading.RLock()  # Single-process domain read/modify/write transactions.
_LOGGER = logging.getLogger(__name__)


class TaskBlocked(Exception):
    """An injected worker has cleaned up and durably staged its input request."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def bg_concurrency() -> int:
    """后台任务并发上限（env VORTOCODE_BG_TASKS，默认 2，下限 1）。"""
    try:
        return max(1, int(os.getenv("VORTOCODE_BG_TASKS", "2")))
    except (TypeError, ValueError):
        return 2


def _clean_id(tid: str) -> Optional[str]:
    return safe_id(tid)


@dataclass
class BackgroundTask:
    id: str
    kind: str                                    # "dev"（跑一句话 dev 流水线）等
    prompt: str
    status: str = "queued"
    owner_session: str = ""                       # 发起任务的稳定会话键（sid-*）；终态交接只唤醒它
    goal_id: str = ""                            # 关联的一等 Goal 合同（完成任务不等于达成目标）
    plan_id: str = ""                            # 关联的 C1 dev_plan（可 dev_resume 续跑）
    branch: str = ""                            # 产出分支
    parent_task_id: str = ""                    # 暂停/中断后恢复时保留任务血缘
    result: str = ""                            # 最终结论尾部
    error: str = ""
    collaboration: dict = field(default_factory=dict)  # Delegation state; legacy tasks remain unchanged.
    handoff_receipt: dict = field(default_factory=dict)  # Owner acknowledgement of a specific terminal result.
    continuation: dict = field(default_factory=dict)  # Explicit result-bound check grants and attempts.
    recovery: dict = field(default_factory=dict)  # Durable, single-use development resumption contract.
    development: dict = field(default_factory=dict)  # Bounded, plan/block-bound development questions.
    dependencies: dict = field(default_factory=dict)  # Explicit read-task requirements and consumed versions.
    chain_budget: dict = field(default_factory=dict)  # Root execution allowances or a member's fixed root identity.
    log: List[str] = field(default_factory=list)   # 进度尾部
    created: str = ""
    updated: str = ""

    @staticmethod
    def new(kind: str, prompt: str, *, tid: Optional[str] = None, goal_id: str = "",
            plan_id: str = "", parent_task_id: str = "", owner_session: str = "",
            recovery: dict | None = None, development: dict | None = None) -> "BackgroundTask":
        now = _now()
        return BackgroundTask(id=tid or ("task-" + uuid.uuid4().hex[:10]), kind=kind,
                              prompt=prompt, owner_session=owner_session,
                              goal_id=goal_id, plan_id=plan_id,
                              parent_task_id=parent_task_id,
                              recovery=dict(recovery or {}),
                              development=dict(development or {}),
                              created=now, updated=now)

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
@dataclass(frozen=True)
class LedgerScan:
    tasks: tuple[BackgroundTask, ...]
    scanned: int
    skipped: int
    truncated: bool
    readable: bool = True

    @property
    def complete(self) -> bool:
        return self.readable and not self.truncated and self.skipped == 0


class TaskLedger:
    def __init__(self, repo_root: str):
        self.repo_root = str(repo_root)

    def _dir(self) -> Path:
        return Path(self.repo_root) / ".vortocode" / _DIRNAME

    def _path(self, tid: str) -> Path:
        return self._dir() / f"{tid}.json"

    def create(
        self,
        kind: str,
        prompt: str,
        *,
        goal_id: str = "",
        plan_id: str = "",
        parent_task_id: str = "",
        owner_session: str = "",
        tid: str | None = None,
        recovery: dict | None = None,
        development: dict | None = None,
    ) -> BackgroundTask:
        task = BackgroundTask.new(
            kind,
            prompt,
            goal_id=goal_id,
            plan_id=plan_id,
            parent_task_id=parent_task_id,
            owner_session=owner_session,
            tid=tid,
            recovery=recovery, development=development,
        )
        with TASK_STATE_LOCK:
            if tid is not None and (_clean_id(tid) != tid or self._path(tid).exists()):
                raise ValueError("任务 ID 无效或已存在")
            if not self.save(task):
                raise OSError("任务未能持久化；未排队执行")
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
            from src.utils.state_dir import ensure_state_gitignore
            ensure_state_gitignore(self.repo_root)       # .vortocode/ 自忽略：台账不污染目标仓库 git status
            p.parent.mkdir(parents=True, exist_ok=True)
            tmp = p.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(task.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.replace(p)
            return True
        except (OSError, TypeError, ValueError):
            return False

    def load(self, tid: str, *, max_bytes: int | None = None) -> Optional[BackgroundTask]:
        cid = _clean_id(tid)
        if not isinstance(tid, str) or cid is None or cid != tid:
            return None
        p = self._path(cid)
        if not p.is_file():
            return None
        try:
            if max_bytes is None:
                data = json.loads(p.read_text(encoding="utf-8"))
            else:
                if type(max_bytes) is not int or not 1 <= max_bytes <= MAX_SCAN_RECORD_BYTES:
                    raise ValueError("任务记录读取字节上限无效")
                with p.open("rb") as record:
                    payload = record.read(max_bytes + 1)
                if len(payload) > max_bytes:
                    return None
                data = json.loads(payload)
        except (OSError, ValueError, RecursionError):
            return None
        if not isinstance(data, dict):
            return None
        # A damaged record must not redirect updates into another task's file.
        if not isinstance(data.get("id"), str) or data["id"] != cid:
            return None
        try:
            return BackgroundTask.from_dict(data)
        except (TypeError, ValueError):
            return None

    def has_record(self, tid: str) -> bool:
        return isinstance(tid, str) and _clean_id(tid) == tid and self._path(tid).exists()

    def scan_inventory(self):
        from src.gateway.task_scan import inventory
        return inventory(self)

    def read_scan_record(self, entry):
        from src.gateway.task_scan import read_record
        return read_record(self, entry)

    def load_scan(self, scan_id: str):
        from src.gateway.task_scan import load
        return load(self, scan_id)

    def read_scan_checkpoint(self, scan_id: str, *, max_bytes: int):
        from src.gateway.task_scan import read_checkpoint
        return read_checkpoint(self, scan_id, max_bytes=max_bytes)

    def save_scan(self, state: dict) -> None:
        from src.gateway.task_scan import save
        save(self, state)

    def scan_ids(self) -> list[str]:
        from src.gateway.task_scan import ids
        return ids(self)

    def load_scan_archive(self, archive_id: str):
        from src.gateway.task_scan import load_archive
        return load_archive(self, archive_id)

    def save_scan_archive(self, artifact: dict) -> None:
        from src.gateway.task_scan import save_archive
        save_archive(self, artifact)

    def scan_archive_ids(self) -> list[str]:
        from src.gateway.task_scan import archive_ids
        return archive_ids(self)

    def load_scan_retirement(self, scan_id: str):
        from src.gateway.task_scan import load_retirement
        return load_retirement(self, scan_id)

    def save_scan_retirement(self, record: dict) -> None:
        from src.gateway.task_scan import save_retirement
        save_retirement(self, record)

    def scan_retirement_ids(self) -> list[str]:
        from src.gateway.task_scan import retirement_ids
        return retirement_ids(self)

    def list(self, limit: Optional[int] = None) -> List[BackgroundTask]:
        d = self._dir()
        if not d.is_dir():
            return []
        paths: List[tuple[float, Path]] = []
        for p in d.glob("*.json"):
            try:
                paths.append((p.stat().st_mtime, p))
            except OSError:
                paths.append((0.0, p))
        paths.sort(key=lambda item: item[0], reverse=True)
        if limit is not None:
            paths = paths[:max(0, int(limit))]
        out: List[BackgroundTask] = []
        for _mtime, path in paths:
            task = self.load(path.stem)
            if task is not None:
                out.append(task)
        return out

    def scan(self, limit: int = MAX_SCAN_ENTRIES) -> LedgerScan:
        """Bound directory work and record reads; never imply a partial scan is complete.

        Directory order is not a pagination contract. A caller must report
        truncation/unreadable records instead of claiming every task was found.
        """
        if type(limit) is not int or not 1 <= limit <= MAX_SCAN_ENTRIES:
            raise ValueError("任务扫描上限必须为 1 到 512 的整数")
        tasks, scanned, skipped = [], 0, 0
        try:
            with os.scandir(self._dir()) as entries:
                for entry in entries:
                    if scanned == limit:
                        return LedgerScan(tuple(tasks), scanned, skipped, True)
                    scanned += 1
                    if not entry.name.endswith(".json"):
                        continue
                    try:
                        if (entry.is_symlink() or not entry.is_file()
                                or entry.stat().st_size > MAX_SCAN_RECORD_BYTES):
                            skipped += 1
                            continue
                        task = self.load(entry.name[:-5], max_bytes=MAX_SCAN_RECORD_BYTES)
                    except OSError:
                        task = None
                    if task is None:
                        skipped += 1
                    else:
                        tasks.append(task)
        except FileNotFoundError:
            if scanned:
                return LedgerScan(tuple(tasks), scanned, skipped, False, False)
        except OSError:
            return LedgerScan(tuple(tasks), scanned, skipped, False, False)
        return LedgerScan(tuple(tasks), scanned, skipped, False)

    def recover_interrupted(self) -> List[BackgroundTask]:
        """启动时调用：把上次崩在半路（仍标 running）的任务改标 interrupted（可交 dev_resume 续跑）。"""
        recovered = []
        for t in self.list():
            dispatch = t.collaboration.get("dispatch", {}) if isinstance(t.collaboration, dict) else {}
            if t.status == "running" or (
                t.status == "queued" and (
                    isinstance(dispatch, dict) and dispatch.get("source") == "api"
                    or isinstance(t.recovery, dict) and t.recovery.get("source_task_id")
                    or isinstance(t.development, dict) and t.development.get("execution_revision")
                )
            ):
                t.status = "interrupted"
                if t.development:
                    from src.gateway.task_questions import cancel_open_questions
                    cancel_open_questions(t)
                    t.development["waiting"] = ""
                if not self.save(t):
                    raise OSError("任务中断状态未能持久化")
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
        self._closing = False
        self._pause_requested: set[str] = set()
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
    async def shutdown(self):
        """Drain executing workers before relinquishing workspace ownership."""
        self._closing = True
        pending = list(self._running.values())
        for future in pending:
            future.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)

    def recover(self) -> List[BackgroundTask]:
        self._closing = False
        recovered = self.ledger.recover_interrupted()
        from src.gateway.continuations import recover_claims
        recovered.extend(recover_claims(self.ledger))
        for task in recovered:
            self._notify(task)                         # Goal/WS 同步看到 running→interrupted
        return recovered

    async def submit(self, prompt: str, kind: str = "dev", *, goal_id: str = "",
                     plan_id: str = "", parent_task_id: str = "",
                     owner_session: str = "") -> BackgroundTask:
        if self._closing:
            raise RuntimeError("Runtime is shutting down")
        task = self.ledger.create(
            kind,
            prompt,
            goal_id=goal_id,
            plan_id=plan_id,
            parent_task_id=parent_task_id,
            owner_session=owner_session,
        )
        # write-ahead：排队即落盘；goal_id/plan_id 在第一次通知前已经固定，避免订阅者串单。
        self.enqueue_worker(task.id)
        return task

    def _save_and_notify(self, task: BackgroundTask) -> None:
        if not self.ledger.save(task):
            raise OSError("任务状态未能持久化；未继续执行或报告成功")
        self._notify(task)

    def enqueue_worker(self, tid: str) -> None:
        """Run an already durable queued task with the ordinary worker/lifecycle."""
        if self._closing:
            raise RuntimeError("Runtime is shutting down")
        with TASK_STATE_LOCK:
            task = self.ledger.load(tid)
            if task is None or task.status != "queued" or self.is_active(tid):
                raise ValueError("任务必须已持久排队且尚未执行")
            future = asyncio.create_task(self._run(task))
            self._running[tid] = future

            def done(completed):
                try:
                    if completed.cancelled():
                        current = self.ledger.load(tid)
                        if current is not None and current.status in {"queued", "running"}:
                            current.status = "paused" if tid in self._pause_requested else "cancelled"
                            self._save_and_notify(current)
                    else:
                        error = completed.exception()
                        if error is not None:
                            _LOGGER.error("后台任务 %s 未能完成持久状态更新：%s", tid, error)
                except OSError as error:
                    _LOGGER.error("后台任务 %s 取消状态未能持久化：%s", tid, error)
                finally:
                    self._pause_requested.discard(tid)
                    if self._running.get(tid) is completed:
                        self._running.pop(tid, None)

            future.add_done_callback(done)
            self._notify(task)

    @property
    def active_count(self) -> int:
        return sum(not task.done() for task in self._running.values())

    def enqueue_existing(self, tid: str, execute: Callable[[], Awaitable[None]], *,
                         on_cancel: Callable[[], None], on_failure: Callable[[Exception], None]) -> bool:
        """Use the shared pool for durable work whose lifecycle is owned by another service.

        The runner owns concurrency/cancellation; injected callbacks own state.
        Register synchronously so two callers cannot schedule the same task.
        """
        if self._closing:
            raise RuntimeError("Runtime is shutting down")
        if self.is_active(tid):
            return False
        if self.ledger.load(tid) is None:
            raise ValueError("任务必须先持久化")

        async def run():
            try:
                async with self._sem:
                    await execute()
            except asyncio.CancelledError:
                raise
            except Exception as error:  # noqa: BLE001
                on_failure(error)

        task = asyncio.create_task(run())
        self._running[tid] = task

        def done(future):
            # Cancelled before its first timeslice still needs a durable receipt.
            try:
                if future.cancelled():
                    on_cancel()
                else:
                    future.exception()  # Retrieve errors from a failed persistence callback.
            finally:
                if self._running.get(tid) is future:
                    self._running.pop(tid, None)
        task.add_done_callback(done)
        return True

    async def _run(self, task: BackgroundTask) -> None:
        last_save = [0.0]

        def _progress(msg: str) -> None:
            task.log.append(str(msg))
            now = time.monotonic()
            if now - last_save[0] >= _PROGRESS_SAVE_INTERVAL:   # 进度写盘节流，避免频繁 IO
                last_save[0] = now
                if not self.ledger.save(task):
                    raise OSError("任务进度未能持久化；停止执行")
            self._notify(task)

        try:
            async with self._sem:                        # 并发上限：超额排队等空位
                task.status = "running"
                self._save_and_notify(task)              # write-ahead：真开跑前先落 running
                result = await self._worker(task, _progress)
                task.result = str(result or "")[-4000:]
                task.status = "done"
        except TaskBlocked:
            task.status, task.result, task.error = "blocked", "", ""
        except asyncio.CancelledError:
            task.status = "paused" if task.id in self._pause_requested else "cancelled"
            if task.development:
                from src.gateway.task_questions import cancel_open_questions
                cancel_open_questions(task)
                task.development["waiting"] = ""
            self._save_and_notify(task)
            raise
        except Exception as e:  # noqa: BLE001
            task.status = "failed"
            task.error = str(e)[-1000:]
            if task.development:
                from src.gateway.task_questions import cancel_open_questions
                cancel_open_questions(task)
                task.development["waiting"] = ""
        self._save_and_notify(task)                      # 终态落盘成功后才报告

    def cancel(self, tid: str) -> bool:
        t = self._running.get(tid)
        if t is not None and not t.done():
            if not cancel_requested(t):
                request_cancel(t)
            return True
        return False

    async def pause(self, tid: str) -> Optional[BackgroundTask]:
        """Cooperatively stop an active task while preserving its resumable plan."""
        running = self._running.get(tid)
        if running is None or running.done():
            return None
        self._pause_requested.add(tid)
        request_cancel(running)
        try:
            try:
                await running
            except asyncio.CancelledError:
                pass
            task = self.ledger.load(tid)
            if task is not None and task.status in {"queued", "running"}:
                # The cancellation callback may not have had its first timeslice.
                task.status = "paused"
                self._save_and_notify(task)
            return task
        finally:
            self._pause_requested.discard(tid)
            if self._running.get(tid) is running:
                self._running.pop(tid, None)

    def get(self, tid: str) -> Optional[BackgroundTask]:
        return self.ledger.load(tid)

    def list(self) -> List[BackgroundTask]:
        return self.ledger.list()

    def is_active(self, tid: str) -> bool:
        t = self._running.get(tid)
        return t is not None and not t.done()

    async def join(self, tid: str) -> None:
        """等某个后台任务的 asyncio 任务真正跑完（供一次性/CLI 场景 drain，别让进程退出前把它取消掉）。

        已完成/未知 id → 立即返回。取消异常吞掉（join 只为等落地，不重抛内部取消）。
        """
        t = self._running.get(tid)
        if t is None:
            return
        try:
            await t
        except asyncio.CancelledError:
            pass
