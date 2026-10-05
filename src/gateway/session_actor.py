"""Transport-independent ownership of foreground turns and their input queues.

One stable session key owns one active operation, regardless of which observer
submitted it. Storage, sanitization, execution and event publication are injected;
this layer has no model, FastAPI or WebSocket imports.
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

from src.utils.async_ops import request_cancel

MAX_PROMPT_QUEUE = 20
Prompt = dict[str, Any]
logger = logging.getLogger(__name__)


def clean_prompt_item(raw: Any, *, sanitize_images: Callable, sanitize_audio: Callable,
                      sanitize_context: Callable) -> Prompt | None:
    if not isinstance(raw, dict):
        return None
    text = str(raw.get("text") or "").strip()
    images = sanitize_images(raw.get("images"))
    audio = sanitize_audio(raw.get("audio"))
    raw_context = raw.get("context_items") if isinstance(raw.get("context_items"), list) else []
    files = [item.get("path") for item in raw_context if isinstance(item, dict) and "start" not in item]
    selections = [item for item in raw_context if isinstance(item, dict) and "start" in item]
    context_items = sanitize_context({"context_files": files, "context_selections": selections})
    if not text and not images and not audio and not context_items:
        return None
    item_id = str(raw.get("id") or "")[:64]
    if not item_id:
        return None
    version = raw.get("version")
    return {
        "id": item_id,
        "rid": str(raw.get("rid"))[:64] if raw.get("rid") is not None else None,
        "version": max(0, int(version)) if isinstance(version, int) and not isinstance(version, bool) else 0,
        "text": text, "mode": "build" if raw.get("mode") == "build" else "plan",
        "created_at": str(raw.get("created_at") or "")[:80],
        "images": images, "audio": audio, "context_items": context_items,
        "want_reasoning": bool(raw.get("want_reasoning")),
    }


def public_prompt_item(item: Prompt, position: int = 0) -> Prompt:
    return {"id": item["id"], "version": int(item.get("version") or 0),
            "text": str(item.get("text") or ""), "mode": "build" if item.get("mode") == "build" else "plan",
            "position": position, "created_at": str(item.get("created_at") or ""),
            "context_count": len(item.get("context_items") or [])}


def new_prompt_item(*, text: str, mode: str, images: list, audio: list, rid: str | None,
                    want_reasoning: bool, context_items: list) -> Prompt:
    return {
        "id": rid or ("turn-" + uuid.uuid4().hex[:24]), "rid": rid, "version": 0,
        "text": text, "mode": "build" if mode == "build" else "plan",
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "images": images, "audio": audio, "context_items": context_items,
        "want_reasoning": bool(want_reasoning),
    }


@dataclass(frozen=True)
class ActorContext:
    key: str
    get_session: Callable[[], dict]
    touch: Callable[[], None]
    persist: Callable[[], None]
    publish: Callable[[], Awaitable[None]]
    execute: Callable[[Prompt], Awaitable[None]]
    cancel_unstarted: Callable[[Prompt, str | None], Awaitable[None]] | None = None


class SessionActors:
    def __init__(self, *, clean_item: Callable[[Any], Prompt | None], max_queue: int = MAX_PROMPT_QUEUE):
        self.clean_item = clean_item
        self.max_queue = max_queue
        self.tasks: dict[str, Any] = {}
        self.running: dict[str, Prompt] = {}
        self.priority: dict[str, Prompt] = {}
        self.stop_reasons: dict[str, str] = {}
        self._operation_kind: dict[str, str] = {}
        self.auto_stopped: set[str] = set()

    @property
    def background(self) -> dict[str, asyncio.Task]:
        return {key: self.tasks[key] for key, kind in self._operation_kind.items()
                if kind.startswith("background") and key in self.tasks}

    def busy(self, key: str) -> bool:
        task = self.tasks.get(key)
        if task is None:
            self._operation_kind.pop(key, None)
            return False
        return key in self._operation_kind or not task.done()

    def queue(self, context: ActorContext) -> list[Prompt]:
        session = context.get_session()
        raw = session.get("prompt_queue")
        cleaned = []
        if isinstance(raw, list):
            for value in raw[:self.max_queue]:
                item = self.clean_item(value)
                if item is not None and all(existing["id"] != item["id"] for existing in cleaned):
                    cleaned.append(item)
        session["prompt_queue"] = cleaned
        return cleaned

    def snapshot(self, context: ActorContext) -> dict:
        running = self.running.get(context.key)
        return {"items": [public_prompt_item(item, index) for index, item in enumerate(self.queue(context))],
                "running": public_prompt_item(running) if running else None}

    async def enqueue(self, context: ActorContext, item: Prompt) -> str:
        queue = self.queue(context)
        running = self.running.get(context.key)
        if any(queued["id"] == item["id"] for queued in queue) or (running and running["id"] == item["id"]):
            await context.publish()
            return "duplicate"
        # A foreground operation not yet entered must be restorable on stop.
        reserved = self._operation_kind.get(context.key) in {"foreground_pending", "foreground_cleanup"}
        if len(queue) >= self.max_queue - int(reserved):
            return "full"
        queue.append(item)
        context.touch()
        context.persist()
        if self._operation_kind.get(context.key, "").startswith("background"):
            self.cancel(context.key, reason="foreground")
        await context.publish()
        return "queued"

    async def start(self, context: ActorContext, item: Prompt) -> bool:
        if self.busy(context.key):
            return False
        self.running[context.key] = item
        self.auto_stopped.discard(context.key)
        context.touch()
        context.persist()
        # Reserve before yielding, so another observer sees the same busy actor.
        self._launch(context, lambda: context.execute(item), kind="foreground", pending_item=item)
        await context.publish()
        return True

    async def start_background(self, context: ActorContext,
                               execute: Callable[[], Awaitable[None]], *,
                               on_cancel_before_start: Callable[[str | None], None] | None = None) -> bool:
        """Reserve the same actor slot; foreground queue and explicit stop win."""
        key = context.key
        if (self.busy(key) or self.queue(context) or key in self.priority
                or key in self.auto_stopped):
            return False

        self._launch(context, execute, kind="background", on_cancel_before_start=on_cancel_before_start)
        await context.publish()
        return True

    def _launch(self, context: ActorContext, execute: Callable[[], Awaitable[None]], *,
                kind: str, pending_item: Prompt | None = None,
                on_cancel_before_start: Callable[[str | None], None] | None = None) -> None:
        """Single lifecycle wrapper for foreground turns and background checks."""
        key = context.key
        entered = False

        async def finish(task):
            if self.tasks.get(key) is not task:
                return
            reason = self.release(key, task)
            await self.advance(context, reason=reason)

        async def run():
            nonlocal entered
            entered = True
            self._operation_kind[key] = kind
            try:
                await execute()
            finally:
                await finish(asyncio.current_task())

        task = asyncio.create_task(run())
        self.tasks[key] = task
        self._operation_kind[key] = kind + "_pending"

        def done(completed):
            # Retrieve errors; the injected executor owns durable failure state.
            if not completed.cancelled():
                completed.exception()
            if self.tasks.get(key) is completed:
                # Cancellation before the coroutine's first timeslice skips its
                # finally. Reserve cleanup before yielding to other observers.
                cleanup = asyncio.create_task(finish_cleanup())
                self.tasks[key] = cleanup
                self._operation_kind[key] = kind + "_cleanup"
                cleanup.add_done_callback(lambda task: None if task.cancelled() else task.exception())

        async def finish_cleanup():
            try:
                if task.cancelled() and key not in self.stop_reasons:
                    self.stop_reasons[key] = "stop"
                    self.auto_stopped.add(key)
                if not entered and pending_item is not None:
                    queue = self.queue(context)
                    if not any(item["id"] == pending_item["id"] for item in queue):
                        queue.insert(0, pending_item)
                    context.persist()
                    if context.cancel_unstarted is not None:
                        await context.cancel_unstarted(pending_item, self.stop_reasons.get(key))
                if not entered and task.cancelled() and on_cancel_before_start is not None:
                    try:
                        on_cancel_before_start(self.stop_reasons.get(key))
                    except Exception:  # noqa: BLE001 - cleanup must still release the actor.
                        logger.exception("Background pre-start cancellation could not be persisted")
            finally:
                await finish(asyncio.current_task())

        task.add_done_callback(done)

    async def advance(self, context: ActorContext, *, reason: str | None) -> None:
        if self.busy(context.key):
            return  # A stale completion must not clear or dequeue a newer turn.
        self.running.pop(context.key, None)
        if reason in ("stop", "disconnect"):
            context.persist()
            await context.publish()
            return
        item = self.priority.pop(context.key, None)
        queue = self.queue(context)
        if item is None and queue:
            item = queue.pop(0)
        context.persist()
        if item is None or not await self.start(context, item):
            await context.publish()

    async def resume(self, context: ActorContext) -> None:
        if not self.busy(context.key) and (self.priority.get(context.key) or self.queue(context)):
            await self.advance(context, reason=None)

    async def remove(self, context: ActorContext, item_id: str) -> None:
        queue = self.queue(context)
        queue[:] = [item for item in queue if item["id"] != item_id]
        context.touch()
        context.persist()
        await context.publish()

    async def send_now(self, context: ActorContext, item_id: str) -> None:
        queue = self.queue(context)
        selected = next((item for item in queue if item["id"] == item_id), None)
        if selected is None:
            await context.publish()
            return
        queue.remove(selected)
        previous = self.priority.get(context.key)
        if previous is not None:
            queue.insert(0, previous)
        self.priority[context.key] = selected
        context.touch()
        context.persist()
        if not self.cancel(context.key, reason="send_now"):
            self.priority.pop(context.key, None)
            await self.start(context, selected)
        else:
            await context.publish()

    def cancel(self, key: str, *, reason: str = "stop") -> bool:
        if reason in {"stop", "disconnect"}:
            self.auto_stopped.add(key)
        task = self.tasks.get(key)
        if task is not None and (key in self._operation_kind or not task.done()):
            if key in self.running or key in self._operation_kind:
                self.stop_reasons[key] = reason
            if task.done() or self._operation_kind.get(key, "").endswith("_cleanup"):
                return True  # Cleanup owns the slot; let it apply the latest stop reason.
            request_cancel(task)
            return True
        return False

    def release(self, key: str, task: asyncio.Task | None) -> str | None:
        if self.tasks.get(key) is task:
            self.tasks.pop(key, None)
            self._operation_kind.pop(key, None)
            return self.stop_reasons.pop(key, None)
        return None

    def forget(self, key: str) -> bool:
        """Explicit deletion clears transient state only after lifecycle cleanup."""
        if self.busy(key):
            return False
        self.tasks.pop(key, None)
        self.running.pop(key, None)
        self.priority.pop(key, None)
        self.stop_reasons.pop(key, None)
        self._operation_kind.pop(key, None)
        self.auto_stopped.discard(key)
        return True
