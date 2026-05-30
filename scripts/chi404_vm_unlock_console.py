#!/usr/bin/env python3
"""Unlock / keep Windows VM console usable for VNC (disable lock, wake session)."""
import base64
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from chi404_vm_winrm import require_vm_password, session


def main() -> int:
    pw = require_vm_password()
    b64 = base64.b64encode(pw.encode("utf-8")).decode("ascii")
    s = session()

    ps = f"""
$AdminPassword = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('{b64}'))
powercfg /change monitor-timeout-ac 0
powercfg /change standby-timeout-ac 0
powercfg /change hibernate-timeout-ac 0
New-Item -Path 'HKLM:\\SOFTWARE\\Policies\\Microsoft\\Windows\\Personalization' -Force | Out-Null
Set-ItemProperty 'HKLM:\\SOFTWARE\\Policies\\Microsoft\\Windows\\Personalization' -Name NoLockScreen -Value 1
Set-ItemProperty 'HKLM:\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Policies\\System' -Name DisableLockWorkstation -Value 1
Set-ItemProperty 'HKLM:\\SOFTWARE\\Microsoft\\Windows NT\\CurrentVersion\\Winlogon' -Name AutoAdminLogon -Value '1'
Set-ItemProperty 'HKLM:\\SOFTWARE\\Microsoft\\Windows NT\\CurrentVersion\\Winlogon' -Name DefaultUserName -Value 'Administrator'
Set-ItemProperty 'HKLM:\\SOFTWARE\\Microsoft\\Windows NT\\CurrentVersion\\Winlogon' -Name DefaultPassword -Value $AdminPassword
Set-ItemProperty 'HKLM:\\SOFTWARE\\Microsoft\\Windows NT\\CurrentVersion\\Winlogon' -Name ForceAutoLogon -Value '1'
query user
"""
    r = s.run_ps(ps)
    print(r.std_out.decode(errors="replace"))
    print("lock-screen policy updated; reconnect VNC or reboot VM if still locked")
    return 0 if r.status_code == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
