import pytest

from wificsi.protocol import (
    CsiFrame,
    Header,
    Heartbeat,
    Hello,
    Packet,
    SensingConfig,
    SensingState,
    StableState,
    ProcessState,
    InitStage,
)
from wificsi.registry import NodeLifecycle, NodeRegistry
from wificsi.web.models import (
    Presence,
    build_node_summary,
    build_sensing_detail,
    csi_batch_data,
    lifecycle_to_presence,
    mac_to_str,
    parse_mac,
)
from wificsi.web.stream import DerivedCsiSample


NODE_ID = bytes.fromhex("288485872bf4")
PEER_ID = bytes.fromhex("5ee388d95b42")


@pytest.mark.parametrize(
    "lifecycle,presence",
    [
        (NodeLifecycle.ACTIVE, Presence.PRESENT),
        (NodeLifecycle.INACTIVE, Presence.ABSENT),
        (NodeLifecycle.INITIALIZING, Presence.UNKNOWN),
        (NodeLifecycle.OFFLINE, Presence.OFFLINE),
    ],
)
def test_lifecycle_to_presence_is_unambiguous(lifecycle, presence):
    assert lifecycle_to_presence(lifecycle) is presence


def test_initializing_and_offline_are_not_absent():
    assert lifecycle_to_presence(NodeLifecycle.INITIALIZING) is not Presence.ABSENT
    assert lifecycle_to_presence(NodeLifecycle.OFFLINE) is not Presence.ABSENT


def test_mac_roundtrip():
    assert mac_to_str(NODE_ID) == "28:84:85:87:2b:f4"
    assert parse_mac("28:84:85:87:2b:f4") == NODE_ID
    assert parse_mac("288485872BF4") == NODE_ID


@pytest.mark.parametrize("value", ["", "28:84:85", "not-a-mac", "28:84:85:87:2b:f4:ff"])
def test_parse_mac_rejects_malformed(value):
    with pytest.raises(ValueError):
        parse_mac(value)


def _hello_packet(sequence=1, boot_id=1):
    hello = Hello(1, 1, 0, 0, 15, 612, 512, 1000, 1000, 5501, PEER_ID)
    return Packet(Header(NODE_ID, boot_id, sequence, sequence * 1000), hello)


def _csi_packet(sequence=2, boot_id=1):
    frame = CsiFrame(PEER_ID, NODE_ID, -43, -95, 4, 0, 1, 128, 11, 1, 3, 0, 0, 0, 0, False, bytes(range(8)))
    return Packet(Header(NODE_ID, boot_id, sequence, sequence * 1000), frame)


def test_build_node_summary_maps_initializing():
    registry = NodeRegistry()
    registry.accept(_hello_packet(), ("127.0.0.1", 5501))
    snapshot = registry.get(NODE_ID)
    summary = build_node_summary(snapshot, now_mono=1.0, csi_rate_hz=None)

    assert summary.node_id == "28:84:85:87:2b:f4"
    assert summary.presence is Presence.UNKNOWN
    assert summary.presence_label == "初始化中"
    assert summary.firmware == "1.0.0"
    assert summary.ap_bssid == "5e:e3:88:d9:5b:42"
    assert summary.rssi_dbm is None
    assert summary.csi_available is False
    assert summary.sensing_available is False


def test_build_node_summary_present_with_csi():
    registry = NodeRegistry()
    registry.accept(_hello_packet(), ("127.0.0.1", 5501))
    registry.accept(_csi_packet(), ("127.0.0.1", 5501))
    registry.accept(
        Packet(
            Header(NODE_ID, 1, 3, 3000),
            SensingState(
                PEER_ID, StableState.ACTIVE, ProcessState.ACTIVE, InitStage.STABLE,
                3, 0, 0.5, 0.25, 10, 20, 5, 0.125, 0.25, SensingConfig(0.5, 0.25, 0.125, 300),
            ),
        ),
        ("127.0.0.1", 5501),
    )
    snapshot = registry.get(NODE_ID)
    summary = build_node_summary(snapshot, now_mono=1.0, csi_rate_hz=99.8)

    assert summary.presence is Presence.PRESENT
    assert summary.presence_label == "有人"
    assert summary.rssi_dbm == -43
    assert summary.channel == 4
    assert summary.csi_available is True
    assert summary.csi_rate_hz == 99.8


def test_build_sensing_detail_none_when_absent():
    registry = NodeRegistry()
    registry.accept(_hello_packet(), ("127.0.0.1", 5501))
    assert build_sensing_detail(registry.get(NODE_ID)) is None


def test_csi_batch_data_shape():
    sample = DerivedCsiSample(
        node_id=NODE_ID, boot_id=1, server_time_ms=1000, device_time_us=2000,
        rssi=-43, channel=4, first_word_invalid=False, amplitude=(3.0, 4.0),
        mean_amplitude=3.5, rms_amplitude=5.0, variance=0.25, source_frames=10,
        ui_frames_coalesced=9,
    )
    data = csi_batch_data(sample)
    assert data["node_id"] == "28:84:85:87:2b:f4"
    assert data["amplitude"] == [3.0, 4.0]
    assert data["source_frames"] == 10
    assert data["ui_frames_coalesced"] == 9
