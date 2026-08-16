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


class ManualClock:
    def __init__(self, now=100.0):
        self.now = now
        self.delays = []

    def __call__(self):
        return self.now

    async def sleep(self, delay):
        self.delays.append(delay)
        self.now += delay
        await asyncio.sleep(0)


def test_timeout_retries_identical_command_at_bounded_schedule():
    sent = []
    clock = ManualClock()
    manager = CommandManager(
        registry_with_node(),
        lambda data, endpoint: sent.append((data, endpoint)),
        clock=clock,
        sleep=clock.sleep,
        correlation_start=76,
    )

    result = asyncio.run(manager.execute(NODE_ID, CommandOpcode.GET_CONFIG))

    assert result.ok is False
    assert result.error == "timeout"
    assert result.attempts == 3
    assert clock.delays == [0.5, 0.5, 0.5]
    assert len(sent) == 3
    assert sent[0] == sent[1] == sent[2]
    command = decode_packet(sent[0][0])
    assert sent[0][1] == ENDPOINT
    assert command.header.node_id == NODE_ID
    assert command.header.boot_id == BOOT_ID
    assert command.header.sequence == command.payload.correlation_id
    assert command.header.device_time_us == 0


def test_retry_send_times_are_absolute_and_timeout_includes_final_ack_window():
    sent_at = []
    clock = ManualClock()
    manager = CommandManager(
        registry_with_node(),
        lambda data, endpoint: sent_at.append(clock()),
        clock=clock,
        sleep=clock.sleep,
        correlation_start=77,
    )

    result = asyncio.run(manager.execute(NODE_ID, CommandOpcode.GET_CONFIG))

    assert result.error == "timeout"
    assert sent_at == [100.0, 100.5, 101.0]
    assert clock.delays == [0.5, 0.5, 0.5]


def test_ack_on_event_loop_turn_after_third_send_completes_command():
    async def scenario():
        clock = ManualClock()
        sent = []
        manager = None

        def sendto(data, endpoint):
            sent.append(data)
            if len(sent) == 3:
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
                asyncio.get_running_loop().call_soon(manager.handle_ack, ack)

        manager = CommandManager(
            registry_with_node(),
            sendto,
            clock=clock,
            sleep=clock.sleep,
            correlation_start=78,
        )
        result = await manager.execute(NODE_ID, CommandOpcode.GET_CONFIG)
        return result, sent, clock

    result, sent, clock = asyncio.run(scenario())

    assert result.ok is True
    assert result.attempts == 3
    assert len(sent) == 3
    assert sent[0] == sent[1] == sent[2]
    assert clock.delays == [0.5, 0.5, 0.5]


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


def test_real_udp_server_restart_uses_new_correlation_for_same_boot_reset():
    class NodeProtocol(asyncio.DatagramProtocol):
        def __init__(self, node):
            self.node = node
            self.transport = None

        def connection_made(self, transport):
            self.transport = transport

        def datagram_received(self, data, addr):
            ack = self.node.handle_command(data)
            if ack is not None:
                self.transport.sendto(ack, addr)

    async def scenario():
        loop = asyncio.get_running_loop()
        node = SimulatedNode(
            SimulatorConfig(node_id=NODE_ID, boot_id=BOOT_ID, duration=1.0)
        )
        node_transport, _ = await loop.create_datagram_endpoint(
            lambda: NodeProtocol(node),
            local_addr=("127.0.0.1", 0),
        )
        server_transport = None
        try:
            server_transport, _ = await loop.create_datagram_endpoint(
                asyncio.DatagramProtocol,
                local_addr=("127.0.0.1", 0),
            )
            server_address = server_transport.get_extra_info("sockname")
            server_transport.close()
            await asyncio.sleep(0)

            async def server_session(correlation_start):
                registry = NodeRegistry()
                ingest = IngestProtocol(registry)
                transport, _ = await loop.create_datagram_endpoint(
                    lambda: ingest,
                    local_addr=server_address,
                )
                manager = CommandManager(
                    registry,
                    transport.sendto,
                    correlation_start=correlation_start,
                )
                ingest.command_manager = manager
                node_transport.sendto(node.hello(0), server_address)
                async with asyncio.timeout(0.5):
                    while registry.get(NODE_ID) is None:
                        await asyncio.sleep(0)
                result = await manager.execute(NODE_ID, CommandOpcode.RESET_BASELINE)
                transport.close()
                await asyncio.sleep(0)
                return result

            first = await server_session(101)
            second = await server_session(4001)
            return first, second, node.reset_applications
        finally:
            if server_transport is not None:
                server_transport.close()
            node_transport.close()

    first, second, reset_applications = asyncio.run(scenario())

    assert first.ok is True
    assert second.ok is True
    assert first.correlation_id == 101
    assert second.correlation_id == 4001
    assert reset_applications == 2


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
