#!/usr/bin/env python3
"""Poll VM sweep_manifest.json until real UI sweep completes (interactive session)."""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from chi404_vm_winrm import session

RUN_PS = r"""
param([string]$ScriptPath, [string]$TaskName)
$ErrorActionPreference = 'Stop'
Get-ScheduledTask $TaskName -ErrorAction SilentlyContinue | Unregister-ScheduledTask -Confirm:$false
$action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument "-ExecutionPolicy Bypass -WindowStyle Hidden -File `"$ScriptPath`""
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddSeconds(5)
$principal = New-ScheduledTaskPrincipal -UserId 'Administrator' -LogonType Interactive -RunLevel Highest
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Principal $principal -Force | Out-Null
Start-ScheduledTask -TaskName $TaskName
Write-Output "started_interactive_task=$TaskName script=$ScriptPath"
"""


def _poll_manifest(sess, manifest_unc: str, min_confirmed: int) -> tuple[bool, str]:
    ps = f"""
$path = '{manifest_unc}'
if (-not (Test-Path $path)) {{ Write-Output 'MANIFEST_MISSING'; exit 0 }}
try {{
  $j = Get-Content $path -Raw | ConvertFrom-Json
  $mode = [string]$j.mode
  $confirmed = [int]$j.confirmed_export
  $done = [string]$j.done_utc
  $target = [int]$j.target_orders
  Write-Output "mode=$mode confirmed=$confirmed target=$target done=$done"
  if ($mode -eq 'rtrader_ui_real' -and $done -and $confirmed -ge {min_confirmed}) {{
    Write-Output 'SWEEP_COMPLETE'
  }}
}} catch {{
  Write-Output "MANIFEST_ERR=$($_.Exception.Message)"
}}
"""
    r = sess.run_ps(ps)
    out = r.std_out.decode(errors="replace")
    return "SWEEP_COMPLETE" in out, out


def _poll_login(sess, poll_path: str) -> tuple[bool, str]:
    ps = f"""
if (Test-Path '{poll_path}') {{ Get-Content '{poll_path}' -Raw }} else {{ Write-Output 'NO_SESSION' }}
"""
    r = sess.run_ps(ps)
    out = r.std_out.decode(errors="replace")
    if "logged_in_guess" in out and "true" in out.lower():
        return True, out
    if "Login succeeded" in out:
        return True, out
    return False, out


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--script", default=r"C:\chi404_vm_rtrader_login.ps1")
    p.add_argument("--task", default="HFT3-RunInteractiveOnce")
    p.add_argument("--wait-sec", type=int, default=600)
    p.add_argument("--poll", default=r"C:\chi404_rtrader_session.json")
    p.add_argument(
        "--manifest-unc",
        default=r"\\192.168.122.1\rtrader_watch\sweep_manifest.json",
    )
    p.add_argument("--min-confirmed", type=int, default=0)
    p.add_argument(
        "--success-pattern",
        default="",
        help="Deprecated; sweep uses manifest poll, login uses session json",
    )
    args = p.parse_args()

    is_sweep = "paper_order_sweep" in args.script.lower()
    sess = session()
    r = sess.run_ps(
        f"& {{ {RUN_PS} }} -ScriptPath '{args.script}' -TaskName '{args.task}'"
    )
    print(r.std_out.decode(errors="replace"))
    if r.status_code != 0:
        print(r.std_err.decode(errors="replace")[:1500], file=sys.stderr)
        return 1

    deadline = time.time() + args.wait_sec
    while time.time() < deadline:
        if is_sweep:
            ok, out = _poll_manifest(sess, args.manifest_unc, args.min_confirmed)
        else:
            ok, out = _poll_login(sess, args.poll)
        print("--- poll ---")
        print(out.strip())
        if ok:
            return 0
        time.sleep(15)

    print("timeout waiting for interactive script", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
