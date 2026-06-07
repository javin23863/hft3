#!/usr/bin/env python3
"""Priority-lane coverage: CME MBO (7 symbols) + VIX.OPT per macro event window."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))
for sub in ("packages", "apps"):
    p = str(_REPO / sub)
    if p not in sys.path:
        sys.path.insert(0, p)

from hft3_bootstrap import setup_repo_paths

setup_repo_paths()

OUT_PATH = _REPO / "runtime" / "data_audits" / "priority_lane_coverage.json"


def main() -> int:
    from data_system.src.event_data_resolver import build_priority_lane_coverage

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=_REPO)
    args = parser.parse_args()

    report = build_priority_lane_coverage(args.repo_root.resolve())
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(report, indent=2), encoding="utf-8")

    mbo = report["mbo"]
    vix = report["vix"]
    print(f"Windows: {report['window_count']}")
    print(f"MBO NPZ: {mbo['complete']}/{mbo['total_slots']} ({mbo['complete_pct']}%)")
    print(f"MBO status: {mbo['status_counts']}")
    print(
        f"VIX sensor: {vix['complete']}/{vix['eligible_post_cutoff']} eligible "
        f"({vix['complete_pct_eligible']}%), pre-cutoff skipped={vix['skipped_pre_cmbp1']}"
    )
    print(f"VIX status: {vix['status_counts']}")
    print(f"Wrote {OUT_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
