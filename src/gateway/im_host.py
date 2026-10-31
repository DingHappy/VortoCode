"""Opt-in Project IM hosting: addressed transports and bounded model admission.

No task database, message bus or model orchestration loop. Busy inputs are
rejected until durable SessionActors input admission is available for IM.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path

from src.gateway import im_registry, im_service
from src.gateway.im_service import IMConfigError

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class BindingSpec:
    id: str
    bot_id: str
    channel: str
    account_id: str
    owner_ref: str
    env_prefix: str
    policy: str


@dataclass(frozen=True)
class BotSpec:
    id: str
    policy: str


@dataclass(frozen=True)
class HostConfig:
    primary_bot_id: str
    primary_binding_id: str
    max_model_turns: int
    bots: tuple[BotSpec, ...]
    bindings: tuple[BindingSpec, ...]


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise IMConfigError("Duplicate IM host configuration key")
        result[key] = value
    return result


def _positive(value, *, maximum=32):
    if type(value) is not int or not 1 <= value <= maximum:
        raise IMConfigError("IM host limits must be positive bounded integers")
    return value


def load_host_config(root: str) -> HostConfig:
    if os.getenv("VORTOCODE_IM", "").strip():
        raise IMConfigError("Choose legacy VORTOCODE_IM or VORTOCODE_IM_BINDINGS_FILE, not both")
    if os.getenv("VORTOCODE_WORKSPACE_SCOPE", "").strip().lower() != "project":
        raise IMConfigError("Multi-binding IM requires explicit Project scope")
    if os.getenv("VORTOCODE_IM_ROLE", "interactive").strip().lower() != "interactive":
        raise IMConfigError("Multi-binding IM requires interactive mode; legacy notify is separate")
    raw = os.getenv("VORTOCODE_IM_BINDINGS_FILE", "")
    path = Path(raw)
    if not raw or not path.is_absolute():
        raise IMConfigError("IM host configuration requires an explicit absolute file path")
    try:
        path = path.resolve(strict=True)
        if path == Path(root).resolve() or Path(root).resolve() in path.parents:
            raise IMConfigError("IM host configuration must be outside the project")
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_size > 65536 or (os.name != "nt" and info.st_mode & 0o022):
                raise IMConfigError("IM host configuration must be a bounded, owner-writable regular file")
            if hasattr(os, "getuid") and info.st_uid != os.getuid():
                raise IMConfigError("IM host configuration must belong to the launching user")
            payload = os.read(fd, 65537)
            if len(payload) > 65536:
                raise IMConfigError("IM host configuration exceeds its byte limit")
            data = json.loads(payload.decode("utf-8"), object_pairs_hook=_object)
        finally:
            os.close(fd)
    except IMConfigError:
        raise
    except Exception:
        raise IMConfigError("IM host configuration is missing, unreadable or invalid") from None
    allowed = {"version", "primary_bot_id", "primary_binding_id", "max_bots", "max_bindings", "max_model_turns", "bots", "bindings"}
    if not isinstance(data, dict) or set(data) - allowed or type(data.get("version")) is not int or data["version"] != 1:
        raise IMConfigError("Unknown IM host configuration schema")
    maximum = _positive(data.get("max_bindings", 4))
    max_bots = _positive(data.get("max_bots", 4))
    slots = _positive(data.get("max_model_turns", 2))
    bot_rows = data.get("bots")
    if not isinstance(bot_rows, list) or not 1 <= len(bot_rows) <= max_bots:
        raise IMConfigError("Invalid logical bot count")
    bots = {}
    for row in bot_rows:
        if not isinstance(row, dict) or set(row) != {"id", "policy"} or not isinstance(row["id"], str) \
                or re.fullmatch(r"[a-z][a-z0-9_-]{0,47}", row["id"]) is None or row["id"] in bots:
            raise IMConfigError("Invalid or duplicate logical bot identity")
        if row["policy"] != "research_read":
            raise IMConfigError("The initial multi-binding host requires research_read for every bot")
        bots[row["id"]] = BotSpec(**row)
    rows = data.get("bindings")
    if not isinstance(rows, list) or not 1 <= len(rows) <= maximum or (len(bots) > 1 and slots < 2):
        raise IMConfigError("IM host binding count or model-slot reservation is invalid")
    specs = []
    for row in rows:
        fields = {"id", "bot_id", "channel", "account_id", "owner_ref", "env_prefix"}
        if not isinstance(row, dict) or set(row) != fields or any(not isinstance(row[key], str) for key in fields):
            raise IMConfigError("Invalid IM binding fields; credentials must remain environment references")
        if any(re.fullmatch(r"[a-z][a-z0-9_-]{0,47}", row[key]) is None for key in ("id", "bot_id", "owner_ref")) \
                or row["id"] == "legacy" or re.fullmatch(r"[A-Z][A-Z0-9_]{0,79}", row["env_prefix"]) is None:
            raise IMConfigError("Invalid IM binding identifiers")
        if row["channel"] not in {"telegram", "dingtalk"} or not 1 <= len(row["account_id"]) <= 200 \
                or any(ord(char) < 32 for char in row["account_id"]):
            raise IMConfigError("Invalid IM channel or account identity")
        if row["bot_id"] not in bots:
            raise IMConfigError("Binding references an unknown logical bot")
        specs.append(BindingSpec(**row, policy=bots[row["bot_id"]].policy))
    primary = data.get("primary_binding_id")
    if not isinstance(primary, str) or primary not in {item.id for item in specs}:
        raise IMConfigError("IM host requires one explicit primary binding")
    primary_bot = data.get("primary_bot_id")
    if not isinstance(primary_bot, str) or primary_bot not in bots or next(item for item in specs if item.id == primary).bot_id != primary_bot:
        raise IMConfigError("Primary binding must belong to the explicit primary bot")
    if len({item.id for item in specs}) != len(specs) \
            or len({(item.channel, item.account_id) for item in specs}) != len(specs) \
            or len({item.env_prefix for item in specs}) != len(specs) or len({item.owner_ref for item in specs}) != 1:
        raise IMConfigError("Duplicate IM identity or inconsistent owner mapping")
    return HostConfig(primary_bot, primary, slots, tuple(bots.values()), tuple(specs))


def _adapter(spec: BindingSpec):
    suffixes = (("TOKEN", "OWNER_ID", "BOT_USERNAME") if spec.channel == "telegram" else
                ("CLIENT_ID", "CLIENT_SECRET", "OWNER_ID"))
    namespace = "TG" if spec.channel == "telegram" else "DD"
    values = {f"VORTOCODE_{namespace}_{key}": os.getenv(f"{spec.env_prefix}_{key}", "") for key in suffixes}
    return im_service.build_adapter(spec.channel, env=values)


class TurnAdmission:
    """Admission only; no waiting queue or task ownership. Release is idempotent."""
    def __init__(self, maximum):
        self.maximum = maximum
        self.held: dict[object, tuple[str, bool]] = {}

    def claim(self, bot_id, primary):
        if any(value[0] == bot_id for value in self.held.values()) or len(self.held) >= self.maximum \
                or (not primary and sum(not value[1] for value in self.held.values()) >= self.maximum - 1):
            return None
        token = object()
        self.held[token] = (bot_id, primary)
        return lambda: self.held.pop(token, None)


class IMHost:
    def __init__(self, config):
        self.config = config
        self.admission = TurnAdmission(config.max_model_turns)
        self.bindings: dict[str, im_registry.Binding] = {}

    async def _run(self, binding):
        try:
            await binding.bridge.run()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            binding.state, binding.error = "failed", type(exc).__name__
            logger.warning("IM binding failed: %s (%s)", binding.id, binding.error)
        finally:
            binding.stop_requested = True
            if binding.state != "failed":
                binding.state = "stopping"
            im_registry.suspend(binding)
            try:
                await binding.bridge.shutdown()
                await binding.adapter.close()
                binding.closed = True
                if binding.state != "failed":
                    binding.state = "stopped"
            except Exception as exc:
                binding.state, binding.error = "failed", type(exc).__name__

    async def stop(self, binding_id):
        binding = self.bindings.get(binding_id)
        if binding is None:
            return False
        task = binding.task
        if not task.done():
            binding.state = "stopping"
            im_registry.suspend(binding)
            if not binding.stop_requested:
                binding.stop_requested = True
                task.cancel()
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                if not task.done():
                    raise
        if not binding.closed:
            # Cold cancellation skips finally. Explicit stop may also retry a
            # failed adapter cleanup, never the model work.
            try:
                await binding.bridge.shutdown()
                await binding.adapter.close()
                binding.closed = True
            except Exception as exc:
                binding.state, binding.error = "failed", type(exc).__name__
                raise
        im_registry.unregister(binding.id, expected=binding.bridge)
        self.bindings.pop(binding_id, None)
        return True

    async def close(self):
        results = await asyncio.gather(*(self.stop(key) for key in tuple(self.bindings)), return_exceptions=True)
        if any(result is not True for result in results):
            raise RuntimeError("Some IM bindings did not finish cleanup; registrations retained")


async def start_host(root, *, runner, register_worker, adapter_factory=None):
    config = load_host_config(root)
    if im_registry.entries():
        raise IMConfigError("An IM host or legacy bridge already owns this Runtime")
    host = IMHost(config)
    adapters = []
    try:
        for spec in config.bindings:
            adapter, owner = (adapter_factory or _adapter)(spec)
            adapters.append(adapter)
            if getattr(adapter, "conversation_identity", lambda: "")() != spec.account_id:
                raise IMConfigError("Configured IM account does not match adapter identity")
            primary = spec.id == config.primary_binding_id
            coordinator = spec.bot_id == config.primary_bot_id
            bridge, _ = im_service.start_embedded(spec.channel, root, adapter=adapter, owner=owner,
                runner=runner, register_worker=register_worker, binding_id=spec.id, bot_id=spec.bot_id,
                owner_ref=spec.owner_ref, conversation="isolated", primary_binding=primary,
                session_role=spec.policy, notification_only=False,
                claim_turn=lambda bot_id=spec.bot_id, coordinator=coordinator: host.admission.claim(bot_id, coordinator))
            host.bindings[spec.id] = next(item for item in im_registry.entries() if item.bridge is bridge)
        for binding in host.bindings.values():
            binding.task = asyncio.create_task(host._run(binding))
        return host
    except BaseException:
        await asyncio.gather(*(binding.bridge.shutdown() for binding in host.bindings.values()), return_exceptions=True)
        for binding in host.bindings.values():
            im_registry.unregister(binding.id, expected=binding.bridge)
        await asyncio.gather(*(adapter.close() for adapter in adapters), return_exceptions=True)
        raise
