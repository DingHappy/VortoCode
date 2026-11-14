"""Only the General host owns staff; other scheduler duties keep their original scope."""

import asyncio
from datetime import timezone

import pytest

from src.scheduling import cron, heartbeat, scheduler, staff_runner


async def two_ticks(tmp_path, monkeypatch, *, cron_enabled=True):
    from src.scheduling import notices

    monkeypatch.setenv("VORTOONE_CRON", "1" if cron_enabled else "0")
    monkeypatch.setenv("VORTOONE_HEARTBEAT", "1")
    monkeypatch.setattr(heartbeat, "heartbeat_every_seconds", lambda: 0)
    calls = []

    class TwoTicks:
        ticks = 0

        def is_set(self):
            return self.ticks == 2

        async def wait(self):
            self.ticks += 1
            if self.ticks < 2:
                raise asyncio.TimeoutError()

    async def run_cron(*args, **kwargs):
        calls.append("cron")

    async def beat(*args, **kwargs):
        calls.append("heartbeat")

    async def digest(*args, **kwargs):
        calls.append("digest")

    monkeypatch.setattr(cron, "run_due", run_cron)
    monkeypatch.setattr(heartbeat, "run_heartbeat", beat)
    monkeypatch.setattr(notices, "flush_quiet_digest", digest)
    await scheduler.scheduler_loop(TwoTicks(), cwd=str(tmp_path), runner=None, notify=None)
    return calls


@pytest.mark.parametrize("scope", ["project", "scratch", None, "unknown-scope"])
async def test_non_general_scheduler_does_not_look_up_staff_or_warn_but_keeps_duties(
    tmp_path, monkeypatch, caplog, scope
):
    if scope is None:
        monkeypatch.delenv("VORTOONE_WORKSPACE_SCOPE", raising=False)
    else:
        monkeypatch.setenv("VORTOONE_WORKSPACE_SCOPE", scope)
    original = staff_runner.get_staff_service
    lookups = []

    def lookup(root):
        lookups.append(root)
        return original(root)  # No staff pool is registered for this host.

    monkeypatch.setattr(staff_runner, "get_staff_service", lookup)
    assert await two_ticks(tmp_path, monkeypatch) == ["cron", "heartbeat", "digest"] * 2
    assert lookups == []
    assert "Routine dispatch unavailable" not in caplog.text


async def test_general_scheduler_still_dispatches_with_its_registered_pool_each_tick(
    tmp_path, monkeypatch
):
    from src.workflow.staff.routines import Routines

    monkeypatch.setenv("VORTOONE_WORKSPACE_SCOPE", "general")
    pool, lookups, dispatched = object(), [], []

    def lookup(root):
        lookups.append(root)
        return pool

    def dispatch(self, staff, now):
        dispatched.append((staff, now.tzinfo))

    monkeypatch.setattr(staff_runner, "get_staff_service", lookup)
    monkeypatch.setattr(Routines, "dispatch", dispatch)
    assert await two_ticks(tmp_path, monkeypatch) == ["cron", "heartbeat", "digest"] * 2
    assert lookups == [str(tmp_path)] * 2
    assert dispatched == [(pool, timezone.utc)] * 2


async def test_general_missing_pool_remains_visible_without_stopping_other_duties(
    tmp_path, monkeypatch, caplog
):
    monkeypatch.setenv("VORTOONE_WORKSPACE_SCOPE", "general")
    assert await two_ticks(tmp_path, monkeypatch) == ["cron", "heartbeat", "digest"] * 2
    assert caplog.text.count("Routine dispatch unavailable (ValueError)") == 2


async def test_general_with_cron_disabled_does_not_look_up_staff(tmp_path, monkeypatch):
    monkeypatch.setenv("VORTOONE_WORKSPACE_SCOPE", "general")
    monkeypatch.setattr(
        staff_runner, "get_staff_service", lambda _: pytest.fail("staff lookup while cron disabled")
    )
    assert await two_ticks(tmp_path, monkeypatch, cron_enabled=False) == ["heartbeat", "digest"] * 2
