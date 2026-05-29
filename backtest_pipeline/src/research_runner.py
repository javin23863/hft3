"""
Generate 44 per-hypothesis research cards + HftBacktest combined replay + fills export.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO))

from backtest_pipeline.src.runner import LATENCY_BANDS_MS, QUEUE_MODELS, ReplayRunner
from backtest_pipeline.src.signal_backtester import SignalBacktester
from features_engine.src.features.npz_feed import load_npz_events
from features_engine.src.hypotheses.registry import HypothesisRegistry, get_active_hypotheses

LATENCY_BANDS = LATENCY_BANDS_MS


def run_all_research_cards(
    npz_path: str,
    output_dir: str | None = None,
    skip_hft: bool = False,
) -> Path:
    out = Path(output_dir or _REPO / "research_cards")
    out.mkdir(parents=True, exist_ok=True)

    raw = load_npz_events(npz_path)
    hyps = get_active_hypotheses()
    registry = HypothesisRegistry()
    backtester = SignalBacktester()
    all_fills = []

    print(f"Running {len(hyps)} hypotheses x {len(LATENCY_BANDS)} latency bands (batched)...", flush=True)
    matrix = backtester.run_latency_matrix(hyps, raw, LATENCY_BANDS)

    cards = {}
    for hyp in hyps:
        band_results = {}
        for lat in LATENCY_BANDS:
            res = matrix[lat][hyp.hyp_id]
            band_results[f"{lat}ms"] = {
                "net_pnl": res.net_pnl,
                "num_trades": res.num_trades,
                "win_rate": res.win_rate,
                "expectancy": res.expectancy,
                "adverse_selection_ticks": res.adverse_selection_ticks,
                "tail_loss": res.tail_loss,
            }
            all_fills.extend(res.fills)

        pnls = [band_results[f"{b}ms"]["net_pnl"] for b in LATENCY_BANDS]
        trades = sum(band_results[f"{b}ms"]["num_trades"] for b in LATENCY_BANDS)
        as_vals = [band_results[f"{b}ms"]["adverse_selection_ticks"] for b in LATENCY_BANDS]
        card = registry.generate_research_card(
            hyp.hyp_id,
            {
                "years_tested": "2024",
                "events_tested": 1,
                "latency_bands": LATENCY_BANDS,
                "queue_model": "MBO_event_replay",
                "net_pnl": sum(pnls) / len(pnls) if pnls else 0.0,
                "expectancy": sum(band_results[f"{b}ms"]["expectancy"] for b in LATENCY_BANDS)
                / len(LATENCY_BANDS),
                "win_rate": sum(band_results[f"{b}ms"]["win_rate"] for b in LATENCY_BANDS)
                / len(LATENCY_BANDS),
                "adverse_selection": sum(as_vals) / len(as_vals) if as_vals else 0.0,
                "tail_loss": min(band_results[f"{b}ms"]["tail_loss"] for b in LATENCY_BANDS),
                "results_by_band": band_results,
                "num_trades": trades,
            },
        )
        if trades == 0:
            card["approval_status"] = "FAIL"
        cards[f"HYP_{hyp.hyp_id}"] = card
        print(
            f"HYP_{hyp.hyp_id}: pnl={card['net_pnl']:.2f} trades={trades} {card['approval_status']}",
            flush=True,
        )

    fills_df = backtester.fills_to_dataframe(all_fills, raw)
    fills_path = out / "fills.csv"
    if not fills_df.empty:
        fills_df.to_csv(fills_path, index=False)

    hbt_summary = {}
    if not skip_hft:
        runner = ReplayRunner(npz_path)
        for qm in QUEUE_MODELS:
            print(f"HftBacktest combined ({qm})...", flush=True)
            hbt_summary[qm] = runner.generate_research_card(
                "COMBINED_HFT",
                queue_model=qm,
                latency_bands=[1.0, 5.0],
            )

    index = {
        "hypothesis_count": len(cards),
        "cards": cards,
        "hftbacktest_combined": hbt_summary,
        "fills_csv": str(fills_path) if fills_path.exists() else None,
    }
    index_path = out / "all_hypotheses.json"
    with open(index_path, "w", encoding="utf-8") as f:
        json.dump(index, f, indent=2, default=str)

    smoke = {
        "npz_path": npz_path,
        "hypothesis_count": len(cards),
        "pass_count": sum(1 for c in cards.values() if c.get("approval_status") == "PASS"),
        "fail_count": sum(1 for c in cards.values() if c.get("approval_status") == "FAIL"),
        "total_trades": sum(c.get("num_trades", 0) for c in cards.values()),
        "hftbacktest_combined": hbt_summary,
        "fills_csv": str(fills_path) if fills_path.exists() else None,
    }
    smoke_path = out / "matrix_smoke.json"
    with open(smoke_path, "w", encoding="utf-8") as f:
        json.dump(smoke, f, indent=2, default=str)

    print(f"Wrote {index_path}", flush=True)
    print(f"Wrote {smoke_path}", flush=True)
    return index_path


if __name__ == "__main__":
    import argparse

    p = argparse.ArgumentParser()
    p.add_argument("--data", required=True)
    p.add_argument("--out", default=None)
    p.add_argument("--skip-hft", action="store_true", help="Skip slow HftBacktest combined replay")
    args = p.parse_args()
    run_all_research_cards(args.data, args.out, skip_hft=args.skip_hft)
