#!/usr/bin/env python3

"""Merge chi404 MBO release lane + NPZ into local repo and paid data root."""



from __future__ import annotations



import argparse

import json

import shutil

import subprocess

import sys

import time

from pathlib import Path



_REPO = Path(__file__).resolve().parents[1]

if str(_REPO) not in sys.path:

    sys.path.insert(0, str(_REPO))



from hft3_bootstrap import setup_repo_paths



setup_repo_paths()



REMOTE_HOST = "chi404"

REMOTE_DATA = "/root/hft3/repo/data"

LOCAL_DATA = _REPO / "data"

MAX_RETRIES = 3

RETRY_BACKOFF_S = 2.0





def _paid_npz_dir() -> Path | None:

    from data_system.src.data_roots import paid_data_root



    paid = paid_data_root(_REPO)

    npz = paid / "npz"

    if npz.resolve() == (_REPO / "data" / "npz").resolve():

        return None

    return npz





def _ssh_run(remote_cmd: str, *, timeout_s: int = 120) -> subprocess.CompletedProcess[str]:

    return subprocess.run(

        ["ssh", REMOTE_HOST, remote_cmd],

        capture_output=True,

        text=True,

        timeout=timeout_s,

    )





def _remote_npz_inventory() -> dict[str, int]:

    """Map remote NPZ basename -> byte size."""

    cmd = f"find {REMOTE_DATA}/npz -maxdepth 1 -name '*.npz' -printf '%f %s\\n' 2>/dev/null"

    proc = _ssh_run(cmd, timeout_s=180)

    if proc.returncode != 0:

        raise RuntimeError(f"remote inventory failed: {(proc.stderr or proc.stdout)[-300:]}")

    out: dict[str, int] = {}

    for line in (proc.stdout or "").splitlines():

        parts = line.strip().split()

        if len(parts) == 2:

            out[parts[0]] = int(parts[1])

    return out





def _local_npz_inventory(local_dir: Path) -> dict[str, int]:

    out: dict[str, int] = {}

    if not local_dir.is_dir():

        return out

    for path in local_dir.glob("*.npz"):

        try:

            if path.is_file():

                out[path.name] = path.stat().st_size

        except OSError:

            continue

    return out





def _scp_one(remote_rel: str, local_path: Path) -> subprocess.CompletedProcess[str]:

    remote = f"{REMOTE_HOST}:{REMOTE_DATA}/{remote_rel}"

    local_path.parent.mkdir(parents=True, exist_ok=True)

    return subprocess.run(

        ["scp", remote, str(local_path)],

        capture_output=True,

        text=True,

        timeout=600,

    )





def _sync_npz_resilient(local_dir: Path, *, dry_run: bool) -> dict:

    local_dir.mkdir(parents=True, exist_ok=True)

    remote = _remote_npz_inventory()

    local = _local_npz_inventory(local_dir)



    to_fetch: list[tuple[str, int, str]] = []

    skipped = 0

    for name, rsize in sorted(remote.items()):

        lsize = local.get(name)

        if lsize is not None and lsize >= rsize:

            skipped += 1

            continue

        reason = "missing" if lsize is None else "remote_newer"

        to_fetch.append((name, rsize, reason))



    only_local = sorted(set(local) - set(remote))



    result: dict = {

        "remote_count": len(remote),

        "local_count": len(local),

        "only_local_count": len(only_local),

        "only_local_sample": only_local[:20],

        "skipped_up_to_date": skipped,

        "to_fetch_count": len(to_fetch),

        "to_fetch_sample": [{"name": n, "remote_bytes": s, "reason": r} for n, s, r in to_fetch[:20]],

        "copied": 0,

        "failed": [],

        "dry_run": dry_run,

    }



    if dry_run or not to_fetch:

        return result



    copied = 0

    failed: list[dict] = []

    for i, (name, rsize, reason) in enumerate(to_fetch, 1):

        dest = local_dir / name

        last_err = ""

        for attempt in range(1, MAX_RETRIES + 1):

            proc = _scp_one(f"npz/{name}", dest)

            if proc.returncode == 0 and dest.is_file() and dest.stat().st_size >= rsize:

                copied += 1

                if copied % 50 == 0:

                    print(f"  copied {copied}/{len(to_fetch)}...", flush=True)

                break

            last_err = (proc.stderr or proc.stdout or "")[-300:]

            if attempt < MAX_RETRIES:

                time.sleep(RETRY_BACKOFF_S * attempt)

        else:

            failed.append({"name": name, "reason": reason, "error": last_err})



    result["copied"] = copied

    result["failed"] = failed

    result["failed_count"] = len(failed)

    return result





def _sync_tree_tar(remote_sub: str, local_dir: Path, *, dry_run: bool) -> dict:

    """Fallback: stream remote subtree via ssh tar (single retry loop)."""

    local_dir.mkdir(parents=True, exist_ok=True)

    remote_cmd = f"tar cf - -C {REMOTE_DATA}/{remote_sub} ."

    if dry_run:

        return {

            "remote": f"{REMOTE_HOST}:{REMOTE_DATA}/{remote_sub}",

            "local": str(local_dir),

            "method": "ssh_tar",

            "dry_run": True,

        }



    last_err = ""

    for attempt in range(1, MAX_RETRIES + 1):

        ssh = subprocess.Popen(

            ["ssh", REMOTE_HOST, remote_cmd],

            stdout=subprocess.PIPE,

            stderr=subprocess.PIPE,

        )

        assert ssh.stdout is not None

        tar = subprocess.run(

            ["tar", "xf", "-", "-C", str(local_dir)],

            stdin=ssh.stdout,

            capture_output=True,

            text=True,

        )

        ssh.wait(timeout=3600)

        stderr = (ssh.stderr.read() if ssh.stderr else b"").decode(errors="replace")

        if ssh.returncode == 0 and tar.returncode == 0:

            return {

                "remote": f"{REMOTE_HOST}:{REMOTE_DATA}/{remote_sub}",

                "local": str(local_dir),

                "method": "ssh_tar",

                "returncode": 0,

                "attempts": attempt,

            }

        last_err = (stderr + (tar.stderr or ""))[-500:]

        if attempt < MAX_RETRIES:

            time.sleep(RETRY_BACKOFF_S * attempt)



    return {

        "remote": f"{REMOTE_HOST}:{REMOTE_DATA}/{remote_sub}",

        "local": str(local_dir),

        "method": "ssh_tar",

        "returncode": 1,

        "stderr": last_err,

        "attempts": MAX_RETRIES,

    }





def _copy_npz_to_paid(paid_npz: Path) -> int:

    paid_npz.mkdir(parents=True, exist_ok=True)

    copied = 0

    src_dir = _REPO / "data" / "npz"

    for src in src_dir.glob("*.npz"):

        try:

            dest = paid_npz / src.name

            if dest.is_file() and dest.stat().st_size >= src.stat().st_size:

                continue

            shutil.copy2(src, dest)

            copied += 1

        except OSError:

            continue

    return copied





def main() -> int:

    parser = argparse.ArgumentParser(description=__doc__)

    parser.add_argument("--dry-run", action="store_true")

    parser.add_argument("--npz-only", action="store_true", help="Sync NPZ only (~435MB on chi404)")

    parser.add_argument("--skip-paid-sync", action="store_true")

    parser.add_argument(

        "--use-tar",

        action="store_true",

        help="Use ssh tar for mbo_release (default for npz is per-file rsync-style scp)",

    )

    args = parser.parse_args()



    report: dict = {"steps": [], "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}



    if args.npz_only or args.dry_run:

        npz_step = _sync_npz_resilient(LOCAL_DATA / "npz", dry_run=args.dry_run)

        report["steps"].append({"name": "sync_npz", **npz_step})

    elif args.use_tar:

        npz_step = _sync_tree_tar("npz", LOCAL_DATA / "npz", dry_run=args.dry_run)

        report["steps"].append({"name": "tar_npz", **npz_step})

    else:

        npz_step = _sync_npz_resilient(LOCAL_DATA / "npz", dry_run=args.dry_run)

        report["steps"].append({"name": "sync_npz", **npz_step})



    if not args.npz_only and not args.dry_run:

        mbo_step = _sync_tree_tar("mbo_release", LOCAL_DATA / "mbo_release", dry_run=False)

        report["steps"].append({"name": "tar_mbo_release", **mbo_step})



    if not args.dry_run and not args.skip_paid_sync:

        paid_npz = _paid_npz_dir()

        if paid_npz:

            report["paid_npz_copied"] = _copy_npz_to_paid(paid_npz)



    out = _REPO / "runtime" / "data_downloads" / "chi404_merge_report.json"

    out.parent.mkdir(parents=True, exist_ok=True)

    out.write_text(json.dumps(report, indent=2), encoding="utf-8")

    print(json.dumps(report, indent=2))



    failed = False

    for step in report["steps"]:

        if step.get("returncode", 0) not in (0, None):

            failed = True

        if step.get("failed_count", 0) > 0:

            failed = True

    return 1 if failed else 0





if __name__ == "__main__":

    raise SystemExit(main())

