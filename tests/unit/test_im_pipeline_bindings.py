"""IM 上推进流水线工序：确认与进度只在这一次推进期间指向它的事件流，结束（含出错）后释放。"""
from __future__ import annotations

import asyncio

import pytest

from src.gateway.turn_bindings import SLOTS, TurnBindings
from src.im.bridge import IMBridge


def _bridge(tmp_path):
    bridge = IMBridge.__new__(IMBridge)
    bridge.repo_root = str(tmp_path)
    bridge._bindings = TurnBindings(session="im")
    bridge._make_confirm = lambda q: (lambda message: asyncio.sleep(0, result=True))
    bridge._advance_report = lambda run_id, result: f"{run_id}: {result}"
    seen = []

    async def _drain(q, inner):
        await inner
        while not q.empty():
            seen.append(q.get_nowait())

    bridge._drain = _drain
    return bridge, seen


@pytest.mark.asyncio
@pytest.mark.parametrize("fails", [False, True])
async def test_pipeline_advance_releases_its_callbacks(tmp_path, monkeypatch, fails):
    bridge, seen = _bridge(tmp_path)
    during = {}

    def fake_executor(repo_root, *, confirm, on_progress):
        return (confirm, on_progress)

    async def fake_advance(repo_root, run_id, *, execute):
        during.update(confirm=bridge._bindings.confirm, progress=bridge._bindings.progress)
        bridge._bindings.emit_progress("第一道工序")
        if fails:
            raise RuntimeError("工序失败")
        return "ok"

    monkeypatch.setattr("src.agents.pipeline_exec.build_stage_executor", fake_executor)
    monkeypatch.setattr("src.workflow.pipeline.pipeline.advance", fake_advance)
    await bridge._run_pipeline_advance("run-1")

    assert during["confirm"] is not None and during["progress"] is not None
    assert ("progress", "第一道工序") in seen
    assert {slot: getattr(bridge._bindings, slot) for slot in SLOTS} == dict.fromkeys(SLOTS)
    assert await bridge._bindings.ask("推进结束后才来的确认") is False
