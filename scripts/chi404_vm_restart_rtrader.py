#!/usr/bin/env python3
"""Stop stuck HFT3 logon tasks and restart MapSMB -> R|Trader -> interactive login."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from chi404_vm_winrm import session

PS = r"""
Get-ScheduledTask HFT3-* -ErrorAction SilentlyContinue | Stop-ScheduledTask -ErrorAction SilentlyContinue
Get-Process 'Rithmic Trader Pro' -ErrorAction SilentlyContinue | Stop-Process -Force
Start-Sleep -Seconds 2
powershell.exe -ExecutionPolicy Bypass -WindowStyle Hidden -File C:\chi404_vm_map_smb.ps1
Start-Sleep -Seconds 5
Start-ScheduledTask -TaskName 'HFT3-RithmicTrader'
Start-Sleep -Seconds 45
Get-ScheduledTask HFT3-* | Format-Table TaskName,State -AutoSize
Get-Process 'Rithmic Trader Pro' -ErrorAction SilentlyContinue |
  Format-Table Id,MainWindowTitle,StartTime -AutoSize
"""


def main() -> int:
    r = session().run_ps(PS)
    print(r.std_out.decode(errors="replace"))
    err = r.std_err.decode(errors="replace")
    if err.strip():
        print("stderr:", err[:800], file=sys.stderr)
    if r.status_code != 0:
        return 1

    runner = Path(__file__).resolve().parent / "chi404_vm_run_interactive.py"
    login = subprocess.run(
        [
            sys.executable,
            str(runner),
            "--script",
            r"C:\chi404_vm_rtrader_login.ps1",
            "--task",
            "HFT3-RithmicLoginOnce",
            "--wait-sec",
            "600",
        ],
        check=False,
    )
    if login.returncode != 0:
        return login.returncode

    sub = session().run_ps("Start-ScheduledTask -TaskName 'HFT3-RithmicSubscribe'")
    print(sub.std_out.decode(errors="replace"))
    return 0 if sub.status_code == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
