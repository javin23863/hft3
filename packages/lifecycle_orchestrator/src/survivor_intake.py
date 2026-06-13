"""Survivor intake — mint new CERTIFIED models from a fresh universe sweep.

The autonomous loop (``run_lifecycle_eval``) is a *maintenance* loop: it scans the
existing registry for decay and re-screens. It has no *discovery* path — nothing
turns the survivors of a fresh full-universe sweep
(``scripts/run_event_universe.py`` -> ``universe_result.json``) into new lifecycle
models. This module is that bridge.

For each (hypothesis, event_type, latency-band) cell that:
  * survives Holm correction (``corrections[event_type].holm.passed_slugs``), AND
  * passes the gauntlet robustness gate (DSR>0, PBO<0.5, bootstrap CI_lower>0,
    fee-x2 stress) per ``gauntlet_reader.read_verdict``,
this mints a CERTIFIED lifecycle record carrying a frozen behavior envelope and
the ``cell`` metadata (hyp_id, event_type, band) that the param/edge re-screen
routes (``routes._materialize_rescreen``) need to build a runnable command.

Idempotent: a cell already in the registry is skipped. The gauntlet verdict IS
the promotion gate, so ``certify_and_snapshot`` runs with ``validate_gate=False``.
One bad cell never aborts the whole intake.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Optional

from model_metrics import certify, lifecycle

from . import gauntlet_reader

# Holm survivor slugs are ``hyp_{id}_band_{band}`` (run_event_universe._apply_corrections).
_SLUG_RE = re.compile(r"^hyp_(?P<hyp_id>\d+)_band_(?P<band>[0-9.]+)$")
DEFAULT_SYMBOL = "MES.v.0"


def _lifecycle_id(hyp_id: int, event_type: str, band: float) -> str:
    et = re.sub(r"[^A-Za-z0-9]+", "_", event_type).strip("_").upper()
    return f"M_HYP{hyp_id}_{et}_B{band:g}".replace(".", "p")


def _aggregated_cell(universe: dict, hyp_id: int, event_type: str, band: float) -> dict:
    """Best-effort lookup of the aggregated cell for its metrics. Tolerant to the
    float-key string formatting that survives JSON round-tripping."""
    agg = universe.get("aggregated") or {}
    etmap = (agg.get(str(hyp_id)) or {}).get(event_type) or {}
    if not isinstance(etmap, dict):
        return {}
    for k, v in etmap.items():
        try:
            if abs(float(k) - band) < 1e-6:
                return v if isinstance(v, dict) else {}
        except (TypeError, ValueError):
            continue
    return {}


def _build_candidate(hyp_id: int, event_type: str, band: float, cell: dict, symbol: str):
    """A minimal PromotedCandidate carrying the cell's aggregate metrics. The
    gauntlet verdict already gated this cell, so certify runs validate_gate=False."""
    from backtest_pipeline.src.promotion_gate import PromotedCandidate

    expectancy = float(cell.get("mean_expectancy_usd", 0.0) or 0.0)
    win_rate = float(cell.get("mean_win_rate", 0.0) or 0.0)
    n_trades = int(cell.get("total_trades", 0) or 0)
    return PromotedCandidate(
        candidate_id=f"sweep_hyp{hyp_id}_{event_type}_b{band:g}",
        hypothesis_id=str(hyp_id),
        strategy_family=str(cell.get("hypothesis_name", "") or f"HYP_{hyp_id}"),
        asset_class="futures",
        symbol=symbol,
        timeframe="event",
        param_values={},
        vectorbt_run_id="",
        vectorbt_results={
            "oos_expectancy": expectancy,
            "win_rate": win_rate,
            "num_trades": n_trades,
        },
        pass_reason="survivor_intake: Holm + DSR + PBO + bootstrap-CI + fee-x2 passed",
        drawdown_metrics={},
    )


def intake_survivors(
    universe_path: str | Path,
    *,
    symbol: str = DEFAULT_SYMBOL,
    actor: str = "survivor_intake",
    dry_run: bool = False,
) -> dict:
    """Mint CERTIFIED models for every gauntlet-passing Holm survivor not already
    in the registry. Returns a summary dict (minted / skipped / failed)."""
    path = Path(universe_path)
    universe = json.loads(path.read_text(encoding="utf-8"))
    corrections = universe.get("corrections") or {}

    existing = set(lifecycle.load_registry().keys())
    minted: list[dict] = []
    skipped: list[dict] = []
    failed: list[dict] = []
    seen: set[str] = set()

    for event_type, block in corrections.items():
        if not isinstance(block, dict):
            continue
        holm = block.get("holm") or {}
        passed = holm.get("passed_slugs") or block.get("passed_slugs") or []
        for slug in passed:
            m = _SLUG_RE.match(str(slug))
            if not m:
                skipped.append({"slug": slug, "event_type": event_type, "reason": "unparseable slug"})
                continue
            hyp_id = int(m.group("hyp_id"))
            band = float(m.group("band"))
            lid = _lifecycle_id(hyp_id, event_type, band)
            if lid in seen:
                continue
            seen.add(lid)
            if lid in existing:
                skipped.append({"slug": slug, "event_type": event_type,
                                "lifecycle_id": lid, "reason": "already registered"})
                continue

            verdict = gauntlet_reader.read_verdict(universe, slug, event_type=event_type)
            if not verdict.passed:
                skipped.append({"slug": slug, "event_type": event_type, "lifecycle_id": lid,
                                "reason": "gauntlet fail: " + "; ".join(verdict.reasons)})
                continue
            if dry_run:
                minted.append({"slug": slug, "event_type": event_type, "lifecycle_id": lid, "dry_run": True})
                continue

            cell = _aggregated_cell(universe, hyp_id, event_type, band)
            try:
                candidate = _build_candidate(hyp_id, event_type, band, cell, symbol)
                _, rec = certify.certify_and_snapshot(
                    candidate,
                    lifecycle_id=lid,
                    validate_gate=False,  # the gauntlet verdict IS the gate
                    returns=cell.get("per_event_expectancies") or [],
                    cell={"hyp_id": hyp_id, "event_type": event_type, "band_ms": band, "slug": slug},
                    links={"source": "survivor_intake", "universe_result": str(path), "slug": slug},
                    actor=actor,
                )
                minted.append({"slug": slug, "event_type": event_type, "lifecycle_id": lid,
                               "state": rec.current_state, "envelope_id": rec.current_envelope_id})
            except Exception as exc:  # one bad cell must not abort the intake
                failed.append({"slug": slug, "event_type": event_type, "lifecycle_id": lid,
                               "error": f"{type(exc).__name__}: {exc}"})

    return {"universe_result": str(path), "minted": minted, "skipped": skipped, "failed": failed,
            "n_minted": len(minted), "n_skipped": len(skipped), "n_failed": len(failed)}


def main(argv: Optional[list] = None) -> int:
    import argparse

    p = argparse.ArgumentParser(prog="survivor_intake",
                                description="Mint CERTIFIED models from a run_event_universe sweep")
    p.add_argument("universe_result", help="path to a universe_result.json")
    p.add_argument("--symbol", default=DEFAULT_SYMBOL)
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args(argv)
    summary = intake_survivors(args.universe_result, symbol=args.symbol, dry_run=args.dry_run)
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
