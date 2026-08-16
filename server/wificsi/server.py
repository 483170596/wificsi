"""Headless WCSI UDP server."""

from __future__ import annotations

import argparse
import asyncio
import json
import time
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
    registry: NodeRegistry,
    ingest: IngestProtocol,
    node,
    observed_at: float,
    server_run_started_at: float,
    run_elapsed: float,
    previous: tuple[float, int, int, Counter[MessageType]] | None,
    command_results: dict[bytes, list[dict[str, object]]],
) -> dict[str, object]:
    parse_errors = sum(ingest.parse_errors.values())
    if node is None:
        return {
            "observed_at": observed_at, "server_run_started_at": server_run_started_at,
            "run_elapsed": run_elapsed,
            "server_host": host, "server_port": port,
            "node_id": None, "boot_id": None, "state": "OFFLINE",
            "message_counts": {}, "message_deltas": {}, "sample_interval": None,
            "sample_started_at": None,
            "csi_delta": 0, "csi_rate": None,
            "device_queue_dropped": 0, "device_send_errors": 0,
            "server_sequence_gaps": 0, "server_duplicates": 0,
            "server_out_of_order": 0, "parse_errors": parse_errors,
            "last_seen_age": None, "command_results": [],
        }
    counts = Counter(dict(node.message_counts))
    heartbeat = node.heartbeat
    csi_count = counts[MessageType.CSI_FRAME]
    if previous is not None and previous[1] == node.boot_id:
        sample_started_at = previous[0]
        interval = observed_at - sample_started_at
        csi_delta = csi_count - previous[2]
        if interval <= 0 or csi_delta < 0:
            interval = None
            csi_delta = 0
            csi_rate = None
            message_deltas = {kind.name: 0 for kind in MessageType}
        else:
            csi_rate = csi_delta / interval
            message_deltas = {
                kind.name: max(0, counts[kind] - previous[3][kind])
                for kind in MessageType
            }
    elif previous is None:
        sample_started_at = server_run_started_at
        interval = observed_at - sample_started_at
        csi_delta = csi_count
        csi_rate = csi_delta / interval if interval > 0 else None
        if interval <= 0:
            interval = None
        message_deltas = {kind.name: counts[kind] for kind in MessageType}
    else:
        sample_started_at = None
        interval = None
        csi_delta = 0
        csi_rate = None
        message_deltas = {kind.name: counts[kind] for kind in MessageType}
    endpoint_errors = ingest.parse_errors_by_endpoint.get(node.endpoint, Counter())
    return {
        "observed_at": observed_at, "server_run_started_at": server_run_started_at,
        "run_elapsed": run_elapsed,
        "server_host": host, "server_port": port,
        "node_id": _mac(node.node_id), "boot_id": node.boot_id,
        "state": node.lifecycle.value,
        "message_counts": {kind.name: counts[kind] for kind in MessageType},
        "message_deltas": message_deltas,
        "sample_interval": interval,
        "sample_started_at": sample_started_at,
        "csi_delta": csi_delta,
        "csi_rate": csi_rate,
        "device_queue_dropped": heartbeat.queue_dropped if heartbeat else 0,
        "device_send_errors": heartbeat.udp_send_errors if heartbeat else 0,
        "server_sequence_gaps": node.counters.sequence_gaps,
        "server_duplicates": node.counters.duplicates,
        "server_out_of_order": node.counters.out_of_order,
        "parse_errors": sum(endpoint_errors.values()),
        "last_seen_age": max(0.0, asyncio.get_running_loop().time() - node.last_seen),
        "command_results": command_results.pop(node.node_id, []),
    }


async def run_server(
    host: str,
    port: int,
    duration: float | None = None,
    command_requests: tuple[tuple[bytes, CommandOpcode], ...] = (),
    metrics_jsonl: Path | None = None,
    metrics_interval: float = 1.0,
) -> None:
    if metrics_interval <= 0:
        raise ValueError("metrics interval must be positive")
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
    command_results: dict[bytes, list[dict[str, object]]] = {}
    for node_id, opcode in command_requests:
        task = asyncio.create_task(execute_when_online(registry, manager, node_id, opcode))
        def record_result(completed, node_id=node_id, opcode=opcode) -> None:
            result = _print_command_result(node_id, opcode, completed)
            if result is not None:
                command_results.setdefault(node_id, []).append(_command_summary(node_id, opcode, result))
        task.add_done_callback(record_result)
        command_tasks.append(task)
    started = loop.time()
    server_run_started_at = time.time()
    previous_samples: dict[bytes, tuple[float, int, int, Counter[MessageType]]] = {}
    try:
        while duration is None or loop.time() - started < duration:
            await asyncio.sleep(metrics_interval)
            registry.expire()
            print(format_summary(registry, ingest), flush=True)
            if metrics_jsonl is not None:
                snapshots = registry.list()
                with metrics_jsonl.open("a", encoding="utf-8") as output:
                    for node in snapshots or (None,):
                        observed_at = time.time()
                        node_id = node.node_id if node is not None else b""
                        row = _metrics_row(
                            host, port, registry, ingest, node, observed_at, server_run_started_at,
                            loop.time() - started, previous_samples.get(node_id), command_results,
                        )
                        output.write(json.dumps(row, separators=(",", ":")) + "\n")
                        if node is not None:
                            previous_samples[node_id] = (
                                observed_at, node.boot_id,
                                Counter(dict(node.message_counts))[MessageType.CSI_FRAME],
                                Counter(dict(node.message_counts)),
                            )
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
