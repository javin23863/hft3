#!/usr/bin/env python3
"""Reset stuck VM tasks, restart R|Trader in console session, poll for HWND."""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from chi404_vm_winrm import session

RESET = r"""
$ErrorActionPreference = 'Continue'
Get-ScheduledTask HFT3-* -ErrorAction SilentlyContinue | Stop-ScheduledTask -ErrorAction SilentlyContinue
Start-Sleep 3
Get-Process 'Rithmic Trader Pro' -ErrorAction SilentlyContinue | Stop-Process -Force
Start-Sleep 2
powershell.exe -ExecutionPolicy Bypass -File C:\chi404_vm_map_smb.ps1
Start-Sleep 3
Start-ScheduledTask -TaskName 'HFT3-RithmicTrader'
Write-Output 'restarted_trader_task'
"""

POLL = r"""
$p = Get-Process 'Rithmic Trader Pro' -ErrorAction SilentlyContinue | Select-Object -First 1
if (-not $p) { Write-Output 'no_process'; exit 0 }
Write-Output "pid=$($p.Id) session=$($p.SessionId) hwnd=$($p.MainWindowHandle) title=$($p.MainWindowTitle)"
(Get-ItemProperty 'HKLM:\Software\Microsoft\Windows NT\CurrentVersion').InstallationType
"""

if __name__ == "__main__":
    s = session()
    print(s.run_ps(RESET).std_out.decode(errors="replace"))
    for i in range(36):
        out = s.run_ps(POLL).std_out.decode(errors="replace")
        print(f"poll {i+1}: {out.strip()}")
        if "hwnd=" in out and "hwnd=0" not in out.split("title=")[0]:
            # hwnd non-zero
            if "hwnd=0" not in out.replace("hwnd=0", "", 1) or int(out.split("hwnd=")[1].split()[0]) > 0:
                pass
        if "hwnd=" in out:
            part = out.split("hwnd=")[1].split()[0]
            if part.isdigit() and int(part) > 0:
                print("HWND ready")
                sys.exit(0)
        if "title=" in out and "Rithmic" in out and "Login" not in out:
            print("title ready")
            sys.exit(0)
        time.sleep(10)
    sys.exit(1)
