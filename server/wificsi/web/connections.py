"""WebSocket connection manager with merge-on-latest client mailboxes.

Each connection owns a ``ClientMailbox`` that keeps at most one pending event per
``(event_type, node_id)`` key. A slow browser therefore cannot backpressure the
UDP ingest loop: an unsent ``csi_batch`` is simply overwritten by the newer one.
"""

from __future__ import annotations

import asyncio
from typing import Any


class ClientMailbox:
    def __init__(self) -> None:
        self._slots: dict[tuple[str, str], dict[str, Any]] = {}
        self._ready = asyncio.Event()
        self._closed = False
        self.coalesced = 0

    @property
    def closed(self) -> bool:
        return self._closed

    @property
    def pending(self) -> int:
        return len(self._slots)

    def offer_latest(self, key: tuple[str, str], event: dict[str, Any]) -> None:
        if self._closed:
            return
        if key in self._slots:
            self.coalesced += 1
        self._slots[key] = event
        self._ready.set()

    def close(self) -> None:
        self._closed = True
        self._ready.set()

    async def next_event(self) -> list[dict[str, Any]]:
        while not self._slots and not self._closed:
            self._ready.clear()
            await self._ready.wait()
        events = list(self._slots.values())
        self._slots.clear()
        self._ready.clear()
        return events


class _Connection:
    def __init__(self) -> None:
        self.mailbox = ClientMailbox()
        self.subscription: set[bytes] | None = None  # None means "all nodes"

    def accepts(self, event_type: str, node_id: bytes | None) -> bool:
        if event_type == "nodes_snapshot":
            return True
        if node_id is None:
            return True
        if self.subscription is None:
            return True
        return node_id in self.subscription


class ConnectionManager:
    def __init__(self) -> None:
        self._connections: list[_Connection] = []

    def add(self) -> _Connection:
        connection = _Connection()
        self._connections.append(connection)
        return connection

    def remove(self, connection: _Connection) -> None:
        if connection in self._connections:
            self._connections.remove(connection)
        connection.mailbox.close()

    def set_subscription(self, connection: _Connection, node_ids: set[bytes] | None) -> None:
        connection.subscription = node_ids

    def publish(self, event_type: str, node_id: bytes | None, event: dict[str, Any]) -> None:
        for connection in self._connections:
            if connection.accepts(event_type, node_id):
                connection.mailbox.offer_latest((event_type, node_id or ""), event)

    @property
    def connection_count(self) -> int:
        return len(self._connections)
