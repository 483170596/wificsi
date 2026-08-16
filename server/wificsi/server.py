"""Headless WCSI UDP server."""

from __future__ import annotations

import argparse
import asyncio
from collections import Counter

from .ingest import IngestProtocol
from .protocol import MessageType
from .registry import NodeRegistry


def _mac(node_id: bytes) -> str:
    return ":".join(f"{byte:02x}" for byte in node_id)


def format_summary(registry: NodeRegistry, ingest: IngestProtocol) -> str:
    nodes = registry.list()
    if not nodes:
        return f"nodes=0 parse_errors={sum(ingest.parse_errors.values())}"
    parts = []
    for node in nodes:
        counts = Counter(dict(node.message_counts))
        parts.append(
            f"node={_mac(node.node_id)} state={node.lifecycle.value} "
            f"csi={counts[MessageType.CSI_FRAME]} gaps={node.counters.sequence_gaps} "
            f"duplicates={node.counters.duplicates} old={node.counters.out_of_order}"
        )
    return f"nodes={len(nodes)} parse_errors={sum(ingest.parse_errors.values())} " + " | ".join(parts)


async def run_server(host: str, port: int, duration: float | None = None) -> None:
    loop = asyncio.get_running_loop()
    registry = NodeRegistry()
    ingest = IngestProtocol(registry)
    transport, _ = await loop.create_datagram_endpoint(
        lambda: ingest,
        local_addr=(host, port),
    )
    started = loop.time()
    try:
        while duration is None or loop.time() - started < duration:
            await asyncio.sleep(1.0)
            registry.expire()
            print(format_summary(registry, ingest), flush=True)
    finally:
        transport.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=5500)
    parser.add_argument("--duration", type=float)
    args = parser.parse_args(argv)
    asyncio.run(run_server(args.host, args.port, args.duration))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
