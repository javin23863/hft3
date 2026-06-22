#!/usr/bin/env python3
"""Upload NPZ lake in large tar chunks (fastest without rsync on Windows)."""
from __future__ import annotations

import argparse
import math
import subprocess
import sys
import tempfile
from pathlib import Path


def run(cmd: list[str], *, stdin=None) -> int:
    print("+", " ".join(cmd[:8]), "...", flush=True)
    return subprocess.run(cmd, stdin=stdin).returncode


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--local-npz", default=r"C:\hft3-lake\npz")
    ap.add_argument("--ssh-host", default="root@ssh8.vast.ai")
    ap.add_argument("--ssh-port", type=int, default=22954)
    ap.add_argument("--remote-dir", default="/data/npz")
    ap.add_argument("--chunks", type=int, default=8)
    args = ap.parse_args()

    root = Path(args.local_npz)
    files = sorted(root.glob("*.npz"))
    if not files:
        print(f"No NPZ under {root}", file=sys.stderr)
        return 1

    ssh_base = [
        "ssh",
        "-o",
        "ConnectTimeout=20",
        "-o",
        "StrictHostKeyChecking=accept-new",
        "-p",
        str(args.ssh_port),
        args.ssh_host,
    ]
    subprocess.check_call(ssh_base + [f"mkdir -p {args.remote_dir}"])

    n = len(files)
    size = math.ceil(n / args.chunks)
    print(f"Uploading {n} NPZ files in {args.chunks} tar streams (~{size} files each)")
    for i in range(args.chunks):
        chunk = files[i * size : (i + 1) * size]
        if not chunk:
            continue
        with tempfile.NamedTemporaryFile("w", suffix=".lst", delete=False) as tf:
            for f in chunk:
                tf.write(f.name + "\n")
            list_path = tf.name
        print(f"=== chunk {i + 1}/{args.chunks} files={len(chunk)} ===")
        tar_cmd = ["tar", "-c", "-T", list_path]
        remote_cmd = f"tar -x -C {args.remote_dir} -f -"
        with open(list_path, "rb") as _:
            pass
        tar = subprocess.Popen(tar_cmd, cwd=str(root), stdout=subprocess.PIPE)
        assert tar.stdout is not None
        rc = subprocess.run(ssh_base + [remote_cmd], stdin=tar.stdout).returncode
        tar.wait()
        Path(list_path).unlink(missing_ok=True)
        if rc != 0 or tar.returncode != 0:
            print(f"chunk {i + 1} failed tar={tar.returncode} ssh={rc}", file=sys.stderr)
            return 1
    verify = subprocess.run(
        ssh_base + [f"find {args.remote_dir} -maxdepth 1 -name '*.npz' | wc -l"],
        capture_output=True,
        text=True,
    )
    print("remote_npz_count=", verify.stdout.strip())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
