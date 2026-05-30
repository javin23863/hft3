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
NPZ = REPO / "data" / "npz" / "MES.v.0_CPI_2024_09_11_TIGHT_mbo.npz"
EVENT = "CPI_2024_09_11_TIGHT"
LATENCY = REPO / "runtime" / "latency_reports" / "latency_summary.json"


def _fail(msg: str) -> int:
    print(f"PIPELINE TRIAL FAIL: {msg}", file=sys.stderr)
    return 1


def trial_single_event() -> tuple[bool, str]:
    from workbench.src.run.engine import WorkbenchEngine

    if not NPZ.is_file():
        return False, f"Missing NPZ: {NPZ}"

    t0 = time.time()
    engine = WorkbenchEngine(REPO)
    out = engine.run(
        "HYP_5",
        EVENT,
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
    return True, f"single-event OK in {elapsed:.1f}s — trades={rep.get('num_trades')} pnl={rep.get('net_pnl')}"


def trial_campaign() -> tuple[bool, str]:
    from workbench.src.run.campaign_runner import run_campaign

    if not NPZ.is_file():
        return False, f"Missing NPZ: {NPZ}"

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
    diag = (
        Path(result.artifact_dir)
        / "periods"
        / "Holdout"
        / "events"
        / EVENT
        / "diagnostics.json"
    )
    if not diag.is_file():
        return False, f"Missing event diagnostics: {diag}"
    return True, (
        f"trial-campaign OK in {elapsed:.1f}s — status={result.status} events_ran={events_ran} "
        f"wfc={summary.get('wfc_status')}"
    )


def main() -> int:
    sys.path.insert(0, str(REPO))
    print("Phase 1/2: single-event backtest (HYP_5 / CPI NPZ)...", flush=True)
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
