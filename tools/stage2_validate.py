"""Validate timestamped Stage 2 node-to-server acceptance evidence."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


MIN_CSI_RATE = 43.88 * 0.5
REQUIRED_COUNTERS = (
    "device_queue_dropped", "device_send_errors", "server_sequence_gaps",
    "server_duplicates", "server_out_of_order",
)
REQUIRED_RESUMED_MESSAGES = ("HELLO", "CSI_FRAME", "SENSING_STATE", "HEARTBEAT")


@dataclass(frozen=True, slots=True)
class Stage2Result:
    passed: bool
    failures: tuple[str, ...]
    metrics: dict[str, Any]


def _number(value: object) -> float | None:
    return float(value) if isinstance(value, int | float) else None


def _command_events(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        event for row in rows for event in row.get("command_results", [])
        if isinstance(event, dict)
    ]


def _message_total(rows: list[dict[str, Any]], name: str) -> int:
    return sum(
        int(value) for row in rows
        if isinstance(row.get("message_deltas"), dict)
        if (value := _number(row["message_deltas"].get(name))) is not None and value > 0
    )


def _sample_interval(row: dict[str, Any]) -> float | None:
    started = _number(row.get("sample_started_at"))
    observed = _number(row.get("observed_at"))
    interval = _number(row.get("sample_interval"))
    run_started = _number(row.get("server_run_started_at"))
    if (
        started is None
        or observed is None
        or interval is None
        or run_started is None
        or interval <= 0
        or started < run_started
        or observed <= started
        or abs((observed - started) - interval) > 0.001
    ):
        return None
    return interval


def _has_target_telemetry(row: dict[str, Any]) -> bool:
    deltas = row.get("message_deltas")
    return (
        row.get("state") != "OFFLINE"
        and isinstance(deltas, dict)
        and any((_number(deltas.get(name)) or 0) > 0 for name in REQUIRED_RESUMED_MESSAGES)
    )


def validate(
    rows: list[dict[str, Any]],
    observations: dict[str, Any],
    *,
    expected_host: str = "10.204.75.168",
    expected_port: int = 5500,
    minimum_duration: float = 600.0,
    minimum_csi_seconds: float = 60.0,
    minimum_server_absence: float = 10.0,
    maximum_rediscovery: float = 35.0,
) -> Stage2Result:
    """Return an eight-condition Stage 2 result from absolute-time JSONL rows."""
    failures: list[str] = []
    target = observations.get("target_node_id")
    if not isinstance(target, str):
        return Stage2Result(False, ("missing_target_node",), {"duration_seconds": 0, "qualified_csi_seconds": 0})
    target_rows = [row for row in rows if row.get("node_id") == target]
    if not target_rows:
        return Stage2Result(False, ("missing_target_evidence",), {"duration_seconds": 0, "qualified_csi_seconds": 0})

    times = [_number(row.get("observed_at")) for row in target_rows]
    if any(value is None for value in times):
        failures.append("missing_observed_at")
    timestamps = [value for value in times if value is not None]
    if len(timestamps) > 1 and any(later <= earlier for earlier, later in zip(timestamps, timestamps[1:])):
        failures.append("non_monotonic_observed_at")
    start_time = min(timestamps) if timestamps else 0.0
    end_time = max(timestamps) if timestamps else 0.0
    duration = 0.0

    if any(row.get("server_host") != expected_host or row.get("server_port") != expected_port for row in target_rows):
        failures.append("wrong_server_endpoint")
    qualified_seconds = 0.0
    for row in target_rows:
        interval = _sample_interval(row)
        delta = _number(row.get("csi_delta"))
        rate = _number(row.get("csi_rate"))
        if _number(row.get("sample_interval")) is not None and interval is None:
            failures.append("invalid_sample_interval")
        if interval is None:
            continue
        if _has_target_telemetry(row):
            duration += interval
        if delta is None or delta < 0 or rate is None or abs(rate - delta / interval) > 0.001:
            failures.append("invalid_csi_measurement")
            continue
        if rate >= MIN_CSI_RATE and (_number(row.get("parse_errors")) or 0) == 0:
            qualified_seconds += interval
    if duration < minimum_duration:
        failures.append("duration_below_600_seconds")
    if any((_number(row.get("parse_errors")) or 0) > 0 for row in target_rows):
        failures.append("protocol_parse_errors")
    if qualified_seconds < minimum_csi_seconds:
        failures.append("insufficient_valid_csi_rate")

    states = {row.get("state") for row in target_rows}
    if "ACTIVE" not in states:
        failures.append("missing_active_state")
    if "INACTIVE" not in states:
        failures.append("missing_inactive_state")
    if any(any(counter not in row or _number(row[counter]) is None for counter in REQUIRED_COUNTERS) for row in target_rows):
        failures.append("missing_link_counters")

    stopped = _number(observations.get("server_stopped_at"))
    started = _number(observations.get("server_started_at"))
    rediscovered = _number(observations.get("server_rediscovered_at"))
    if stopped is None or started is None or rediscovered is None:
        failures.append("missing_server_restart_observation")
    elif not stopped <= started <= rediscovered:
        failures.append("invalid_restart_observation_order")
    elif not start_time <= stopped <= started <= rediscovered <= end_time:
        failures.append("restart_outside_evidence_window")
    else:
        if started - stopped < minimum_server_absence:
            failures.append("server_absence_below_10_seconds")
        if rediscovered - started > maximum_rediscovery:
            failures.append("server_rediscovery_exceeded_35_seconds")
        restart_rows = [
            row for row, timestamp in zip(target_rows, times)
            if timestamp is not None and started <= timestamp <= rediscovered
        ]
        if not any(
            (run_started := _number(row.get("server_run_started_at"))) is not None
            and run_started >= started
            for row in restart_rows
        ):
            failures.append("missing_post_restart_server_run")
        if not any(_message_total([row], "HELLO") > 0 for row in restart_rows):
            failures.append("missing_post_restart_hello")

    disconnected = _number(observations.get("reconnect_disconnected_at"))
    reconnected = _number(observations.get("reconnect_reconnected_at"))
    if disconnected is None or reconnected is None:
        failures.append("missing_reconnect_cycle")
    elif reconnected < disconnected:
        failures.append("invalid_reconnect_observation_order")
    elif not start_time <= disconnected <= reconnected <= end_time:
        failures.append("reconnect_outside_evidence_window")
    else:
        boot_ids = {row.get("boot_id") for row in target_rows if row.get("boot_id") is not None}
        if len(boot_ids) != 1:
            failures.append("reconnect_reboot_detected")
        post_reconnect = [
            row for row, timestamp in zip(target_rows, times)
            if (
                timestamp is not None
                and timestamp > reconnected
                and (sample_started := _number(row.get("sample_started_at"))) is not None
                and sample_started >= reconnected
            )
        ]
        if not all(_message_total(post_reconnect, name) > 0 for name in REQUIRED_RESUMED_MESSAGES):
            failures.append("missing_post_reconnect_telemetry")
        if any(event in {stopped, started, rediscovered} for event in (disconnected, reconnected)):
            failures.append("reconnect_not_distinct_from_server_restart")
        if stopped is not None and rediscovered is not None and not (
            reconnected < stopped or disconnected > rediscovered
        ):
            failures.append("reconnect_overlaps_server_restart")

    events = _command_events(rows)
    command_nodes = {event.get("node_id") for event in events if event.get("opcode") in {"GET_CONFIG", "RESET_BASELINE"}}
    if command_nodes != {target}:
        failures.append("command_node_mismatch")
    target_successes = [event for event in events if event.get("node_id") == target and event.get("ok") is True]
    for opcode, missing, unexpected in (
        ("GET_CONFIG", "missing_successful_get_config", "unexpected_get_config_count"),
        ("RESET_BASELINE", "missing_successful_reset_baseline", "unexpected_reset_baseline_count"),
    ):
        count = sum(event.get("opcode") == opcode for event in target_successes)
        if count == 0:
            failures.append(missing)
        elif count != 1:
            failures.append(unexpected)

    metrics = {
        "duration_seconds": duration,
        "qualified_csi_seconds": qualified_seconds,
        "rows": len(target_rows),
        "target_node_id": target,
    }
    return Stage2Result(not failures, tuple(dict.fromkeys(failures)), metrics)


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
