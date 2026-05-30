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
        "Get-ChildItem -Path $d -File -ErrorAction SilentlyContinue | "
        "Sort-Object LastWriteTime -Descending | Select-Object -First 5 Name,Length,LastWriteTime | "
        "Format-Table -AutoSize",
    ),
    (
        "log_fresh",
        "$share='\\\\192.168.122.1\\rtrader_watch'; "
        "$cut=(Get-Date).AddMinutes(-10); "
        "Get-ChildItem -Path $share -File -ErrorAction SilentlyContinue | "
        "Where-Object { $_.LastWriteTime -ge $cut -and $_.Extension -match '\\.(log|txt)$' } | "
        "Select-Object Name,Length,LastWriteTime | Format-Table -AutoSize",
    ),
    (
        "tasks",
        "Get-ScheduledTask HFT3-* | Format-Table TaskName,State -AutoSize",
    ),
    (
        "session",
        "if (Test-Path C:\\chi404_rtrader_session.json) { Get-Content C:\\chi404_rtrader_session.json -Raw }",
    ),
    (
        "rithmic_tcp",
        f"(Test-NetConnection {RITHMIC_HOST} -Port 443 -WarningAction SilentlyContinue).TcpTestSucceeded",
    ),
]

BAD_TITLE_PATTERNS = ("login", "waiting for price history")


def main() -> int:
    sess = session()
    failed = False
    rithmic_running = False
    tcp_ok = False
    window_title = ""
    tasks_running_stuck = False
    log_fresh = False

    for label, ps in CHECKS:
        r = sess.run_ps(ps)
        print("===", label, "===")
        out = r.std_out.decode(errors="replace").strip()
        print(out)
        if r.status_code != 0:
            failed = True
        if label == "rithmic_proc" and "Rithmic" in out:
            rithmic_running = True
            for line in out.splitlines():
                if "Rithmic" in line and "MainWindowTitle" not in line:
                    parts = line.split()
                    if len(parts) >= 3:
                        window_title = " ".join(parts[2:]).strip()
        if label == "rithmic_tcp" and "True" in out:
            tcp_ok = True
        if label == "log_fresh" and out and "Name" in out:
            log_fresh = True
        if label == "tasks":
            for line in out.splitlines():
                if "Running" not in line or "HFT3-" not in line:
                    continue
                if "HFT3-RithmicTrader" in line:
                    continue
                tasks_running_stuck = True

    if not rithmic_running:
        print("FAIL: R|Trader process not running", file=sys.stderr)
        failed = True
    if not tcp_ok:
        print(f"FAIL: TCP 443 to {RITHMIC_HOST} failed", file=sys.stderr)
        failed = True
    if window_title and any(p in window_title.lower() for p in BAD_TITLE_PATTERNS):
        print(f"FAIL: R|Trader session not ready (title={window_title!r})", file=sys.stderr)
        failed = True
    if tasks_running_stuck:
        print("WARN: HFT3 PowerShell task still Running (login/subscribe may be in progress)", file=sys.stderr)
    if not log_fresh:
        print("WARN: no log file modified in last 10 minutes on SMB watch share", file=sys.stderr)

    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
