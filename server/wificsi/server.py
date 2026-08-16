"""Headless WCSI UDP server."""

from __future__ import annotations

import argparse
import asyncio
from collections import Counter

from .commands import CommandManager, CommandResult
from .ingest import IngestProtocol
from .protocol import CommandOpcode, MessageType
from .registry import NodeLifecycle, NodeRegistry


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


async def execute_when_online(
    registry: NodeRegistry,
    manager: CommandManager,
    node_id: bytes,
    opcode: CommandOpcode,
    *,
    timeout: float = 35.0,
    poll_interval: float = 0.1,
) -> CommandResult:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        snapshot = registry.get(node_id)
        if snapshot is not None and snapshot.lifecycle is not NodeLifecycle.OFFLINE:
            return await manager.execute(node_id, opcode)
        await asyncio.sleep(poll_interval)
    return CommandResult(False, 0, 0, error="discovery_timeout")


def _print_command_result(node_id: bytes, opcode: CommandOpcode, task) -> None:
    if task.cancelled():
        return
    result = task.result()
    print(
        f"command node={_mac(node_id)} opcode={opcode.name} ok={str(result.ok).lower()} "
        f"attempts={result.attempts} error={result.error or '-'}",
        flush=True,
    )


async def run_server(
    host: str,
    port: int,
    duration: float | None = None,
    command_requests: tuple[tuple[bytes, CommandOpcode], ...] = (),
) -> None:
    loop = asyncio.get_running_loop()
    registry = NodeRegistry()
    ingest = IngestProtocol(registry)
    transport, _ = await loop.create_datagram_endpoint(
        lambda: ingest,
        local_addr=(host, port),
    )
    manager = CommandManager(registry, transport.sendto)
    ingest.command_manager = manager
    command_tasks = []
    for node_id, opcode in command_requests:
        task = asyncio.create_task(execute_when_online(registry, manager, node_id, opcode))
        task.add_done_callback(
            lambda completed, node_id=node_id, opcode=opcode: _print_command_result(
                node_id, opcode, completed
            )
        )
        command_tasks.append(task)
    started = loop.time()
    try:
        while duration is None or loop.time() - started < duration:
            await asyncio.sleep(1.0)
            registry.expire()
            print(format_summary(registry, ingest), flush=True)
    finally:
        for task in command_tasks:
            if not task.done():
                task.cancel()
        transport.close()


def _node_id(value: str) -> bytes:
    try:
        parsed = bytes.fromhex(value.replace(":", "").replace("-", ""))
    except ValueError as error:
        raise argparse.ArgumentTypeError("node ID must be hexadecimal") from error
    if len(parsed) != 6:
        raise argparse.ArgumentTypeError("node ID must contain six bytes")
    return parsed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=5500)
    parser.add_argument("--duration", type=float)
    parser.add_argument("--get-config", action="append", type=_node_id, default=[])
    parser.add_argument("--reset-baseline", action="append", type=_node_id, default=[])
    args = parser.parse_args(argv)
    requests = tuple(
        [(node_id, CommandOpcode.GET_CONFIG) for node_id in args.get_config]
        + [(node_id, CommandOpcode.RESET_BASELINE) for node_id in args.reset_baseline]
    )
    asyncio.run(run_server(args.host, args.port, args.duration, requests))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
