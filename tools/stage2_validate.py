"""Validate machine-readable evidence for the Stage 2 node-to-server gate."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


MIN_CSI_RATE = 43.88 * 0.5
REQUIRED_COUNTERS = (
    "device_queue_dropped",
    "device_send_errors",
    "server_sequence_gaps",
    "server_duplicates",
    "server_out_of_order",
)
REQUIRED_RESUMED_MESSAGES = ("HELLO", "CSI_FRAME", "SENSING_STATE", "HEARTBEAT")


@dataclass(frozen=True, slots=True)
class Stage2Result:
    passed: bool
    failures: tuple[str, ...]
    metrics: dict[str, Any]


def _number(value: object) -> float | None:
    return float(value) if isinstance(value, int | float) else None


def _last_command_results(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    for row in reversed(rows):
        results = row.get("command_results")
        if isinstance(results, list) and results:
            return [item for item in results if isinstance(item, dict)]
    return []


def validate(rows: list[dict[str, Any]], observations: dict[str, Any]) -> Stage2Result:
    """Return the Stage 2 result using only compact server summaries and observations."""
    failures: list[str] = []
    if not rows:
        return Stage2Result(False, ("missing_metrics",), {"duration_seconds": 0, "qualified_csi_seconds": 0})

    results = _last_command_results(rows)
    command_nodes = {item.get("node_id") for item in results if isinstance(item.get("node_id"), str)}
    if command_nodes:
        rows = [row for row in rows if row.get("node_id") in command_nodes]
    elapsed = [value for row in rows if (value := _number(row.get("elapsed"))) is not None]
    duration = max(elapsed) - min(elapsed) if elapsed else 0.0
    if any(row.get("server_host") != "10.204.75.168" or row.get("server_port") != 5500 for row in rows):
        failures.append("wrong_server_endpoint")
    if duration < 600:
        failures.append("duration_below_600_seconds")

    qualified_seconds = sum(
        1
        for row in rows
        if (_number(row.get("csi_rate")) or 0) >= MIN_CSI_RATE and (_number(row.get("parse_errors")) or 0) == 0
    )
    if any((_number(row.get("parse_errors")) or 0) > 0 for row in rows):
        failures.append("protocol_parse_errors")
    if qualified_seconds < 60:
        failures.append("insufficient_valid_csi_rate")

    states = {row.get("state") for row in rows}
    if "ACTIVE" not in states:
        failures.append("missing_active_state")
    if "INACTIVE" not in states:
        failures.append("missing_inactive_state")
    if any(any(counter not in row or _number(row[counter]) is None for counter in REQUIRED_COUNTERS) for row in rows):
        failures.append("missing_link_counters")

    stopped = _number(observations.get("server_stopped_at"))
    started = _number(observations.get("server_started_at"))
    rediscovered = _number(observations.get("server_rediscovered_at"))
    if stopped is None or started is None or rediscovered is None:
        failures.append("missing_server_restart_observation")
    else:
        if started - stopped < 10:
            failures.append("server_absence_below_10_seconds")
        if rediscovered - started > 35:
            failures.append("server_rediscovery_exceeded_35_seconds")

    disconnected = _number(observations.get("reconnect_disconnected_at"))
    reconnected = _number(observations.get("reconnect_reconnected_at"))
    counts = [row.get("message_counts") for row in rows if isinstance(row.get("message_counts"), dict)]
    boot_ids = {row.get("boot_id") for row in rows if row.get("boot_id") is not None}
    if (
        disconnected is None
        or reconnected is None
        or reconnected < disconnected
        or len(boot_ids) != 1
        or not all(any((_number(count.get(kind)) or 0) > 0 for count in counts) for kind in REQUIRED_RESUMED_MESSAGES)
    ):
        failures.append("missing_reconnect_cycle")

    successful = [str(item.get("opcode")) for item in results if item.get("ok") is True]
    get_config_count = successful.count("GET_CONFIG")
    reset_baseline_count = successful.count("RESET_BASELINE")
    if get_config_count == 0:
        failures.append("missing_successful_get_config")
    elif get_config_count != 1:
        failures.append("unexpected_get_config_count")
    if reset_baseline_count == 0:
        failures.append("missing_successful_reset_baseline")
    elif reset_baseline_count != 1:
        failures.append("unexpected_reset_baseline_count")

    metrics = {
        "duration_seconds": duration,
        "qualified_csi_seconds": qualified_seconds,
        "rows": len(rows),
        "boot_ids": len(boot_ids),
    }
    return Stage2Result(not failures, tuple(failures), metrics)


def _read_metrics(path: Path) -> list[dict[str, Any]]:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError("metrics JSONL entries must be objects")
            rows.append(value)
    return rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--metrics", type=Path, required=True)
    parser.add_argument("--observations", type=Path, required=True)
    args = parser.parse_args(argv)
    observations = json.loads(args.observations.read_text(encoding="utf-8"))
    if not isinstance(observations, dict):
        raise ValueError("observations JSON must be an object")
    result = validate(_read_metrics(args.metrics), observations)
    print(json.dumps(asdict(result), sort_keys=True, separators=(",", ":")))
    return 0 if result.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
