import json
import subprocess
import sys
from pathlib import Path

from tools.baseline_validate import (
    parse_csi_line,
    stage_gate,
    summarize_csi,
    summarize_sensing,
)


VALID_CSI = (
    'CSI_DATA,7,94:d9:b3:80:8c:81,-42,11,1,6,0,1,0,0,0,0,0,'
    '-93,0,6,0,2751923,0,67,0,8,0,"[3,4,0,5,-6,8,5,12]"'
)


def test_parse_csi_line_preserves_variable_length_and_iq():
    frame = parse_csi_line(VALID_CSI)

    assert frame is not None
    assert frame.mac == "94:d9:b3:80:8c:81"
    assert frame.rssi == -42
    assert frame.channel == 6
    assert frame.length == 8
    assert frame.first_word_invalid is False
    assert frame.iq == (3, 4, 0, 5, -6, 8, 5, 12)


def test_parse_csi_line_ignores_non_csi_log_lines():
    assert parse_csi_line("I (1234) wifi: connected") is None


def test_summary_counts_length_mismatch_as_invalid():
    invalid = VALID_CSI.replace(',8,0,"[', ',10,0,"[')

    summary = summarize_csi([VALID_CSI, invalid])

    assert summary.frames == 1
    assert summary.invalid_lines == 1
    assert summary.lengths == {8: 1}


def test_summary_reports_rssi_first_word_and_peak_one_second_volume():
    frames = [
        VALID_CSI,
        VALID_CSI.replace("-42,11", "-55,11")
        .replace("2751923", "2771923")
        .replace(',8,0,"[', ',8,1,"['),
        VALID_CSI.replace("-42,11", "-48,11").replace("2751923", "3851923"),
    ]

    summary = summarize_csi(frames)

    assert summary.rssi_min == -55
    assert summary.rssi_max == -42
    assert summary.first_word_invalid_frames == 1
    assert summary.peak_frames_per_second == 2


def test_parse_csi_line_rejects_non_boolean_first_word_flag():
    invalid = VALID_CSI.replace(',8,0,"[', ',8,2,"[')

    assert parse_csi_line(invalid) is None


def test_parse_csi_line_rejects_values_outside_signed_int8():
    invalid = VALID_CSI.replace("5,12]", "5,128]")

    assert parse_csi_line(invalid) is None


def test_sensing_summary_counts_stable_events():
    lines = [
        "I (1000) wifi_sensing_demo: [AP] ACTIVE "
        "peer=aa:bb:cc:dd:ee:ff data=17",
        "I (2000) wifi_sensing_demo: [AP] INACTIVE "
        "peer=aa:bb:cc:dd:ee:ff",
    ]

    summary = summarize_sensing(lines)

    assert summary.active_events == 1
    assert summary.inactive_events == 1
    assert summary.ap_peers == ("aa:bb:cc:dd:ee:ff",)


def test_stage_gate_requires_real_volume_and_both_states():
    frames = [
        VALID_CSI.replace("2751923", str(2_751_923 + index * 20_000))
        for index in range(100)
    ]
    csi = summarize_csi(frames)
    sensing = summarize_sensing(
        [
            "I (1000) wifi_sensing_demo: [AP] ACTIVE "
            "peer=aa:bb:cc:dd:ee:ff data=17",
            "I (2000) wifi_sensing_demo: [AP] INACTIVE "
            "peer=aa:bb:cc:dd:ee:ff",
        ]
    )

    result = stage_gate(csi, sensing, minimum_csi_frames=100)

    assert result.passed is True
    assert result.failures == ()


def test_stage_gate_cli_prints_json_and_exits_zero(tmp_path: Path):
    csi_log = tmp_path / "csi.log"
    sensing_log = tmp_path / "sensing.log"
    csi_log.write_text(
        "\n".join(
            VALID_CSI.replace("2751923", str(2_751_923 + index * 20_000))
            for index in range(100)
        ),
        encoding="utf-8",
    )
    sensing_log.write_text(
        "\n".join(
            [
                "I (1000) wifi_sensing_demo: [AP] ACTIVE "
                "peer=aa:bb:cc:dd:ee:ff data=17",
                "I (2000) wifi_sensing_demo: [AP] INACTIVE "
                "peer=aa:bb:cc:dd:ee:ff",
            ]
        ),
        encoding="utf-8",
    )

    completed = subprocess.run(
        [
            sys.executable,
            "tools/baseline_validate.py",
            "stage-gate",
            "--csi-log",
            str(csi_log),
            "--sensing-log",
            str(sensing_log),
            "--min-frames",
            "100",
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0
    assert json.loads(completed.stdout)["passed"] is True


def test_stage_gate_cli_exits_one_with_measured_failures(tmp_path: Path):
    csi_log = tmp_path / "csi.log"
    sensing_log = tmp_path / "sensing.log"
    csi_log.write_text(VALID_CSI, encoding="utf-8")
    sensing_log.write_text("I (1000) wifi_sensing_demo: initialized", encoding="utf-8")

    completed = subprocess.run(
        [
            sys.executable,
            "tools/baseline_validate.py",
            "stage-gate",
            "--csi-log",
            str(csi_log),
            "--sensing-log",
            str(sensing_log),
            "--min-frames",
            "100",
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    result = json.loads(completed.stdout)
    assert completed.returncode == 1
    assert result["passed"] is False
    assert "missing AP ACTIVE event" in result["failures"]


def test_csi_cli_prints_summary_and_honors_minimum(tmp_path: Path):
    csi_log = tmp_path / "csi.log"
    csi_log.write_text(
        "\n".join(
            VALID_CSI.replace("2751923", str(2_751_923 + index * 20_000))
            for index in range(2)
        ),
        encoding="utf-8",
    )

    completed = subprocess.run(
        [
            sys.executable,
            "tools/baseline_validate.py",
            "csi",
            str(csi_log),
            "--min-frames",
            "2",
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    result = json.loads(completed.stdout)
    assert completed.returncode == 0
    assert result["frames"] == 2
    assert result["sample_rate_hz"] == 50.0


def test_sensing_cli_prints_ap_summary(tmp_path: Path):
    sensing_log = tmp_path / "sensing.log"
    sensing_log.write_text(
        "\n".join(
            [
                "I (1000) wifi_sensing_demo: [AP] ACTIVE "
                "peer=aa:bb:cc:dd:ee:ff data=17",
                "I (2000) wifi_sensing_demo: [AP] INACTIVE "
                "peer=aa:bb:cc:dd:ee:ff",
            ]
        ),
        encoding="utf-8",
    )

    completed = subprocess.run(
        [
            sys.executable,
            "tools/baseline_validate.py",
            "sensing",
            str(sensing_log),
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    result = json.loads(completed.stdout)
    assert completed.returncode == 0
    assert result["active_events"] == 1
    assert result["inactive_events"] == 1
    assert result["ap_peers"] == ["aa:bb:cc:dd:ee:ff"]
