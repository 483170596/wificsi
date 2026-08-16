import asyncio
from collections import Counter

from wificsi.ingest import IngestProtocol
from wificsi.protocol import (
    Header,
    Heartbeat,
    MessageType,
    Packet,
    encode_packet,
)
from wificsi.registry import NodeLifecycle, NodeRegistry
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
