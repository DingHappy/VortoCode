"""Managed text admission using the original SessionActors and SessionStore.

No second executor, durable waiting queue, automatic replay or delivery claim.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

from src.gateway.im_input_store import InputStore, InputStoreError, fingerprint
from src.gateway.session_actor import ActorContext


class ManagedInputs:
    def __init__(self, bridge, actors):
        self.bridge, self.actors = bridge, actors
        self.store = InputStore(bridge.channel, bridge.adapter.conversation_identity(), project_root=bridge.repo_root)
        self.target = fingerprint([str(Path(bridge.repo_root).resolve()), bridge._sid])
        self.observers = set()
        self.closed = False
        # B1 history without receipts cannot be reconstructed from message text.
        from src.gateway.session_store import load_session
        old = load_session(bridge.repo_root, bridge._sid, strict=True)
        if old and any(old.get(field) for field in ("history", "transcript", "plan", "activities")) \
                and old.get("managed_im") is not True:
            raise InputStoreError("Existing IM history needs explicit migration before managed admission")
        bridge._input_controller = self
        bridge._persist()  # Mark native history before a Web observer can acquire it.

    def busy(self):
        return self.actors.busy(self.bridge._sid)

    def _persist_actor(self):
        # A storage failure still permits cancellation and lifecycle release.
        if not self.store.blocked:
            self.store.flush()

    def _context(self, key, text):
        async def publish():
            pass

        async def execute(_item):
            state = "interrupted"
            try:
                await self.bridge._run_turn(text, images=[], files=[])
                state = "settled"  # local return only, not model success or delivery
            finally:
                try:
                    self.store.settle(key, state)
                except InputStoreError:
                    pass  # Durable claimed receipt remains non-replayable.

        async def cancel_unstarted(_item, _reason):
            self.store.session["prompt_queue"] = []
            try:
                self.store.settle(key, "interrupted")
            except InputStoreError:
                pass

        return ActorContext(self.bridge._sid, lambda: self.store.session, lambda: None,
                            self._persist_actor, publish, execute, cancel_unstarted)

    async def _observe(self, release):
        try:
            while self.busy():
                task = self.actors.tasks.get(self.bridge._sid)
                if task is not None:
                    await asyncio.wait({task})
                await asyncio.sleep(0)  # Include the actor's cold-cancel successor.
        finally:
            release()

    async def receive(self, event, text, images, files, command):
        bridge = self.bridge
        if command in {"/status", "/help"}:
            await bridge._handle_command(text)
            if command == "/status":
                try:
                    self.store.check()
                except InputStoreError:
                    pass
                await bridge._safe_send("输入记录：" + ("暂停，需核对存储" if self.store.blocked else
                    "已满，暂停新输入" if self.store.full else "可用")
                    + f"；本账号待核对 {sum(row['state'] == 'interrupted' for row in self.store.receipts.values())} 条；不自动重跑。")
            return
        release = None
        launched = False
        try:
            if self.closed:
                raise InputStoreError("Binding is stopping")
            key, payload = self.store.identify(event, bridge.channel, bridge.adapter.conversation_identity(), text)
            if self.store.seen(key, payload):
                await bridge._safe_send("这条原始消息已有处理记录，不会再次执行；如需重试，请发一条新消息。")
                return
            rejected = not text or bool(images or files or event.unsupported) or (self.busy() and command != "/stop")
            if not command and not rejected:
                release = bridge._claim_turn()
                rejected = release is None
            self.store.record(key, payload, self.target, "rejected" if rejected else "claimed")
            if rejected:
                await bridge._safe_send("当前执行/清理或附件输入不可受理，这条消息未受理；请稍后发送新的纯文本消息。")
                return
            if command:
                if command == "/stop":
                    active = self.stop()
                    await bridge._safe_send("⏹ 已请求停止，正在核对执行退出。" if active else "当前没有在跑的任务。")
                else:
                    await bridge._handle_command(text)
                self.store.settle(key, "settled")
                return
            context = self._context(key, text)
            launched = await self.actors.start(context, {"id": key, "text": "IM message", "version": 0})
            if not launched:
                self.store.settle(key, "rejected")
                return
            bridge._turn_task = self.actors.tasks[bridge._sid]  # compatibility observation only
            observer = asyncio.create_task(self._observe(release))
            self.observers.add(observer)
            observer.add_done_callback(self.observers.discard)
        except (InputStoreError, OSError, ValueError):
            await bridge._safe_send("输入身份或持久记录不可用，这条消息未受理；请核对存储后再发送新消息。")
        finally:
            if release is not None and not launched:
                release()
                self.actors.forget(bridge._sid)

    def stop(self):
        try:
            return self.actors.cancel(self.bridge._sid, reason="stop")
        except InputStoreError:
            return self.actors.cancel(self.bridge._sid, reason="stop")

    async def close(self):
        self.closed = True
        self.stop()
        while self.busy():
            task = self.actors.tasks.get(self.bridge._sid)
            if task is not None:
                await asyncio.wait({task})
            await asyncio.sleep(0)
        if self.observers:
            await asyncio.gather(*tuple(self.observers))
