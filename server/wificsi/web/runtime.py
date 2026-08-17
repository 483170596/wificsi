"""Composable dashboard runtime: UDP ingest + registry + stream hub + publisher."""

from __future__ import annotations

import asyncio
import time
import uuid
from collections import deque

from ..ingest import IngestProtocol
from ..protocol import Packet
from ..registry import NodeLifecycle, NodeRegistry
from .connections import ConnectionManager
from .models import build_node_summary, csi_batch_data
from .settings import DashboardSettings
from .stream import NodeStreamHub


class DashboardRuntime:
    """Own the UDP endpoint, registry, hub and publisher in a single process."""

    def __init__(self, settings: DashboardSettings):
        self.settings = settings
        self.registry = NodeRegistry()
        self.hub = NodeStreamHub(max_nodes=settings.max_nodes, max_history=settings.max_history)
        self.connections = ConnectionManager()
        self.ingest = IngestProtocol(self.registry, observer=self._observe)
        self.transport: asyncio.DatagramTransport | None = None
        self.publisher_task: asyncio.Task | None = None
        self.server_instance_id = uuid.uuid4().hex
        self.udp_bound = False
        self.udp_endpoint: str | None = None
        self._event_id = 0
        self._source_history: dict[bytes, deque[tuple[float, int]]] = {}
        self._status_emitted: dict[bytes, tuple[float, NodeLifecycle, int]] = {}

    async def start(self) -> None:
        loop = asyncio.get_running_loop()
        self.transport, _ = await loop.create_datagram_endpoint(
            lambda: self.ingest,
            local_addr=(self.settings.udp_host, self.settings.udp_port),
        )
        sockname = self.transport.get_extra_info("sockname")
        self.udp_bound = True
        self.udp_endpoint = f"{sockname[0]}:{sockname[1]}"
        self.publisher_task = asyncio.create_task(self._publisher_loop())

    def _observe(self, packet: Packet, _endpoint: tuple[str, int], received_at: float) -> None:
        self.hub.offer(packet, received_at)

    async def stop(self) -> None:
        if self.publisher_task is not None:
            self.publisher_task.cancel()
            try:
                await self.publisher_task
            except asyncio.CancelledError:
                pass
            self.publisher_task = None
        if self.transport is not None:
            self.transport.close()
            self.transport = None
        self.udp_bound = False

    async def _publisher_loop(self) -> None:
        loop = asyncio.get_running_loop()
        interval = 1.0 / self.settings.publish_hz
        next_tick = loop.time()
        while True:
            await asyncio.sleep(max(0.0, next_tick - loop.time()))
            next_tick += interval
            self._publish_once()

    def _publish_once(self) -> None:
        now_mono = time.monotonic()
        now_wall = time.time()
        self.registry.expire()
        for sample in self.hub.flush_latest(now_wall):
            self._track_source(sample.node_id, sample.source_frames, now_mono)
            self.connections.publish(
                "csi_batch",
                sample.node_id,
                self.envelope("csi_batch", csi_batch_data(sample), now_wall),
            )
        self._publish_node_status(now_mono, now_wall)

    def _track_source(self, node_id: bytes, frames: int, now_mono: float) -> None:
        history = self._source_history.setdefault(node_id, deque())
        history.append((now_mono, frames))
        while history and now_mono - history[0][0] > 1.0:
            history.popleft()

    def csi_rate_hz(self, node_id: bytes, now_mono: float | None = None) -> float | None:
        if now_mono is None:
            now_mono = time.monotonic()
        history = self._source_history.get(node_id)
        if not history:
            return None
        while history and now_mono - history[0][0] > 1.0:
            history.popleft()
        if not history:
            return None
        window = now_mono - history[0][0]
        if window <= 0:
            return None
        return sum(frames for _, frames in history) / window

    def _publish_node_status(self, now_mono: float, now_wall: float) -> None:
        for snapshot in self.registry.list():
            previous = self._status_emitted.get(snapshot.node_id)
            if previous is not None:
                prev_mono, prev_lifecycle, prev_boot = previous
                changed = (snapshot.lifecycle, snapshot.boot_id) != (prev_lifecycle, prev_boot)
                due = now_mono - prev_mono >= 1.0
                if not changed and not due:
                    continue
            self._status_emitted[snapshot.node_id] = (
                now_mono,
                snapshot.lifecycle,
                snapshot.boot_id,
            )
            summary = build_node_summary(
                snapshot,
                now_mono=now_mono,
                csi_rate_hz=self.csi_rate_hz(snapshot.node_id, now_mono),
            )
            self.connections.publish(
                "node_status",
                snapshot.node_id,
                self.envelope("node_status", summary.model_dump(mode="json"), now_wall),
            )

    def envelope(self, event_type: str, data: object, now_wall: float | None = None) -> dict:
        if now_wall is None:
            now_wall = time.time()
        self._event_id += 1
        return {
            "type": event_type,
            "schema_version": 1,
            "event_id": self._event_id,
            "server_time_ms": int(now_wall * 1000),
            "data": data,
        }

    def snapshot_data(self, now_mono: float | None = None) -> dict:
        if now_mono is None:
            now_mono = time.monotonic()
        summaries = [
            build_node_summary(
                snapshot,
                now_mono=now_mono,
                csi_rate_hz=self.csi_rate_hz(snapshot.node_id, now_mono),
            )
            for snapshot in self.registry.list()
        ]
        return {"nodes": [summary.model_dump(mode="json") for summary in summaries]}
