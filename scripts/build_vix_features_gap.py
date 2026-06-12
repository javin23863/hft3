#!/usr/bin/env python3
"""Build VIX.OPT feature files for every quotes NPZ missing a feature file.

Stage 3 of the VIX.OPT data-gap closure. For each
``GH/data/npz/VIX.OPT_<event_id>_quotes.npz`` lacking
``GH/data/features/VIX.OPT/VIX.OPT_<event_id>_features_v1.npz``,
calls ``build_vix_feature_file``. Classifies: built / vix_unavailable
(VixDataUnavailable — valid skip) / error. Embargo: event dates >=
2026-01-01 are skipped and reported.

Usage:
    python scripts/build_vix_features_gap.py [--limit N] [--workers W]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from datetime import date, datetime, timezone
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]  # C:\Users\MSI\repos\hft3
for p in (
    _REPO,
    _REPO / "packages",
    _REPO / "packages" / "features_engine" / "src" / "features",
):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from data_system.src.npz_resolver import npz_root  # noqa: E402
from data_system.src.feature_store import feature_store_root  # noqa: E402

NPZ_DIR = npz_root(_REPO)
SENSORS_DIR = NPZ_DIR.parent / "sensors"
OUT_DIR = feature_store_root(_REPO) / "VIX.OPT"
REPORT_PATH = _REPO / "runtime" / "data_downloads" / "vix_opt_features_build_report.json"

EMBARGO_START = date(2026, 1, 1)
_DATE_RE = re.compile(r"(\d{4})_(\d{2})_(\d{2})")


def _event_date(event_id: str) -> date | None:
    m = _DATE_RE.search(event_id)
    if not m:
        return None
    try:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


def _build_one(args: tuple[str, str]) -> tuple[str, str, str]:
    """Worker: returns (event_id, status, message)."""
    quotes_path_s, event_id = args
    from vix_features import VixDataUnavailable, build_vix_feature_file

    quotes_npz = Path(quotes_path_s)
    sensors = SENSORS_DIR / f"{event_id}_sensors.parquet"
    sensors_arg = sensors if sensors.is_file() else None
    out_path = OUT_DIR / f"VIX.OPT_{event_id}_features_v1.npz"
    try:
        build_vix_feature_file(quotes_npz, sensors_arg, out_path, vix_extra_latency_ms=0.0)
        return (event_id, "built", "")
    except VixDataUnavailable as exc:
        return (event_id, "vix_unavailable", str(exc))
    except Exception as exc:  # noqa: BLE001
        return (event_id, "error", f"{type(exc).__name__}: {exc}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--workers", type=int, default=1, help="Process workers (capped at 4)")
    args = parser.parse_args()
    workers = max(1, min(4, args.workers))

    from vix_features import derive_event_id  # validate import before fan-out

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    quote_files = sorted(NPZ_DIR.glob("VIX.OPT_*_quotes.npz"))
    report = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "quotes_npz_total": len(quote_files),
        "skipped_existing": 0,
        "embargoed_2026": [],
        "built": [],
        "vix_unavailable": [],
        "errors": [],
    }

    todo: list[tuple[str, str]] = []
    for qf in quote_files:
        event_id = derive_event_id(qf.name)
        d = _event_date(event_id)
        if d is not None and d >= EMBARGO_START:
            report["embargoed_2026"].append(event_id)
            continue
        out_path = OUT_DIR / f"VIX.OPT_{event_id}_features_v1.npz"
        if out_path.is_file():
            report["skipped_existing"] += 1
            continue
        todo.append((str(qf), event_id))

    if args.limit:
        todo = todo[: args.limit]

    print(
        f"quotes_npz={len(quote_files)} existing_features={report['skipped_existing']} "
        f"embargoed={len(report['embargoed_2026'])} todo={len(todo)} workers={workers}",
        flush=True,
    )

    t0 = time.monotonic()

    def _record(res: tuple[str, str, str], i: int) -> None:
        event_id, status, msg = res
        if status == "built":
            report["built"].append(event_id)
        elif status == "vix_unavailable":
            report["vix_unavailable"].append({"event_id": event_id, "reason": msg})
        else:
            report["errors"].append({"event_id": event_id, "error": msg})
        if i % 25 == 0 or status == "error":
            elapsed = time.monotonic() - t0
            print(f"[{i}/{len(todo)}] {event_id} -> {status} {msg[:120]} ({elapsed:.0f}s)", flush=True)

    if workers == 1:
        for i, item in enumerate(todo, 1):
            _record(_build_one(item), i)
    else:
        from concurrent.futures import ProcessPoolExecutor, as_completed

        with ProcessPoolExecutor(max_workers=workers) as pool:
            futures = [pool.submit(_build_one, item) for item in todo]
            for i, fut in enumerate(as_completed(futures), 1):
                _record(fut.result(), i)

    elapsed = time.monotonic() - t0
    summary = {
        "built": len(report["built"]),
        "vix_unavailable": len(report["vix_unavailable"]),
        "errors": len(report["errors"]),
        "skipped_existing": report["skipped_existing"],
        "embargoed_2026": len(report["embargoed_2026"]),
        "elapsed_s": round(elapsed, 1),
        "per_file_s": round(elapsed / max(len(todo), 1), 2),
    }
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(summary), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
