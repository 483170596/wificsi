# Stage 2 node/server link verification

## Decision

`PASS` — a real ESP32-S3 node on COM8 completed 638.9115786552429 seconds of
qualified headless reporting to `10.204.75.168:5500`. The formal Stage 2 gate
accepted the unmodified aggregate metrics with no failures. Stage 3 remains
unimplemented.

## Reproducibility and bounded startup

- Test date/timezone: 2026-08-16, Asia/Shanghai (UTC+08:00)
- Firmware commit: `526d993e51e1a29c00dce3884986cee320ec7132`
- ESP-IDF: 5.4.4; resolved `esp_wifi_sensing`: 0.1.1~2; `esp-radar`: 0.3.4
- Dependency-lock SHA-256: `214B6AF6C0F038D1B5ADD96AFDA24FF3FBD49A5DF18796D5EDB76FE54AA6A913`
- WCSI vector SHA-256: `3C8C0E68866515AB1B04D3086E78E77D3793C44E5832E9D305F60AC99C9AD624`
- Final image SHA-256: `27DCC950A49A098E0340E59AAA0B99C77375829D30126D54BE9C4B29EB46FBFF`
- Hardware: ESP32-S3 QFN56 revision 0.2, embedded 8 MB PSRAM; COM8 STA MAC
  `28:84:85:87:2b:f4`
- AP identity: BSSID `5e:e3:88:d9:5b:42`, channel 4; node UDP command port
  5501; telemetry server `10.204.75.168:5500`

The final flashed image passed the C protocol-vector self-test and queue
self-test. Its bounded serial capture also recorded IPv4 readiness, official
FSM start, and baseline completion. The serial handle was closed at
2026-08-16T12:26:59.7701434Z; a process query immediately before the final
headless run found no `idf.py monitor`, pyserial, `SerialPort`, or esptool COM8
holder.

The acceptance configuration is reproducible without reusing that ignored
configuration: `firmware/node/sdkconfig.acceptance.defaults` composes with
`sdkconfig.defaults` into an ignored isolated SDKCONFIG/build directory and
sets only the one-shot reconnect probe and its 5,000 ms delay. The complete
two-run, measured-observation recipe is in `firmware/node/README.md`.

The one-shot local reconnect probe was enabled for this image. In the final
headless collector it was observed as the first zero-CSI sample at
1786883808.1331775 and a new HELLO at 1786883812.1757283 (4.0425508 seconds
at the collector's one-second resolution), with the same boot ID before and
after. Subsequent HELLO, CSI, sensing-state, and heartbeat traffic resumed.

## Measured headless acceptance

Two real production-server runs, both explicitly bound to
`10.204.75.168:5500`, produced 640 target-node metric rows. The server was
absent after run 1 and before run 2 for 33.0165478 seconds from the final run-1
observation to the run-2 start. Run 2 rediscovered the same boot through HELLO
in 18.1673912 seconds.

| Measurement | Result |
| --- | ---: |
| Formal qualified duration | 638.9115786552429 s |
| Qualified CSI interval rate | 43.5813–127.9158 frames/s; mean 99.9422 |
| CSI frames | 63,997 |
| CSI length distribution (separate 60.0017 s aggregate production-ingest sample) | 384 bytes × 6,004 |
| HELLO / sensing-state / heartbeat | 23 / 1,245 / 637 |
| Official state samples | 44 ACTIVE, 587 INACTIVE |
| Parse errors by reason | none (all rows zero) |
| Device queue drops | 0 in both server runs |
| Device cumulative UDP send errors | 13 |
| Server gaps / duplicates / old | run 1: 14 / 0 / 1; run 2: 1 / 0 / 0 |

The link counters are reported separately: the deliberate reconnect and
periods with no server contributed to nonzero send/gap counters. They are not
parse failures and were not concealed. No raw CSI I/Q, metrics JSONL, or serial
log is tracked.

GET_CONFIG completed once for the target (`correlation_id=1`, one attempt) and
RESET_BASELINE completed once (`correlation_id=2`, one attempt). Both returned
OK. The formal gate was run against the ignored real artifacts:

```text
{"failures":[],"metrics":{"duration_seconds":638.9115786552429,"qualified_csi_seconds":638.9115786552429,"rows":640,"target_node_id":"28:84:85:87:2b:f4"},"passed":true}
```

## Scope and limitations

The official ACTIVE/INACTIVE state is an activity proxy, not an accuracy claim
for a static person, room boundary, false-positive rate, or multi-person
behavior. UDP control is intentionally unauthenticated and is appropriate only
inside the trusted lab LAN. This verification implements no Stage 3 analytics,
recording, model, web, or dashboard work.
