#!/usr/bin/env python3
"""Stop stuck HFT3 logon tasks and restart R|Trader + login (headless maintenance)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from chi404_vm_winrm import session

PS = r"""
Get-ScheduledTask HFT3-* -ErrorAction SilentlyContinue | Stop-ScheduledTask -ErrorAction SilentlyContinue
Get-Process 'Rithmic Trader Pro' -ErrorAction SilentlyContinue | Stop-Process -Force
Start-Sleep -Seconds 2
Start-ScheduledTask -TaskName 'HFT3-RithmicTrader'
Start-Sleep -Seconds 5
Start-ScheduledTask -TaskName 'HFT3-RithmicLogin'
Get-ScheduledTask HFT3-* | Format-Table TaskName,State -AutoSize
Get-Process 'Rithmic Trader Pro' -ErrorAction SilentlyContinue |
  Format-Table Id,MainWindowTitle,StartTime -AutoSize
"""


def main() -> int:
    r = session().run_ps(PS)
    print(r.std_out.decode(errors="replace"))
    return 0 if r.status_code == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
