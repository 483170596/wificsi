import asyncio
import math

from wificsi.protocol import (
    CsiFrame,
    Header,
    Hello,
    Packet,
    SensingConfig,
    SensingState,
    StableState,
    ProcessState,
    InitStage,
)
from wificsi.web.runtime import DashboardRuntime
from wificsi.web.settings import DashboardSettings


NODE_ID = bytes.fromhex("288485872bf4")
PEER_ID = bytes.fromhex("5ee388d95b42")


def _settings():
    return DashboardSettings(udp_host="127.0.0.1", udp_port=0, http_host="127.0.0.1", http_port=8000)


def _hello():
    return Packet(Header(NODE_ID, 1, 1, 1000), Hello(1, 1, 0, 0, 15, 612, 512, 1000, 1000, 5501, PEER_ID))


def _sensing():
    return Packet(
        Header(NODE_ID, 1, 2, 2000),
        SensingState(
            PEER_ID, StableState.ACTIVE, ProcessState.ACTIVE, InitStage.STABLE,
            3, 0, 0.5, 0.25, 10, 20, 5, 0.125, 0.25, SensingConfig(0.5, 0.25, 0.125, 300),
        ),
    )


def _csi():
    frame = CsiFrame(PEER_ID, NODE_ID, -43, -95, 4, 0, 1, 128, 11, 1, 3, 0, 0, 0, 0, False, bytes(range(8)))
    return Packet(Header(NODE_ID, 1, 3, 3000), frame)


def test_runtime_start_stop_binds_single_udp_endpoint():
    async def scenario():
        runtime = DashboardRuntime(_settings())
        await runtime.start()
        assert runtime.udp_bound is True
        assert runtime.udp_endpoint is not None
        assert runtime.publisher_task is not None
        await runtime.stop()
        assert runtime.udp_bound is False
        assert runtime.transport is None

    asyncio.run(scenario())


def test_publish_once_emits_csi_batch_and_node_status():
    async def scenario():
        runtime = DashboardRuntime(_settings())
        runtime.registry.accept(_hello(), ("127.0.0.1", 5501))
        runtime.registry.accept(_sensing(), ("127.0.0.1", 5501))
        runtime.hub.offer(_csi(), 0.0)
        connection = runtime.connections.add()
        runtime._publish_once()

        events = await connection.mailbox.next_event()
        types = {event["type"] for event in events}
        assert "csi_batch" in types
        assert "node_status" in types
        csi = next(event for event in events if event["type"] == "csi_batch")
        assert csi["data"]["node_id"] == "28:84:85:87:2b:f4"
        expected = [
            math.hypot(1, 0),
            math.hypot(3, 2),
            math.hypot(5, 4),
            math.hypot(7, 6),
        ]
        assert csi["data"]["amplitude"] == expected

    asyncio.run(scenario())


def test_snapshot_data_reports_presence():
    runtime = DashboardRuntime(_settings())
    runtime.registry.accept(_hello(), ("127.0.0.1", 5501))
    runtime.registry.accept(_sensing(), ("127.0.0.1", 5501))
    data = runtime.snapshot_data()
    assert data["nodes"][0]["presence"] == "PRESENT"
    assert data["nodes"][0]["presence_label"] == "有人"
