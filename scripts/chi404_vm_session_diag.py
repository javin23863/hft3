#!/usr/bin/env python3
"""Diagnose R|Trader process session vs WinRM session on VM."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from chi404_vm_winrm import session

PS = r"""
Write-Output '=== query user ==='
query user
Write-Output '=== rithmic process ==='
Get-Process 'Rithmic Trader Pro' -ErrorAction SilentlyContinue |
  Select-Object Id,SessionId,MainWindowTitle,MainWindowHandle,StartTime | Format-List
Write-Output '=== interactive task states ==='
Get-ScheduledTask HFT3-* | Format-Table TaskName,State -AutoSize
Write-Output '=== session json ==='
if (Test-Path C:\chi404_rtrader_session.json) { Get-Content C:\chi404_rtrader_session.json -Raw }
"""

if __name__ == "__main__":
    r = session().run_ps(PS)
    print(r.std_out.decode(errors="replace"))
    sys.exit(r.status_code)
