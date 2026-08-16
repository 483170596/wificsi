import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

from stage2_validate import validate


def _metric(elapsed, *, state="INACTIVE", csi_rate=43.88, **changes):
    value = {
        "elapsed": elapsed,
        "server_host": "10.204.75.168",
        "server_port": 5500,
        "node_id": "28:84:85:87:2b:f4",
        "boot_id": 0x10203040,
        "state": state,
        "message_counts": {
            "HELLO": 2,
            "CSI_FRAME": 100,
            "SENSING_STATE": 2,
            "HEARTBEAT": 2,
            "COMMAND_ACK": 2,
        },
        "csi_rate": csi_rate,
        "device_queue_dropped": 0,
        "device_send_errors": 0,
        "server_sequence_gaps": 0,
        "server_duplicates": 0,
        "server_out_of_order": 0,
        "parse_errors": 0,
        "last_seen_age": 0.1,
        "command_results": [],
    }
    value.update(changes)
    return value


def _passing_inputs():
    metrics = [_metric(second, state="ACTIVE" if second == 0 else "INACTIVE") for second in range(60)]
    metrics.append(_metric(600, csi_rate=0, command_results=[
        {"node_id": "28:84:85:87:2b:f4", "opcode": "GET_CONFIG", "ok": True, "attempts": 1},
        {"node_id": "28:84:85:87:2b:f4", "opcode": "RESET_BASELINE", "ok": True, "attempts": 1},
    ]))
    observations = {
        "server_stopped_at": 100,
        "server_started_at": 110,
        "server_rediscovered_at": 145,
        "reconnect_disconnected_at": 200,
        "reconnect_reconnected_at": 205,
    }
    return metrics, observations


def test_stage2_gate_accepts_exact_design_boundaries():
    metrics, observations = _passing_inputs()

    result = validate(metrics, observations)

    assert result.passed is True
    assert result.failures == ()
    assert result.metrics["duration_seconds"] == 600
    assert result.metrics["qualified_csi_seconds"] == 60


def test_each_missing_stage2_condition_has_a_stable_reason():
    cases = {
        "server_endpoint": (lambda metrics, obs: metrics[0].update(server_port=5501), "wrong_server_endpoint"),
        "continuous_duration": (lambda metrics, obs: metrics[-1].update(elapsed=599.999), "duration_below_600_seconds"),
        "csi_rate": (
            lambda metrics, obs: [metric.update(csi_rate=21.939) for metric in metrics],
            "insufficient_valid_csi_rate",
        ),
        "states": (lambda metrics, obs: [metric.update(state="ACTIVE") for metric in metrics], "missing_inactive_state"),
        "counter_accounting": (lambda metrics, obs: metrics[-1].pop("server_duplicates"), "missing_link_counters"),
        "server_restart": (lambda metrics, obs: obs.update(server_rediscovered_at=145.001), "server_rediscovery_exceeded_35_seconds"),
        "reconnect": (lambda metrics, obs: obs.pop("reconnect_reconnected_at"), "missing_reconnect_cycle"),
        "commands": (lambda metrics, obs: metrics[-1].update(command_results=[]), "missing_successful_get_config"),
    }

    for mutate, expected in cases.values():
        metrics, observations = _passing_inputs()
        mutate(metrics, observations)
        result = validate(metrics, observations)
        assert expected in result.failures


def test_gate_rejects_incomplete_restart_outage_and_nonpositive_command_ack():
    metrics, observations = _passing_inputs()
    observations["server_started_at"] = 109.999
    metrics[-1]["command_results"][1]["ok"] = False

    result = validate(metrics, observations)

    assert "server_absence_below_10_seconds" in result.failures
    assert "missing_successful_reset_baseline" in result.failures


def test_gate_requires_exactly_one_successful_ack_for_each_acceptance_command():
    metrics, observations = _passing_inputs()
    metrics[-1]["command_results"].append(
        {"node_id": "28:84:85:87:2b:f4", "opcode": "GET_CONFIG", "ok": True, "attempts": 1}
    )

    result = validate(metrics, observations)

    assert "unexpected_get_config_count" in result.failures


def test_stage2_validate_cli_reads_jsonl_and_prints_one_summary(tmp_path):
    metrics, observations = _passing_inputs()
    metrics_path = tmp_path / "metrics.jsonl"
    observations_path = tmp_path / "observations.json"
    metrics_path.write_text("".join(json.dumps(row) + "\n" for row in metrics), encoding="utf-8")
    observations_path.write_text(json.dumps(observations), encoding="utf-8")

    process = subprocess.run(
        [sys.executable, "tools/stage2_validate.py", "--metrics", str(metrics_path), "--observations", str(observations_path)],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert process.returncode == 0
    assert json.loads(process.stdout)["passed"] is True


def test_gate_scopes_a_multi_node_server_log_to_the_commanded_node():
    metrics, observations = _passing_inputs()
    metrics.append(_metric(
        600,
        node_id="02:aa:bb:cc:dd:ee",
        boot_id=0x50607080,
        state="OFFLINE",
        csi_rate=0,
        server_port=5501,
        command_results=[],
    ))

    result = validate(metrics, observations)

    assert result.passed is True
