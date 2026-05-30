#!/usr/bin/env python3
"""SMB connectivity diagnostics from Windows VM via WinRM."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from chi404_vm_winrm import session

TESTS = [
    "Test-Connection -ComputerName 192.168.122.1 -Count 1",
    "Test-NetConnection -ComputerName 192.168.122.1 -Port 445 | Select-Object TcpTestSucceeded,PingSucceeded",
    "Get-Content C:\\rtrader_smb.env | ForEach-Object { if ($_ -match '^RTRADER_SMB_PASS=') { 'RTRADER_SMB_PASS=***' } else { $_ } }",
    (
        "$e=Get-Content C:\\rtrader_smb.env | Where-Object {$_ -match '^RTRADER_SMB_(HOST|USER|PASS)='}; "
        "foreach($line in $e){ if($line -match '^([^=]+)=(.*)$'){ Set-Item env:$($matches[1]) $matches[2] } }; "
        "cmdkey /add:$env:RTRADER_SMB_HOST /user:$env:RTRADER_SMB_USER /pass:$env:RTRADER_SMB_PASS | Out-Null; "
        "net use \\\\$env:RTRADER_SMB_HOST\\rtrader_watch /user:$env:RTRADER_SMB_USER $env:RTRADER_SMB_PASS /persistent:yes; "
        "net use"
    ),
]


def main() -> int:
    s = session()
    failed = False
    for ps in TESTS:
        r = s.run_ps(ps)
        print(f"--- {ps[:60]} --- status={r.status_code}")
        out = r.std_out.decode(errors="replace").strip()
        if out:
            print(out)
        if r.status_code != 0:
            failed = True
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
