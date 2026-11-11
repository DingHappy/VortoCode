"""Routine defaults only; existing reports and other clocks keep their current rules."""
import os
from pathlib import Path

from src.scheduling.routine_schedule import zone


def host_timezone_name():
    candidates = [os.getenv('TZ', '').lstrip(':')]
    try:
        candidates.append(Path('/etc/timezone').read_text().strip())
    except OSError:
        pass
    try:
        resolved = str(Path('/etc/localtime').resolve())
        if '/zoneinfo/' in resolved:
            candidates.append(resolved.split('/zoneinfo/', 1)[1])
    except OSError:
        pass
    for name in candidates:
        try:
            zone(name)
            return name
        except ValueError:
            continue
    return None  # A local abbreviation/offset cannot prove the original IANA rules.
