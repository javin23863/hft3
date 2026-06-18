#!/usr/bin/env python3
"""Benchmark HftBacktest campaign execution paths."""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
import time
from pathlib import Path

from hft3_bootstrap import repo_root

from backtest_pipeline.src.hft_campaign.config import HftCampaignConfig
from backtest_pipeline.src.hft_campaign.prepared_data import prepare_replay_data
from backtest_pipeline.src.hft_campaign.runner import run_hftbacktest_campaign
from backtest_pipeline.src.hft_campaign.scenario import HftReplayScenario, compute_scenario_id
from backtest_pipeline.src.replay_matrix import run_all_hypotheses_replay
from backtest_pipeline.src.replay_npz_fixture import build_minimal_mbo_npz
from features_engine.src.hypotheses.registry import get_active_hypotheses


def _base_scenario(prepared_path: Path, prepared_hash: str, candidate_id: str, model_id: str) -> HftReplayScenario:
    payload = {
        "candidate_id": candidate_id,
        "model_id": model_id,
        "symbol": "MES.v.0",
        "event_id": "BENCH",
        "event_type": "screen",
        "prepared_data_hash": prepared_hash,
        "source_data_hash": prepared_hash,
        "feature_set_id": "bench",
        "feature_set_hash": "bench",
        "research_clock": "continuous_intraday",
        "latency_model_hash": "bench_latency",
        "fill_queue_model_hash": "bench_queue",
        "fee_model_id": "default",
        "split_scheme_id": "default",
        "replay_mode": "baseline",
        "seed": 0,
        "upstream_screening_artifact_hash": "bench",
    }
    return HftReplayScenario(
        scenario_id=compute_scenario_id(payload),
        upstream_screening_artifact=Path("bench.json"),
        upstream_screening_artifact_hash="bench",
        candidate_id=candidate_id,
        model_id=model_id,
        symbol="MES.v.0",
        event_id="BENCH",
        event_type="screen",
        prepared_data_path=prepared_path,
        prepared_data_hash=prepared_hash,
        source_data_hash=prepared_hash,
        feature_set_id="bench",
        feature_set_hash="bench",
        research_clock="continuous_intraday",
        latency_model_path=Path("bench_latency.json"),
        latency_model_hash="bench_latency",
        fill_queue_model_path=Path("bench_queue.json"),
        fill_queue_model_hash="bench_queue",
        fee_model_id="default",
        split_scheme_id="default",
        replay_mode="baseline",
        seed=0,
        transitional_handoff=True,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Benchmark HftBacktest campaign paths")
    args = parser.parse_args()
    root = repo_root()
    hyps = get_active_hypotheses()[:3]

    with tempfile.TemporaryDirectory() as tmp:
        npz_path = Path(tmp) / "minimal.npz"
        build_minimal_mbo_npz(npz_path)
        latency_path = Path(tmp) / "latency.json"
        queue_path = Path(tmp) / "queue.json"
        latency_path.write_text('{"order_entry_latency_ms": 1.0, "order_response_latency_ms": 1.0}\n')
        queue_path.write_text('{"fill_model_scope": "l3_mbo", "tick_size": 0.25, "lot_size": 1.0}\n')

        t0 = time.perf_counter()
        run_all_hypotheses_replay(hyps, str(npz_path), max_steps=200)
        legacy_matrix_s = time.perf_counter() - t0

        t1 = time.perf_counter()
        prepared = prepare_replay_data(
            source_npz_path=npz_path,
            repo_root=root,
            symbol="MES",
            event_id="BENCH",
        )
        prep_s = time.perf_counter() - t1

        scenarios = []
        for hyp in hyps:
            sc = _base_scenario(prepared.path, prepared.prepared_data_hash, f"cand_{hyp.hyp_id}", f"HYP_{hyp.hyp_id}")
            sc = HftReplayScenario(
                **{**sc.__dict__, "latency_model_path": latency_path, "fill_queue_model_path": queue_path}
            )
            scenarios.append(sc)

        t2 = time.perf_counter()
        cfg = HftCampaignConfig(
            campaign_id="bench_campaign",
            repo_root=root,
            workers=1,
            resume=False,
            out_dir=Path(tmp) / "campaign",
        )
        # Stage0 will fail without real screening artifact; benchmark worker path only
        report = {
            "legacy_matrix_s": legacy_matrix_s,
            "prepared_data_build_s": prep_s,
            "prepared_data_hash": prepared.prepared_data_hash,
            "scenario_count": len(scenarios),
            "note": "Full campaign benchmark requires validated screening artifact",
        }
        print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
