# Stage 1 official baseline

This stage runs pinned Espressif examples without source modification:

- Raw router CSI: `third_party/esp-csi/examples/get-started/csi_recv_router`
- Official sensing FSM: `third_party/esp-csi/examples/esp-radar/wifi_sensing_demo`

Activate ESP-IDF v5.4.4 before every build:

```powershell
& 'C:\Espressif\tools\Microsoft.v5.4.4.PowerShell_profile.ps1'
idf.py --version
```

Configure Wi-Fi credentials only through local runtime or generated
configuration. The verified run enabled `Get ssid and password from stdin` and
supplied the saved WLAN profile directly to the serial prompt, so credentials
were not compiled into firmware. Generated configuration and serial captures
are ignored and must never be committed.

The baseline result is accepted only through:

```powershell
uv run python tools/baseline_validate.py stage-gate `
  --csi-log .artifacts/raw-router-com8.log `
  --sensing-log .artifacts/sensing-com8.log
```

## Verified versions

- ESP-IDF v5.4.4
- `esp-csi` commit `8633d67152db2808f141cc1595970aa9cf406045`
- `esp_wifi_sensing` component `0.1.1~2`

See the measured [Stage 1 verification report](../../docs/verification/stage1-official-baseline.md).
