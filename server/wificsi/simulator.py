"""Deterministic WCSI node simulator."""

from __future__ import annotations

import argparse
import asyncio
import math
from collections import OrderedDict
from dataclasses import dataclass

from .protocol import (
    AckStatus,
    Command,
    CommandAck,
    CommandOpcode,
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
    decode_packet,
    encode_packet,
)


DEFAULT_NODE_ID = bytes.fromhex("028485872bf4")
DEFAULT_AP_BSSID = bytes.fromhex("5ee388d95b42")


@dataclass(frozen=True, slots=True)
class SimulatorConfig:
    host: str = "127.0.0.1"
    port: int = 5500
    node_id: bytes = DEFAULT_NODE_ID
    rate: float = 50.0
    duration: float = 5.0
    csi_lengths: tuple[int, ...] = (128,)
    start_sequence: int = 0
    corrupt_every: int = 0
    hello_interval: float = 30.0
    state_interval: float = 1.0
    heartbeat_interval: float = 1.0
    boot_id: int = 0x10203040

    def __post_init__(self):
        if len(self.node_id) != 6:
            raise ValueError("node_id must contain 6 bytes")
        if self.rate <= 0 or self.duration <= 0:
            raise ValueError("rate and duration must be positive")
        if not self.csi_lengths or any(length <= 0 for length in self.csi_lengths):
            raise ValueError("CSI lengths must be positive")
        if self.boot_id == 0:
            raise ValueError("boot_id must be nonzero")


class SimulatedNode:
    def __init__(self, config: SimulatorConfig):
        self.config = config
        self.sequence = config.start_sequence & 0xFFFFFFFF
        self.csi_accepted = 0
        self.csi_sent = 0
        self.sensing_config = SensingConfig(0.5, 0.25, 0.125, 300)
        self.reset_applications = 0
        self._ack_cache: OrderedDict[tuple[int, int], bytes] = OrderedDict()

    def encode(self, payload, device_time_us: int) -> bytes:
        packet = Packet(
            Header(
                node_id=self.config.node_id,
                boot_id=self.config.boot_id,
                sequence=self.sequence,
                device_time_us=device_time_us,
            ),
            payload,
        )
        self.sequence = (self.sequence + 1) & 0xFFFFFFFF
        return encode_packet(packet)

    def hello(self, device_time_us: int) -> bytes:
        return self.encode(
            Hello(1, 1, 0, 0, 0x000F, 612, 512, 1000, 1000, 5501, DEFAULT_AP_BSSID),
            device_time_us,
        )

    def sensing(self, device_time_us: int, state: StableState) -> bytes:
        process = ProcessState.ACTIVE if state is StableState.ACTIVE else ProcessState.IDLE
        return self.encode(
            SensingState(
                DEFAULT_AP_BSSID,
                state,
                process,
                InitStage.STABLE,
                0x03,
                0,
                0.5 if state is StableState.ACTIVE else 0.0,
                0.25,
                10,
                20,
                5,
                0.125,
                0.25,
                self.sensing_config,
            ),
            device_time_us,
        )

    def heartbeat(self, device_time_us: int) -> bytes:
        return self.encode(
            Heartbeat(
                device_time_us,
                300_000,
                250_000,
                self.csi_accepted,
                self.csi_sent,
                0,
                0,
            ),
            device_time_us,
        )

    def csi(self, device_time_us: int, frame_index: int) -> bytes:
        length = self.config.csi_lengths[frame_index % len(self.config.csi_lengths)]
        iq = bytes((frame_index + offset) & 0x7F for offset in range(length))
        self.csi_accepted += 1
        encoded = self.encode(
            CsiFrame(
                DEFAULT_AP_BSSID,
                self.config.node_id,
                -80,
                -95,
                4,
                0,
                device_time_us & 0xFFFFFFFF,
                128,
                11,
                1,
                3,
                0,
                0,
                0,
                0,
                False,
                iq,
            ),
            device_time_us,
        )
        self.csi_sent += 1
        if self.config.corrupt_every and (frame_index + 1) % self.config.corrupt_every == 0:
            damaged = bytearray(encoded)
            damaged[-1] ^= 1
            return bytes(damaged)
        return encoded

    def handle_command(self, data: bytes) -> bytes | None:
        try:
            packet = decode_packet(data)
        except Exception:
            return None
        if not isinstance(packet.payload, Command):
            return None
        if packet.header.node_id != self.config.node_id or packet.header.boot_id != self.config.boot_id:
            return None
        key = (packet.header.boot_id, packet.payload.correlation_id)
        cached = self._ack_cache.get(key)
        if cached is not None:
            return cached

        command = packet.payload
        status = AckStatus.OK
        config = None
        if command.opcode is CommandOpcode.GET_CONFIG:
            config = self.sensing_config
        elif command.opcode is CommandOpcode.RESET_BASELINE:
            self.reset_applications += 1
        elif command.opcode is CommandOpcode.SET_CONFIG:
            if command.config is None:
                status = AckStatus.INVALID_ARGUMENT
            else:
                self.sensing_config = command.config
                config = self.sensing_config
        ack = self.encode(
            CommandAck(command.correlation_id, command.opcode, status, config),
            0,
        )
        self._ack_cache[key] = ack
        self._ack_cache.move_to_end(key)
        while len(self._ack_cache) > 16:
            self._ack_cache.popitem(last=False)
        return ack


class _SimulatorProtocol(asyncio.DatagramProtocol):
    def __init__(self, node: SimulatedNode):
        self.node = node
        self.transport: asyncio.DatagramTransport | None = None

    def connection_made(self, transport) -> None:
        self.transport = transport

    def datagram_received(self, data: bytes, addr) -> None:
        ack = self.node.handle_command(data)
        if ack is not None and self.transport is not None:
            self.transport.sendto(ack)


async def run_simulator(config: SimulatorConfig) -> None:
    loop = asyncio.get_running_loop()
    node = SimulatedNode(config)
    transport, _ = await loop.create_datagram_endpoint(
        lambda: _SimulatorProtocol(node),
        remote_addr=(config.host, config.port),
    )
    start = loop.time()
    next_hello = start
    next_state = start
    next_heartbeat = start
    total_frames = math.ceil(config.duration * config.rate)

    def elapsed_us() -> int:
        return max(0, int((loop.time() - start) * 1_000_000))

    try:
        for frame_index in range(total_frames):
            target = start + frame_index / config.rate
            await asyncio.sleep(max(0.0, target - loop.time()))
            now = loop.time()
            while now >= next_hello:
                transport.sendto(node.hello(elapsed_us()))
                next_hello += config.hello_interval
            while now >= next_state:
                state = (
                    StableState.ACTIVE
                    if now - start < config.duration / 2
                    else StableState.INACTIVE
                )
                transport.sendto(node.sensing(elapsed_us(), state))
                next_state += config.state_interval
            while now >= next_heartbeat:
                transport.sendto(node.heartbeat(elapsed_us()))
                next_heartbeat += config.heartbeat_interval
            transport.sendto(node.csi(elapsed_us(), frame_index))

        transport.sendto(node.sensing(elapsed_us(), StableState.INACTIVE))
        transport.sendto(node.heartbeat(elapsed_us()))
        await asyncio.sleep(0)
    finally:
        transport.close()


def _mac(value: str) -> bytes:
    compact = value.replace(":", "").replace("-", "")
    parsed = bytes.fromhex(compact)
    if len(parsed) != 6:
        raise argparse.ArgumentTypeError("node ID must contain six bytes")
    return parsed


def _lengths(value: str) -> tuple[int, ...]:
    try:
        parsed = tuple(int(item) for item in value.split(","))
    except ValueError as error:
        raise argparse.ArgumentTypeError("CSI lengths must be comma-separated integers") from error
    if not parsed or any(item <= 0 for item in parsed):
        raise argparse.ArgumentTypeError("CSI lengths must be positive")
    return parsed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5500)
    parser.add_argument("--node-id", type=_mac, default=DEFAULT_NODE_ID)
    parser.add_argument("--rate", type=float, default=50.0)
    parser.add_argument("--duration", type=float, default=5.0)
    parser.add_argument("--csi-lengths", type=_lengths, default=(128,))
    parser.add_argument("--start-sequence", type=int, default=0)
    parser.add_argument("--corrupt-every", type=int, default=0)
    args = parser.parse_args(argv)
    asyncio.run(
        run_simulator(
            SimulatorConfig(
                host=args.host,
                port=args.port,
                node_id=args.node_id,
                rate=args.rate,
                duration=args.duration,
                csi_lengths=args.csi_lengths,
                start_sequence=args.start_sequence,
                corrupt_every=args.corrupt_every,
            )
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
