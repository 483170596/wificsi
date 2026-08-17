"""JSON v1 contract models for the web dashboard.

The REST/WebSocket payloads are a separate JSON v1 schema from the binary WCSI v1
protocol. This module owns the presence mapping, MAC helpers and Pydantic models,
but never touches raw ``iq`` bytes or the UDP ingest path.
"""

from __future__ import annotations

from enum import Enum
from typing import TYPE_CHECKING

from pydantic import BaseModel

from ..registry import NodeLifecycle, NodeSnapshot

if TYPE_CHECKING:  # pragma: no cover - type checking only
    from .stream import DerivedCsiSample


class Presence(str, Enum):
    PRESENT = "PRESENT"
    ABSENT = "ABSENT"
    UNKNOWN = "UNKNOWN"
    OFFLINE = "OFFLINE"


PRESENCE_LABELS: dict[Presence, str] = {
    Presence.PRESENT: "有人",
    Presence.ABSENT: "无人",
    Presence.UNKNOWN: "初始化中",
    Presence.OFFLINE: "离线",
}

_LIFECYCLE_TO_PRESENCE: dict[NodeLifecycle, Presence] = {
    NodeLifecycle.ACTIVE: Presence.PRESENT,
    NodeLifecycle.INACTIVE: Presence.ABSENT,
    NodeLifecycle.INITIALIZING: Presence.UNKNOWN,
    NodeLifecycle.OFFLINE: Presence.OFFLINE,
}


def lifecycle_to_presence(lifecycle: NodeLifecycle) -> Presence:
    return _LIFECYCLE_TO_PRESENCE[lifecycle]


def mac_to_str(node_id: bytes) -> str:
    return ":".join(f"{byte:02x}" for byte in node_id)


def parse_mac(value: str) -> bytes:
    """Parse a lowercase colon MAC into 6 bytes, raising on malformed input."""
    compact = value.replace(":", "").replace("-", "")
    try:
        parsed = bytes.fromhex(compact)
    except ValueError as error:
        raise ValueError("invalid_node_id") from error
    if len(parsed) != 6:
        raise ValueError("invalid_node_id")
    return parsed


class NodeSummary(BaseModel):
    node_id: str
    boot_id: int
    lifecycle: str
    presence: Presence
    presence_label: str
    last_seen_age_ms: int | None
    firmware: str | None
    ap_bssid: str | None
    channel: int | None
    rssi_dbm: int | None
    csi_rate_hz: float | None
    accepted_packets: int
    sequence_gaps: int
    duplicates: int
    out_of_order: int
    queue_dropped: int
    udp_send_errors: int
    free_heap_bytes: int | None
    csi_available: bool
    sensing_available: bool


class SensingDetail(BaseModel):
    stable_state: str | None
    process_state: str | None
    init_stage: str | None
    jitter: float | None
    wander: float | None
    reason: int | None


def build_node_summary(
    snapshot: NodeSnapshot,
    *,
    now_mono: float,
    csi_rate_hz: float | None,
) -> NodeSummary:
    hello = snapshot.hello
    csi = snapshot.csi
    heartbeat = snapshot.heartbeat
    presence = lifecycle_to_presence(snapshot.lifecycle)
    firmware = None
    if hello is not None:
        firmware = f"{hello.firmware_major}.{hello.firmware_minor}.{hello.firmware_patch}"
    return NodeSummary(
        node_id=mac_to_str(snapshot.node_id),
        boot_id=snapshot.boot_id,
        lifecycle=snapshot.lifecycle.value,
        presence=presence,
        presence_label=PRESENCE_LABELS[presence],
        last_seen_age_ms=max(0, int((now_mono - snapshot.last_seen) * 1000)),
        firmware=firmware,
        ap_bssid=mac_to_str(hello.ap_bssid) if hello is not None else None,
        channel=csi.channel if csi is not None else None,
        rssi_dbm=csi.rssi if csi is not None else None,
        csi_rate_hz=csi_rate_hz,
        accepted_packets=snapshot.counters.accepted_packets,
        sequence_gaps=snapshot.counters.sequence_gaps,
        duplicates=snapshot.counters.duplicates,
        out_of_order=snapshot.counters.out_of_order,
        queue_dropped=heartbeat.queue_dropped if heartbeat is not None else 0,
        udp_send_errors=heartbeat.udp_send_errors if heartbeat is not None else 0,
        free_heap_bytes=heartbeat.free_heap if heartbeat is not None else None,
        csi_available=csi is not None,
        sensing_available=snapshot.sensing is not None,
    )


def build_sensing_detail(snapshot: NodeSnapshot) -> SensingDetail | None:
    sensing = snapshot.sensing
    if sensing is None:
        return None
    return SensingDetail(
        stable_state=sensing.stable_state.name,
        process_state=sensing.process_state.name,
        init_stage=sensing.init_stage.name,
        jitter=sensing.jitter,
        wander=sensing.wander,
        reason=sensing.reason,
    )


def csi_batch_data(sample: DerivedCsiSample) -> dict[str, object]:
    """Build the ``csi_batch`` data object shared by WebSocket and latest-csi."""
    return {
        "node_id": mac_to_str(sample.node_id),
        "boot_id": sample.boot_id,
        "device_time_us": sample.device_time_us,
        "rssi_dbm": sample.rssi,
        "channel": sample.channel,
        "amplitude": list(sample.amplitude),
        "mean_amplitude": sample.mean_amplitude,
        "rms_amplitude": sample.rms_amplitude,
        "variance": sample.variance,
        "source_frames": sample.source_frames,
        "ui_frames_coalesced": sample.ui_frames_coalesced,
    }
