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
