# Start exactly one MBO download supervisor (dual-host priority).
$Repo = Split-Path $PSScriptRoot -Parent
$Supervisor = Join-Path $Repo "scripts\monitor_mbo_dual_download.ps1"

Get-CimInstance Win32_Process -Filter "Name='powershell.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -like '*monitor_mbo_dual_download*' } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }

Start-Sleep -Seconds 2
Start-Process -WindowStyle Hidden powershell -ArgumentList @(
    "-NoProfile", "-ExecutionPolicy", "Bypass",
    "-File", $Supervisor,
    "-PollSeconds", "120",
    "-StallMinutes", "15",
    "-Workers", "24"
)
Write-Host "Supervisor started. Status: runtime/data_downloads/monitor_status.json"
Write-Host "Log:        runtime/data_downloads/monitor.log"
