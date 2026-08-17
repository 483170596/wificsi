from wificsi.protocol import (
    CsiFrame,
    Header,
    Heartbeat,
    Hello,
    InitStage,
    Packet,
    ProcessState,
    SensingConfig,
    SensingState,
    StableState,
)
from wificsi.registry import NodeLifecycle, NodeRegistry


NODE_ID = bytes.fromhex("288485872bf4")
PEER_ID = bytes.fromhex("5ee388d95b42")
ENDPOINT = ("10.204.75.215", 5501)


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


def packet(payload, sequence, *, boot_id=1):
    return Packet(Header(NODE_ID, boot_id, sequence, sequence * 1000), payload)


def hello():
    return Hello(1, 1, 0, 0, 15, 612, 512, 1000, 1000, 5501, PEER_ID)


def heartbeat(*, queue_dropped=0):
    return Heartbeat(1_000_000, 300_000, 250_000, 50, 49, queue_dropped, 0)


def sensing(state):
    return SensingState(
        PEER_ID,
        state,
        ProcessState.ACTIVE if state is StableState.ACTIVE else ProcessState.IDLE,
        InitStage.STABLE,
        3,
        0,
        0.5,
        0.25,
        10,
        20,
        5,
        0.125,
        0.25,
        SensingConfig(0.5, 0.25, 0.125, 300),
    )


def test_hello_initializes_node_and_sensing_replaces_stable_state():
    clock = Clock()
    registry = NodeRegistry(clock)

    assert registry.accept(packet(hello(), 1), ENDPOINT) is True
    assert registry.get(NODE_ID).lifecycle is NodeLifecycle.INITIALIZING

    registry.accept(packet(sensing(StableState.ACTIVE), 2), ENDPOINT)
    assert registry.get(NODE_ID).lifecycle is NodeLifecycle.ACTIVE

    registry.accept(packet(sensing(StableState.INACTIVE), 3), ENDPOINT)
    snapshot = registry.get(NODE_ID)
    assert snapshot.lifecycle is NodeLifecycle.INACTIVE
    assert snapshot.sensing.stable_state is StableState.INACTIVE
    assert registry.list() == (snapshot,)


def test_boot_change_resets_sequence_metrics_and_state():
    registry = NodeRegistry(Clock())
    registry.accept(packet(hello(), 10), ENDPOINT)
    registry.accept(packet(sensing(StableState.ACTIVE), 13), ENDPOINT)
    assert registry.get(NODE_ID).counters.sequence_gaps == 2

    registry.accept(packet(hello(), 0, boot_id=2), ENDPOINT)

    snapshot = registry.get(NODE_ID)
    assert snapshot.boot_id == 2
    assert snapshot.lifecycle is NodeLifecycle.INITIALIZING
    assert snapshot.sensing is None
    assert snapshot.counters.sequence_gaps == 0


def test_sequence_wrap_gap_duplicate_and_old_are_counted_separately():
    registry = NodeRegistry(Clock())
    registry.accept(packet(hello(), 0xFFFFFFFE), ENDPOINT)
    registry.accept(packet(heartbeat(), 0xFFFFFFFF), ENDPOINT)
    registry.accept(packet(heartbeat(), 0), ENDPOINT)
    registry.accept(packet(heartbeat(), 3), ENDPOINT)

    assert registry.accept(packet(heartbeat(queue_dropped=99), 3), ENDPOINT) is False
    assert registry.accept(packet(heartbeat(queue_dropped=88), 2), ENDPOINT) is False

    snapshot = registry.get(NODE_ID)
    assert snapshot.counters.sequence_gaps == 2
    assert snapshot.counters.duplicates == 1
    assert snapshot.counters.out_of_order == 1
    assert snapshot.heartbeat.queue_dropped == 0


def test_node_expires_only_after_five_seconds_and_recovers_initializing():
    clock = Clock()
    registry = NodeRegistry(clock)
    registry.accept(packet(hello(), 1), ENDPOINT)

    clock.now = 5.0
    assert registry.expire() == ()
    clock.now = 5.001
    assert registry.expire() == (NODE_ID,)
    assert registry.get(NODE_ID).lifecycle is NodeLifecycle.OFFLINE

    clock.now = 6.0
    registry.accept(packet(heartbeat(), 2), ENDPOINT)
    assert registry.get(NODE_ID).lifecycle is NodeLifecycle.INITIALIZING
    assert registry.get(NODE_ID).last_seen == 6.0


def test_device_queue_drops_do_not_change_server_sequence_gaps():
    registry = NodeRegistry(Clock())
    registry.accept(packet(hello(), 1), ENDPOINT)
    registry.accept(packet(heartbeat(queue_dropped=7), 4), ENDPOINT)

    snapshot = registry.get(NODE_ID)
    assert snapshot.counters.sequence_gaps == 2
    assert snapshot.heartbeat.queue_dropped == 7


def test_latest_csi_is_retained_for_current_boot():
    registry = NodeRegistry(Clock())
    frame = CsiFrame(
        PEER_ID, NODE_ID, -80, -95, 4, 0, 1, 128, 11, 1, 3, 0, 0, 0, 0,
        False, bytes(range(8)),
    )

    registry.accept(packet(frame, 1), ENDPOINT)

    assert registry.get(NODE_ID).csi == frame
