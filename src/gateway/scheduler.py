"""Shared opt-in scheduler; transport supplies delivery and execution hooks."""
import os


async def scheduler_loop(stop_event, *, cwd, runner, notify):
    import asyncio
    from datetime import datetime
    from src.gateway import cron as _cron
    from src.gateway import heartbeat as _hb

    cron_on = os.getenv("VORTOCODE_CRON", "").strip().lower() in ("1", "true", "yes", "on")
    hb_on = os.getenv("VORTOCODE_HEARTBEAT", "").strip().lower() in ("1", "true", "yes", "on")
    hb_every = _hb.heartbeat_every_seconds()
    last_hb = 0.0

    async def _submit(item):
        await runner.submit(item, kind="dev")

    _notify = notify

    while not stop_event.is_set():
        try:
            now = datetime.now()
            if cron_on:
                await _cron.run_due(cwd, now, notify=_notify)   # 到点的作业各自隔离跑，结果按 announce 投递
            if hb_on:
                mono = asyncio.get_event_loop().time()
                if mono - last_hb >= hb_every:
                    last_hb = mono
                    await _hb.run_heartbeat(cwd, submit=_submit, notify=_notify, hour=now.hour)
        except Exception:  # noqa: BLE001 —— 单次 tick 出错不拖垮循环
            pass
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=60)   # 每分钟 tick 一次（可被停止打断）
        except asyncio.TimeoutError:
            pass
