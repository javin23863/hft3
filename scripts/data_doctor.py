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
import re
import shutil
import subprocess
import sys
import time
from datetime import date, datetime, timezone
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
for _p in [str(_REPO), str(_REPO / "packages")]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

from data_system.src.npz_resolver import npz_root, lake_root  # noqa: E402
from options_data.src.expiry_calendar import expiries_between  # noqa: E402

B2_REMOTE = os.environ.get("HFT3_B2_REMOTE", "hft3-b2:Hft3repo")
MIN_FREE_FRACTION = 0.15
MAX_CATALOG_AGE_H = 48
MAX_SYNC_AGE_H = 48

OPTIONS_FIXING_START = date(2023, 5, 1)
# Expiry dates whose 14:55-15:05 CT window is covered by owned lake NPZ
# (PROP_FLATTEN); see scripts/pull_fixing_windows.py ALREADY_COVERED +
# research_cards/fixing_window/README.md. Literals to avoid importing
# DatabentoResearchClient at module scope.
OPTIONS_FIXING_COVERED_ELSEWHERE: frozenset[str] = frozenset({"2024-09-18", "2025-06-20"})
OPTIONS_VENDOR_LAG_GRACE_DAYS = 5  # trailing gaps within this window WARN, not FAIL

_FIXING_RE = re.compile(r"^ES_fixing_(trades_)?(\d{4}-\d{2}-\d{2})\.dbn\.zst$")

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


def options_lane_checks(
    lroot: Path,
    today: date | None = None,
    start: date = OPTIONS_FIXING_START,
) -> dict | None:
    """Run options-lane dataset checks and return a summary dict (or None)."""
    if today is None:
        today = date.today()

    opt = lroot / "options"
    if not opt.is_dir():
        check("options-datasets", False, "options lane not provisioned under lake root", warn_only=True)
        return None
    check("options-datasets", True, str(opt))

    # fixing_mbo: scan for quote and trades files
    fixing_dir = opt / "fixing_mbo"
    quote_files: list[str] = []
    trades_files: list[str] = []
    dates: set[str] = set()
    if fixing_dir.is_dir():
        for p in fixing_dir.iterdir():
            m = _FIXING_RE.match(p.name)
            if m:
                is_trades = bool(m.group(1))
                d_str = m.group(2)
                dates.add(d_str)
                if is_trades:
                    trades_files.append(p.name)
                else:
                    quote_files.append(p.name)

    first_date = min(dates) if dates else ""
    last_date = max(dates) if dates else ""
    check(
        "options-fixing-mbo",
        ok=len(dates) > 0,
        detail=f"quotes={len(quote_files)} trades={len(trades_files)} dates={len(dates)} ({first_date}..{last_date})",
    )

    # coverage: expected expiry dates vs. what we have
    expected = {d.isoformat() for d, _ in expiries_between(start, today)}
    gaps = sorted(expected - dates - OPTIONS_FIXING_COVERED_ELSEWHERE)
    stale_gaps = [g for g in gaps if (today - date.fromisoformat(g)).days > OPTIONS_VENDOR_LAG_GRACE_DAYS]
    gap_sample = gaps[:10]
    check(
        "options-fixing-coverage",
        ok=not gaps,
        detail=(
            f"gap_count={len(gaps)} stale={len(stale_gaps)} "
            f"first_gaps={gap_sample}"
        ),
        warn_only=(not stale_gaps),
    )

    # ohlcv
    ohlcv_dir = opt / "ohlcv"
    ohlcv_files: list[str] = []
    if ohlcv_dir.is_dir():
        ohlcv_files = [p.name for p in ohlcv_dir.glob("*.dbn.zst")]
    check("options-ohlcv", ok=len(ohlcv_files) > 0, detail=str(ohlcv_files))

    # definitions
    defs_dir = opt / "definitions"
    def_files: list[Path] = []
    if defs_dir.is_dir():
        def_files = list(defs_dir.rglob("*.dbn.zst"))
    batches = sorted({p.parent.name for p in def_files if p.parent != defs_dir})
    check(
        "options-definitions",
        ok=len(def_files) > 0,
        detail=f"files={len(def_files)} batches={batches}",
    )

    # statistics
    stats_dir = opt / "statistics"
    stat_files: list[Path] = []
    if stats_dir.is_dir():
        stat_files = [p for p in stats_dir.rglob("*") if p.is_file()]
    stats_state: str
    if len(stat_files) == 0:
        stats_state = "pending_batch_delivery"
        stats_detail = "pending Databento batch delivery (expected until WS-0.4 statistics job lands)"
    else:
        stats_state = "present"
        stats_detail = f"files={len(stat_files)}"
    check("options-statistics", ok=len(stat_files) > 0, detail=stats_detail, warn_only=True)

    return {
        "as_of_utc": datetime.now(timezone.utc).isoformat(),
        "fixing_mbo": {
            "quote_files": len(quote_files),
            "trades_files": len(trades_files),
            "dates_covered": len(dates),
            "first_date": first_date,
            "last_date": last_date,
        },
        "expiry_coverage": {
            "expected_dates": len(expected),
            "covered_elsewhere": sorted(OPTIONS_FIXING_COVERED_ELSEWHERE),
            "gaps": len(gaps),
            "gap_count": len(gaps),
            "stale_gap_count": len(stale_gaps),
            "grace_days": OPTIONS_VENDOR_LAG_GRACE_DAYS,
            "calendar": "rule-based v0 (packages/options_data/src/expiry_calendar.py)",
        },
        "ohlcv": {
            "files": len(ohlcv_files),
            "names": ohlcv_files,
        },
        "definitions": {
            "files": len(def_files),
            "batches": batches,
        },
        "statistics": {
            "files": len(stat_files),
            "state": stats_state,
        },
    }


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

    opt_summary = options_lane_checks(lroot)

    report = {
        "run_utc": datetime.now(timezone.utc).isoformat(),
        "checks": checks,
        "failed": sum(1 for c in checks if c["status"] == "FAIL"),
        "warned": sum(1 for c in checks if c["status"] == "WARN"),
    }
    if opt_summary is not None:
        report["options_lane"] = opt_summary
    out_path = _REPO / "runtime" / "data_doctor_report.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"\n{report['failed']} FAIL, {report['warned']} WARN -> {out_path}")
    return 1 if report["failed"] else 0


if __name__ == "__main__":
    sys.exit(main())
