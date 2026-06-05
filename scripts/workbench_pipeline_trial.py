#!/usr/bin/env python3
"""~1–2 min end-to-end workbench pipeline smoke (single event + trial campaign)."""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
os.environ.setdefault("HFT3_CPP_STACK_VERIFY", "off")
LATENCY = REPO / "runtime" / "latency_reports" / "latency_summary.json"


def _fail(msg: str) -> int:
    print(f"PIPELINE TRIAL FAIL: {msg}", file=sys.stderr)
    return 1


def _available_catalog_event():
    from workbench.src.data.event_catalog import list_campaign_events, load_periods

    candidates = []
    for period in load_periods(REPO):
        for event in list_campaign_events("SPREAD_BLOWOUT_RECOMPRESSION", period, "MES.v.0", REPO):
            if event.npz_present and event.npz_symbol_used == "MES.v.0" and event.npz_path.is_file():
                candidates.append(event)
    if not candidates:
        raise FileNotFoundError("no MES catalog MBO NPZ present locally")
    return min(candidates, key=lambda event: event.npz_path.stat().st_size)


def trial_single_event() -> tuple[bool, str]:
    from workbench.src.run.engine import WorkbenchEngine

    event = _available_catalog_event()
    t0 = time.time()
    engine = WorkbenchEngine(REPO)
    out = engine.run(
        "SPREAD_BLOWOUT_RECOMPRESSION",
        event.event_id,
        symbol="MES.v.0",
        npz_path=event.npz_path,
        chi404_summary=LATENCY if LATENCY.is_file() else None,
        seed=42,
        skip_history_gate=True,
        fast_sweep=True,
    )
    elapsed = time.time() - t0
    art = Path(out["artifact_dir"])
    for name in ("report.md", "diagnostics.json", "manifest.json"):
        if not (art / name).is_file():
            return False, f"Missing artifact {name} under {art}"
    rep = out.get("report", {})
    return True, (
        f"single-event OK in {elapsed:.1f}s — event={event.event_id} "
        f"trades={rep.get('num_trades')} pnl={rep.get('net_pnl')}"
    )


def trial_campaign() -> tuple[bool, str]:
    from workbench.src.run.campaign_runner import run_campaign

    t0 = time.time()
    result = run_campaign(
        REPO,
        "HYP_5",
        "MES.v.0",
        chi404_summary=LATENCY if LATENCY.is_file() else None,
        trial_mode=True,
        campaign_id=f"HYP_5_MES_v_0_TRIAL_{int(t0)}",
    )
    elapsed = time.time() - t0
    summary_path = Path(result.artifact_dir) / "summary.json"
    if not summary_path.is_file():
        return False, "Campaign summary.json missing"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    events_ran = int(summary.get("events_ran", 0))
    if events_ran < 1:
        return False, f"No events ran (status={result.status})"
    return True, (
        f"trial-campaign OK in {elapsed:.1f}s — status={result.status} events_ran={events_ran} "
        f"wfc={summary.get('wfc_status')}"
    )


def main() -> int:
    sys.path.insert(0, str(REPO))
    print("Phase 1/2: single-event backtest (catalog MBO NPZ)...", flush=True)
    ok1, msg1 = trial_single_event()
    print(msg1, flush=True)
    if not ok1:
        return _fail(msg1)

    print("Phase 2/2: trial campaign orchestrator (skips WFC, partial NPZ)...", flush=True)
    ok2, msg2 = trial_campaign()
    print(msg2, flush=True)
    if not ok2:
        return _fail(msg2)

    print("PIPELINE TRIAL OK — engine + trial campaign verified", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
