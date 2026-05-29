"""One-shot: install local public key on CHI404. Password from env CHI404_ROOT_PASSWORD."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import paramiko

HOST = "64.44.98.219"
USER = "root"
KEY_PUB = Path.home() / ".ssh" / "hft3_chi404.pub"


def main() -> int:
    password = os.environ.get("CHI404_ROOT_PASSWORD")
    if not password:
        print("Set CHI404_ROOT_PASSWORD env var (do not commit).", file=sys.stderr)
        return 1
    if not KEY_PUB.exists():
        print(f"Missing {KEY_PUB}", file=sys.stderr)
        return 1
    pub = KEY_PUB.read_text(encoding="utf-8").strip()

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(
        HOST, username=USER, password=password, timeout=20, allow_agent=False, look_for_keys=False
    )
    sftp = client.open_sftp()
    try:
        client.exec_command("mkdir -p ~/.ssh && chmod 700 ~/.ssh")[1].channel.recv_exit_status()
        auth_path = "/root/.ssh/authorized_keys"
        try:
            existing = sftp.open(auth_path).read().decode("utf-8", errors="replace")
        except OSError:
            existing = ""
        if pub not in existing:
            with sftp.open(auth_path, "a") as f:
                f.write(pub + "\n")
        sftp.chmod(auth_path, 0o600)
    finally:
        sftp.close()
        client.close()
    print("SSH public key installed on", HOST)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
