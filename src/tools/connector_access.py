"""Revocable connector capabilities; no credentials, paths or results in receipts."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Callable
from weakref import WeakKeyDictionary, WeakSet

from src.utils.async_ops import timeout_scope
from .mcp_client import MCPClient, MCPToolCall

CLEANUP_TIMEOUT = 5.0
_ACCESSES: WeakSet = WeakSet()
_CLIENTS: WeakSet = WeakSet()
_MANAGERS: WeakKeyDictionary = WeakKeyDictionary()
_REVOKED_IDENTITIES: dict[str, tuple] = {}


class ConnectorRevoked(PermissionError):
    def __init__(self):
        super().__init__("连接器凭据已撤销或变化；后续调用已禁用，请重新授权并连接。")


class ConnectorAccess:
    def __init__(self, name: str, identity: Callable[[], tuple]):
        self.name = name
        self._identity = identity
        self._initial = identity()
        if _REVOKED_IDENTITIES.get(name) == self._initial:
            raise ConnectorRevoked()
        self._revoked = False
        _ACCESSES.add(self)

    def revoke(self):
        self._revoked = True

    def matches_digest(self, digest: bytes) -> bool:
        return self._initial[-1] == digest

    def current(self) -> bool:
        if not self._revoked:
            try:
                self._revoked = self._identity() != self._initial
            except (OSError, ValueError):
                self._revoked = True
        return not self._revoked

    def require(self):
        if not self.current():
            raise ConnectorRevoked()


class GuardedConnectorClient(MCPClient):
    """Guard actual dispatch after permission awaits, and discard revoked responses."""
    def __init__(self, client: MCPClient, access: ConnectorAccess):
        self._client = client
        self.access = access
        self.server_name, self.transport = client.server_name, client.transport
        self.tools = []
        self._connecting = False
        self._closed = False
        self._close_lock = asyncio.Lock()
        _CLIENTS.add(self)

    @property
    def connected(self):
        return self._client.connected and self.access.current()

    @connected.setter
    def connected(self, value):
        self._client.connected = value

    @property
    def cleanup_confirmed(self):
        return self._closed and not self._connecting

    async def connect(self):
        self.access.require()
        self._connecting = True
        self._closed = False
        try:
            result = await self._client.connect()
            if not self.access.current():
                # A late connect can create resources after an earlier close.
                await self._disconnect(force=True)
                raise ConnectorRevoked()
            return result
        finally:
            self._connecting = False

    async def _disconnect(self, *, force=False):
        self.access.revoke()
        async with self._close_lock:
            if not self._closed or force:
                self._closed = False
                try:
                    await self._client.disconnect()
                except Exception:
                    raise RuntimeError("连接器本地连接清理未确认") from None
                self._closed = True

    async def disconnect(self):
        await self._disconnect()

    async def list_tools(self):
        self.access.require()
        result = await self._client.list_tools()
        self.access.require()
        return result

    async def call_tool(self, tool_call: MCPToolCall):
        self.access.require()
        result = await self._client.call_tool(tool_call)
        self.access.require()
        return result


@dataclass
class DetachedManager:
    manager: Any
    servers: frozenset[str]


@dataclass
class ConnectorRevocation:
    managers: list[DetachedManager]
    clients: list[GuardedConnectorClient]


def bind_connector_access(manager, accesses: dict[str, ConnectorAccess]):
    manager.connector_access = accesses
    _MANAGERS[manager] = accesses


def connector_identity_revoked(name: str, identity: tuple) -> bool:
    return _REVOKED_IDENTITIES.get(name) == identity


def has_connector_access(name: str) -> bool:
    return any(access.name == name for access in list(_ACCESSES))


def revoke_connector_access(name: str, identity: tuple | None = None) -> ConnectorRevocation:
    """Disable capabilities and remove registries synchronously, before cleanup awaits."""
    if identity is not None:
        _REVOKED_IDENTITIES[name] = identity
    for access in list(_ACCESSES):
        if access.name == name:
            access.revoke()
    clients = [client for client in list(_CLIENTS) if client.access.name == name and not client.cleanup_confirmed]
    managers = []
    for manager, accesses in list(_MANAGERS.items()):
        servers = frozenset(server for server, access in accesses.items() if access.name == name)
        if servers:
            manager.detach_connector_servers(servers)
            managers.append(DetachedManager(manager, servers))
    return ConnectorRevocation(managers, clients)


async def cleanup_revocation(revocation: ConnectorRevocation) -> dict:
    clients = revocation.clients
    try:
        async with timeout_scope(CLEANUP_TIMEOUT):
            await asyncio.gather(*(client.disconnect() for client in clients), return_exceptions=True)
    except TimeoutError:
        pass  # Disabled capabilities stay disabled even when cleanup cannot be confirmed.
    closed = sum(client.cleanup_confirmed for client in clients)
    return {"connections_disabled": len(clients), "closed_connections": closed,
            "cleanup_status": "confirmed" if closed == len(clients) else "unconfirmed"}
