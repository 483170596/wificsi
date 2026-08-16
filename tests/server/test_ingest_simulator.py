import asyncio
from collections import Counter
import socket

from wificsi.ingest import IngestProtocol
from wificsi.protocol import (
    Header,
    Heartbeat,
    MessageType,
    Packet,
    encode_packet,
)
from wificsi.registry import NodeLifecycle, NodeRegistry
from wificsi.server import run_server
from wificsi.simulator import SimulatorConfig, run_simulator


NODE_ID = bytes.fromhex("288485872bf4")


async def open_receiver(registry, port=0):
    loop = asyncio.get_running_loop()
    protocol = IngestProtocol(registry)
    transport, _ = await loop.create_datagram_endpoint(
        lambda: protocol,
        local_addr=("127.0.0.1", port),
    )
    return transport, protocol


def message_counts(snapshot):
    return Counter(dict(snapshot.message_counts))


def test_simulator_streams_all_telemetry_and_variable_csi_lengths():
    async def scenario():
        registry = NodeRegistry()
        transport, ingest = await open_receiver(registry)
        port = transport.get_extra_info("sockname")[1]
        try:
            await run_simulator(
                SimulatorConfig(
                    host="127.0.0.1",
                    port=port,
                    node_id=NODE_ID,
                    rate=120.0,
                    duration=0.18,
                    csi_lengths=(8, 128, 256),
                    hello_interval=0.05,
                    state_interval=0.04,
                    heartbeat_interval=0.04,
                    boot_id=0x10203040,
                )
            )
            await asyncio.sleep(0.03)
        finally:
            transport.close()

        snapshot = registry.get(NODE_ID)
        counts = message_counts(snapshot)
        assert counts[MessageType.HELLO] >= 2
        assert counts[MessageType.CSI_FRAME] >= 9
        assert counts[MessageType.SENSING_STATE] >= 2
        assert counts[MessageType.HEARTBEAT] >= 2
        assert ingest.csi_lengths == Counter({8: 8, 128: 7, 256: 7})
        assert snapshot.lifecycle is NodeLifecycle.INACTIVE
        assert ingest.parse_errors == Counter()

    asyncio.run(scenario())


def test_corrupt_packet_does_not_stop_later_valid_ingest():
    async def scenario():
        registry = NodeRegistry()
        transport, ingest = await open_receiver(registry)
        port = transport.get_extra_info("sockname")[1]
        loop = asyncio.get_running_loop()
        sender, _ = await loop.create_datagram_endpoint(
            asyncio.DatagramProtocol,
            remote_addr=("127.0.0.1", port),
        )
        valid = encode_packet(
            Packet(
                Header(NODE_ID, 1, 1, 1000),
                Heartbeat(1000, 100, 90, 2, 2, 0, 0),
            )
        )
        corrupt = bytearray(valid)
        corrupt[-1] ^= 1
        try:
            sender.sendto(bytes(corrupt))
            sender.sendto(valid)
            await asyncio.sleep(0.05)
        finally:
            sender.close()
            transport.close()

        assert ingest.parse_errors == Counter({"crc": 1})
        assert registry.get(NODE_ID).heartbeat.csi_sent == 2

    asyncio.run(scenario())


def test_periodic_hello_rediscovers_node_after_receiver_restart():
    async def scenario():
        first_registry = NodeRegistry()
        first_transport, _ = await open_receiver(first_registry)
        port = first_transport.get_extra_info("sockname")[1]
        simulator = asyncio.create_task(
            run_simulator(
                SimulatorConfig(
                    host="127.0.0.1",
                    port=port,
                    node_id=NODE_ID,
                    rate=20.0,
                    duration=0.35,
                    csi_lengths=(8,),
                    hello_interval=0.05,
                    state_interval=0.05,
                    heartbeat_interval=0.05,
                    boot_id=0x10203040,
                )
            )
        )
        await asyncio.sleep(0.1)
        first_transport.close()
        await asyncio.sleep(0.04)

        second_registry = NodeRegistry()
        second_transport, _ = await open_receiver(second_registry, port)
        try:
            await simulator
            await asyncio.sleep(0.03)
        finally:
            second_transport.close()

        snapshot = second_registry.get(NODE_ID)
        assert snapshot is not None
        assert message_counts(snapshot)[MessageType.HELLO] >= 1

    asyncio.run(scenario())


def test_server_metrics_capture_simulated_link_health_without_raw_csi(tmp_path):
    async def scenario():
        metrics_path = tmp_path / "server-metrics.jsonl"
        reservation = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
        reservation.close()
        server = asyncio.create_task(
            run_server("127.0.0.1", port, duration=1.1, metrics_jsonl=metrics_path)
        )
        await asyncio.sleep(0.02)
        await run_simulator(
            SimulatorConfig(
                host="127.0.0.1", port=port, node_id=NODE_ID, rate=40.0,
                duration=1.0, csi_lengths=(8, 128), hello_interval=0.05,
                state_interval=0.05, heartbeat_interval=0.05, boot_id=0x10203040,
            )
        )
        await server
        return [json.loads(line) for line in metrics_path.read_text().splitlines()]

    import json

    rows = asyncio.run(scenario())
    assert rows[-1]["node_id"] == "28:84:85:87:2b:f4"
    assert rows[-1]["message_counts"]["CSI_FRAME"] >= 30
    assert rows[-1]["parse_errors"] == 0
    measured = [row for row in rows if row["sample_interval"] is not None]
    assert measured[-1]["csi_rate"] == measured[-1]["csi_delta"] / measured[-1]["sample_interval"]
    assert measured[-1]["message_deltas"]["CSI_FRAME"] > 0
    assert rows[-1]["observed_at"] > 0
    assert rows[-1]["server_run_started_at"] <= rows[-1]["observed_at"]
    assert "iq" not in rows[-1]


def test_simulator_tolerates_server_absence_then_wraps_and_replaces_boot_id():
    async def scenario():
        absent = SimulatorConfig(
            host="127.0.0.1", port=57991, node_id=NODE_ID, rate=30.0,
            duration=0.05, csi_lengths=(8,), hello_interval=0.02,
            state_interval=0.02, heartbeat_interval=0.02, start_sequence=0xFFFFFFFE,
            boot_id=0x10203040,
        )
        await run_simulator(absent)

        registry = NodeRegistry()
        transport, ingest = await open_receiver(registry)
        port = transport.get_extra_info("sockname")[1]
        try:
            await run_simulator(
                SimulatorConfig(
                    host="127.0.0.1", port=port, node_id=NODE_ID, rate=80.0,
                    duration=0.08, csi_lengths=(8,), hello_interval=0.02,
                    state_interval=0.02, heartbeat_interval=0.02, start_sequence=0xFFFFFFFE,
                    boot_id=0x10203040,
                )
            )
            first = registry.get(NODE_ID)
            assert first.counters.sequence_gaps == 0
            await run_simulator(
                SimulatorConfig(
                    host="127.0.0.1", port=port, node_id=NODE_ID, rate=40.0,
                    duration=0.06, csi_lengths=(128,), hello_interval=0.02,
                    state_interval=0.02, heartbeat_interval=0.02, boot_id=0x50607080,
                )
            )
            await asyncio.sleep(0.02)
        finally:
            transport.close()
        return registry.get(NODE_ID), ingest

    snapshot, ingest = asyncio.run(scenario())
    assert snapshot.boot_id == 0x50607080
    assert snapshot.csi is not None and len(snapshot.csi.iq) == 128
    assert snapshot.counters.sequence_gaps == 0
    assert ingest.parse_errors == Counter()
