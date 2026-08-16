# WCSI node firmware

This ESP-IDF 5.4.4 application sends WCSI v1 telemetry to the Stage 2 server
and receives its UDP commands. The checked-in defaults target the designated
public PRTS lab WLAN and `10.204.75.168:5500`; commands are received on UDP
port 5501. Local `sdkconfig` is ignored because it is deployment-specific.

Build and flash COM8:

```powershell
& 'C:\Espressif\tools\Microsoft.v5.4.4.PowerShell_profile.ps1'
$env:IDF_CCACHE_ENABLE='0'
idf.py -C firmware/node build
idf.py -C firmware/node -p COM8 flash
```

For a bounded diagnostic session only, use `idf.py -C firmware/node -p COM8
monitor`, then exit with `Ctrl+]`. Do not leave a serial monitor running during
headless telemetry measurements.

Run the headless server with metrics:

```powershell
$env:PYTHONPATH='server'
& 'C:\Espressif\tools\python\v5.4.4\venv\Scripts\python.exe' -m wificsi.server `
  --host 10.204.75.168 --duration 620 `
  --metrics-jsonl .artifacts/stage2/server-metrics.jsonl
```

For the Stage 2 acceptance gate, add exactly one `--get-config` and one
`--reset-baseline` argument for the discovered node, record a real local
reconnect separately from a server restart, and validate the resulting
aggregate-only metrics with `tools/stage2_validate.py`.
