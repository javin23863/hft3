#!/usr/bin/env python3
"""Shared WinRM helpers for CHI404 Windows VM sidecar scripts."""
from __future__ import annotations

import base64
import os
import re
import subprocess
import sys
from pathlib import Path

try:
    import winrm
except ImportError:  # pragma: no cover - optional on dev workstation
    winrm = None  # type: ignore[assignment]

DEFAULT_VM = "192.168.122.136"
DEFAULT_USER = "Administrator"
DEFAULT_REPO = "/root/hft3/repo"
DEFAULT_WATCH = "/root/hft3/rtrader_watch"


def require_vm_password() -> str:
    pw = os.environ.get("VM_ADMIN_PASSWORD", "").strip()
    if not pw:
        print("VM_ADMIN_PASSWORD is required", file=sys.stderr)
        sys.exit(1)
    return pw


def resolve_vm_host() -> str:
    explicit = os.environ.get("VM_WINRM_HOST", "").strip()
    if explicit:
        return explicit
    try:
        r = subprocess.run(
            ["virsh", "domifaddr", "hft3-rtrader-win"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        for line in r.stdout.splitlines():
            m = re.search(r"(\d+\.\d+\.\d+\.\d+)/\d+", line)
            if m:
                return m.group(1)
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        pass
    print(
        "Could not resolve VM IP from virsh; set VM_WINRM_HOST in .env",
        file=sys.stderr,
    )
    sys.exit(1)


def vm_host() -> str:
    return resolve_vm_host()


def vm_user() -> str:
    return os.environ.get("VM_ADMIN_USER", DEFAULT_USER)


def repo_root() -> Path:
    return Path(
        os.environ.get("HFT3_REPO_DIR")
        or os.environ.get("HFT3_REPO")
        or DEFAULT_REPO
    )


def watch_root() -> Path:
    raw = os.environ.get("RTRADER_WATCH_DIRS") or os.environ.get("RTRADER_WATCH")
    if raw:
        return Path(raw.split(";")[0].strip().strip('"'))
    return Path(DEFAULT_WATCH)


def session():
    if winrm is None:
        print("pywinrm is required on CHI404: pip install pywinrm", file=sys.stderr)
        sys.exit(1)
    return winrm.Session(
        f"http://{vm_host()}:5985/wsman",
        auth=(vm_user(), require_vm_password()),
        transport="ntlm",
        server_cert_validation="ignore",
    )


def upload_file(sess, dest: str, data: bytes) -> None:
    staging = dest + ".b64"
    sess.run_ps(f"Remove-Item -Force '{dest}','{staging}' -ErrorAction SilentlyContinue")
    step = 1200
    b64 = base64.b64encode(data).decode("ascii")
    for i in range(0, len(b64), step):
        chunk = b64[i : i + step]
        ur = sess.run_ps(f"Add-Content -Path '{staging}' -Value '{chunk}' -NoNewline")
        if ur.status_code != 0:
            raise RuntimeError(f"upload chunk failed: {dest}")
    ps = (
        f"$raw=[IO.File]::ReadAllText('{staging}');"
        f"$bytes=[Convert]::FromBase64String($raw);"
        f"[IO.File]::WriteAllBytes('{dest}', $bytes);"
        f"Remove-Item -Force '{staging}'"
    )
    ur = sess.run_ps(ps)
    if ur.status_code != 0:
        raise RuntimeError(f"upload decode failed: {dest}")


def upload_paths(sess, mapping: dict[str, Path], *, required: bool = True) -> None:
    for dest, src in mapping.items():
        if not src.exists():
            msg = f"missing required file: {src}"
            if required:
                print(msg, file=sys.stderr)
                sys.exit(1)
            print("skip missing", src)
            continue
        upload_file(sess, dest, src.read_bytes())
        print("uploaded", dest, src.stat().st_size)


def run_ps1_with_password(sess, script: str, password: str) -> winrm.Response:
    b64 = base64.b64encode(password.encode("utf-8")).decode("ascii")
    ps = (
        f"$env:VM_ADMIN_PASSWORD = [Text.Encoding]::UTF8.GetString("
        f"[Convert]::FromBase64String('{b64}'));"
        f"powershell -ExecutionPolicy Bypass -File '{script}' "
        f"-AdminPassword $env:VM_ADMIN_PASSWORD"
    )
    return sess.run_ps(ps)
