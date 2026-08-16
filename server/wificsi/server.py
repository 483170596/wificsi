"""Headless WCSI UDP server."""

from __future__ import annotations

import argparse
import asyncio
import json
from collections import Counter
from pathlib import Path

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


def _command_summary(node_id: bytes, opcode: CommandOpcode, result: CommandResult) -> dict[str, object]:
    return {
        "node_id": _mac(node_id),
        "opcode": opcode.name,
        "ok": result.ok,
        "attempts": result.attempts,
        "correlation_id": result.correlation_id,
        "error": result.error,
    }


def _print_command_result(node_id: bytes, opcode: CommandOpcode, task) -> CommandResult | None:
    if task.cancelled():
        return None
    result = task.result()
    print(
        f"command node={_mac(node_id)} opcode={opcode.name} ok={str(result.ok).lower()} "
        f"attempts={result.attempts} error={result.error or '-'}",
        flush=True,
    )
    return result


def _metrics_row(
    host: str,
    port: int,
    elapsed: float,
    registry: NodeRegistry,
    ingest: IngestProtocol,
    node,
    previous_csi: int,
    command_results: list[dict[str, object]],
) -> dict[str, object]:
    parse_errors = sum(ingest.parse_errors.values())
    if node is None:
        return {
            "elapsed": elapsed, "server_host": host, "server_port": port,
            "node_id": None, "boot_id": None, "state": "OFFLINE",
            "message_counts": {}, "csi_rate": 0.0,
            "device_queue_dropped": 0, "device_send_errors": 0,
            "server_sequence_gaps": 0, "server_duplicates": 0,
            "server_out_of_order": 0, "parse_errors": parse_errors,
            "last_seen_age": None, "command_results": command_results,
        }
    counts = Counter(dict(node.message_counts))
    heartbeat = node.heartbeat
    csi_count = counts[MessageType.CSI_FRAME]
    return {
        "elapsed": elapsed, "server_host": host, "server_port": port,
        "node_id": _mac(node.node_id), "boot_id": node.boot_id,
        "state": node.lifecycle.value,
        "message_counts": {kind.name: counts[kind] for kind in MessageType},
        "csi_rate": max(0, csi_count - previous_csi),
        "device_queue_dropped": heartbeat.queue_dropped if heartbeat else 0,
        "device_send_errors": heartbeat.udp_send_errors if heartbeat else 0,
        "server_sequence_gaps": node.counters.sequence_gaps,
        "server_duplicates": node.counters.duplicates,
        "server_out_of_order": node.counters.out_of_order,
        "parse_errors": parse_errors,
        "last_seen_age": max(0.0, asyncio.get_running_loop().time() - node.last_seen),
        "command_results": command_results,
    }


async def run_server(
    host: str,
    port: int,
    duration: float | None = None,
    command_requests: tuple[tuple[bytes, CommandOpcode], ...] = (),
    metrics_jsonl: Path | None = None,
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
    if metrics_jsonl is not None:
        metrics_jsonl.parent.mkdir(parents=True, exist_ok=True)
    command_tasks = []
    command_results: list[dict[str, object]] = []
    for node_id, opcode in command_requests:
        task = asyncio.create_task(execute_when_online(registry, manager, node_id, opcode))
        def record_result(completed, node_id=node_id, opcode=opcode) -> None:
            result = _print_command_result(node_id, opcode, completed)
            if result is not None:
                command_results.append(_command_summary(node_id, opcode, result))
        task.add_done_callback(record_result)
        command_tasks.append(task)
    started = loop.time()
    previous_csi: dict[bytes, int] = {}
    try:
        while duration is None or loop.time() - started < duration:
            await asyncio.sleep(1.0)
            registry.expire()
            print(format_summary(registry, ingest), flush=True)
            if metrics_jsonl is not None:
                snapshots = registry.list()
                with metrics_jsonl.open("a", encoding="utf-8") as output:
                    for node in snapshots or (None,):
                        node_id = node.node_id if node is not None else b""
                        prior = previous_csi.get(node_id, 0)
                        row = _metrics_row(
                            host, port, loop.time() - started, registry, ingest, node,
                            prior, command_results,
                        )
                        output.write(json.dumps(row, separators=(",", ":")) + "\n")
                        if node is not None:
                            previous_csi[node_id] = Counter(dict(node.message_counts))[MessageType.CSI_FRAME]
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
    parser.add_argument("--metrics-jsonl", type=Path)
    args = parser.parse_args(argv)
    requests = tuple(
        [(node_id, CommandOpcode.GET_CONFIG) for node_id in args.get_config]
        + [(node_id, CommandOpcode.RESET_BASELINE) for node_id in args.reset_baseline]
    )
    asyncio.run(run_server(args.host, args.port, args.duration, requests, args.metrics_jsonl))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
