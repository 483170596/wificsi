"""Async UDP ingestion for WCSI datagrams."""

from __future__ import annotations

import asyncio
import logging
from collections import Counter
from collections.abc import Callable

from .protocol import CsiFrame, ProtocolError, decode_packet
from .registry import NodeRegistry


LOGGER = logging.getLogger(__name__)


class IngestProtocol(asyncio.DatagramProtocol):
    def __init__(
        self,
        registry: NodeRegistry,
        on_transport: Callable[[asyncio.DatagramTransport], None] | None = None,
    ):
        self.registry = registry
        self.on_transport = on_transport
        self.transport: asyncio.DatagramTransport | None = None
        self.parse_errors: Counter[str] = Counter()
        self.csi_lengths: Counter[int] = Counter()
        self.unexpected_errors = 0

    def connection_made(self, transport) -> None:
        self.transport = transport
        if self.on_transport is not None:
            self.on_transport(transport)

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        try:
            packet = decode_packet(data)
            accepted = self.registry.accept(packet, addr)
            if accepted and isinstance(packet.payload, CsiFrame):
                self.csi_lengths[len(packet.payload.iq)] += 1
        except ProtocolError as error:
            self.parse_errors[error.code] += 1
        except Exception:
            self.unexpected_errors += 1
            LOGGER.exception("unexpected WCSI ingest failure from %s", addr)

    def error_received(self, exc: Exception) -> None:
        LOGGER.warning("UDP transport error: %s", exc)
