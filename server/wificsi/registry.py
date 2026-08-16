"""In-memory node lifecycle and link accounting."""

from __future__ import annotations

import time
from collections import Counter
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable

from .protocol import (
    CommandAck,
    CsiFrame,
    Heartbeat,
    Hello,
    MessageType,
    Packet,
    SensingState,
    StableState,
    sequence_relation,
)


class NodeLifecycle(str, Enum):
    OFFLINE = "OFFLINE"
    INITIALIZING = "INITIALIZING"
    ACTIVE = "ACTIVE"
    INACTIVE = "INACTIVE"


@dataclass(frozen=True, slots=True)
class LinkCounters:
    accepted_packets: int
    sequence_gaps: int
    duplicates: int
    out_of_order: int


@dataclass(frozen=True, slots=True)
class NodeSnapshot:
    node_id: bytes
    endpoint: tuple[str, int]
    boot_id: int
    lifecycle: NodeLifecycle
    last_seen: float
    hello: Hello | None
    csi: CsiFrame | None
    sensing: SensingState | None
    heartbeat: Heartbeat | None
    last_ack: CommandAck | None
    counters: LinkCounters
    message_counts: tuple[tuple[MessageType, int], ...]


@dataclass(slots=True)
class _NodeState:
    node_id: bytes
    endpoint: tuple[str, int]
    boot_id: int
    lifecycle: NodeLifecycle
    last_seen: float
    last_sequence: int
    hello: Hello | None = None
    csi: CsiFrame | None = None
    sensing: SensingState | None = None
    heartbeat: Heartbeat | None = None
    last_ack: CommandAck | None = None
    accepted_packets: int = 0
    sequence_gaps: int = 0
    duplicates: int = 0
    out_of_order: int = 0
    message_counts: Counter[MessageType] = field(default_factory=Counter)


def _payload_type(payload: object) -> MessageType:
    if isinstance(payload, Hello):
        return MessageType.HELLO
    if isinstance(payload, CsiFrame):
        return MessageType.CSI_FRAME
    if isinstance(payload, SensingState):
        return MessageType.SENSING_STATE
    if isinstance(payload, Heartbeat):
        return MessageType.HEARTBEAT
    if isinstance(payload, CommandAck):
        return MessageType.COMMAND_ACK
    return MessageType.COMMAND


class NodeRegistry:
    """Own mutable node state and expose deterministic immutable snapshots."""

    def __init__(self, clock: Callable[[], float] = time.monotonic):
        self._clock = clock
        self._nodes: dict[bytes, _NodeState] = {}

    def accept(self, packet: Packet, endpoint: tuple[str, int]) -> bool:
        now = self._clock()
        node_id = packet.header.node_id
        state = self._nodes.get(node_id)

        if state is None:
            state = self._new_state(packet, endpoint, now)
            self._nodes[node_id] = state
        elif packet.header.boot_id != state.boot_id:
            state = self._new_state(packet, endpoint, now)
            self._nodes[node_id] = state
        else:
            relation = sequence_relation(state.last_sequence, packet.header.sequence)
            if relation == "duplicate":
                state.duplicates += 1
                return False
            if relation == "old":
                state.out_of_order += 1
                return False
            if relation == "gap":
                state.sequence_gaps += (
                    (packet.header.sequence - state.last_sequence) & 0xFFFFFFFF
                ) - 1
            state.last_sequence = packet.header.sequence
            state.last_seen = now
            state.endpoint = endpoint
            state.accepted_packets += 1
            if state.lifecycle is NodeLifecycle.OFFLINE:
                state.lifecycle = NodeLifecycle.INITIALIZING

        self._apply_payload(state, packet.payload)
        state.message_counts[_payload_type(packet.payload)] += 1
        return True

    def _new_state(
        self,
        packet: Packet,
        endpoint: tuple[str, int],
        now: float,
    ) -> _NodeState:
        return _NodeState(
            node_id=packet.header.node_id,
            endpoint=endpoint,
            boot_id=packet.header.boot_id,
            lifecycle=NodeLifecycle.INITIALIZING,
            last_seen=now,
            last_sequence=packet.header.sequence,
            accepted_packets=1,
        )

    @staticmethod
    def _apply_payload(state: _NodeState, payload: object) -> None:
        if isinstance(payload, Hello):
            state.hello = payload
        elif isinstance(payload, CsiFrame):
            state.csi = payload
        elif isinstance(payload, SensingState):
            state.sensing = payload
            if payload.stable_state is StableState.ACTIVE:
                state.lifecycle = NodeLifecycle.ACTIVE
            elif payload.stable_state is StableState.INACTIVE:
                state.lifecycle = NodeLifecycle.INACTIVE
            else:
                state.lifecycle = NodeLifecycle.INITIALIZING
        elif isinstance(payload, Heartbeat):
            state.heartbeat = payload
        elif isinstance(payload, CommandAck):
            state.last_ack = payload

    def expire(self) -> tuple[bytes, ...]:
        now = self._clock()
        expired: list[bytes] = []
        for node_id, state in self._nodes.items():
            if state.lifecycle is not NodeLifecycle.OFFLINE and now - state.last_seen > 5.0:
                state.lifecycle = NodeLifecycle.OFFLINE
                expired.append(node_id)
        return tuple(sorted(expired))

    def get(self, node_id: bytes) -> NodeSnapshot | None:
        state = self._nodes.get(node_id)
        return self._snapshot(state) if state is not None else None

    def list(self) -> tuple[NodeSnapshot, ...]:
        return tuple(self._snapshot(self._nodes[node_id]) for node_id in sorted(self._nodes))

    @staticmethod
    def _snapshot(state: _NodeState) -> NodeSnapshot:
        return NodeSnapshot(
            node_id=state.node_id,
            endpoint=state.endpoint,
            boot_id=state.boot_id,
            lifecycle=state.lifecycle,
            last_seen=state.last_seen,
            hello=state.hello,
            csi=state.csi,
            sensing=state.sensing,
            heartbeat=state.heartbeat,
            last_ack=state.last_ack,
            counters=LinkCounters(
                accepted_packets=state.accepted_packets,
                sequence_gaps=state.sequence_gaps,
                duplicates=state.duplicates,
                out_of_order=state.out_of_order,
            ),
            message_counts=tuple(sorted(state.message_counts.items(), key=lambda item: item[0])),
        )
