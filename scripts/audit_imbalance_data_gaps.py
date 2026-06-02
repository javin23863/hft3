#!/usr/bin/env python3
"""Report missing imbalance-lane data files."""
from __future__ import annotations

import json
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO))
from hft3_bootstrap import setup_repo_paths

setup_repo_paths()


def main() -> int:
    from data_system.src.events_parser import load_and_parse_events
    from data_system.src.npz_resolver import resolve_npz_for_event

    ev = load_and_parse_events(str(_REPO / "packages/data_system/config/events.csv"))
    mbo_missing, mbp_missing = [], []
    mbp_dir = _REPO / "data/replay/mbp10"
    have_mbp = {p.name for p in mbp_dir.glob("*.dbn.zst")} if mbp_dir.is_dir() else set()
    for _, row in ev.iterrows():
        eid = row["event_id"]
        parsed = tuple(str(s) for s in row["parsed_symbols"])
        sym = parsed[0]
        p, ok, _ = resolve_npz_for_event(_REPO, eid, sym, parsed)
        if not ok:
            mbo_missing.append(eid)
        fn = f"{sym}_{eid}_mbp-10.dbn.zst"
        if fn not in have_mbp:
            mbp_missing.append(eid)

    import yaml

    decadal = yaml.safe_load(
        (_REPO / "packages/equities_lane/config/decadal_runners.yaml").read_text(encoding="utf-8")
    )
    eq_mbo, eq_auc = [], []
    for s in decadal.get("sessions") or []:
        if s.get("skip_pull"):
            continue
        sym, dt = s["symbol"], s["date"]
        raw = _REPO / "data/equities/raw" / f"{sym}_{dt}_mbo.dbn.zst"
        if not raw.is_file():
            eq_mbo.append(f"{sym}:{dt}")
        eid = f"{sym}_{dt.replace('-', '_')}"
        auc = _REPO / "data/equities/normalized" / f"{sym}_{eid}_auction.ndjson"
        if not auc.is_file():
            eq_auc.append(f"{sym}:{dt}")

    report = {
        "macro_events": len(ev),
        "mbo_npz_missing": mbo_missing,
        "mbp10_missing": mbp_missing,
        "mbp10_have": len(have_mbp),
        "equities_mbo_missing": eq_mbo,
        "equities_auction_missing": eq_auc,
    }
    out = _REPO / "runtime/data_audits/imbalance_data_gaps.json"
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
