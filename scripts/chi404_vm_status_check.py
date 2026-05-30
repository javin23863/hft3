#!/usr/bin/env python3
"""WinRM health checks for headless R|Trader VM on CHI404."""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from chi404_vm_winrm import session

RITHMIC_HOST = os.environ.get("HFT3_RITHMIC_HOST", "ritpz04063.04.rithmic.com")

CHECKS = [
    (
        "rithmic_proc",
        "Get-Process | Where-Object { $_.ProcessName -match 'Rithmic' } | "
        "Format-Table ProcessName,Id,MainWindowTitle -AutoSize",
    ),
    (
        "symlink",
        "Get-Item C:\\Users\\Administrator\\Documents\\Rithmic -ErrorAction SilentlyContinue | "
        "Select-Object FullName,LinkType,Target | Format-List",
    ),
    (
        "logs",
        "$d='C:\\Users\\Administrator\\Documents\\Rithmic'; "
        "Get-ChildItem -Path $d -Filter *.log -ErrorAction SilentlyContinue; "
        "Get-ChildItem -Path $d -Filter *.cur.txt -ErrorAction SilentlyContinue | "
        "Sort-Object LastWriteTime -Descending | Select-Object -First 5 Name,Length,LastWriteTime | "
        "Format-Table -AutoSize",
    ),
    (
        "tasks",
        "Get-ScheduledTask HFT3-* | Format-Table TaskName,State -AutoSize",
    ),
    (
        "rithmic_tcp",
        f"(Test-NetConnection {RITHMIC_HOST} -Port 443 -WarningAction SilentlyContinue).TcpTestSucceeded",
    ),
]


def main() -> int:
    sess = session()
    failed = False
    rithmic_running = False
    tcp_ok = False

    for label, ps in CHECKS:
        r = sess.run_ps(ps)
        print("===", label, "===")
        out = r.std_out.decode(errors="replace").strip()
        print(out)
        if r.status_code != 0:
            failed = True
        if label == "rithmic_proc" and "Rithmic" in out:
            rithmic_running = True
        if label == "rithmic_tcp" and "True" in out:
            tcp_ok = True

    if not rithmic_running:
        print("FAIL: R|Trader process not running", file=sys.stderr)
        failed = True
    if not tcp_ok:
        print(f"FAIL: TCP 443 to {RITHMIC_HOST} failed", file=sys.stderr)
        failed = True

    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
