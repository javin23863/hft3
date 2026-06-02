#!/usr/bin/env python3
"""Manifest-backed audit: macro imbalance + equities decadal lane (MBO, daily, normalized, options)."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO))
from hft3_bootstrap import setup_repo_paths

setup_repo_paths()

_DECADAL_CFG = _REPO / "packages/equities_lane/config/decadal_runners.yaml"
_MANIFEST = _REPO / "data/equities/manifest/decadal_pull.json"


def _imbalance_macro_gaps() -> dict[str, Any]:
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
        _, ok, _ = resolve_npz_for_event(_REPO, eid, sym, parsed)
        if not ok:
            mbo_missing.append(eid)
        fn = f"{sym}_{eid}_mbp-10.dbn.zst"
        if fn not in have_mbp:
            mbp_missing.append(eid)
    return {
        "macro_events": len(ev),
        "mbo_npz_missing": mbo_missing,
        "mbp10_missing": mbp_missing,
        "mbp10_have": len(have_mbp),
    }


def _manifest_by_id() -> dict[str, dict[str, Any]]:
    if not _MANIFEST.is_file():
        return {}
    data = json.loads(_MANIFEST.read_text(encoding="utf-8"))
    return {s["session_id"]: s for s in data.get("sessions", []) if s.get("session_id")}


def _equities_gaps() -> dict[str, Any]:
    import yaml

    decadal = yaml.safe_load(_DECADAL_CFG.read_text(encoding="utf-8"))
    paths = decadal.get("paths", {})
    raw_root = _REPO / paths.get("raw_root", "data/equities/raw")
    norm_root = _REPO / paths.get("normalized_root", "data/equities/normalized")
    daily_root = _REPO / paths.get("daily_root", "data/equities/daily")
    opts_norm = _REPO / "data/options/equity_chains/normalized"

    manifest = _manifest_by_id()
    eq_mbo, eq_auc, eq_norm, eq_daily = [], [], [], []
    options_missing, options_failed = [], []

    for s in decadal.get("sessions") or []:
        sid = s["id"]
        sym, dt = s["symbol"], s["date"]
        if s.get("skip_pull"):
            continue

        raw = raw_root / f"{sym}_{dt}_mbo.dbn.zst"
        if not raw.is_file():
            eq_mbo.append(f"{sym}:{dt}")

        eid = f"{sym}_{dt.replace('-', '_')}"
        auc = norm_root / f"{sym}_{eid}_auction.ndjson"
        if not auc.is_file():
            eq_auc.append(f"{sym}:{dt}")

        norm = norm_root / f"{sym}_{dt}.ndjson"
        if not norm.is_file():
            eq_norm.append(f"{sym}:{dt}")

        daily = daily_root / f"{sym}.parquet"
        if not daily.is_file() or daily.stat().st_size == 0:
            eq_daily.append(f"{sym}:{dt}")

        defaults_opts = (decadal.get("defaults") or {}).get("options", {})
        sess_opts = s.get("options") or {}
        opts_enabled = sess_opts.get("enabled", defaults_opts.get("enabled", False))
        if not opts_enabled:
            continue

        mrow = manifest.get(sid, {})
        opt = mrow.get("options") or {}
        err = opt.get("pull_error")
        norm_path = opt.get("normalized_path")
        if norm_path:
            np = Path(norm_path)
            if not np.is_file():
                np = opts_norm / f"{sid}.ndjson"
        else:
            np = opts_norm / f"{sid}.ndjson"

        if err:
            options_failed.append({"session_id": sid, "error": str(err)[:120]})
        elif not np.is_file() or np.stat().st_size == 0:
            options_missing.append(sid)

    return {
        "equities_mbo_missing": eq_mbo,
        "equities_auction_missing": eq_auc,
        "equities_normalized_missing": eq_norm,
        "equities_daily_missing": eq_daily,
        "options_missing": options_missing,
        "options_failed": options_failed,
    }


def audit_report() -> dict[str, Any]:
    macro = _imbalance_macro_gaps()
    eq = _equities_gaps()
    report = {**macro, **eq}
    # options_failed = Databento symbology limits; does not block "ready" if file on disk
    report["ready"] = (
        not macro["mbo_npz_missing"]
        and not macro["mbp10_missing"]
        and not eq["equities_mbo_missing"]
        and not eq["equities_auction_missing"]
        and not eq["equities_normalized_missing"]
        and not eq["equities_daily_missing"]
        and not eq["options_missing"]
    )
    report["options_failed_count"] = len(eq["options_failed"])
    return report


def has_imbalance_gaps(report: dict[str, Any]) -> bool:
    return bool(
        report.get("mbo_npz_missing")
        or report.get("mbp10_missing")
        or report.get("equities_mbo_missing")
        or report.get("equities_auction_missing")
    )


def main() -> int:
    report = audit_report()
    out = _REPO / "runtime/data_audits/research_data_gaps.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 0 if report.get("ready") else 1


if __name__ == "__main__":
    raise SystemExit(main())
