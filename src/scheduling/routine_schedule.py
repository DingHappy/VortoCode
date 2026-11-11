"""Timezone-aware schedules; UTC storage and one catch-up per dispatch."""
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from src.scheduling.cron import parse_schedule, _cron_matches

UTC = timezone.utc
HORIZON = timedelta(days=62)


def zone(name):
    if not isinstance(name, str) or not name or len(name) > 100:
        raise ValueError('先核对例行工作的默认时区，或明确指定 IANA 时区')
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError) as error:
        raise ValueError('时区无效，请使用 IANA 名称，例如 Asia/Shanghai') from error


def instant(value):
    parsed = datetime.fromisoformat(value.replace('Z', '+00:00')) if isinstance(value, str) else value
    if not isinstance(parsed, datetime) or parsed.tzinfo is None:
        raise ValueError('时间必须带明确时区偏移')
    return parsed.astimezone(UTC)


class RoutineSchedule:
    def __init__(self, expression, timezone_name):
        self.zone = zone(timezone_name)
        if not isinstance(expression, str) or len(expression) > 200:
            raise ValueError('排期无效')
        self.once = instant(expression[5:].strip()) if expression.startswith('once ') else None
        self.schedule = None if self.once else parse_schedule(expression)

    def _matches(self, at):
        local = at.astimezone(self.zone)
        if local.fold:
            return False  # A repeated wall-clock minute runs once, at its first occurrence.
        if self.schedule.kind == 'at':
            return (local.hour, local.minute) == self.schedule.at
        return _cron_matches(self.schedule.cron, local)

    def due(self, now, last):
        now, last = instant(now), instant(last)
        if self.once:
            return self.once if last < self.once <= now else None
        if self.schedule.kind == 'every':
            target = last + self.schedule.interval
            return now if target <= now else None
        probe, lower = now.replace(second=0, microsecond=0), max(last, now - HORIZON)
        while probe > lower:
            if self._matches(probe):
                return probe
            probe -= timedelta(minutes=1)
        return None

    def next(self, now, last):
        now, last = instant(now), instant(last)
        overdue = self.due(now, last)
        if overdue:
            return overdue
        if self.once:
            return self.once if self.once > max(now, last) else None
        if self.schedule.kind == 'every':
            return last + self.schedule.interval
        probe = max(now, last).replace(second=0, microsecond=0) + timedelta(minutes=1)
        end = probe + HORIZON
        while probe <= end:
            if self._matches(probe):
                return probe
            probe += timedelta(minutes=1)
        return None
