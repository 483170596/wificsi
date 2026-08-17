# Wi-Fi CSI Target 1 Stage 1 Official Baseline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reproduce and objectively validate Espressif's raw router-CSI and `ACTIVE/INACTIVE` sensing paths on the ESP32-S3 attached to COM8 before any custom firmware, server, or Web implementation begins.

**Architecture:** Pin the official `espressif/esp-csi` repository as a Git submodule and run its router CSI and Wi-Fi sensing examples unchanged except for local, untracked configuration. A small standard-library Python validator parses bounded serial transcripts and emits machine-readable summaries; the stage report records the actual hardware, dependency, CSI-length, sample-rate, and state-transition evidence used by Stage 2.

**Tech Stack:** ESP-IDF v5.4.4, ESP32-S3-N8R8, Espressif `esp-csi` commit `8633d67152db2808f141cc1595970aa9cf406045`, `esp_wifi_sensing >=0.1.1` as resolved by the official example, PowerShell 7, Python managed by `uv`, pytest.

## Global Constraints

- Activate ESP-IDF with `& 'C:\Espressif\tools\Microsoft.v5.4.4.PowerShell_profile.ps1'`; `idf.py --version` must print `ESP-IDF v5.4.4`.
- Use only the ESP32-S3 currently attached to `COM8` in this stage.
- Connect only to the 2.4 GHz WLAN `PRTS`; never commit or print its password.
- Treat official `ACTIVE` as MVP “有人” and official `INACTIVE` as MVP “无人”; do not claim strict static-person detection.
- Do not add recording, annotation, movement classification, fall detection, model, server, Web, MQTT, cloud, or 802.11bf functionality.
- Keep serial evidence under ignored `.artifacts/stage1/`; do not commit raw CSI logs or generated `sdkconfig` files.
- Do not modify official example source during baseline reproduction. If the pinned example fails, document the failure before considering any patch.
- Stage 2 is blocked until the final stage-gate command exits successfully and the report contains measured results.

---

## Planned File Structure

```text
.
├── .gitignore
├── .gitmodules
├── pyproject.toml
├── uv.lock
├── firmware/
│   └── baseline/
│       └── README.md
├── third_party/
│   └── esp-csi/                         # pinned Git submodule
├── tools/
│   └── baseline_validate.py             # bounded-log parser and stage gate
├── tests/
│   └── tools/
│       └── test_baseline_validate.py
└── docs/
    └── verification/
        └── stage1-official-baseline.md  # actual observed results, no secrets
```

`tools/baseline_validate.py` owns serial-log parsing and pass/fail criteria. It does not know how to flash hardware. `firmware/baseline/README.md` owns reproducible operator commands. The verification report contains results, not executable logic.

---

### Task 1: Pin the Official Reference and Protect Local Evidence

**Files:**
- Create: `.gitignore`
- Create: `.gitmodules` through `git submodule add`
- Create: `third_party/esp-csi` as a pinned gitlink
- Create: `firmware/baseline/README.md`

**Interfaces:**
- Consumes: Git repository and authenticated GitHub network access.
- Produces: immutable official source at `third_party/esp-csi` and documented build locations used by Tasks 3 and 4.

- [ ] **Step 1: Add ignore rules before generating any local configuration**

Create `.gitignore` with exactly these project-owned rules:

```gitignore
.worktrees/
.artifacts/
.venv/
__pycache__/
.pytest_cache/
*.py[cod]

# ESP-IDF generated files and local secrets
sdkconfig
sdkconfig.old
build/
managed_components/
dependencies.lock
```

- [ ] **Step 2: Add and pin the official repository**

Run:

```powershell
git submodule add https://github.com/espressif/esp-csi.git third_party/esp-csi
git -C third_party/esp-csi checkout 8633d67152db2808f141cc1595970aa9cf406045
git submodule status
```

Expected: the submodule line begins with `8633d67152db2808f141cc1595970aa9cf406045` and ends with `third_party/esp-csi`.

- [ ] **Step 3: Document the two official examples and secret-handling rule**

Create `firmware/baseline/README.md` containing:

````markdown
# Stage 1 official baseline

This stage runs pinned Espressif examples without source modification:

- Raw router CSI: `third_party/esp-csi/examples/get-started/csi_recv_router`
- Official sensing FSM: `third_party/esp-csi/examples/esp-radar/wifi_sensing_demo`

Activate ESP-IDF v5.4.4 before every build:

```powershell
& 'C:\Espressif\tools\Microsoft.v5.4.4.PowerShell_profile.ps1'
idf.py --version
```

Configure Wi-Fi credentials only through each example's generated `sdkconfig`
using `idf.py menuconfig` → `Example Connection Configuration`. Generated
configuration and serial captures are ignored and must never be committed.

The baseline result is accepted only through:

```powershell
uv run python tools/baseline_validate.py stage-gate `
  --csi-log .artifacts/stage1/csi_recv_router.log `
  --sensing-log .artifacts/stage1/wifi_sensing_demo.log
```
````

- [ ] **Step 4: Verify the reference is clean and the password is absent**

Run:

```powershell
git -C third_party/esp-csi status --short
rg -n --hidden --glob '!third_party/**' --glob '!.git/**' 'CONFIG_EXAMPLE_WIFI_PASSWORD="[^"]+"|gho_[A-Za-z0-9_]+' .
```

Expected: the submodule status is empty and `rg` returns no matches.

- [ ] **Step 5: Commit the pinned baseline reference**

```powershell
git add .gitignore .gitmodules third_party/esp-csi firmware/baseline/README.md
git commit -m "build: pin Espressif CSI baseline"
```

---

### Task 2: Build a Tested Serial-Evidence Validator

**Files:**
- Create: `pyproject.toml`
- Create: `uv.lock`
- Create: `tools/baseline_validate.py`
- Create: `tests/tools/test_baseline_validate.py`

**Interfaces:**
- Consumes: UTF-8/ANSI serial transcript paths.
- Produces: `parse_csi_line(line: str) -> CsiFrame | None`, `summarize_csi(lines: Iterable[str]) -> CsiSummary`, `summarize_sensing(lines: Iterable[str]) -> SensingSummary`, and CLI subcommand `stage-gate` with exit code 0 only when both official paths pass.

- [ ] **Step 1: Create the minimal uv project**

Create `pyproject.toml`:

```toml
[project]
name = "wificsi-tools"
version = "0.1.0"
requires-python = ">=3.12,<3.14"
dependencies = []

[dependency-groups]
dev = ["pytest>=8.4,<9"]

[tool.pytest.ini_options]
testpaths = ["tests"]
addopts = "-ra"
```

Run `uv lock` and commit the generated `uv.lock` later in this task.

- [ ] **Step 2: Write failing raw-CSI parser tests**

Create `tests/tools/test_baseline_validate.py` with the following initial tests:

```python
from tools.baseline_validate import parse_csi_line, summarize_csi


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
```

- [ ] **Step 3: Run the tests and confirm they fail**

Run:

```powershell
uv run pytest tests/tools/test_baseline_validate.py -v
```

Expected: collection fails because `tools.baseline_validate` does not exist.

- [ ] **Step 4: Implement the minimal CSI parser**

Create `tools/baseline_validate.py` with dataclasses `CsiFrame` and `CsiSummary`. Use `csv.reader([line])`, accept only rows whose first field is `CSI_DATA`, parse the official column positions (`mac=2`, `rssi=3`, `channel=16`, `local_timestamp=18`, `len=22`, `first_word=23`, `data=24`), decode the final JSON integer array, and reject a frame when `len(data) != len` or any I/Q value is outside signed 8-bit range.

The public shape must be:

```python
@dataclass(frozen=True)
class CsiFrame:
    mac: str
    rssi: int
    channel: int
    local_timestamp_us: int
    length: int
    first_word_invalid: bool
    iq: tuple[int, ...]


@dataclass(frozen=True)
class CsiSummary:
    frames: int
    invalid_lines: int
    lengths: dict[int, int]
    macs: tuple[str, ...]
    channels: tuple[int, ...]
    duration_seconds: float
    sample_rate_hz: float
```

`summarize_csi` calculates duration from the first and last ESP local timestamps and reports `0.0` when fewer than two valid frames exist.

- [ ] **Step 5: Run the raw-CSI tests and confirm they pass**

```powershell
uv run pytest tests/tools/test_baseline_validate.py -v
```

Expected: all three tests pass.

- [ ] **Step 6: Add failing sensing-event and gate tests**

Append tests that use actual official log forms:

```python
from tools.baseline_validate import summarize_sensing, stage_gate


def test_sensing_summary_counts_stable_events():
    lines = [
        "I (1000) wifi_sensing_demo: [AP] ACTIVE peer=aa:bb:cc:dd:ee:ff data=17",
        "I (2000) wifi_sensing_demo: [AP] INACTIVE peer=aa:bb:cc:dd:ee:ff",
    ]
    summary = summarize_sensing(lines)
    assert summary.active_events == 1
    assert summary.inactive_events == 1
    assert summary.ap_peers == ("aa:bb:cc:dd:ee:ff",)


def test_stage_gate_requires_real_volume_and_both_states():
    frames = [
        VALID_CSI.replace("2751923", str(2751923 + index * 20000))
        for index in range(100)
    ]
    csi = summarize_csi(frames)
    sensing = summarize_sensing([
        "I (1000) wifi_sensing_demo: [AP] ACTIVE peer=aa:bb:cc:dd:ee:ff data=17",
        "I (2000) wifi_sensing_demo: [AP] INACTIVE peer=aa:bb:cc:dd:ee:ff",
    ])
    result = stage_gate(csi, sensing, minimum_csi_frames=100)
    assert result.passed is True
    assert result.failures == ()
```

- [ ] **Step 7: Implement sensing parsing and the CLI**

Add immutable dataclasses:

```python
@dataclass(frozen=True)
class SensingSummary:
    active_events: int
    inactive_events: int
    ap_peers: tuple[str, ...]


@dataclass(frozen=True)
class GateResult:
    passed: bool
    failures: tuple[str, ...]
```

`summarize_sensing` must only count lines containing `[AP] ACTIVE peer=` or `[AP] INACTIVE peer=`. `stage_gate` fails when valid CSI frames are below the requested minimum, no CSI length was observed, measured CSI sample rate is not positive, or either AP ACTIVE or AP INACTIVE is missing.

Implement argparse subcommands:

```text
csi <log> [--min-frames 100]
sensing <log>
stage-gate --csi-log <path> --sensing-log <path> [--min-frames 100]
```

Each command prints UTF-8 JSON. `stage-gate` exits 0 on pass and 1 on a measured gate failure; malformed arguments or unreadable files use argparse/nonzero OS errors.

- [ ] **Step 8: Run the complete test suite and inspect the CLI**

```powershell
uv run pytest -v
uv run python tools/baseline_validate.py --help
```

Expected: tests pass and help lists `csi`, `sensing`, and `stage-gate`.

- [ ] **Step 9: Commit the validator**

```powershell
git add pyproject.toml uv.lock tools/baseline_validate.py tests/tools/test_baseline_validate.py
git commit -m "test: add CSI baseline validator"
```

---

### Task 3: Reproduce Raw Router CSI on COM8

**Files:**
- Runtime only: `.artifacts/stage1/csi_recv_router.log`
- Runtime only: `third_party/esp-csi/examples/get-started/csi_recv_router/sdkconfig`

**Interfaces:**
- Consumes: pinned `csi_recv_router`, COM8, PRTS credentials entered locally.
- Produces: a bounded serial transcript accepted by `baseline_validate.py csi` and measured CSI metadata for the report.

- [ ] **Step 1: Verify toolchain and physical port before changing the board**

```powershell
& 'C:\Espressif\tools\Microsoft.v5.4.4.PowerShell_profile.ps1'
idf.py --version
esptool.py --port COM8 chip_id
git -C third_party/esp-csi rev-parse HEAD
```

Expected: ESP-IDF v5.4.4, an ESP32-S3 identity from COM8, and commit `8633d67152db2808f141cc1595970aa9cf406045`.

- [ ] **Step 2: Configure the official router receiver locally**

```powershell
Set-Location third_party/esp-csi/examples/get-started/csi_recv_router
idf.py set-target esp32s3
idf.py menuconfig
```

In `Example Connection Configuration`, enter SSID `PRTS` and the password interactively. Confirm Wi-Fi CSI is enabled and monitor baud is 921600. Do not paste the password into a command or transcript.

- [ ] **Step 3: Build before flashing**

```powershell
idf.py build
```

Expected: build exits 0 for target `esp32s3`. If the pinned official example does not build on IDF v5.4.4, preserve the full error as `.artifacts/stage1/csi-build-failure.log`, document it, and stop the stage without patching upstream.

- [ ] **Step 4: Flash and capture at least 60 seconds of real CSI**

From the repository root, create the ignored artifact directory. Start a PowerShell transcript, run the monitor, wait until at least 100 `CSI_DATA` lines are visible, continue for at least 60 seconds, exit the monitor with `Ctrl+]`, then stop the transcript:

```powershell
New-Item -ItemType Directory -Force .artifacts/stage1
Start-Transcript -Path .artifacts/stage1/csi_recv_router.log -Force
idf.py -C third_party/esp-csi/examples/get-started/csi_recv_router -p COM8 flash monitor
Stop-Transcript
```

Do not manufacture traffic or edit the captured lines. The official example's router Ping path must be the source.

- [ ] **Step 5: Validate the captured CSI**

```powershell
uv run python tools/baseline_validate.py csi `
  .artifacts/stage1/csi_recv_router.log --min-frames 100
```

Expected: exit 0; JSON reports at least 100 valid frames, one AP MAC, one Wi-Fi channel, at least one observed CSI `len`, and a positive measured sample rate. Record all observed lengths rather than assuming 128, 256, 384, or 612 bytes.

- [ ] **Step 6: Confirm no generated secret entered Git scope**

```powershell
Set-Location 'D:\Projects\Codex\WIFICSI'
git status --short
rg -n --hidden --glob '!third_party/**' --glob '!.git/**' 'CONFIG_EXAMPLE_WIFI_PASSWORD="[^"]+"|gho_[A-Za-z0-9_]+' .
```

Expected: no password match and no staged/generated ESP-IDF files.

---

### Task 4: Reproduce Official ACTIVE/INACTIVE Sensing on COM8

**Files:**
- Runtime only: `.artifacts/stage1/wifi_sensing_demo.log`
- Runtime only: `third_party/esp-csi/examples/esp-radar/wifi_sensing_demo/sdkconfig`

**Interfaces:**
- Consumes: pinned `wifi_sensing_demo`, COM8, local PRTS configuration, a controlled empty/active test sequence.
- Produces: official AP-channel ACTIVE and INACTIVE evidence accepted by the stage gate.

- [ ] **Step 1: Configure the official sensing demo locally**

```powershell
& 'C:\Espressif\tools\Microsoft.v5.4.4.PowerShell_profile.ps1'
Set-Location third_party/esp-csi/examples/esp-radar/wifi_sensing_demo
idf.py set-target esp32s3
idf.py menuconfig
```

Enter PRTS credentials only in `Example Connection Configuration`. Leave official sensing thresholds at their defaults for the first run. Select the LED type/pin that matches the connected board, or leave the default GPIO LED if LED feedback is not used. Keep Web Serial monitor output enabled at its default 50 ms period.

- [ ] **Step 2: Build without changing the official example**

```powershell
idf.py build
```

Expected: build exits 0 and Component Manager resolves `esp_wifi_sensing >=0.1.1`. Save the resolved component version and generated dependency-lock hash for the report, but do not commit the generated lock from inside the submodule.

- [ ] **Step 3: Flash and run a controlled physical sequence**

From the repository root:

```powershell
New-Item -ItemType Directory -Force .artifacts/stage1
Start-Transcript -Path .artifacts/stage1/wifi_sensing_demo.log -Force
idf.py -C third_party/esp-csi/examples/esp-radar/wifi_sensing_demo -p COM8 flash monitor
Stop-Transcript
```

During the monitored run:

1. Leave the sensing area and keep it static until initialization/onsite training completes.
2. Walk through the primary router-to-node path and make an obvious body movement until an official `[AP] ACTIVE` event appears.
3. Leave the area static long enough for an official `[AP] INACTIVE` event.
4. Repeat the active/static sequence at least three times to rule out a single accidental transition.
5. Exit the monitor with `Ctrl+]` and stop the transcript.

- [ ] **Step 4: Validate official state transitions**

```powershell
uv run python tools/baseline_validate.py sensing `
  .artifacts/stage1/wifi_sensing_demo.log
```

Expected: the JSON identifies the AP peer and reports at least one ACTIVE and one INACTIVE event. If only fixed reference peers change state, the requirement is not met; the AP channel must pass.

- [ ] **Step 5: Run the combined stage gate**

```powershell
uv run python tools/baseline_validate.py stage-gate `
  --csi-log .artifacts/stage1/csi_recv_router.log `
  --sensing-log .artifacts/stage1/wifi_sensing_demo.log `
  --min-frames 100
```

Expected: exit 0 with `"passed": true`. A failure blocks Task 5's acceptance claim and all Stage 2 implementation.

---

### Task 5: Publish the Measured Stage-1 Report and Gate Decision

**Files:**
- Create: `docs/verification/stage1-official-baseline.md`
- Modify: `firmware/baseline/README.md`

**Interfaces:**
- Consumes: successful validator JSON, ESP-IDF build output, component resolution, and physical test observations.
- Produces: explicit PASS/FAIL decision and measured inputs for the future Stage 2 plan.

- [ ] **Step 1: Write the report from observed values**

Create `docs/verification/stage1-official-baseline.md` with these completed sections and no blank values:

```markdown
# Stage 1 Official Baseline Verification

## Decision

`PASS` or `FAIL`, followed by the exact gate reason.

## Reproducibility

- Date and timezone
- ESP-IDF exact version
- esp-csi commit
- esp_wifi_sensing resolved version
- ESP32-S3 chip/revision, flash size and PSRAM result
- COM port and STA MAC
- AP BSSID and channel (never the password)
- Node/AP distance, height and orientation

## Raw Router CSI

- Capture duration
- Valid and invalid frame counts
- CSI length histogram
- Measured sample rate
- RSSI range
- `first_word_invalid` count

## Official Sensing FSM

- Initialization duration
- AP ACTIVE event count and observed response delays
- AP INACTIVE event count and observed return delays
- Threshold/sensitivity settings
- False or unexplained transitions observed

## Stage 2 Inputs

- Actual CSI lengths the custom protocol must accept
- Sustained and peak frame-rate assumptions
- Required queue headroom
- Exact public diagnostics confirmed available

## Known Limitations

- ACTIVE/INACTIVE is an activity proxy, not proof of a completely static person
- Results apply to the recorded room and placement only
```

Do not paste raw CSI arrays, Wi-Fi credentials, access tokens, or complete generated `sdkconfig` files into the report.

- [ ] **Step 2: Update the operator README with actual resolved versions**

Add a short “Verified versions” section to `firmware/baseline/README.md` containing the actual ESP-IDF, esp-csi commit, and resolved `esp_wifi_sensing` version from the successful run. Link the verification report.

- [ ] **Step 3: Run all automated and document-safety checks**

```powershell
uv run pytest -v
uv run python tools/baseline_validate.py stage-gate `
  --csi-log .artifacts/stage1/csi_recv_router.log `
  --sensing-log .artifacts/stage1/wifi_sensing_demo.log `
  --min-frames 100
rg -n 'T[B]D|T[O]DO|CONFIG_EXAMPLE_WIFI_PASSWORD="[^"]+"|gho_[A-Za-z0-9_]+' docs firmware tools tests pyproject.toml
git diff --check
git status --short
```

Expected: pytest and stage gate pass; the secret/placeholder scan has no matches; only intended source, test, lock, README, and report files are modified.

- [ ] **Step 4: Commit the verified baseline report**

```powershell
git add firmware/baseline/README.md docs/verification/stage1-official-baseline.md
git commit -m "docs: verify official CSI baseline"
```

- [ ] **Step 5: Stop at the stage boundary**

Report the PASS/FAIL decision and measured Stage 2 inputs to the user. Do not create custom firmware, UDP protocol code, FastAPI service, Web dashboard, or additional-node changes in this plan. If PASS, write and review the separate Stage 2 implementation plan next; if FAIL, diagnose the official baseline before changing scope.
