import asyncio

import pytest

from wificsi.commands import CommandManager
from wificsi.ingest import IngestProtocol
from wificsi.protocol import (
    AckStatus,
    Command,
    CommandAck,
    CommandOpcode,
    Header,
    Hello,
    Packet,
    SensingConfig,
    decode_packet,
    encode_packet,
)
from wificsi.registry import NodeRegistry
from wificsi.server import execute_when_online
from wificsi.simulator import SimulatedNode, SimulatorConfig, run_simulator


NODE_ID = bytes.fromhex("288485872bf4")
AP_ID = bytes.fromhex("5ee388d95b42")
ENDPOINT = ("10.204.75.215", 5501)
BOOT_ID = 0x10203040


def registry_with_node():
    registry = NodeRegistry(lambda: 0.0)
    registry.accept(
        Packet(
            Header(NODE_ID, BOOT_ID, 1, 1000),
            Hello(1, 1, 0, 0, 15, 612, 512, 1000, 1000, 5501, AP_ID),
        ),
        ENDPOINT,
    )
    return registry


class ImmediateSleeper:
    def __init__(self):
        self.delays = []

    async def __call__(self, delay):
        self.delays.append(delay)


class GateSleeper:
    def __init__(self):
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def __call__(self, delay):
        self.started.set()
        await self.release.wait()


def test_timeout_retries_identical_command_at_bounded_schedule():
    sent = []
    sleeper = ImmediateSleeper()
    manager = CommandManager(
        registry_with_node(),
        lambda data, endpoint: sent.append((data, endpoint)),
        sleep=sleeper,
    )

    result = asyncio.run(manager.execute(NODE_ID, CommandOpcode.GET_CONFIG))

    assert result.ok is False
    assert result.error == "timeout"
    assert result.attempts == 3
    assert sleeper.delays == [0.5, 1.0]
    assert len(sent) == 3
    assert sent[0] == sent[1] == sent[2]
    command = decode_packet(sent[0][0])
    assert sent[0][1] == ENDPOINT
    assert command.header.node_id == NODE_ID
    assert command.header.boot_id == BOOT_ID
    assert command.header.sequence == command.payload.correlation_id
    assert command.header.device_time_us == 0


def test_matching_ack_completes_without_retry():
    sleeper = ImmediateSleeper()
    sent = []
    manager = None

    def sendto(data, endpoint):
        sent.append((data, endpoint))
        command = decode_packet(data)
        ack = Packet(
            Header(NODE_ID, BOOT_ID, 2, 2000),
            CommandAck(
                command.payload.correlation_id,
                command.payload.opcode,
                AckStatus.OK,
                SensingConfig(0.5, 0.25, 0.125, 300),
            ),
        )
        assert manager.handle_ack(ack) is True

    manager = CommandManager(registry_with_node(), sendto, sleep=sleeper)

    result = asyncio.run(manager.execute(NODE_ID, CommandOpcode.GET_CONFIG))

    assert result.ok is True
    assert result.ack.status is AckStatus.OK
    assert result.attempts == 1
    assert sleeper.delays == []
    assert len(sent) == 1


def test_wrong_boot_or_opcode_ack_does_not_complete_command():
    async def scenario():
        sleeper = GateSleeper()
        sent = []
        manager = CommandManager(
            registry_with_node(),
            lambda data, endpoint: sent.append(decode_packet(data)),
            sleep=sleeper,
        )
        task = asyncio.create_task(manager.execute(NODE_ID, CommandOpcode.GET_CONFIG))
        await sleeper.started.wait()
        correlation = sent[0].payload.correlation_id
        wrong_boot = Packet(
            Header(NODE_ID, BOOT_ID + 1, 2, 2000),
            CommandAck(correlation, CommandOpcode.GET_CONFIG, AckStatus.OK),
        )
        wrong_opcode = Packet(
            Header(NODE_ID, BOOT_ID, 2, 2000),
            CommandAck(correlation, CommandOpcode.RESET_BASELINE, AckStatus.OK),
        )
        assert manager.handle_ack(wrong_boot) is False
        assert manager.handle_ack(wrong_opcode) is False
        sleeper.release.set()
        return await task

    assert asyncio.run(scenario()).error == "timeout"


@pytest.mark.parametrize(
    "config",
    [
        SensingConfig(0.0, 0.25, 0.1, 300),
        SensingConfig(1.1, 0.25, 0.1, 300),
        SensingConfig(0.5, -0.1, 0.1, 300),
        SensingConfig(0.5, 1.1, 0.1, 300),
        SensingConfig(0.5, 0.25, -0.1, 300),
        SensingConfig(0.5, 0.25, 0.1, 60001),
    ],
)
def test_invalid_set_config_is_rejected_before_send(config):
    sent = []
    manager = CommandManager(
        registry_with_node(),
        lambda data, endpoint: sent.append(data),
        sleep=ImmediateSleeper(),
    )

    with pytest.raises(ValueError):
        asyncio.run(manager.execute(NODE_ID, CommandOpcode.SET_CONFIG, config))

    assert sent == []


def test_simulator_returns_cached_ack_for_duplicate_reset():
    simulator = SimulatedNode(
        SimulatorConfig(node_id=NODE_ID, boot_id=BOOT_ID, duration=1.0)
    )
    command = encode_packet(
        Packet(
            Header(NODE_ID, BOOT_ID, 77, 0),
            Command(77, CommandOpcode.RESET_BASELINE),
        )
    )

    first = simulator.handle_command(command)
    second = simulator.handle_command(command)

    assert first == second
    assert decode_packet(first).payload.status is AckStatus.OK
    assert simulator.reset_applications == 1


def test_ingest_routes_matching_ack_to_pending_command():
    async def scenario():
        registry = registry_with_node()
        sleeper = GateSleeper()
        sent = []
        manager = CommandManager(
            registry,
            lambda data, endpoint: sent.append(decode_packet(data)),
            sleep=sleeper,
        )
        ingest = IngestProtocol(registry, command_manager=manager)
        task = asyncio.create_task(manager.execute(NODE_ID, CommandOpcode.GET_CONFIG))
        await sleeper.started.wait()
        command = sent[0]
        ack = encode_packet(
            Packet(
                Header(NODE_ID, BOOT_ID, 2, 2000),
                CommandAck(
                    command.payload.correlation_id,
                    CommandOpcode.GET_CONFIG,
                    AckStatus.OK,
                    SensingConfig(0.5, 0.25, 0.125, 300),
                ),
            )
        )

        ingest.datagram_received(ack, ENDPOINT)

        return await task

    assert asyncio.run(scenario()).ok is True


def test_real_udp_simulator_answers_get_config():
    async def scenario():
        loop = asyncio.get_running_loop()
        registry = NodeRegistry()
        ingest = IngestProtocol(registry)
        transport, _ = await loop.create_datagram_endpoint(
            lambda: ingest,
            local_addr=("127.0.0.1", 0),
        )
        port = transport.get_extra_info("sockname")[1]
        manager = CommandManager(registry, transport.sendto)
        ingest.command_manager = manager
        simulator = asyncio.create_task(
            run_simulator(
                SimulatorConfig(
                    host="127.0.0.1",
                    port=port,
                    node_id=NODE_ID,
                    rate=30.0,
                    duration=0.4,
                    hello_interval=0.05,
                    state_interval=0.05,
                    heartbeat_interval=0.05,
                    boot_id=BOOT_ID,
                )
            )
        )
        try:
            async with asyncio.timeout(1.0):
                while registry.get(NODE_ID) is None:
                    await asyncio.sleep(0.01)
            result = await manager.execute(NODE_ID, CommandOpcode.GET_CONFIG)
            await simulator
            return result
        finally:
            transport.close()
            if not simulator.done():
                simulator.cancel()

    result = asyncio.run(scenario())
    assert result.ok is True
    assert result.ack.config == SensingConfig(0.5, 0.25, 0.125, 300)


def test_server_command_waits_for_node_discovery():
    async def scenario():
        registry = NodeRegistry()
        manager = None

        def sendto(data, endpoint):
            command = decode_packet(data)
            manager.handle_ack(
                Packet(
                    Header(NODE_ID, BOOT_ID, 2, 2000),
                    CommandAck(
                        command.payload.correlation_id,
                        command.payload.opcode,
                        AckStatus.OK,
                        SensingConfig(0.5, 0.25, 0.125, 300),
                    ),
                )
            )

        manager = CommandManager(registry, sendto, sleep=ImmediateSleeper())
        task = asyncio.create_task(
            execute_when_online(
                registry,
                manager,
                NODE_ID,
                CommandOpcode.GET_CONFIG,
                timeout=0.1,
                poll_interval=0.001,
            )
        )
        await asyncio.sleep(0)
        registry.accept(
            Packet(
                Header(NODE_ID, BOOT_ID, 1, 1000),
                Hello(1, 1, 0, 0, 15, 612, 512, 1000, 1000, 5501, AP_ID),
            ),
            ENDPOINT,
        )
        return await task

    assert asyncio.run(scenario()).ok is True
