# Stage 1 Official Baseline Verification

## Decision

`PASS` — the combined gate accepted 5,766 valid official router-CSI frames over
65.677 seconds and observed both official AP-channel states (`36 ACTIVE`,
`36 INACTIVE`) on COM8.
This pass authorizes the next Target-1 stage only; it does not validate static
person detection, movement classification, or fall detection.

## Reproducibility

- Test date/timezone: 2026-08-16, Asia/Shanghai (UTC+08:00)
- ESP-IDF: v5.4.4
- `esp-csi`: `8633d67152db2808f141cc1595970aa9cf406045`
- Resolved `esp_wifi_sensing`: `0.1.1~2`
- Sensing dependency-lock SHA-256:
  `E3D235C9AD06401016562F5DEAB37E49791143B47E2266E41FF85F2B633EF8A6`
- Device: ESP32-S3 QFN56 revision v0.2, physical 8 MB flash and embedded
  8 MB PSRAM; the official example image header used the default 2 MB flash
  size
- Port and STA MAC: COM8, `28:84:85:87:2b:f4`
- AP BSSID and RF channel: `5e:e3:88:d9:5b:42`, 2.4 GHz channel 4, 20 MHz
- Node/AP distance, height, and orientation: not instrumented in this run;
  future placement-specific acceptance must record these values
- Credentials were supplied from the saved Windows WLAN profile directly to
  the runtime serial prompt and were not placed in firmware configuration,
  logs, or Git

## Raw Router CSI

- Official example: `examples/get-started/csi_recv_router`
- Capture duration: 65.677460 seconds of valid device timestamps within an
  80-second host capture; this exceeds the automated 60-second minimum
- Valid/invalid CSI lines: 5,766 / 0
- CSI length histogram: 128 bytes × 5,766 frames
- Sustained sample rate: 87.7775 frames/s
- Peak rolling one-second volume: 131 frames
- RSSI range: -85 to -76 dBm
- `first_word_invalid` frames: 0
- Observed source MACs/channels: one AP BSSID, channel 4

## Official Sensing FSM

- Official example: `examples/esp-radar/wifi_sensing_demo`
- FSM start: 14.361 seconds after boot; AP baseline averaged at 18.701 seconds
  after boot (4.340 seconds after FSM start)
- Traffic settings: 20 ms FSM polling and 100 Hz router ping
- AP events: 36 ACTIVE and 36 INACTIVE
- ACTIVE-to-INACTIVE intervals: minimum 40 ms, median 180 ms, mean 337.2 ms,
  maximum 1,680 ms
- INACTIVE-to-next-ACTIVE intervals: minimum 100 ms, median 920 ms, mean
  1,584.0 ms, maximum 7,340 ms
- Runtime configuration: motion sensitivity 0.5, presence sensitivity 0.25,
  active jitter minimum 0.01, active filter/hold 300 ms, confirmation count 2
- Human action times were not synchronized to the device clock, so the event
  intervals above are state dwell/gap measurements, not detection latency
- The 36 rapid state pairs include unexplained transitions during the intended
  still period. The default configuration is therefore suitable for closing
  the official event pipeline but requires placement-specific stabilization
  before product-level “有人/无人” accuracy claims.

## Stage 2 Inputs

- The custom protocol must accept the observed 128-byte CSI payload and retain
  a variable-length field; it must not hard-code 128 as the only legal length.
- Initial throughput assumption: 87.78 sustained frames/s and at least
  131 frames/s rolling peak per node under this AP and placement.
- Queue sizing input: at least 262 frames for two seconds at the observed peak;
  512 frames per node is the recommended initial headroom before measured
  server-side backpressure tests.
- Public serial diagnostics confirmed in the official component include
  `jitter_value`, `wander_value`, `smooth_scaled`, `enter_level_scaled`,
  `exit_level_scaled`, motion state, initialization stage, presence readiness,
  presence status/threshold, and training status/thresholds.
- Target 1 continues to map only official AP `ACTIVE` → “有人” and AP
  `INACTIVE` → “无人”. Other diagnostic and training fields remain outside the
  MVP decision contract.

## Known Limitations

- ACTIVE/INACTIVE is an activity proxy, not proof of a completely static
  person. The MVP mapping is deliberately provisional.
- Results apply only to the tested AP, channel, device, and uninstrumented room
  placement.
- Default sensitivity produced frequent transitions and has not been tuned for
  false-positive rate, hold time, multi-person behavior, or room boundaries.
- No recording, annotation, movement/fall classification, custom model,
  server, Web UI, or multi-node behavior was implemented or evaluated here.
