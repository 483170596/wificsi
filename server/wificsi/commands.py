"""Bounded WCSI command transactions."""

from __future__ import annotations

import asyncio
import math
import secrets
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from .protocol import (
    Command,
    CommandAck,
    CommandOpcode,
    Header,
    Packet,
    SensingConfig,
    encode_packet,
)
from .registry import NodeLifecycle, NodeRegistry


@dataclass(frozen=True, slots=True)
class CommandResult:
    ok: bool
    correlation_id: int
    attempts: int
    ack: CommandAck | None = None
    error: str | None = None


@dataclass(slots=True)
class _Pending:
    boot_id: int
    opcode: CommandOpcode
    future: asyncio.Future[CommandAck]


def validate_config(config: SensingConfig) -> None:
    values = (
        config.motion_sensitivity,
        config.presence_sensitivity,
        config.active_jitter_min,
    )
    if not all(math.isfinite(value) for value in values):
        raise ValueError("sensing config values must be finite")
    if not 0.0 < config.motion_sensitivity <= 1.0:
        raise ValueError("motion sensitivity must be in (0, 1]")
    if not 0.0 <= config.presence_sensitivity <= 1.0:
        raise ValueError("presence sensitivity must be in [0, 1]")
    if config.active_jitter_min < 0.0:
        raise ValueError("active jitter minimum must be nonnegative")
    if not 0 <= config.active_filter_ms <= 60_000:
        raise ValueError("active filter must be between 0 and 60000 ms")


class CommandManager:
    def __init__(
        self,
        registry: NodeRegistry,
        sendto: Callable[[bytes, tuple[str, int]], None],
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        correlation_start: int | None = None,
    ):
        self._registry = registry
        self._sendto = sendto
        self._clock = clock
        self._sleep = sleep
        if correlation_start is None:
            correlation_start = secrets.randbelow(0xFFFFFFFF) + 1
        if not 1 <= correlation_start <= 0xFFFFFFFF:
            raise ValueError("correlation start must be a nonzero uint32")
        self._next_correlation = correlation_start
        self._pending: dict[tuple[bytes, int], _Pending] = {}

    def _allocate_correlation(self) -> int:
        correlation = self._next_correlation
        self._next_correlation = (correlation + 1) & 0xFFFFFFFF
        if self._next_correlation == 0:
            self._next_correlation = 1
        return correlation

    async def execute(
        self,
        node_id: bytes,
        opcode: CommandOpcode,
        config: SensingConfig | None = None,
    ) -> CommandResult:
        if opcode is CommandOpcode.SET_CONFIG:
            if config is None:
                raise ValueError("SET_CONFIG requires a sensing config")
            validate_config(config)
        elif config is not None:
            raise ValueError("this command does not accept a sensing config")

        snapshot = self._registry.get(node_id)
        correlation = self._allocate_correlation()
        if snapshot is None or snapshot.lifecycle is NodeLifecycle.OFFLINE:
            return CommandResult(False, correlation, 0, error="offline")

        packet = Packet(
            Header(
                node_id=node_id,
                boot_id=snapshot.boot_id,
                sequence=correlation,
                device_time_us=0,
            ),
            Command(correlation, opcode, config),
        )
        encoded = encode_packet(packet)
        loop = asyncio.get_running_loop()
        future: asyncio.Future[CommandAck] = loop.create_future()
        key = (node_id, correlation)
        self._pending[key] = _Pending(snapshot.boot_id, opcode, future)
        attempts = 0
        started_at = self._clock()
        try:
            for response_deadline_offset in (0.5, 1.0, 1.5):
                attempts += 1
                self._sendto(encoded, snapshot.endpoint)
                if future.done():
                    ack = future.result()
                    return CommandResult(ack.status.value == 0, correlation, attempts, ack=ack)
                delay = max(0.0, started_at + response_deadline_offset - self._clock())
                sleeper = asyncio.create_task(self._sleep(delay))
                try:
                    done, _ = await asyncio.wait(
                        {future, sleeper},
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                    if future in done:
                        ack = future.result()
                        return CommandResult(
                            ack.status.value == 0,
                            correlation,
                            attempts,
                            ack=ack,
                        )
                    await sleeper
                finally:
                    if not sleeper.done():
                        sleeper.cancel()
                    try:
                        await sleeper
                    except asyncio.CancelledError:
                        pass
            return CommandResult(False, correlation, attempts, error="timeout")
        finally:
            self._pending.pop(key, None)

    def handle_ack(self, packet: Packet) -> bool:
        if not isinstance(packet.payload, CommandAck):
            return False
        key = (packet.header.node_id, packet.payload.correlation_id)
        pending = self._pending.get(key)
        if pending is None:
            return False
        if packet.header.boot_id != pending.boot_id or packet.payload.opcode is not pending.opcode:
            return False
        if pending.future.done():
            return False
        pending.future.set_result(packet.payload)
        return True
