#!/usr/bin/env python3
"""Remap SMB and Documents\\Rithmic symlink on the Windows VM (CHI404)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from chi404_vm_winrm import repo_root, session, upload_paths, watch_root

PS = r"""
Get-ScheduledTask HFT3-* -ErrorAction SilentlyContinue | Stop-ScheduledTask -ErrorAction SilentlyContinue
powershell -ExecutionPolicy Bypass -File C:\chi404_vm_map_smb.ps1
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
$docs = "$env:USERPROFILE\Documents\Rithmic"
Get-Item $docs | Select-Object FullName,LinkType,Target
net use
Write-Output OK
"""


def main() -> int:
    sess = session()
    upload_paths(
        sess,
        {
            "C:/rtrader_smb.env": watch_root() / "rtrader_smb.env",
            "C:/chi404_vm_map_smb.ps1": repo_root() / "scripts" / "chi404_vm_map_smb.ps1",
        },
        required=True,
    )
    r = sess.run_ps(PS)
    print(r.std_out.decode(errors="replace"))
    err = r.std_err.decode(errors="replace")
    if err.strip():
        print("stderr:", err[:800], file=sys.stderr)
    return 0 if r.status_code == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
