"""Shared opt-in scheduler; transport supplies delivery and execution hooks."""
import os
import logging

logger = logging.getLogger(__name__)


async def scheduler_loop(stop_event, *, cwd, runner, notify):
    import asyncio
    from datetime import datetime
    from src.scheduling import cron as _cron
    from src.scheduling import heartbeat as _hb

    cron_on = os.getenv("VORTOCODE_CRON", "").strip().lower() in ("1", "true", "yes", "on")
    hb_on = os.getenv("VORTOCODE_HEARTBEAT", "").strip().lower() in ("1", "true", "yes", "on")
    hb_every = _hb.heartbeat_every_seconds()
    last_hb = 0.0

    async def _submit(item):
        await runner.submit(item, kind="dev")

    _notify = notify

    while not stop_event.is_set():
        now = datetime.now()
        if cron_on:
            try:
                await _cron.run_due(cwd, now, notify=_notify)   # 到点的作业各自隔离跑，结果按 announce 投递
            except Exception as error:  # noqa: BLE001 - heartbeat owns an independent check.
                logger.warning("Cron scheduler tick failed (%s)", type(error).__name__)
        if hb_on:
            try:
                mono = asyncio.get_event_loop().time()
                if mono - last_hb >= hb_every:
                    last_hb = mono
                    await _hb.run_heartbeat(cwd, submit=_submit, notify=_notify, hour=now.hour)
            except Exception as error:  # noqa: BLE001 - keep the scheduler alive, without immediate model retries.
                logger.warning("Heartbeat tick failed (%s)", type(error).__name__)
        try:
            # 安静时段结束后，把攒着的通知合成一条推出去（时段内或没有攒的：什么都不做）
            from src.gateway.im_runtime import notify_owner
            from src.scheduling.notices import flush_quiet_digest
            await flush_quiet_digest(cwd, lambda message: notify_owner(message))
        except Exception as error:  # noqa: BLE001 - delivery retries on the next tick.
            logger.warning("Quiet-hours digest failed (%s)", type(error).__name__)
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=60)   # 每分钟 tick 一次（可被停止打断）
        except asyncio.TimeoutError:
            pass
