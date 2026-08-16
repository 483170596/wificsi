import asyncio
import json
import socket
import subprocess
import sys
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

from stage2_validate import validate
from wificsi.protocol import CommandOpcode
from wificsi.server import run_server
from wificsi.simulator import SimulatorConfig, run_simulator


NODE_ID = "28:84:85:87:2b:f4"
OTHER_NODE_ID = "02:aa:bb:cc:dd:ee"


def _metric(observed_at, *, state="INACTIVE", sample_interval=1.0, csi_rate=43.88,
            message_deltas=None, command_results=None, **changes):
    value = {
        "observed_at": observed_at,
        "server_run_started_at": observed_at - 1,
        "run_elapsed": 1.0,
        "server_host": "10.204.75.168",
        "server_port": 5500,
        "node_id": NODE_ID,
        "boot_id": 0x10203040,
        "state": state,
        "message_counts": {
            "HELLO": 2, "CSI_FRAME": 100, "SENSING_STATE": 2,
            "HEARTBEAT": 2, "COMMAND_ACK": 2,
        },
        "message_deltas": message_deltas or {"CSI_FRAME": 44},
        "sample_interval": sample_interval,
        "csi_delta": 1097 if csi_rate is not None else 0,
        "csi_rate": csi_rate,
        "device_queue_dropped": 0,
        "device_send_errors": 0,
        "server_sequence_gaps": 0,
        "server_duplicates": 0,
        "server_out_of_order": 0,
        "parse_errors": 0,
        "last_seen_age": 0.1,
        "command_results": command_results or [],
    }
    value.update(changes)
    return value


def _passing_inputs():
    resumed = {"HELLO": 1, "CSI_FRAME": 1, "SENSING_STATE": 1, "HEARTBEAT": 1}
    metrics = []
    for observed_at in (1025, 1050, 1075, 1100):
        metrics.append(_metric(
            observed_at, state="ACTIVE" if observed_at == 1025 else "INACTIVE",
            sample_interval=25.0, sample_started_at=observed_at - 25,
            server_run_started_at=1000,
        ))
    metrics.append(_metric(1136, sample_interval=None, csi_rate=None, message_deltas=resumed, server_run_started_at=1111))
    for observed_at in (1161, 1186, 1211, 1236, 1261):
        metrics.append(_metric(
            observed_at, sample_interval=25.0, sample_started_at=observed_at - 25,
            server_run_started_at=1111,
        ))
    metrics.append(_metric(1300, sample_interval=None, csi_rate=None, message_deltas=resumed, server_run_started_at=1111))
    for observed_at in range(1325, 1676, 25):
        metrics.append(_metric(
            observed_at, sample_interval=25.0, sample_started_at=observed_at - 25,
            server_run_started_at=1111, message_deltas=resumed if observed_at == 1325 else {"CSI_FRAME": 44},
        ))
    metrics[-1]["command_results"] = [
        {"node_id": NODE_ID, "opcode": "GET_CONFIG", "ok": True, "attempts": 1},
        {"node_id": NODE_ID, "opcode": "RESET_BASELINE", "ok": True, "attempts": 1},
    ]
    observations = {
        "target_node_id": NODE_ID,
        "server_stopped_at": 1100,
        "server_started_at": 1110,
        "server_rediscovered_at": 1136,
        "reconnect_disconnected_at": 1270,
        "reconnect_reconnected_at": 1300,
    }
    return metrics, observations


def test_stage2_gate_accepts_absolute_exact_design_boundaries():
    metrics, observations = _passing_inputs()

    result = validate(metrics, observations)

    assert result.passed is True
    assert result.failures == ()
    assert result.metrics["duration_seconds"] == 600
    assert result.metrics["qualified_csi_seconds"] == 600


def test_duration_is_sum_of_legitimate_intervals_not_wall_clock_span():
    metrics, observations = _passing_inputs()
    measured = [row for row in metrics if row.get("sample_interval")]
    for row in measured[3:]:
        row["sample_interval"] = None
        row["csi_rate"] = None

    result = validate(metrics, observations)

    assert result.metrics["duration_seconds"] == 75
    assert "duration_below_600_seconds" in result.failures


def test_duration_excludes_silent_target_intervals_even_when_the_wall_span_is_600_seconds():
    metrics, observations = _passing_inputs()
    measured = [row for row in metrics if row.get("sample_interval")]
    for row in measured[3:]:
        row["message_deltas"] = {}

    result = validate(metrics, observations)

    assert result.metrics["duration_seconds"] == 75
    assert "duration_below_600_seconds" in result.failures


def test_reconnect_requires_deltas_strictly_after_the_observed_resume_time():
    metrics, observations = _passing_inputs()
    for row in metrics:
        if row.get("sample_started_at", 0) >= observations["reconnect_reconnected_at"]:
            row["message_deltas"] = {}

    result = validate(metrics, observations)

    assert "missing_post_reconnect_telemetry" in result.failures


def test_reversed_reconnect_timestamps_report_the_order_failure_first():
    metrics, observations = _passing_inputs()
    observations["reconnect_disconnected_at"] = 1400
    observations["reconnect_reconnected_at"] = 1300

    assert "invalid_reconnect_observation_order" in validate(metrics, observations).failures


def test_reconnect_window_must_not_overlap_or_nest_inside_server_restart():
    metrics, observations = _passing_inputs()
    observations.update(reconnect_disconnected_at=1105, reconnect_reconnected_at=1120)
    assert "reconnect_overlaps_server_restart" in validate(metrics, observations).failures

    metrics, observations = _passing_inputs()
    observations.update(reconnect_disconnected_at=1050, reconnect_reconnected_at=1150)
    assert "reconnect_overlaps_server_restart" in validate(metrics, observations).failures


def test_each_missing_stage2_condition_has_a_stable_reason():
    cases = {
        "server_endpoint": (lambda metrics, obs: metrics[0].update(server_port=5501), "wrong_server_endpoint"),
        "continuous_duration": (lambda metrics, obs: [row.update(sample_interval=None, csi_rate=None) for row in metrics if row.get("sample_interval")][3:], "duration_below_600_seconds"),
        "csi_rate": (lambda metrics, obs: [row.update(csi_rate=21.939) for row in metrics if row["csi_rate"] is not None], "insufficient_valid_csi_rate"),
        "states": (lambda metrics, obs: [row.update(state="INACTIVE") for row in metrics], "missing_active_state"),
        "counter_accounting": (lambda metrics, obs: metrics[-1].pop("server_duplicates"), "missing_link_counters"),
        "server_restart": (lambda metrics, obs: obs.update(server_rediscovered_at=1145.001), "server_rediscovery_exceeded_35_seconds"),
        "reconnect": (lambda metrics, obs: [row.update(message_deltas={}) for row in metrics if row.get("sample_started_at", 0) >= obs["reconnect_reconnected_at"]], "missing_post_reconnect_telemetry"),
        "commands": (lambda metrics, obs: metrics[-1].update(command_results=[]), "missing_successful_get_config"),
    }
    for mutate, expected in cases.values():
        metrics, observations = _passing_inputs()
        mutate(metrics, observations)
        assert expected in validate(metrics, observations).failures


def test_gate_rejects_duplicate_timestamps_and_out_of_window_or_reversed_events():
    metrics, observations = _passing_inputs()
    metrics[2]["observed_at"] = metrics[1]["observed_at"]
    observations["server_rediscovered_at"] = 75
    observations["reconnect_reconnected_at"] = 1750

    result = validate(metrics, observations)

    assert "non_monotonic_observed_at" in result.failures
    assert "invalid_restart_observation_order" in result.failures
    assert "reconnect_outside_evidence_window" in result.failures


def test_restart_rediscovery_requires_a_post_restart_hello_row():
    metrics, observations = _passing_inputs()
    next(row for row in metrics if row["observed_at"] == 1136)["message_deltas"].pop("HELLO")

    assert "missing_post_restart_hello" in validate(metrics, observations).failures


def test_restart_rediscovery_requires_a_new_server_run():
    metrics, observations = _passing_inputs()
    next(row for row in metrics if row["observed_at"] == 1136)["server_run_started_at"] = 990

    assert "missing_post_restart_server_run" in validate(metrics, observations).failures


def test_restart_observations_must_fall_inside_the_appended_evidence_window():
    metrics, observations = _passing_inputs()
    observations["server_stopped_at"] = 999

    assert "restart_outside_evidence_window" in validate(metrics, observations).failures


def test_gate_requires_one_commanded_target_and_exactly_one_ack_of_each_kind():
    metrics, observations = _passing_inputs()
    metrics[-1]["command_results"][1]["node_id"] = OTHER_NODE_ID
    metrics.append(_metric(1601, node_id=OTHER_NODE_ID, boot_id=0x50607080, command_results=[]))

    result = validate(metrics, observations)

    assert "command_node_mismatch" in result.failures

    metrics, observations = _passing_inputs()
    metrics[-1]["command_results"].append(
        {"node_id": NODE_ID, "opcode": "RESET_BASELINE", "ok": True, "attempts": 1}
    )
    assert "unexpected_reset_baseline_count" in validate(metrics, observations).failures


def test_gate_scopes_unrelated_parse_errors_and_rejects_reboot():
    metrics, observations = _passing_inputs()
    metrics.append(_metric(1601, node_id=OTHER_NODE_ID, boot_id=0x50607080, parse_errors=20))

    assert validate(metrics, observations).passed is True

    metrics, observations = _passing_inputs()
    metrics[-1]["boot_id"] = 0x50607080
    assert "reconnect_reboot_detected" in validate(metrics, observations).failures


def test_stage2_validate_cli_reads_jsonl_and_prints_one_summary(tmp_path):
    metrics, observations = _passing_inputs()
    metrics_path = tmp_path / "metrics.jsonl"
    observations_path = tmp_path / "observations.json"
    metrics_path.write_text("".join(json.dumps(row) + "\n" for row in metrics), encoding="utf-8")
    observations_path.write_text(json.dumps(observations), encoding="utf-8")

    process = subprocess.run(
        [sys.executable, "tools/stage2_validate.py", "--metrics", str(metrics_path), "--observations", str(observations_path)],
        cwd=ROOT, check=False, capture_output=True, text=True,
    )

    assert process.returncode == 0
    assert json.loads(process.stdout)["passed"] is True


def test_accelerated_simulator_fault_matrix_recovers_then_validates_real_timestamps(tmp_path):
    async def scenario():
        reservation = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
        reservation.close()
        node = bytes.fromhex("021122334455")
        target_simulator = asyncio.create_task(run_simulator(SimulatorConfig(
            host="127.0.0.1", port=port, node_id=node, boot_id=0x10203040,
            rate=200, duration=2.5, hello_interval=0.01, state_interval=0.01,
            heartbeat_interval=0.01, telemetry_pause_at=0.25, telemetry_pause_duration=0.15,
        )))
        metrics_path = tmp_path / "recovery.jsonl"
        first = asyncio.create_task(
            run_server("127.0.0.1", port, duration=0.8, metrics_jsonl=metrics_path, metrics_interval=0.02)
        )
        await asyncio.sleep(0.01)
        await run_simulator(SimulatorConfig(
            host="127.0.0.1", port=port, node_id=bytes.fromhex("02aabbccddee"), boot_id=0x50607080,
            rate=200, duration=0.1, hello_interval=0.02, state_interval=0.02,
            heartbeat_interval=0.02, corrupt_every=2,
        ))
        await first
        stopped = time.time()
        await asyncio.sleep(0.03)
        started = time.time()
        second = asyncio.create_task(
            run_server(
                "127.0.0.1", port, duration=1.3,
                command_requests=((node, CommandOpcode.GET_CONFIG), (node, CommandOpcode.RESET_BASELINE)),
                metrics_jsonl=metrics_path, metrics_interval=0.02,
            )
        )
        await asyncio.sleep(0.01)
        await second
        target_run = await target_simulator
        rows = [json.loads(line) for line in metrics_path.read_text().splitlines()]
        target = "02:11:22:33:44:55"
        restarted = [row for row in rows if row["node_id"] == target and row["observed_at"] >= started]
        return (
            rows,
            {
                "target_node_id": target,
                "server_stopped_at": stopped,
                "server_started_at": started,
                "server_rediscovered_at": restarted[0]["observed_at"],
                "reconnect_disconnected_at": target_run.pause_started_at,
                "reconnect_reconnected_at": target_run.resumed_at,
            },
            port,
        )

    rows, observations, port = asyncio.run(scenario())
    assert observations["reconnect_reconnected_at"] < observations["server_stopped_at"]
    assert any(row["parse_errors"] > 0 for row in rows if row["node_id"] == "02:aa:bb:cc:dd:ee")
    result = validate(
        rows, observations, expected_host="127.0.0.1", expected_port=port,
        minimum_duration=0.1, minimum_csi_seconds=0.04,
        minimum_server_absence=0.02, maximum_rediscovery=0.2,
    )
    assert result.passed is True
    target_rows = [row for row in rows if row["node_id"] == "02:11:22:33:44:55"]
    assert all(row["parse_errors"] == 0 for row in target_rows)
    assert {row["boot_id"] for row in target_rows} == {0x10203040}
    assert any(
        row["observed_at"] < observations["server_stopped_at"]
        and row["sample_started_at"] >= observations["reconnect_reconnected_at"]
        and row["message_deltas"]["HELLO"] > 0
        for row in target_rows
    )
    assert any(row["sample_started_at"] >= observations["reconnect_reconnected_at"] for row in target_rows)
