from wificsi.protocol import (
    CsiFrame,
    Header,
    Heartbeat,
    Packet,
)
from wificsi.web.stream import NodeStreamHub


NODE_A = bytes.fromhex("020000000001")
NODE_B = bytes.fromhex("020000000002")


def _csi(node_id, sequence, boot_id=1, length=8):
    frame = CsiFrame(
        NODE_A, node_id, -43, -95, 4, 0, 1, 128, 11, 1, 3, 0, 0, 0, 0,
        False, bytes((sequence + i) & 0x7F for i in range(length)),
    )
    return Packet(Header(node_id, boot_id, sequence, sequence * 1000), frame)


def _heartbeat(node_id, sequence, boot_id=1):
    return Packet(Header(node_id, boot_id, sequence, sequence * 1000), Heartbeat(1, 2, 3, 4, 5, 0, 0))


def test_non_csi_packets_are_ignored():
    hub = NodeStreamHub()
    hub.offer(_heartbeat(NODE_A, 1), 0.0)
    assert hub.node_count == 0
    assert hub.flush_latest(1.0) == ()


def test_high_frequency_csi_only_keeps_latest_slot_and_counts_sources():
    hub = NodeStreamHub()
    for seq in range(1, 11):
        hub.offer(_csi(NODE_A, seq), 0.0)
    samples = hub.flush_latest(1.0)
    assert len(samples) == 1
    assert samples[0].source_frames == 10
    assert samples[0].ui_frames_coalesced == 9
    assert samples[0].rssi == -43


def test_multi_node_streams_are_isolated():
    hub = NodeStreamHub()
    hub.offer(_csi(NODE_A, 1), 0.0)
    hub.offer(_csi(NODE_B, 1), 0.0)
    samples = hub.flush_latest(1.0)
    assert {s.node_id for s in samples} == {NODE_A, NODE_B}
    assert len(hub.recent(NODE_A)) == 1
    assert len(hub.recent(NODE_B)) == 1


def test_boot_change_clears_ring():
    hub = NodeStreamHub()
    hub.offer(_csi(NODE_A, 1, boot_id=1), 0.0)
    hub.flush_latest(1.0)
    assert len(hub.recent(NODE_A)) == 1

    hub.offer(_csi(NODE_A, 2, boot_id=2), 0.0)
    hub.flush_latest(2.0)
    recent = hub.recent(NODE_A)
    assert len(recent) == 1
    assert recent[0].boot_id == 2


def test_ring_is_bounded_to_max_history():
    hub = NodeStreamHub(max_history=3)
    for seq in range(1, 8):
        hub.offer(_csi(NODE_A, seq), 0.0)
        hub.flush_latest(seq / 10.0)
    assert len(hub.recent(NODE_A)) == 3


def test_node_cap_rejects_extra_streams():
    hub = NodeStreamHub(max_nodes=2)
    hub.offer(_csi(NODE_A, 1), 0.0)
    node_c = bytes.fromhex("020000000003")
    hub.offer(_csi(NODE_B, 1), 0.0)
    hub.offer(_csi(node_c, 1), 0.0)
    assert hub.node_count == 2
    assert hub.rejected_nodes == 1
