"""Async UDP ingestion for WCSI datagrams."""

from __future__ import annotations

import asyncio
import logging
from collections import Counter
from collections.abc import Callable
from typing import TYPE_CHECKING

from .protocol import CommandAck, CsiFrame, ProtocolError, decode_packet
from .registry import NodeRegistry

if TYPE_CHECKING:
    from .commands import CommandManager


LOGGER = logging.getLogger(__name__)


class IngestProtocol(asyncio.DatagramProtocol):
    def __init__(
        self,
        registry: NodeRegistry,
        on_transport: Callable[[asyncio.DatagramTransport], None] | None = None,
        command_manager: CommandManager | None = None,
    ):
        self.registry = registry
        self.on_transport = on_transport
        self.command_manager = command_manager
        self.transport: asyncio.DatagramTransport | None = None
        self.parse_errors: Counter[str] = Counter()
        self.parse_errors_by_endpoint: dict[tuple[str, int], Counter[str]] = {}
        self.csi_lengths: Counter[int] = Counter()
        self.unexpected_errors = 0

    def connection_made(self, transport) -> None:
        self.transport = transport
        if self.on_transport is not None:
            self.on_transport(transport)

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        try:
            packet = decode_packet(data)
            if isinstance(packet.payload, CommandAck) and self.command_manager is not None:
                self.command_manager.handle_ack(packet)
            accepted = self.registry.accept(packet, addr)
            if accepted and isinstance(packet.payload, CsiFrame):
                self.csi_lengths[len(packet.payload.iq)] += 1
        except ProtocolError as error:
            self.parse_errors[error.code] += 1
            self.parse_errors_by_endpoint.setdefault(addr, Counter())[error.code] += 1
        except Exception:
            self.unexpected_errors += 1
            LOGGER.exception("unexpected WCSI ingest failure from %s", addr)

    def error_received(self, exc: Exception) -> None:
        LOGGER.warning("UDP transport error: %s", exc)
