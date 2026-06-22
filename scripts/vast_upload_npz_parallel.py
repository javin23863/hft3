#!/usr/bin/env python3
"""Parallel NPZ upload workstation -> Vast Spain (large chunks, no recompress)."""
from __future__ import annotations

import argparse
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path


def scp_file(local: Path, remote: str, port: int, ssh_host: str) -> tuple[str, int]:
    cmd = [
        "scp",
        "-o",
        "ConnectTimeout=20",
        "-o",
        "StrictHostKeyChecking=accept-new",
        "-P",
        str(port),
        str(local),
        f"{ssh_host}:{remote}",
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    return local.name, proc.returncode


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--local-npz", default=r"C:\hft3-lake\npz")
    ap.add_argument("--ssh-host", default="root@ssh8.vast.ai")
    ap.add_argument("--ssh-port", type=int, default=22954)
    ap.add_argument("--remote-dir", default="/data/npz")
    ap.add_argument("--workers", type=int, default=12)
    ap.add_argument("--limit", type=int, default=0, help="0 = all files")
    args = ap.parse_args()

    root = Path(args.local_npz)
    files = sorted(root.glob("*.npz"))
    if args.limit > 0:
        files = files[: args.limit]
    if not files:
        print(f"No NPZ files under {root}", file=sys.stderr)
        return 1

    mkdir = [
        "ssh",
        "-o",
        "ConnectTimeout=20",
        "-p",
        str(args.ssh_port),
        args.ssh_host,
        f"mkdir -p {args.remote_dir}",
    ]
    subprocess.check_call(mkdir)

    ok = 0
    fail = 0
    total = len(files)
    print(f"Uploading {total} NPZ files with {args.workers} parallel scp streams...")
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futs = {
            pool.submit(scp_file, f, f"{args.remote_dir}/{f.name}", args.ssh_port, args.ssh_host): f
            for f in files
        }
        for i, fut in enumerate(as_completed(futs), 1):
            name, code = fut.result()
            if code == 0:
                ok += 1
            else:
                fail += 1
                print(f"FAIL {name} exit={code}", file=sys.stderr)
            if i % 500 == 0 or i == total:
                print(f"progress {i}/{total} ok={ok} fail={fail}")

    print(f"UPLOAD_DONE ok={ok} fail={fail} total={total}")
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
