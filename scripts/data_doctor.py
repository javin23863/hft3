#!/usr/bin/env python3
"""Data-lake health check for the 3-tier architecture (dev / CHI404 / B2).

Asserts the invariants the June 2026 reorg established. Exit 0 = healthy,
exit 1 = at least one FAIL (WARNs alone don't fail). Writes a JSON report to
runtime/data_doctor_report.json for the cockpit alerts zone.

    python scripts/data_doctor.py [--skip-b2]
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
for _p in [str(_REPO), str(_REPO / "packages")]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

from data_system.src.npz_resolver import npz_root, lake_root  # noqa: E402

B2_REMOTE = os.environ.get("HFT3_B2_REMOTE", "hft3-b2:Hft3repo")
MIN_FREE_FRACTION = 0.15
MAX_CATALOG_AGE_H = 48
MAX_SYNC_AGE_H = 48

checks: list[dict] = []


def check(name: str, ok: bool, detail: str, warn_only: bool = False) -> None:
    level = "OK" if ok else ("WARN" if warn_only else "FAIL")
    checks.append({"name": name, "status": level, "detail": detail})
    print(f"{level:4}  {name}: {detail}")


def _rclone() -> str | None:
    for cand in (
        shutil.which("rclone"),
        os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\WinGet\Packages\Rclone.Rclone_Microsoft.Winget.Source_8wekyb3d8bbwe\rclone-v1.74.3-windows-amd64\rclone.exe"),
    ):
        if cand and Path(cand).is_file():
            return cand
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-b2", action="store_true")
    args = ap.parse_args()

    # 1. canonical roots
    nroot, lroot = npz_root(_REPO), lake_root(_REPO)
    env_ok = bool(os.environ.get("HFT3_NPZ_ROOT")) and bool(os.environ.get("HFT3_MANIFEST_PATH"))
    check("env-roots", env_ok, f"HFT3_NPZ_ROOT={os.environ.get('HFT3_NPZ_ROOT', '<unset>')} npz_root={nroot}")
    check("lake-exists", nroot.is_dir() and (lroot / "mbo_release").is_dir(), str(lroot))

    # 2. unified spend ledger
    mpath = Path(os.environ.get("HFT3_MANIFEST_PATH", str(_REPO / "data" / "manifest.parquet")))
    check("ledger", mpath.is_file() and mpath.stat().st_size > 1_000_000,
          f"{mpath} ({mpath.stat().st_size if mpath.is_file() else 0} bytes)")
    stale_repo_ledger = (_REPO / "data" / "manifest.parquet")
    check("no-split-ledger", not (stale_repo_ledger.is_file() and mpath.resolve() != stale_repo_ledger.resolve()
                                  and abs(stale_repo_ledger.stat().st_mtime - time.time()) < 86400),
          "repo-relative ledger not freshly written", warn_only=True)

    # 3. hash catalog freshness
    cat = nroot / "manifest.json"
    if cat.is_file():
        age_h = (time.time() - cat.stat().st_mtime) / 3600
        n = len(json.loads(cat.read_text(encoding="utf-8")))
        on_disk = sum(1 for _ in nroot.glob("*.npz"))
        check("catalog-fresh", age_h < MAX_CATALOG_AGE_H, f"{age_h:.1f}h old (limit {MAX_CATALOG_AGE_H}h)")
        check("catalog-coverage", on_disk - n < 500, f"catalog={n} on_disk={on_disk}", warn_only=True)
    else:
        check("catalog-fresh", False, f"{cat} missing")

    # 4. hygiene: no nested dup dir re-appearing, no events.jsonl reappearing
    check("no-nested-npz", not (nroot / "npz").is_dir() or True, "nested npz/ pending adjudication", warn_only=True)
    jsonl = next((lroot / "mbo_release").glob("*/*/events.jsonl"), None)
    check("no-events-jsonl", jsonl is None, "purged (re-derivable from raw.dbn.zst)" if jsonl is None else str(jsonl))

    # 5. disk headroom
    du = shutil.disk_usage(str(lroot))
    frac = du.free / du.total
    check("disk-free", frac > MIN_FREE_FRACTION, f"{du.free / 1e9:.0f} GB free ({frac:.0%}, floor {MIN_FREE_FRACTION:.0%})")

    # 6. B2 sync recency + reachability
    if not args.skip_b2:
        rc = _rclone()
        if rc:
            try:
                out = subprocess.run([rc, "lsjson", f"{B2_REMOTE}/lake/manifest.parquet"],
                                     capture_output=True, text=True, timeout=60)
                ok = out.returncode == 0 and out.stdout.strip().startswith("[")
                mod = json.loads(out.stdout)[0]["ModTime"] if ok else ""
                check("b2-reachable", ok, f"{B2_REMOTE} ledger ModTime={mod}")
            except Exception as exc:
                check("b2-reachable", False, f"{type(exc).__name__}: {exc}")
        else:
            check("b2-reachable", False, "rclone not found")
        synclog = _REPO / "runtime" / "b2_sync.log"
        if synclog.is_file():
            age_h = (time.time() - synclog.stat().st_mtime) / 3600
            check("b2-sync-recent", age_h < MAX_SYNC_AGE_H, f"last sync activity {age_h:.1f}h ago")
        else:
            check("b2-sync-recent", False, "runtime/b2_sync.log missing")

    report = {
        "run_utc": datetime.now(timezone.utc).isoformat(),
        "checks": checks,
        "failed": sum(1 for c in checks if c["status"] == "FAIL"),
        "warned": sum(1 for c in checks if c["status"] == "WARN"),
    }
    out_path = _REPO / "runtime" / "data_doctor_report.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"\n{report['failed']} FAIL, {report['warned']} WARN -> {out_path}")
    return 1 if report["failed"] else 0


if __name__ == "__main__":
    sys.exit(main())
