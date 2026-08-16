# Wi-Fi CSI Target 1 Stage 2 Node-to-Server Link Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make one real ESP32-S3 continuously report valid CSI, official ACTIVE/INACTIVE state, and health telemetry to a headless Python server without a serial monitor.

**Architecture:** A strict standard-library Python codec and deterministic simulator establish WCSI v1 before firmware work. A new ESP-IDF application uses the pinned official sensing component, a 512-frame FreeRTOS queue, and one UDP socket; the headless server validates datagrams into an in-memory registry and supports three bounded commands. Shared fixed vectors and a device startup self-test lock the Python/C wire format.

**Tech Stack:** Python 3.13 managed by `uv`, pytest 8.4, Python `asyncio`/`struct`/`zlib`, ESP-IDF v5.4.4, ESP32-S3-N8R8, `esp_wifi_sensing` `0.1.1~2`, `esp-radar` `0.3.4`, FreeRTOS, BSD UDP sockets.

## Global Constraints

- Work only in `D:\Projects\Codex\WIFICSI\.worktrees\stage2-node-server-link` on branch `codex/stage2-node-server-link`, based on Stage 1 commit `865b7bc`.
- Activate ESP-IDF with `& 'C:\Espressif\tools\Microsoft.v5.4.4.PowerShell_profile.ps1'`; `idf.py --version` must report v5.4.4.
- Use only the ESP32-S3 attached to COM8 and the server IPv4 `10.204.75.168`.
- Connect only to the public 2.4 GHz lab WLAN `PRTS`; its public SSID/password
  are tracked firmware defaults and require no secret retrieval or credential scan.
- Keep generated `sdkconfig`, `dependencies.lock`, build trees, serial output, packet captures, and acceptance evidence under ignored paths.
- Map only official AP ACTIVE to “有人” and official AP INACTIVE to “无人”; retain the raw state and its activity-proxy limitation.
- Do not add FastAPI, WebSocket, Web UI, persistence, recording, labeling, movement/fall logic, models, MQTT, cloud access, or four-node behavior.
- WCSI v1 uses network byte order, a 40-byte common header, IEEE CRC-32, and a 1200-byte datagram maximum.
- The CSI callback performs validation, bounded copying, a zero-wait queue send, and counter updates only.
- Preserve variable `wifi_csi_info_t.len` and `first_word_invalid`; never hard-code 128 bytes as the only valid CSI length.
- Run tests red before production code, green after minimal implementation, and commit each task separately.

## Planned File Structure

```text
.
├── protocol/
│   └── vectors/
│       └── wcsi_v1.json                 # Fixed language-neutral byte vectors
├── server/
│   └── wificsi/
│       ├── __init__.py
│       ├── protocol.py                  # WCSI dataclasses, strict codec, CRC
│       ├── registry.py                  # Node lifecycle and sequence metrics
│       ├── ingest.py                    # asyncio UDP adapter
│       ├── commands.py                  # Bounded command/ACK transactions
│       ├── simulator.py                 # Deterministic device simulator
│       └── server.py                    # Headless UDP CLI
├── firmware/
│   └── node/
│       ├── CMakeLists.txt
│       ├── sdkconfig.defaults           # Reproducible ESP-IDF defaults
│       ├── partitions.csv
│       └── main/
│           ├── CMakeLists.txt
│           ├── Kconfig.projbuild
│           ├── idf_component.yml
│           ├── app_main.c               # Bring-up and component ownership
│           ├── wcsi_protocol.h
│           ├── wcsi_protocol.c           # Header/payload codec and CRC
│           ├── wcsi_vectors.h            # Generated expected bytes
│           ├── wcsi_node.h
│           └── wcsi_node.c               # Queues, UDP, FSM, reconnect, commands
├── tools/
│   └── generate_wcsi_c_vectors.py
├── tests/
│   └── server/
│       ├── test_protocol.py
│       ├── test_registry.py
│       ├── test_ingest_simulator.py
│       └── test_commands.py
└── docs/
    └── verification/
        └── stage2-node-server-link.md
```

---

### Task 1: Implement the Strict Python WCSI v1 Codec and Fixed Vectors

**Files:**
- Create: `server/wificsi/__init__.py`
- Create: `server/wificsi/protocol.py`
- Create: `tests/server/test_protocol.py`
- Create: `protocol/vectors/wcsi_v1.json`
- Create: `tools/generate_wcsi_c_vectors.py`
- Modify: `pyproject.toml`

**Interfaces:**
- Produces: `MessageType`, `StableState`, `ProcessState`, `InitStage`, `CommandOpcode`, `AckStatus`, `Header`, `Hello`, `CsiFrame`, `SensingState`, `Heartbeat`, `Command`, `CommandAck`, and `Packet` frozen dataclasses.
- Produces: `encode_packet(packet: Packet) -> bytes`, `decode_packet(data: bytes) -> Packet`, `sequence_relation(previous: int, current: int) -> Literal["next", "gap", "duplicate", "old"]`, and `ProtocolError(ValueError)`.
- Produces: committed JSON vectors consumed later by the C startup self-test.

- [ ] **Step 1: Expose the server package to pytest**

Add `"server"` to `tool.pytest.ini_options.pythonpath` and create an empty
`server/wificsi/__init__.py`. Keep runtime dependencies empty.

- [ ] **Step 2: Write failing codec tests**

In `tests/server/test_protocol.py`, construct one fixed header with node ID
`28:84:85:87:2b:f4`, boot ID `0x10203040`, sequence `0xfffffffe`, and device
time `0x0102030405060708`. Add tests that assert:

```python
assert zlib.crc32(b"123456789") == 0xCBF43926
assert len(encode_packet(hello_packet)) == 64
assert decode_packet(encode_packet(hello_packet)) == hello_packet
assert decode_packet(encode_packet(csi_packet)).payload.iq == bytes(range(8))
assert sequence_relation(0xFFFFFFFF, 0) == "next"
```

Round-trip all six message types. Use 8-byte and 128-byte CSI cases. Mutate
each encoded field to cover bad magic, major, flags, too-short header,
header/payload length mismatch, zero boot ID, CRC, enum, CSI length mismatch,
and 1201-byte datagrams. Each mutation must raise `ProtocolError` with a stable
reason code. Separately assert that nonzero reserved storage bytes are ignored
and that a major-1/minor-1 packet with a four-byte header extension decodes its
payload after `header_length == 44`.

- [ ] **Step 3: Run the protocol tests and verify red**

Run:

```powershell
uv run pytest tests/server/test_protocol.py -v
```

Expected: collection fails because `wificsi.protocol` does not exist.

- [ ] **Step 4: Implement the codec with explicit struct layouts**

Use constants and `struct.Struct` instances matching the approved spec:

```python
MAGIC = b"WCSI"
MAJOR = 1
MINOR = 0
COMMON_HEADER = struct.Struct("!4sBBBBHH6sHIIQI")
HELLO = struct.Struct("!HBBBBHHHHHH6s")
CSI_PREFIX = struct.Struct("!6s6sbbBBIHHBBBBHBBB3x")
SENSING_STATE = struct.Struct("!6sBBBBBBffIIIfffffI")
HEARTBEAT = struct.Struct("!QIIIIII")
COMMAND_PREFIX = struct.Struct("!IB3x")
ACK_PREFIX = struct.Struct("!IBB2x")
CONFIG_BODY = struct.Struct("!fffI")
MAX_DATAGRAM = 1200
```

Encode with CRC bytes zero, calculate `zlib.crc32(header + payload)`, and
repack the final CRC. Decode in this order: total size, magic/major/flags,
header size, payload size, boot ID, CRC, message enum, then payload-specific
lengths and enums. Minor 0 requires a 40-byte header; a higher minor accepts a
header of at least 40 bytes and starts the payload at `header_length`.
`ProtocolError.code` must be one of `size`, `magic`, `version`, `flags`,
`header_length`, `payload_length`, `boot_id`, `crc`, `message_type`, or
`payload`.

Use half-range arithmetic:

```python
delta = (current - previous) & 0xFFFFFFFF
if delta == 0:
    return "duplicate"
if delta == 1:
    return "next"
if delta < 0x80000000:
    return "gap"
return "old"
```

- [ ] **Step 5: Make protocol tests green**

Run the Task 1 test command. Expected: all protocol tests pass.

- [ ] **Step 6: Commit fixed vectors and generate the C header deterministically**

Write `protocol/vectors/wcsi_v1.json` with the exact field inputs and complete
encoded hex for HELLO, 8-byte CSI_FRAME, SENSING_STATE, HEARTBEAT, GET_CONFIG,
SET_CONFIG, and successful ACK. `tools/generate_wcsi_c_vectors.py` must read
that JSON and render named `static const uint8_t` arrays; running it twice must
produce byte-identical output. Test that decoding every committed hex vector
matches its declared fields and that re-encoding produces the same hex.

- [ ] **Step 7: Run all tests and commit**

Run:

```powershell
uv run pytest -q
git diff --check
```

Expected: the Stage 1 tests and all new codec/vector tests pass.

Commit:

```powershell
git add pyproject.toml server tests/server/test_protocol.py protocol tools/generate_wcsi_c_vectors.py
git commit -m "feat: define WCSI v1 protocol"
```

---

### Task 2: Implement Node Registry and Sequence/Lifecycle Accounting

**Files:**
- Create: `server/wificsi/registry.py`
- Create: `tests/server/test_registry.py`

**Interfaces:**
- Consumes: decoded `Packet` values and `sequence_relation` from Task 1.
- Produces: `NodeSnapshot`, `LinkCounters`, and `NodeRegistry(clock: Callable[[], float])`.
- Produces: `NodeRegistry.accept(packet: Packet, endpoint: tuple[str, int]) -> bool`, `expire() -> tuple[bytes, ...]`, `get(node_id: bytes) -> NodeSnapshot | None`, and `list() -> tuple[NodeSnapshot, ...]`.

- [ ] **Step 1: Write failing registry tests**

Use an injected mutable monotonic clock. Cover:

- HELLO creates INITIALIZING state with endpoint and boot ID.
- SENSING_STATE ACTIVE/INACTIVE replaces only the current stable state.
- heartbeat and CSI snapshots update their own fields.
- a boot-ID change resets sequence accounting and stable state.
- contiguous wrap `0xfffffffe`, `0xffffffff`, `0` creates no gap.
- a forward delta of 3 adds two network gaps.
- duplicate/old packets increment separate counters and do not replace data.
- five seconds is still online; greater than five seconds expires to OFFLINE.
- device `queue_dropped` never changes server `sequence_gaps`.

- [ ] **Step 2: Run registry tests and verify red**

Run `uv run pytest tests/server/test_registry.py -v`.

Expected: import failure for `wificsi.registry`.

- [ ] **Step 3: Implement immutable snapshots and controlled mutation**

Keep mutable state private to `NodeRegistry`. Return frozen snapshot copies.
Use `time.monotonic` by default and never device time for offline decisions.
Reject duplicate/old replacement while still refreshing `last_seen` only for
fully valid, forward/current-boot traffic. Sort `list()` by raw node ID for
deterministic CLI output.

- [ ] **Step 4: Run focused and full tests, then commit**

Run:

```powershell
uv run pytest tests/server/test_registry.py -v
uv run pytest -q
```

Commit:

```powershell
git add server/wificsi/registry.py tests/server/test_registry.py
git commit -m "feat: track CSI node lifecycle"
```

---

### Task 3: Build Resilient UDP Ingest, Simulator, and Headless CLI

**Files:**
- Create: `server/wificsi/ingest.py`
- Create: `server/wificsi/simulator.py`
- Create: `server/wificsi/server.py`
- Create: `tests/server/test_ingest_simulator.py`
- Modify: `pyproject.toml`

**Interfaces:**
- Consumes: Task 1 codec and Task 2 registry.
- Produces: `IngestProtocol(registry, on_transport=None)`, whose
  `datagram_received(data, addr)` never raises.
- Produces: `SimulatorConfig`, `SimulatedNode`, and async
  `run_simulator(config: SimulatorConfig) -> None`.
- Produces CLI commands `wificsi-server` and `wificsi-simulator`.

- [ ] **Step 1: Write failing ingest and simulator tests**

Use a real loopback UDP endpoint through `asyncio.run`. Assert a simulated
node sends HELLO, alternating 8/128/256-byte CSI, ACTIVE then INACTIVE state,
and heartbeat; the registry observes all types. Send one bad-CRC packet between
valid packets and assert `parse_errors["crc"] == 1` while later valid traffic
still arrives. Restart the receiver on the same port and assert periodic HELLO
rediscovers the node without simulator restart.

- [ ] **Step 2: Run the tests and verify red**

Run `uv run pytest tests/server/test_ingest_simulator.py -v`.

- [ ] **Step 3: Implement UDP ingest and deterministic simulation**

`IngestProtocol` catches only `ProtocolError`, increments a per-reason counter,
and calls `registry.accept` after decoding. Unexpected exceptions are logged
with traceback but also must not close the transport.

The simulator owns boot ID, uint32 sequence, device clock, state, and counters.
Its defaults are loopback, 50 CSI/s, heartbeat/state every second, HELLO every
30 seconds. CLI switches include `--host`, `--port`, `--node-id`, `--rate`,
`--duration`, `--csi-lengths`, `--start-sequence`, and `--corrupt-every`.

The server CLI binds `0.0.0.0:5500`, calls `registry.expire()` every second,
and prints one compact summary per second. It never prints raw I/Q arrays.

- [ ] **Step 4: Add package scripts**

Add to `pyproject.toml`:

```toml
[project.scripts]
wificsi-server = "wificsi.server:main"
wificsi-simulator = "wificsi.simulator:main"
```

- [ ] **Step 5: Verify loopback behavior and commit**

Run focused tests, the full suite, then a five-second manual loopback smoke run
with server and simulator in separate PowerShell processes. Confirm the server
reports CSI, both states, heartbeat, and the injected corruption counter.

Commit:

```powershell
git add pyproject.toml uv.lock server/wificsi tests/server/test_ingest_simulator.py
git commit -m "feat: ingest simulated CSI telemetry"
```

---

### Task 4: Add Bounded Command Transactions

**Files:**
- Create: `server/wificsi/commands.py`
- Create: `tests/server/test_commands.py`
- Modify: `server/wificsi/ingest.py`
- Modify: `server/wificsi/simulator.py`
- Modify: `server/wificsi/server.py`

**Interfaces:**
- Consumes: last valid registry endpoint and Task 1 COMMAND/ACK codec.
- Produces: `CommandManager(sendto, clock)`, async
  `execute(node_id: bytes, opcode: CommandOpcode, config: SensingConfig | None) -> CommandResult`.
- Produces: `handle_ack(packet: Packet) -> bool` and simulator idempotence cache.

- [ ] **Step 1: Write failing command tests**

With a fake transport and controllable clock, assert sends occur immediately,
at 500 ms, and at 1000 ms only; missing ACK returns timeout after three sends.
Assert wrong node/correlation/opcode ACK does not complete a command. Assert a
valid ACK completes it and cancels retries. Drive the simulator with duplicate
RESET_BASELINE correlation IDs and assert one application but two identical
ACK datagrams. Validate SET_CONFIG ranges before sending:

- motion sensitivity `(0, 1]`;
- presence sensitivity `[0, 1]`;
- active jitter minimum `>= 0`;
- active filter `0..60000` ms.

- [ ] **Step 2: Run command tests and verify red**

Run `uv run pytest tests/server/test_commands.py -v`.

- [ ] **Step 3: Implement manager, ACK routing, and simulator behavior**

Use a monotonically incrementing nonzero correlation ID with uint32 wrap.
Maintain at most one pending entry per `(node_id, correlation_id)`. Route valid
ACK packets from ingest before/alongside registry updates. The simulator caches
the last 16 results in insertion order and never reapplies duplicate commands.
Build COMMAND headers with the target node/boot ID, sequence equal to the
correlation ID, and device time zero. Every retry reuses the identical encoded
datagram.

Add server CLI subcommands or stdin commands for `get-config NODE` and
`reset-baseline NODE`; keep `set-config` available for automated tests but do
not use it in real acceptance.

- [ ] **Step 4: Run all tests and commit**

Run `uv run pytest -q` and `git diff --check`.

Commit:

```powershell
git add server/wificsi tests/server/test_commands.py
git commit -m "feat: add bounded node commands"
```

---

### Task 5: Create the ESP-IDF Project and C-Compatible Protocol Codec

**Files:**
- Create: `firmware/node/CMakeLists.txt`
- Create: `firmware/node/partitions.csv`
- Create: `firmware/node/sdkconfig.defaults`
- Create: `firmware/node/main/CMakeLists.txt`
- Create: `firmware/node/main/Kconfig.projbuild`
- Create: `firmware/node/main/idf_component.yml`
- Create: `firmware/node/main/wcsi_protocol.h`
- Create: `firmware/node/main/wcsi_protocol.c`
- Create: `firmware/node/main/wcsi_vectors.h`
- Create: `firmware/node/main/app_main.c`
- Modify: `tools/generate_wcsi_c_vectors.py`

**Interfaces:**
- Consumes: Task 1 JSON vectors and exact WCSI layouts.
- Produces: `wcsi_encode_header`, `wcsi_encode_hello`, `wcsi_encode_csi`,
  `wcsi_encode_sensing`, `wcsi_encode_heartbeat`, `wcsi_decode_command`, and
  `wcsi_encode_ack`; all return `esp_err_t` and accept explicit output capacity.
- Produces: `wcsi_protocol_self_test(void)`, which compares encoded bytes with
  every applicable Python vector and verifies CRC before Wi-Fi starts.

- [ ] **Step 1: Generate the C vector header and add a failing firmware build**

Run the generator to create `wcsi_vectors.h`. Create the minimal app calling
`ESP_ERROR_CHECK(wcsi_protocol_self_test())`. Register the codec source but
leave its functions undefined, then run:

```powershell
& 'C:\Espressif\tools\Microsoft.v5.4.4.PowerShell_profile.ps1'
idf.py -C firmware/node set-target esp32s3
idf.py -C firmware/node build
```

Expected: link failure naming the missing codec/self-test symbols.

- [ ] **Step 2: Lock component and build configuration**

Use `main/idf_component.yml`:

```yaml
dependencies:
  idf: "==5.4.4"
  esp_wifi_sensing:
    version: "0.1.1~2"
```

Use `EXTRA_COMPONENT_DIRS` for IDF's `protocol_examples_common`. Kconfig must
define server address `10.204.75.168`, server port 5500, local port 5501,
queue capacity 512, heartbeat/state periods 1000 ms, HELLO period 30000 ms,
and the disabled reconnect self-test. The public lab WLAN SSID/password are
tracked Kconfig defaults so a clean checkout builds without interactive
credential provisioning.

- [ ] **Step 3: Implement bounds-checked C serialization**

Do not cast packed structs onto byte buffers. Add explicit big-endian helpers
for u16/u32/u64 and float bit patterns. Write the header with CRC zero, append
the payload, compute
`esp_crc32_le(0, bytes, length)`, then write the network-order CRC value. In
ESP-IDF 5.4.4 this API performs the initial and final complement internally;
the zero seed therefore matches Python `zlib.crc32` and the committed vectors.
Reject null pointers, zero/oversize CSI, output-capacity shortage, unknown
command opcode, bad length, and bad CRC. Send reserved storage bytes as zero
and ignore them on receive, as required by the WCSI v1 contract.

- [ ] **Step 4: Build and run protocol self-test on COM8**

Run `idf.py -C firmware/node build`, flash COM8, and monitor only until the log
shows `WCSI protocol self-test PASS`; exit the monitor. Save this bounded log
under `.artifacts/stage2/protocol-self-test.log`.

- [ ] **Step 5: Verify generated output and commit**

Run the vector generator twice and confirm no diff, run all Python tests, and
run the ESP-IDF build again.

Commit:

```powershell
git add firmware/node protocol tools/generate_wcsi_c_vectors.py
git commit -m "feat: add ESP32 WCSI protocol codec"
```

Do not add `firmware/node/sdkconfig`, `managed_components`, `dependencies.lock`,
or `build`.

---

### Task 6: Implement the ESP32-S3 Sensing and UDP Runtime

**Files:**
- Create: `firmware/node/main/wcsi_node.h`
- Create: `firmware/node/main/wcsi_node.c`
- Modify: `firmware/node/main/app_main.c`
- Modify: `firmware/node/main/CMakeLists.txt`
- Modify: `firmware/node/main/Kconfig.projbuild`

**Interfaces:**
- Consumes: WCSI C codec, ESP-IDF Wi-Fi/FreeRTOS/socket APIs, and public
  `esp_wifi_sensing` APIs.
- Produces: `wcsi_node_init`, `wcsi_node_on_filtered_csi`,
  `wcsi_node_on_sensing_event`, `wcsi_node_on_connected`, and
  `wcsi_node_on_disconnected`.
- Produces: HELLO/CSI_FRAME/SENSING_STATE/HEARTBEAT telemetry and idempotent
  GET_CONFIG/RESET_BASELINE/SET_CONFIG command handling.

- [ ] **Step 1: Add compile-time contract assertions before runtime code**

Add `_Static_assert` checks for every fixed payload length and a unit-like
startup test that enqueues a synthetic 8-byte CSI frame, fills the queue, and
verifies the next zero-wait enqueue increments `queue_dropped`. Guard this
synthetic check with `CONFIG_WIFICSI_PROTOCOL_SELF_TEST` so production telemetry
does not contain synthetic frames.

- [ ] **Step 2: Build and verify red for missing node runtime symbols**

Register `wcsi_node.c` interfaces from `app_main.c` before defining them. Run
the firmware build and expect unresolved node-runtime symbols.

- [ ] **Step 3: Implement callback and queue ownership**

Define a fixed queue item with a 36-byte metadata model plus an I/Q array sized
to the maximum accepted CSI length. Do not register a second driver-level CSI
callback. After FSM creation, call `esp_radar_get_config`, install
`wcsi_node_on_filtered_csi` as `csi_filtered_cb`, and apply it through
`esp_radar_change_config` before FSM start. The tap validates pointers and
length, copies original `wifi_csi_info_t` metadata plus exactly
`wifi_csi_filtered_info_t.raw_len` bytes from `raw_data`, and calls
`xQueueSend(..., 0)`. It increments atomic/task-safe accepted or dropped
counters and returns.

Create a separate control queue for event-triggered state snapshots. Periodic
state and heartbeat snapshots are generated by their owning task and do not
consume the CSI queue.

- [ ] **Step 4: Implement network, discovery, and health tasks**

Generate a nonzero random boot ID once per boot and increment one global
sequence per sent datagram. Bind one UDP socket to port 5501. Send telemetry to
`10.204.75.168:5500`; socket send errors increment a counter and discard the
current real-time frame. Send HELLO immediately after IP, state/heartbeat every
second, and HELLO every 30 seconds.

Register Wi-Fi/IP handlers with reconnect delays 1/2/4/8/15 seconds. After IP
returns, refresh AP BSSID/channel, reset the baseline, and publish reconnect
HELLO/state. The optional one-shot reconnect self-test calls
`esp_wifi_disconnect()` only when its Kconfig flag is enabled.

- [ ] **Step 5: Integrate only the AP sensing channel**

Follow the official example initialization but register only the connected AP
BSSID. Start the FSM and router ping. Event callbacks enqueue ACTIVE/INACTIVE
snapshots. The periodic snapshot reads `esp_wifi_sensing_fsm_get_state`,
`get_channel_diag`, and `get_channel_config`; if initialization is not stable,
send stable state unknown while preserving official process/init enums.

- [ ] **Step 6: Implement command validation and idempotence**

Receive on the same socket, validate WCSI COMMAND packets, and require the
header node ID to equal this STA MAC and the header boot ID to equal the current
boot. Apply the three public operations, map `esp_err_t` to the five ACK
statuses, and cache the last 16 encoded ACKs keyed by
`(boot_id, correlation_id)`. Duplicate RESET must return cached bytes without
calling the component twice.

- [ ] **Step 7: Build, flash, and run bounded telemetry smoke test**

Build with the tracked public lab WLAN defaults, flash COM8, then close the
serial monitor after observing self-test, IP, FSM start, and UDP task start.
Run the headless server for 60 seconds and verify HELLO, CSI, state, and
heartbeat appear without serial participation.

- [ ] **Step 8: Run verification and commit**

Run the full Python suite, deterministic vector generation, `idf.py build`,
`git diff --check`, and submodule status.

Commit:

```powershell
git add firmware/node
git commit -m "feat: stream official CSI sensing over UDP"
```

---

### Task 7: Exercise Fault Recovery and the Complete Stage 2 Gate

**Files:**
- Modify: `tests/server/test_ingest_simulator.py`
- Modify: `tests/server/test_commands.py`
- Create: `tools/stage2_validate.py`
- Create: `tests/tools/test_stage2_validate.py`

**Interfaces:**
- Consumes: server registry snapshots/counters and ignored newline-delimited
  acceptance summaries.
- Produces: `Stage2Result(passed: bool, failures: tuple[str, ...], metrics: dict)`
  and CLI `uv run python tools/stage2_validate.py ...`.

- [ ] **Step 1: Add failing recovery/gate tests**

Extend simulator integration coverage for server absence, server restart,
HELLO rediscovery, sequence wrap, corrupt packets, and boot-ID replacement.
Create validator tests where each of the eight design gate conditions is
individually absent and assert a stable failure reason. Include boundary tests
for exactly 600 seconds, exactly 43.88 CSI/s over the measured window, exactly
one ACTIVE/INACTIVE, restart rediscovery at 35 seconds, and one successful
GET_CONFIG/RESET_BASELINE ACK.

- [ ] **Step 2: Run new tests and verify red**

Run:

```powershell
uv run pytest tests/server tests/tools/test_stage2_validate.py -v
```

- [ ] **Step 3: Implement machine-readable acceptance collection**

Add an optional `--metrics-jsonl .artifacts/stage2/server-metrics.jsonl` to the
server. Emit one compact JSON object per second containing elapsed time, node
ID/boot ID, current state, message counts, measured CSI rate, device queue
drops/send errors, server gaps/duplicates/out-of-order, parse errors, last-seen
age, and command results. Do not include raw I/Q or credentials.

`stage2_validate.py` reads that JSONL plus a small operator-observation JSON
for server stop/start and reconnect timestamps. It exits zero only when all
eight approved conditions pass and prints one summary JSON object.

- [ ] **Step 4: Verify simulator fault matrix and commit**

Run all tests and a scripted simulator sequence that starts the simulator,
starts/stops/restarts the server, injects corruption, and completes commands.
Validate the resulting JSONL with the gate CLI.

Commit:

```powershell
git add server tests tools/stage2_validate.py
git commit -m "test: automate Stage 2 recovery gate"
```

---

### Task 8: Run Real COM8 Acceptance and Publish the Stage 2 Report

**Files:**
- Create: `docs/verification/stage2-node-server-link.md`
- Create: `firmware/node/README.md`
- Modify: `README.md`

**Interfaces:**
- Consumes: real server metrics, bounded serial startup log, command results,
  reconnect/restart observation, build output, component lock, and Stage 2 gate.
- Produces: a measured PASS/FAIL report and exact inputs for Stage 3 planning.

- [ ] **Step 1: Prepare real-device evidence paths**

Create `.artifacts/stage2/`. Use the tracked public PRTS lab WLAN defaults and
confirm the server address, ports, periods, and queue capacity.

- [ ] **Step 2: Build and flash the acceptance firmware**

Build with the reconnect self-test enabled for one delayed disconnect. Flash
COM8, capture only bounded startup/reconnect diagnostics, then close serial.
Record firmware SHA, component versions, device MAC, AP BSSID/channel, boot ID,
and the exact time the serial monitor was closed.

- [ ] **Step 3: Run the ten-minute headless acceptance**

Start the server with JSONL metrics. During the run:

1. observe at least one official ACTIVE and INACTIVE transition;
2. execute GET_CONFIG and one RESET_BASELINE using one correlation each;
3. allow the configured Wi-Fi disconnect/reconnect self-test to complete;
4. stop the server for at least 10 seconds, restart it, and measure rediscovery;
5. continue until at least 600 seconds of node reporting are collected.

Do not run a serial monitor during the headless measurement interval.

- [ ] **Step 4: Run the formal gate**

Run:

```powershell
uv run python tools/stage2_validate.py `
  --metrics .artifacts/stage2/server-metrics.jsonl `
  --observations .artifacts/stage2/observations.json
```

Expected: exit 0 with `"passed": true`. No WLAN credential scan is required
because this deployment uses an explicitly public lab network.

- [ ] **Step 5: Write the measured report without raw data**

Document:

- exact commit/tool/component/hardware/network identifiers;
- protocol vector and C self-test results;
- headless duration, CSI frames/rate/length distribution, and state counts;
- device queue drops/send errors separately from server gaps/duplicates/old;
- parse errors by reason;
- Wi-Fi reconnect and server-restart rediscovery timings;
- GET_CONFIG and idempotent RESET_BASELINE results;
- ACTIVE/INACTIVE accuracy limitation and unauthenticated trusted-LAN boundary;
- explicit PASS/FAIL and the statement that Stage 3 remains unimplemented.

Add concise build/run instructions to `firmware/node/README.md` and link the
Stage 1/Stage 2 reports from the root README.

- [ ] **Step 6: Run final verification and independent review**

Run fresh:

```powershell
uv run pytest -q
idf.py -C firmware/node build
uv run python tools/stage2_validate.py --metrics .artifacts/stage2/server-metrics.jsonl --observations .artifacts/stage2/observations.json
git diff --check
git status --short
git -C third_party/esp-csi status --short
```

Request read-only code/spec/evidence review. Resolve every Critical and
Important issue, rerun affected checks, and keep raw artifacts ignored.

- [ ] **Step 7: Commit and publish a stacked Draft PR**

Commit:

```powershell
git add README.md firmware/node/README.md docs/verification/stage2-node-server-link.md
git commit -m "docs: verify Stage 2 node server link"
```

Push `codex/stage2-node-server-link` and create a Draft PR with base
`codex/stage1-official-baseline`. Include measured metrics, test/build/gate
commands, limitations, and the dependency on Stage 1 PR #2. Keep the worktree
for review feedback and stop before Stage 3.
