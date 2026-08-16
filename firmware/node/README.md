# WCSI node firmware

This ESP-IDF 5.4.4 application sends WCSI v1 telemetry to the Stage 2 server
and receives its UDP commands. The checked-in defaults target the designated
public PRTS lab WLAN and `10.204.75.168:5500`; commands are received on UDP
port 5501. Local `sdkconfig` is ignored because it is deployment-specific.

## Ordinary build

```powershell
& 'C:\Espressif\tools\Microsoft.v5.4.4.PowerShell_profile.ps1'
$env:IDF_CCACHE_ENABLE='0'
idf.py -C firmware/node build
idf.py -C firmware/node -p COM8 flash
```

## Reproducible Stage 2 acceptance recipe

`firmware/node/sdkconfig.acceptance.defaults` is intentionally narrow: it adds
only the one-shot reconnect probe and its 5,000 ms delay. Compose it with the
normal defaults into an ignored SDKCONFIG and ignored build directory so a
clean checkout neither changes `firmware/node/sdkconfig` nor reuses the normal
`firmware/node/build` directory.

Run the following from the repository root. The public lab WLAN policy remains
unchanged; do not put credentials, raw CSI I/Q, serial logs, or JSONL metrics
in Git.

```powershell
& 'C:\Espressif\tools\Microsoft.v5.4.4.PowerShell_profile.ps1'
$env:IDF_CCACHE_ENABLE='0'
$uv = 'D:\Develop\uv\uv.exe'
$env:PYTHONPATH = Join-Path (Get-Location) 'server'
$acceptanceBuild = Join-Path (Get-Location) '.artifacts\stage2\build-acceptance'
$acceptanceSdkconfig = Join-Path (Get-Location) '.artifacts\stage2\sdkconfig.acceptance'
$normalDefaults = Join-Path (Get-Location) 'firmware\node\sdkconfig.defaults'
$acceptanceDefaults = Join-Path (Get-Location) 'firmware\node\sdkconfig.acceptance.defaults'
$defaults = "$normalDefaults;$acceptanceDefaults"

idf.py -C firmware/node -B $acceptanceBuild -DIDF_TARGET=esp32s3 `
  "-DSDKCONFIG=$acceptanceSdkconfig" "-DSDKCONFIG_DEFAULTS=$defaults" build
Select-String -Path $acceptanceSdkconfig `
  -Pattern '^CONFIG_WCSI_RECONNECT_SELF_TEST=y$','^CONFIG_WCSI_RECONNECT_SELF_TEST_DELAY_MS=5000$'
idf.py -C firmware/node -B $acceptanceBuild -p COM8 flash
```

For a bounded diagnostic session only, use `idf.py -C firmware/node -p COM8
monitor`, then exit with `Ctrl+]`. For the acceptance build, substitute
`-B $acceptanceBuild` in that command. Record the C self-test, association,
FSM, and reconnect milestones, then close it. Prove that no serial holder
remains before headless measurement:

```powershell
idf.py -C firmware/node -B $acceptanceBuild -p COM8 monitor
# Exit the monitor with Ctrl+]. Then run:
$serialHolders = Get-CimInstance Win32_Process | Where-Object {
  $_.ProcessId -ne $PID -and $_.CommandLine -match '(?i)idf\.py.*monitor|serial\.Serial|SerialPort.*COM8|esptool.*COM8'
}
if ($serialHolders) { $serialHolders | Format-List; throw 'COM8 holder remains' }
```

The delayed reconnect is one-shot per boot. To measure it inside the headless
metrics window, use the closed-port reset below and immediately start server
run 1; do not open a monitor after this point. `esptool.py` exits and releases
COM8 before the server starts.

```powershell
esptool.py --chip esp32s3 -p COM8 chip_id
if (Get-CimInstance Win32_Process | Where-Object {
  $_.ProcessId -ne $PID -and $_.CommandLine -match '(?i)idf\.py.*monitor|serial\.Serial|SerialPort.*COM8|esptool.*COM8'
}) { throw 'COM8 holder remains after reset' }
```

Set `$targetNode` to the actual six-byte STA MAC observed in the bounded
startup capture. Do not substitute an invented value. First run one production
server for about 330 seconds with exactly one occurrence of each required
command argument; it binds the required non-wildcard address and creates only
ignored aggregate metrics/logs.

```powershell
$python = 'C:\Espressif\tools\python\v5.4.4\venv\Scripts\python.exe'
$targetNode = 'aa:bb:cc:dd:ee:ff' # replace with the measured target MAC
$metrics = Join-Path (Get-Location) '.artifacts\stage2\server-metrics.jsonl'
$server1Log = Join-Path (Get-Location) '.artifacts\stage2\server-1.log'
$server1Err = Join-Path (Get-Location) '.artifacts\stage2\server-1.err'
$server1 = Start-Process -FilePath $python -ArgumentList @(
  '-m','wificsi.server','--host','10.204.75.168','--duration','330',
  '--metrics-jsonl',$metrics,'--get-config',$targetNode,'--reset-baseline',$targetNode
) -WorkingDirectory (Get-Location) -RedirectStandardOutput $server1Log `
  -RedirectStandardError $server1Err -WindowStyle Hidden -PassThru
Wait-Process -Id $server1.Id
$serverStoppedAt = [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds() / 1000.0
```

Leave the port unbound for at least ten seconds, then append an independent
second server run for about 330 seconds. The first sample of each server run
is conservatively uncredited, so these two durations intentionally exceed 600
seconds of legitimate reporting.

```powershell
Start-Sleep -Seconds 12
$server2Log = Join-Path (Get-Location) '.artifacts\stage2\server-2.log'
$server2Err = Join-Path (Get-Location) '.artifacts\stage2\server-2.err'
$server2 = Start-Process -FilePath $python -ArgumentList @(
  '-m','wificsi.server','--host','10.204.75.168','--duration','330',
  '--metrics-jsonl',$metrics
) -WorkingDirectory (Get-Location) -RedirectStandardOutput $server2Log `
  -RedirectStandardError $server2Err -WindowStyle Hidden -PassThru
Wait-Process -Id $server2.Id
```

Derive observations only from the actual captured metric rows. For the local
reconnect, use the actual first zero-CSI `observed_at` row and the actual
subsequent HELLO-delta `observed_at` row from run 1. For restart discovery, use
the first `message_deltas.HELLO > 0` row in run 2. Do not use planned times or
fixed timestamps. The command below extracts the run-2 server-start and first
HELLO observations; inspect the run-1 rows to fill the two reconnect values.

```powershell
$rows = Get-Content $metrics | ForEach-Object { $_ | ConvertFrom-Json }
$targetRows = $rows | Where-Object { $_.node_id -eq $targetNode }
$runs = $targetRows | Group-Object server_run_started_at | Sort-Object { [double]$_.Name }
if ($runs.Count -ne 2) { throw 'expected exactly two server runs for target' }
$serverStartedAt = [double]$runs[1].Name
$serverRediscoveredAt = [double](
  $runs[1].Group | Where-Object { $_.message_deltas.HELLO -gt 0 } |
  Select-Object -First 1 -ExpandProperty observed_at
)
$reconnectDisconnectedAt = <actual run-1 first-zero-CSI observed_at>
$reconnectReconnectedAt = <actual run-1 resumed-HELLO observed_at>
if ($serverRediscoveredAt -le $serverStartedAt) { throw 'missing post-restart HELLO' }

$observations = [ordered]@{
  target_node_id = $targetNode
  reconnect_disconnected_at = $reconnectDisconnectedAt
  reconnect_reconnected_at = $reconnectReconnectedAt
  server_stopped_at = $serverStoppedAt
  server_started_at = $serverStartedAt
  server_rediscovered_at = $serverRediscoveredAt
}
$observationPath = Join-Path (Get-Location) '.artifacts\stage2\observations.json'
$observations | ConvertTo-Json -Compress | Set-Content -NoNewline $observationPath
& $uv run python tools/stage2_validate.py --metrics $metrics --observations $observationPath
```

The final command must print `"passed":true`. It rejects a wildcard server
address, insufficient server absence, a reconnect overlapping the restart, an
uncredited first sample, missing telemetry after recovery, or any duplicate
successful GET_CONFIG/RESET_BASELINE result.
