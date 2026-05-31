"""One-shot generator for crypto alpha YAML artifacts."""
from __future__ import annotations

import subprocess
import yaml
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]

HYPOTHESES = [
    ("CRYPTO_H1", "BTC spot/perp basis compression", "forward_basis_change",
     ["30s", "5m", "15m", "1h", "8h"],
     ["spot_mid", "perp_mid", "spot_perp_basis", "basis_pct", "basis_zscore", "basis_momentum",
      "basis_volatility", "funding_rate", "ou_theta", "ou_mu", "ou_sigma"],
     "crypto_h1_basis_compression", False),
    ("CRYPTO_H2", "Perp latent funding pressure", "forward_net_funding_after_hedge",
     ["1h", "8h", "24h"],
     ["funding_level", "funding_zscore", "latent_funding_pressure", "hedge_drift_estimate",
      "expected_net_funding_after_cost"],
     "crypto_h2_funding_capture", False),
    ("CRYPTO_H3", "Deribit IV/RV lead-lag", "forward_iv_rv_convergence",
     ["1h", "4h", "8h", "24h"],
     ["atm_iv", "iv_rv_spread", "realized_vol_forecast", "put_call_parity_residual", "skew_25d"],
     "crypto_h3_deribit_vol_leadlag", False),
    ("CRYPTO_H4", "BTC mempool fee regime and volatility", "forward_realized_volatility",
     ["30s", "5m", "15m", "1h"],
     ["btc_mempool_usage_bytes", "btc_fee_spike_zscore", "jump_intensity_lambda",
      "btc_node_data_available_flag"],
     "crypto_h4_mempool_volatility", True),
    ("CRYPTO_H5", "BTC blockspace stress and liquidity degradation", "forward_liquidity_stress_flag",
     ["30s", "5m", "15m", "1h"],
     ["btc_blockspace_stress_score", "btc_fee_spike_zscore", "exchange_spread", "exchange_depth"],
     "crypto_h5_blockspace_liquidity_stress", True),
    ("CRYPTO_H6", "BTC mempool clear reversion", "forward_volatility_compression",
     ["30s", "5m", "15m", "1h", "4h"],
     ["btc_mempool_clear_event", "prior_blockspace_stress_score", "btc_fee_spike_zscore"],
     "crypto_h6_mempool_clear_reversion", True),
    ("CRYPTO_H7", "BTC congestion shock event study", "event_window_return",
     ["-300s", "0", "+300s", "+1h", "+4h"],
     ["btc_fee_spike_event", "btc_congestion_shock_event", "event_severity"],
     "crypto_h7_congestion_event_study", True),
]


def _git_sha() -> str | None:
    try:
        out = subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=ROOT,
            stderr=subprocess.DEVNULL,
            text=True,
        )
        return out.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None


def hyp_entry(hid, name, label, horizons, feats, cid, btc_req, commit_sha: str | None):
    return {
        "hypothesis_id": hid,
        "name": name,
        "source_repo": "https://github.com/javin23863/crypto-alpha-engine",
        "source_files": ["ideas-only extraction"],
        "ideas_only": commit_sha is None,
        "source_commit_sha": commit_sha,
        "source_concept_summary": name,
        "economic_mechanism": name,
        "null_hypothesis": "Feature set has zero incremental predictive power OOS.",
        "alternative_hypothesis": "Feature set predicts label with positive OOS IC after costs.",
        "expected_direction": "positive",
        "asset_scope": ["BTC"],
        "venue_scope": ["binance_spot", "binance_perp", "deribit"],
        "prediction_horizons": horizons,
        "features_required": feats,
        "features_optional": ["btc_mempool_usage_bytes"],
        "btc_node_features": feats if btc_req else [],
        "labels": [label],
        "label_formula": f"forward-looking {label}",
        "feature_timestamp_rule": "exchange event-time <= t only",
        "label_timestamp_rule": "strictly t+h forward",
        "point_in_time_controls": ["T_avail <= T_exch_true", "max_staleness_ms"],
        "leakage_risks": ["clock drift", "forward-filled node snapshots"],
        "costs_required": True,
        "latency_assumptions": {"ws_rtt_tracking": True},
        "slippage_assumptions": {"include_spread": True},
        "regime_controls": ["vol_regime"],
        "baseline_models": ["basis_only", "naive_previous_value", "logistic_regression"],
        "challenger_models": ["lightgbm", "xgboost", "elastic_net"],
        "ablation_tests": ["with_btc_node", "without_btc_node"] if btc_req else [],
        "negative_controls": ["shuffled_labels", "shifted_features_forward", "randomized_event_times"],
        "failure_conditions": ["negative OOS IC", "leakage check fail"],
        "promotion_criteria": ["positive_oos_ic", "positive_after_cost_pnl", "walk_forward_stable"],
        "backtest_config": f"backtests/configs/crypto_hypotheses/{cid.replace('crypto_', '')}.yaml",
        "owner_notes": "Production ingest via packages/crypto_lane/pipeline.py ingest.",
    }


def main() -> None:
    commit_sha = _git_sha()
    hyps = [hyp_entry(*row, commit_sha) for row in HYPOTHESES]
    hyp_path = ROOT / "research/hypotheses/crypto_alpha_engine_extracted_hypotheses.yaml"
    hyp_path.parent.mkdir(parents=True, exist_ok=True)
    hyp_path.write_text(yaml.dump({"schema_version": 1, "hypotheses": hyps}, sort_keys=False), encoding="utf-8")

    for hid, name, label, horizons, feats, cid, btc_req in HYPOTHESES:
        bt_id = cid.replace("crypto_", "")
        cand = {
            "candidate_id": cid,
            "hypothesis_id": hid,
            "target": label,
            "horizons": horizons,
            "features": feats,
            "required_data": ["spot_perp_ticks"],
            "optional_data": ["deribit_surface", "mempool_snapshots"],
            "btc_node_required": btc_req,
            "model_family": "tabular_ml",
            "baseline": (
                ["volatility_only", "basis_only", "naive_previous_value", "logistic_regression", "funding_only"]
                if hid == "CRYPTO_H4"
                else ["basis_only", "naive_previous_value", "logistic_regression", "volatility_only", "funding_only"]
            ),
            "challengers": ["lightgbm", "xgboost", "elastic_net", "ridge"],
            "validation": {
                "validation_tier": "production",
                "walk_forward": True,
                "purged_cv": True,
                "embargo": True,
                "holdout_blocked": True,
                "leakage_checks": True,
                "min_folds": 3,
                "fixture_observation_seconds": 512,
                "min_oos_days": None,
            },
            "backtest": {
                "engine": "crypto_lane",
                "include_fees": True,
                "include_spread": True,
                "include_slippage": True,
                "include_latency": True,
            },
            "ablation": {
                "run_without_btc_node_features": btc_req,
                "run_with_btc_node_features": btc_req,
                "report_incremental_ic": True,
                "report_incremental_pnl": True,
            },
            "negative_controls": {
                "shuffled_labels": True,
                "shifted_features_forward": True,
                "randomized_event_times": hid == "CRYPTO_H7",
            },
            "promotion": {
                "require_positive_oos_ic": True,
                "require_positive_after_cost_pnl": True,
                "require_regime_stability": True,
                "require_capacity_estimate": True,
                "reject_single_regime_dependency": True,
            },
        }
        cand_dir = ROOT / "models/candidates"
        cand_dir.mkdir(parents=True, exist_ok=True)
        (cand_dir / f"{cid}.yaml").write_text(yaml.dump(cand, sort_keys=False), encoding="utf-8")

        bt = {
            "config_id": bt_id,
            "hypothesis_id": hid,
            "candidate_id": cid,
            "validation_mode": "production",
            "holdout_days": 30,
            "universe": ["BTC"],
            "venues": ["binance_spot", "binance_perp", "deribit"],
            "date_range": {"start": "2024-01-01", "end": "2024-12-31"},
            "training_window": "90d",
            "test_window": "30d",
            "embargo": "24h",
            "bar_event_frequency": "1h",
            "cost_assumptions": {"fee_bps": 2, "spread_bps": 1},
            "latency_assumptions": {"ws_rtt_ms": 5},
            "slippage_assumptions": {"model": "linear_bps"},
            "max_feature_staleness_ms": 15000 if btc_req else 300000,
            "btc_node_feature_availability_mode": "pit_strict" if btc_req else "optional",
            "output_report_path": f"research_cards/crypto/{cid}/report.json",
        }
        bt_dir = ROOT / "backtests/configs/crypto_hypotheses"
        bt_dir.mkdir(parents=True, exist_ok=True)
        (bt_dir / f"{bt_id}.yaml").write_text(yaml.dump(bt, sort_keys=False), encoding="utf-8")

    manifest = {
        "source_repo_url": "https://github.com/javin23863/crypto-alpha-engine",
        "ideas_only": commit_sha is None,
        "source_commit_sha": commit_sha,
        "extracted_hypotheses": [h[0] for h in HYPOTHESES],
        "feature_builders": [
            "packages/crypto_lane/src/features/crypto/basis_features.py",
            "packages/crypto_lane/src/features/crypto/funding_features.py",
            "packages/crypto_lane/src/features/crypto/deribit_vol_features.py",
            "packages/crypto_lane/src/features/onchain/btc_node_mempool_features.py",
            "packages/crypto_lane/src/features/onchain/btc_blockspace_event_features.py",
        ],
        "align_modules": [
            "packages/crypto_lane/src/align/clock_sync.py",
            "packages/crypto_lane/src/align/pit_join.py",
        ],
        "model_candidates": [row[5] for row in HYPOTHESES],
        "backtest_configs": [row[5].replace("crypto_", "") for row in HYPOTHESES],
        "tests": ["tests/test_crypto_lane/"],
        "no_runtime_dependency": True,
        "integration_points": ["packages/crypto_lane/pipeline.py"],
        "blocked_items": ["CHI404 hot path"],
    }
    (ROOT / "research/hypotheses/crypto_alpha_engine_manifest.yaml").write_text(
        yaml.dump(manifest, sort_keys=False), encoding="utf-8"
    )


if __name__ == "__main__":
    main()
