"""Bounded realtime CSI stream hub for display.

``NodeRegistry`` keeps the latest authoritative node state; this hub keeps the
per-node latest raw CSI slot plus a fixed-capacity ring of derived samples that
is safe to feed a ~10 Hz publisher. It is intentionally synchronous and does no
I/O, JSON encoding or awaiting.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

from ..protocol import CsiFrame, Packet
from .transform import amplitude_stats, iq_to_amplitude


@dataclass(frozen=True, slots=True)
class DerivedCsiSample:
    node_id: bytes
    boot_id: int
    server_time_ms: int
    device_time_us: int
    rssi: int
    channel: int
    first_word_invalid: bool
    amplitude: tuple[float, ...]
    mean_amplitude: float
    rms_amplitude: float
    variance: float
    source_frames: int
    ui_frames_coalesced: int


@dataclass(slots=True)
class _NodeStream:
    boot_id: int
    latest_raw: CsiFrame | None = None
    latest_device_time_us: int = 0
    source_frames: int = 0
    ui_frames_coalesced: int = 0
    ring: deque[DerivedCsiSample] = field(default_factory=lambda: deque(maxlen=100))


class NodeStreamHub:
    def __init__(self, max_nodes: int = 16, max_history: int = 100):
        self.max_nodes = max_nodes
        self.max_history = max_history
        self._nodes: dict[bytes, _NodeStream] = {}
        self.rejected_nodes = 0
        self.transform_errors = 0

    def offer(self, packet: Packet, received_at: float) -> None:
        """Store the latest CSI frame for a node (called from the ingest observer)."""
        if not isinstance(packet.payload, CsiFrame):
            return
        node_id = packet.header.node_id
        stream = self._nodes.get(node_id)
        if stream is None:
            if len(self._nodes) >= self.max_nodes:
                self.rejected_nodes += 1
                return
            stream = self._new_stream(packet.header.boot_id)
            self._nodes[node_id] = stream
        elif stream.boot_id != packet.header.boot_id:
            stream = self._new_stream(packet.header.boot_id)
            self._nodes[node_id] = stream

        if stream.latest_raw is not None:
            stream.ui_frames_coalesced += 1
        stream.latest_raw = packet.payload
        stream.latest_device_time_us = packet.header.device_time_us
        stream.source_frames += 1

    def flush_latest(self, now: float) -> tuple[DerivedCsiSample, ...]:
        """Derive and emit one sample per node that received new CSI this cycle."""
        samples: list[DerivedCsiSample] = []
        for node_id, stream in self._nodes.items():
            frame = stream.latest_raw
            if frame is None:
                continue
            if (len(frame.iq) - (4 if frame.first_word_invalid else 0)) % 2:
                self.transform_errors += 1
            amplitude = iq_to_amplitude(frame.iq, frame.first_word_invalid)
            mean, rms, variance = amplitude_stats(amplitude)
            sample = DerivedCsiSample(
                node_id=node_id,
                boot_id=stream.boot_id,
                server_time_ms=int(now * 1000),
                device_time_us=stream.latest_device_time_us,
                rssi=frame.rssi,
                channel=frame.channel,
                first_word_invalid=frame.first_word_invalid,
                amplitude=amplitude,
                mean_amplitude=mean,
                rms_amplitude=rms,
                variance=variance,
                source_frames=stream.source_frames,
                ui_frames_coalesced=stream.ui_frames_coalesced,
            )
            stream.ring.append(sample)
            stream.latest_raw = None
            stream.latest_device_time_us = 0
            stream.source_frames = 0
            stream.ui_frames_coalesced = 0
            samples.append(sample)
        return tuple(samples)

    def recent(self, node_id: bytes) -> tuple[DerivedCsiSample, ...]:
        stream = self._nodes.get(node_id)
        return tuple(stream.ring) if stream is not None else ()

    def latest(self, node_id: bytes) -> DerivedCsiSample | None:
        stream = self._nodes.get(node_id)
        if stream is None or not stream.ring:
            return None
        return stream.ring[-1]

    def reset_boot(self, node_id: bytes, boot_id: int) -> None:
        stream = self._nodes.get(node_id)
        if stream is not None and stream.boot_id != boot_id:
            self._nodes[node_id] = self._new_stream(boot_id)

    def _new_stream(self, boot_id: int) -> _NodeStream:
        return _NodeStream(boot_id=boot_id, ring=deque(maxlen=self.max_history))

    @property
    def node_count(self) -> int:
        return len(self._nodes)
