"""Persisted memory origin; approval of external material never changes its origin."""
import json

_OWNER_SOURCES = frozenset({'web', 'cli', 'agent', 'desktop', 'app', 'owner_device', 'assistant'})


def origin_fields(*, source, tainted, confirmed, confirmed_by, decision, owner_confirmed=False):
    origin = 'unknown'
    if tainted is True or source == 'im':
        origin = 'external'
    elif (tainted is False and confirmed is True and owner_confirmed is True
          and isinstance(source, str) and source in _OWNER_SOURCES
          and isinstance(confirmed_by, str) and confirmed_by.strip() and decision == 'durable'):
        origin = 'owner_confirmed'
    return {'memory_origin': origin, 'origin_version': 1, 'owner_confirmed': owner_confirmed is True}


def owner_confirmed_memory(row):
    """Old 'confirmed' also included preauthorization, so it is not proof of owner origin."""
    try:
        metadata = json.loads(row.get('metadata') or '{}')
    except (TypeError, ValueError):
        return False
    if not isinstance(metadata, dict):
        return False
    valid = (isinstance(metadata.get('source'), str) and metadata['source'] in _OWNER_SOURCES and metadata.get('tainted') is False
             and metadata.get('confirmed') is True and metadata.get('owner_confirmed') is True
             and isinstance(metadata.get('confirmed_by'), str)
             and bool(metadata['confirmed_by'].strip()))
    if 'memory_origin' in metadata or 'origin_version' in metadata:
        return valid and type(metadata.get('origin_version')) is int and metadata['origin_version'] == 1 and metadata.get('memory_origin') == 'owner_confirmed'
    return False
