"""Cockpit backend tests — aggregator shape, graceful-missing, API auth.

Run from repo root:  python -m pytest apps/cockpit/backend/tests -q
"""
import json
import os
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from apps.cockpit.backend import loaders, paths
from apps.cockpit.backend import control
from apps.cockpit.backend import schemas as sc
from apps.cockpit.backend import main as cockpit_main
from apps.cockpit.backend.aggregate import ZONES
from apps.cockpit.backend.aggregate import pipeline as pipeline_agg
from apps.cockpit.backend.aggregate import system as system_agg
from apps.cockpit.backend.main import app

VIEW_KEYS = {"zone", "generated_utc", "health"}


def _json_roundtrip(obj):
    # Every zone payload must be JSON-serializable (FastAPI ships it as-is).
    return json.loads(json.dumps(obj))


def _write_options_spec(root: Path, status: str) -> None:
    specs = root / "specs"
    specs.mkdir(parents=True, exist_ok=True)
    (specs / "OPTIONS_LANE.md").write_text(
        "# OPTIONS_LANE.md\n\n"
        "| ID | Component | Description | Status |\n"
        "|----|-----------|-------------|--------|\n"
        f"| o-a | `vol_clock` | placeholder | {status} |\n",
        encoding="utf-8",
    )


def _options_ok_checks() -> list[dict]:
    return [{"name": name, "status": "OK", "detail": "ok"} for name in system_agg.MANDATORY_OPTIONS_CHECKS]


def _write_jsonl(path: Path, *records: dict) -> None:
    path.write_text("\n".join(json.dumps(record) for record in records) + "\n", encoding="utf-8")


def _full_universe_cli_args(repo: Path) -> dict:
    return {
        "lane": "cme",
        "bands_override": "6.255764",
        "event_type": None,
        "symbols": "MES.v.0,MNQ.v.0,ES.v.0,NQ.v.0,ZN.v.0,ZB.v.0,RTY.v.0",
        "events_csv": str(repo / "packages" / "data_system" / "config" / "events.csv"),
        "workers": 12,
        "max_events": None,
        "from_stage_a": "research_cards/stage_a_full/stage_a_survivors.json",
        "cells": None,
        "shard": None,
    }


def _write_universe_result(path: Path, **overrides) -> None:
    repo = path.parents[1]
    payload = {
        "schema": "universe_result_v1",
        "run_end_utc": "2026-06-12T07:07:18+00:00",
        "cli_args": _full_universe_cli_args(repo),
        "latency_bands_ms": [0.5, 1.0],
        "units_run": 1,
        "units_skipped": 0,
        "units_errored": 0,
        "certification_stamp": {
            "status": "GREEN",
            "stale": False,
            "promotion_eligible": True,
            "promotion_label": "PROMOTION_ELIGIBLE_FROM_BACKTESTER_SIDE",
        },
        "robustness": {
            "dsr_by_cell": {"hyp_2_band_1.0_CPI": {"dsr": 0.8}},
            "pbo": {
                "pbo": 0.12,
                "n_splits": 8,
                "n_configs": 2,
                "n_partitions": 16,
                "n_excluded": 0,
            },
            "bootstrap_by_cell": {"hyp_2_band_1.0_CPI": {"ci_lower": 1.5}},
            "fee_stress_by_cell": {"hyp_2_band_1.0_CPI": {"fee_x2_pass": True}},
        },
        "corrections": {"CPI": {"holm": {"passed_slugs": ["hyp_2_band_1.0"]}}},
        "unit_results": [
            {
                "event_id": "CPI_2024_09_11_TIGHT",
                "symbol": "MES.v.0",
                "latency_ms": 1.0,
                "error": None,
                "skip_reason": None,
                "hypotheses": [
                    {"hypothesis_id": 2, "hypothesis_name": "Stop-run exhaustion fade"}
                ],
            }
        ],
    }
    payload.update(overrides)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _write_latency_evidence_files(root: Path) -> None:
    (root / "runtime" / "latency_reports").mkdir(parents=True, exist_ok=True)
    (root / "reports" / "latency_baselines").mkdir(parents=True, exist_ok=True)
    (root / "data" / "latency_baselines" / "2026-06-11").mkdir(parents=True, exist_ok=True)
    (root / "runtime" / "latency_reports" / "order_ack_distribution.json").write_text(
        json.dumps({"percentiles": {"p99": 6255.76436}}),
        encoding="utf-8",
    )
    (root / "runtime" / "latency_reports" / "latency_summary.json").write_text(
        json.dumps({}),
        encoding="utf-8",
    )
    (root / "runtime" / "latency_reports" / "latency_truth.json").write_text(
        json.dumps({"compute": {"tick_to_decision_ns": 15300}}),
        encoding="utf-8",
    )
    (root / "reports" / "latency_baselines" / "current_baseline.json").write_text(
        json.dumps({
            "metrics": {
                "tick_to_send_us": {"p99_us": 23.314},
                "decision_to_send_us": {"p99_us": 22.572},
            }
        }),
        encoding="utf-8",
    )
    (root / "reports" / "latency_baselines" / "order_ack_campaign_20260611T072116Z_summary.json").write_text(
        json.dumps({"metrics": {"decision_to_send_us": {"p50_us": 12.404, "p99_us": 38.693}}}),
        encoding="utf-8",
    )
    _write_jsonl(
        root / "data" / "latency_baselines" / "2026-06-11" / "order_ack_campaign_20260611T071952Z.jsonl",
        {"order_action": "cancel", "cancel_to_send_us": 14.677, "cancel_to_ack_us": None},
    )


def _point_latency_paths(monkeypatch, root: Path) -> None:
    monkeypatch.setattr(paths, "ORDER_ACK_DISTRIBUTION", root / "runtime" / "latency_reports" / "order_ack_distribution.json")
    monkeypatch.setattr(paths, "LATENCY_SUMMARY", root / "runtime" / "latency_reports" / "latency_summary.json")
    monkeypatch.setattr(paths, "LATENCY_TRUTH", root / "runtime" / "latency_reports" / "latency_truth.json")
    monkeypatch.setattr(paths, "LATENCY_CURRENT_BASELINE", root / "reports" / "latency_baselines" / "current_baseline.json")
    monkeypatch.setattr(paths, "LATENCY_LATEST_ORDER_SUMMARY", root / "reports" / "latency_baselines" / "order_ack_campaign_20260611T072116Z_summary.json")
    monkeypatch.setattr(paths, "LATENCY_DEFENSIVE_CANCEL_SAMPLE", root / "data" / "latency_baselines" / "2026-06-11" / "order_ack_campaign_20260611T071952Z.jsonl")


def _point_non_universe_pipeline_paths(monkeypatch, root: Path) -> None:
    monkeypatch.setattr(paths, "CAPTURE_BASELINE", root / "missing_capture.json")
    monkeypatch.setattr(paths, "FEATURE_FABRIC", root / "missing_feature.json")
    monkeypatch.setattr(paths, "STAGE_A_RESULT", root / "missing_stage_a.json")
    monkeypatch.setattr(paths, "STAGE_A_SURVIVORS", root / "missing_survivors.json")
    monkeypatch.setattr(paths, "ALPHA_CME_SPEC", root / "missing.md")
    loaders._cache.clear()


def _read_universe_stage(monkeypatch, tmp_path: Path, payload: dict) -> dict:
    artifact = tmp_path / "research_cards" / "universe_result.json"
    _write_universe_result(artifact, **payload)
    monkeypatch.setattr(paths, "REPO", tmp_path)
    return pipeline_agg._universe_stage("gauntlet_b", "Gauntlet B", artifact)


@pytest.mark.parametrize("name", list(ZONES))
def test_zone_shape(name):
    payload = ZONES[name]()
    assert VIEW_KEYS.issubset(payload), f"{name} missing base keys: {payload.keys()}"
    assert payload["zone"] == name
    _json_roundtrip(payload)  # raises if non-serializable


def test_pipeline_has_six_stages():
    p = ZONES["pipeline"]()
    ids = [s["id"] for s in p["stages"]]
    assert ids == ["capture", "feature_build", "stage_a", "gauntlet_b", "m6_gate", "promote"]
    for s in p["stages"]:
        assert {"id", "label", "status"}.issubset(s)


def test_models_registry_and_silent_zero():
    m = ZONES["models"]()
    assert m["health"] == sc.AMBER
    assert m["registry_total"] == 50
    assert len(m["rows"]) == 50
    # The six structurally-dead prop hyps must be surfaced as silent-zero.
    assert m["silent_zero"]["count"] == 6
    dead_ids = {h["id"] for h in m["silent_zero"]["hypotheses"]}
    assert dead_ids == {20, 30, 32, 35, 36, 38}
    for row in m["rows"]:
        if row["id"] in dead_ids:
            assert row["structurally_dead"] is True
            assert row["status"] == "structurally_dead"


def test_models_exposes_stage_a_vix_coverage(monkeypatch, tmp_path):
    stage_a = tmp_path / "stage_a_result.json"
    stage_a.write_text(
        json.dumps({
            "cells": [
                {
                    "hypothesis_id": 46,
                    "hypothesis_name": "VIX spike event fade",
                    "event_type": "CPI",
                    "n_events": 2,
                    "n_events_with_vix": 1,
                    "total_trades": 3,
                    "mean_expectancy_usd": 1.25,
                },
                {
                    "hypothesis_id": 47,
                    "hypothesis_name": "VIX quote-pull liquidity vacuum",
                    "event_type": "NFP",
                    "vix_coverage": {"n_events": 3, "n_events_with_vix": 2},
                    "total_trades": 1,
                    "mean_expectancy_usd": -0.5,
                },
                {
                    "hypothesis_id": 1,
                    "hypothesis_name": "Second-wave continuation",
                    "event_type": "ADP_EMPLOYMENT",
                    "n_events": 5,
                    "n_events_with_vix": 0,
                    "total_trades": 2,
                    "mean_expectancy_usd": 0.1,
                },
            ]
        }),
        encoding="utf-8",
    )
    monkeypatch.setattr(paths, "STAGE_A_RESULT", stage_a)
    monkeypatch.setattr(paths, "STAGE_A_SURVIVORS", tmp_path / "missing_survivors.json")
    loaders._cache.clear()

    m = ZONES["models"]()

    coverage = m["vix_coverage"]
    assert coverage["status"] == "covered"
    assert coverage["cell_event_observations"] == 10
    assert coverage["cell_event_observations_with_vix"] == 3
    assert coverage["cells_with_vix"] == 2
    assert coverage["invalid_cells"] == 0
    assert coverage["coverage_pct"] == 30.0
    assert any("HOT_MEMORY_UNIVERSE.md" in s["source_ref"] for s in coverage["authority_sources"])
    by_id = {r["id"]: r for r in m["rows"]}
    assert by_id[46]["n_events"] == 2
    assert by_id[46]["n_events_with_vix"] == 1
    assert by_id[46]["vix_coverage_pct"] == 50.0
    assert by_id[47]["n_events"] == 3
    assert by_id[47]["n_events_with_vix"] == 2
    assert by_id[47]["vix_coverage_pct"] == 66.67
    assert by_id[1]["n_events_with_vix"] == 0


def test_models_vix_zero_coverage_is_visible_and_amber(monkeypatch, tmp_path):
    stage_a = tmp_path / "stage_a_result.json"
    stage_a.write_text(
        json.dumps({
            "cells": [
                {
                    "hypothesis_id": 46,
                    "hypothesis_name": "VIX spike event fade",
                    "event_type": "CPI",
                    "n_events": 4,
                    "n_events_with_vix": 0,
                    "total_trades": 0,
                    "mean_expectancy_usd": 0.0,
                }
            ]
        }),
        encoding="utf-8",
    )
    monkeypatch.setattr(paths, "STAGE_A_RESULT", stage_a)
    monkeypatch.setattr(paths, "STAGE_A_SURVIVORS", tmp_path / "missing_survivors.json")
    loaders._cache.clear()

    m = ZONES["models"]()

    assert m["health"] == sc.AMBER
    coverage = m["vix_coverage"]
    assert coverage["status"] == "zero"
    assert coverage["cell_event_observations"] == 4
    assert coverage["cell_event_observations_with_vix"] == 0
    assert coverage["coverage_pct"] == 0.0
    assert coverage["invalid_cells"] == 0
    by_id = {r["id"]: r for r in m["rows"]}
    assert by_id[46]["n_events"] == 4
    assert by_id[46]["n_events_with_vix"] == 0
    assert by_id[46]["vix_coverage_pct"] == 0.0


def test_models_vix_malformed_counts_fail_closed(monkeypatch, tmp_path):
    stage_a = tmp_path / "stage_a_result.json"
    stage_a.write_text(
        json.dumps({
            "cells": [
                {
                    "hypothesis_id": 46,
                    "hypothesis_name": "VIX spike event fade",
                    "event_type": "CPI",
                    "n_events": 1,
                    "n_events_with_vix": 2,
                    "total_trades": 0,
                    "mean_expectancy_usd": 0.0,
                }
            ]
        }),
        encoding="utf-8",
    )
    monkeypatch.setattr(paths, "STAGE_A_RESULT", stage_a)
    monkeypatch.setattr(paths, "STAGE_A_SURVIVORS", tmp_path / "missing_survivors.json")
    loaders._cache.clear()

    m = ZONES["models"]()

    assert m["health"] == sc.RED
    coverage = m["vix_coverage"]
    assert coverage["status"] == "corrupt"
    assert coverage["invalid_cells"] == 1
    assert coverage["cell_event_observations"] == 0
    assert coverage["cell_event_observations_with_vix"] == 0
    assert coverage["coverage_pct"] is None


def test_portfolio_live_session_flag(monkeypatch):
    monkeypatch.setenv("EXECUTION_MODE", "REPLAY")
    p = ZONES["portfolio"]()
    assert p["live_session"] is False
    assert p["banner"] and "No live session" in p["banner"]


def test_portfolio_live_without_session_is_amber(monkeypatch, tmp_path):
    monkeypatch.setenv("EXECUTION_MODE", "LIVE")
    monkeypatch.setattr(paths, "SESSIONS_ROOT", tmp_path / "sessions")
    p = ZONES["portfolio"]()
    assert p["live_session"] is False
    assert p["health"] == "amber"
    assert p["source"] == "no live session"
    assert any("no readable" in note for note in p["notes"])


def test_portfolio_uses_newest_session_artifact_over_directory_mtime(monkeypatch, tmp_path):
    monkeypatch.setenv("EXECUTION_MODE", "LIVE")
    sessions = tmp_path / "sessions"
    older_dir = sessions / "older-dir"
    newer_dir = sessions / "newer-dir"
    older_dir.mkdir(parents=True)
    newer_dir.mkdir(parents=True)
    _write_jsonl(older_dir / "positions.jsonl", {"timestamp_ns": 2, "positions": {"MES": 7}})
    _write_jsonl(older_dir / "kill_switch_events.jsonl", {"timestamp_ns": 3, "active": False})
    _write_jsonl(newer_dir / "positions.jsonl", {"timestamp_ns": 1, "positions": {"MES": 1}})
    now = time.time()
    os.utime(older_dir / "positions.jsonl", (now - 4, now - 4))
    os.utime(older_dir / "kill_switch_events.jsonl", (now - 3, now - 3))
    os.utime(newer_dir / "positions.jsonl", (now - 300, now - 300))
    os.utime(older_dir, (now - 1000, now - 1000))
    os.utime(newer_dir, (now - 1, now - 1))
    monkeypatch.setattr(paths, "SESSIONS_ROOT", sessions)

    p = ZONES["portfolio"]()

    assert p["live_session"] is True
    assert p["session_id"] == "older-dir"
    assert p["positions"] == [{"symbol": "MES", "quantity": 7}]
    assert p["session_age_s"] is not None and p["session_age_s"] < 30


def test_missing_artifact_is_graceful(monkeypatch, tmp_path):
    # Point Stage A at a nonexistent file; pipeline must render MISSING, not crash.
    monkeypatch.setattr(paths, "STAGE_A_RESULT", tmp_path / "nope.json")
    loaders._cache.clear()
    p = ZONES["pipeline"]()
    stage_a = next(s for s in p["stages"] if s["id"] == "stage_a")
    assert stage_a["status"] == "missing"


def test_universe_all_empty_or_skip_only_artifact_is_stale(monkeypatch, tmp_path):
    stage = _read_universe_stage(monkeypatch, tmp_path, {
        "units_run": 2,
        "units_skipped": 2,
        "unit_results": [
            {"error": None, "skip_reason": "empty_npz", "hypotheses": []},
            {
                "error": None,
                "skip_reason": "npz_missing",
                "hypotheses": [{"hypothesis_id": 2, "hypothesis_name": "Stop-run exhaustion fade"}],
            },
        ],
    })

    assert stage["status"] == sc.STALE
    assert stage["evaluated_model_rows"] == 0
    assert stage["detail"] == "no model hypotheses evaluated"


def test_universe_bounded_smoke_with_evaluated_model_is_stale(monkeypatch, tmp_path):
    stage = _read_universe_stage(monkeypatch, tmp_path, {
        "cli_args": {
            "lane": "cme",
            "max_events": 1,
            "event_type": "CPI",
            "symbols": "MES.v.0",
            "bands_override": "6.255764",
        },
    })

    assert stage["status"] == sc.STALE
    assert stage["scope"] == "smoke"
    assert stage["scope_detail"] == ["max_events", "event_type", "symbols"]
    assert stage["evaluated_model_rows"] == 1
    assert stage["evaluated_models"] == ["2: Stop-run exhaustion fade"]
    assert "bounded/smoke scope" in stage["detail"]


def test_universe_aborted_artifact_is_fail(monkeypatch, tmp_path):
    stage = _read_universe_stage(monkeypatch, tmp_path, {
        "status": "ABORTED_NO_PROGRESS",
        "abort_reason": "all units skipped before model evaluation",
        "units_run": 5,
        "unit_results": [],
    })

    assert stage["status"] == sc.FAIL
    assert stage["detail"] == "all units skipped before model evaluation"


def test_universe_errored_units_are_fail(monkeypatch, tmp_path):
    stage = _read_universe_stage(monkeypatch, tmp_path, {
        "units_errored": 1,
        "unit_results": [
            {"error": "replay invariant failed", "hypotheses": []},
        ],
    })

    assert stage["status"] == sc.FAIL
    assert stage["detail"] == "1 unit(s) errored"


def test_universe_missing_cli_args_is_stale_not_full(monkeypatch, tmp_path):
    stage = _read_universe_stage(monkeypatch, tmp_path, {
        "cli_args": None,
    })

    assert stage["status"] == sc.STALE
    assert stage["scope"] == "unknown"
    assert stage["detail"] == "missing cli_args scope metadata"


@pytest.mark.parametrize("cli_args", [{}, {"lane": "cme"}])
def test_universe_incomplete_cli_args_is_stale_not_full(monkeypatch, tmp_path, cli_args):
    stage = _read_universe_stage(monkeypatch, tmp_path, {
        "cli_args": cli_args,
    })

    assert stage["status"] == sc.STALE
    assert stage["scope"] == "unknown"
    assert stage["detail"].startswith("missing cli_args keys:")


@pytest.mark.parametrize(
    ("key", "value", "detail"),
    [
        ("lane", "equities", "non-cme lane scope"),
        ("bands_override", "23.0", "non-canonical M6 band scope"),
        ("workers", 1, "non-canonical worker scope"),
        ("from_stage_a", "research_cards/stage_a_smoke/stage_a_survivors.json", "non-canonical Stage A survivor scope"),
    ],
)
def test_universe_wrong_full_scope_values_are_stale(monkeypatch, tmp_path, key, value, detail):
    cli_args = _full_universe_cli_args(tmp_path)
    cli_args[key] = value
    stage = _read_universe_stage(monkeypatch, tmp_path, {
        "cli_args": cli_args,
    })

    assert stage["status"] == sc.STALE
    assert stage["scope"] == "unknown"
    assert stage["detail"] == detail


def test_universe_noncanonical_events_csv_is_stale_not_full(monkeypatch, tmp_path):
    cli_args = _full_universe_cli_args(tmp_path)
    cli_args["events_csv"] = str(tmp_path / "subset_events.csv")
    stage = _read_universe_stage(monkeypatch, tmp_path, {
        "cli_args": cli_args,
    })

    assert stage["status"] == sc.STALE
    assert stage["scope"] == "unknown"
    assert stage["detail"] == "non-canonical events_csv scope"


def test_universe_missing_explicit_symbol_scope_is_stale_not_full(monkeypatch, tmp_path):
    cli_args = _full_universe_cli_args(tmp_path)
    cli_args["symbols"] = None
    stage = _read_universe_stage(monkeypatch, tmp_path, {
        "cli_args": cli_args,
    })

    assert stage["status"] == sc.STALE
    assert stage["scope"] == "unknown"
    assert stage["detail"] == "missing explicit symbol scope"


def test_universe_subset_symbol_scope_is_smoke_not_full(monkeypatch, tmp_path):
    cli_args = _full_universe_cli_args(tmp_path)
    cli_args["symbols"] = "MES.v.0"
    stage = _read_universe_stage(monkeypatch, tmp_path, {
        "cli_args": cli_args,
    })

    assert stage["status"] == sc.STALE
    assert stage["scope"] == "smoke"
    assert stage["scope_detail"] == ["symbols"]
    assert stage["detail"] == "bounded/smoke scope: symbols"


@pytest.mark.parametrize("bad_pbo", [1.2, -0.1, float("nan"), float("inf")])
def test_universe_invalid_pbo_is_stale(monkeypatch, tmp_path, bad_pbo):
    stage = _read_universe_stage(monkeypatch, tmp_path, {
        "robustness": {"pbo": {"pbo": bad_pbo}},
    })

    assert stage["status"] == sc.STALE
    assert stage["robustness_status"] == sc.STALE
    assert stage["robustness_detail"].startswith("pbo invalid:")


@pytest.mark.parametrize("high_pbo", [0.21, 0.7])
def test_universe_high_finite_pbo_is_stale(monkeypatch, tmp_path, high_pbo):
    stage = _read_universe_stage(monkeypatch, tmp_path, {
        "robustness": {"pbo": {"pbo": high_pbo, "n_configs": 8, "n_partitions": 70}},
    })

    assert stage["status"] == sc.STALE
    assert stage["robustness_status"] == sc.STALE
    assert stage["robustness_detail"] == f"pbo {high_pbo} > maximum_pbo 0.2"


@pytest.mark.parametrize("bad_threshold", [float("nan"), float("inf"), -0.1, 1.1, "bad"])
def test_universe_invalid_pbo_threshold_is_stale(monkeypatch, tmp_path, bad_threshold):
    monkeypatch.setattr(pipeline_agg, "_pbo_max", lambda: bad_threshold)
    stage = _read_universe_stage(monkeypatch, tmp_path, {
        "robustness": {"pbo": {"pbo": 0.12, "n_configs": 8, "n_partitions": 70}},
    })

    assert stage["status"] == sc.STALE
    assert stage["robustness_status"] == sc.STALE
    assert stage["robustness_detail"] == "maximum_pbo threshold invalid"


def test_universe_pbo_threshold_config_parse_error_is_stale(monkeypatch, tmp_path):
    cfg = tmp_path / "configs" / "model_metrics.yaml"
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text("global: [", encoding="utf-8")
    stage = _read_universe_stage(monkeypatch, tmp_path, {
        "robustness": {"pbo": {"pbo": 0.12, "n_configs": 8, "n_partitions": 70}},
    })

    assert stage["status"] == sc.STALE
    assert stage["robustness_status"] == sc.STALE
    assert stage["robustness_detail"] == "maximum_pbo threshold invalid"


@pytest.mark.parametrize("config_body", ["global:\n", "global: []\n"])
def test_universe_missing_pbo_threshold_config_is_stale(monkeypatch, tmp_path, config_body):
    cfg = tmp_path / "configs" / "model_metrics.yaml"
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text(config_body, encoding="utf-8")
    stage = _read_universe_stage(monkeypatch, tmp_path, {
        "robustness": {"pbo": {"pbo": 0.12, "n_configs": 8, "n_partitions": 70}},
    })

    assert stage["status"] == sc.STALE
    assert stage["robustness_status"] == sc.STALE
    assert stage["robustness_detail"] == "maximum_pbo threshold invalid"


def test_universe_pbo_without_partition_counts_is_stale(monkeypatch, tmp_path):
    stage = _read_universe_stage(monkeypatch, tmp_path, {
        "robustness": {"pbo": {"pbo": 0.12}},
    })

    assert stage["status"] == sc.STALE
    assert stage["robustness_detail"] == "pbo n_configs insufficient: -1 < 2"


def test_universe_missing_dsr_holm_bootstrap_or_fee_is_stale(monkeypatch, tmp_path):
    stage = _read_universe_stage(monkeypatch, tmp_path, {
        "robustness": {
            "pbo": {"pbo": 0.12, "n_configs": 8, "n_partitions": 70},
        },
        "corrections": {},
    })

    assert stage["status"] == sc.STALE
    assert stage["robustness_status"] == sc.STALE
    assert stage["robustness_detail"] == "dsr_by_cell missing"


def test_universe_gauntlet_survivor_failure_is_stale(monkeypatch, tmp_path):
    stage = _read_universe_stage(monkeypatch, tmp_path, {
        "robustness": {
            "dsr_by_cell": {"hyp_2_band_1.0_CPI": {"dsr": -0.2}},
            "pbo": {"pbo": 0.12, "n_configs": 8, "n_partitions": 70},
            "bootstrap_by_cell": {"hyp_2_band_1.0_CPI": {"ci_lower": -1.0}},
            "fee_stress_by_cell": {"hyp_2_band_1.0_CPI": {"fee_x2_pass": False}},
        },
        "corrections": {"CPI": {"holm": {"passed_slugs": ["hyp_2_band_1.0"]}}},
    })

    assert stage["status"] == sc.STALE
    assert stage["robustness_status"] == sc.STALE
    assert "gauntlet gates failed" in stage["robustness_detail"]


def test_universe_insufficient_pbo_reason_is_stale(monkeypatch, tmp_path):
    stage = _read_universe_stage(monkeypatch, tmp_path, {
        "robustness": {"pbo": {"pbo": None, "reason": "insufficient_events_for_cscv: 1 < 8"}},
    })

    assert stage["status"] == sc.STALE
    assert stage["robustness_status"] == sc.STALE
    assert stage["robustness_detail"] == "insufficient_events_for_cscv: 1 < 8"


def test_universe_non_embargo_skips_are_stale(monkeypatch, tmp_path):
    stage = _read_universe_stage(monkeypatch, tmp_path, {
        "units_skipped": 2,
        "skipped": [
            {"reason": "npz_missing"},
            {"reason": "empty_npz"},
        ],
    })

    assert stage["status"] == sc.STALE
    assert stage["skip_reason_counts"] == {"npz_missing": 1, "empty_npz": 1}
    assert stage["detail"] == "coverage skips: empty_npz=1, npz_missing=1"


def test_universe_embargo_only_skips_do_not_block_full_ok(monkeypatch, tmp_path):
    stage = _read_universe_stage(monkeypatch, tmp_path, {
        "units_skipped": 1,
        "skipped": [{"reason": "embargo_2026"}],
    })

    assert stage["status"] == sc.OK
    assert stage["skip_reason_counts"] == {"embargo_2026": 1}


def test_universe_stale_certification_stamp_is_stale(monkeypatch, tmp_path):
    stage = _read_universe_stage(monkeypatch, tmp_path, {
        "certification_stamp": {
            "status": "GREEN",
            "stale": True,
            "promotion_eligible": False,
            "promotion_label": "STALE_CERTIFICATION",
        },
    })

    assert stage["status"] == sc.STALE
    assert stage["certification_stale"] is True
    assert stage["promotion_eligible"] is False
    assert stage["detail"] == "certification_stamp stale=True"


def test_universe_missing_certification_stamp_is_stale(monkeypatch, tmp_path):
    stage = _read_universe_stage(monkeypatch, tmp_path, {
        "certification_stamp": None,
    })

    assert stage["status"] == sc.STALE
    assert stage["detail"] == "certification_stamp missing"


def test_universe_full_artifact_with_numeric_pbo_is_ok(monkeypatch, tmp_path):
    stage = _read_universe_stage(monkeypatch, tmp_path, {
        "unit_results": [
            {
                "event_id": "CPI_2024_09_11_TIGHT",
                "symbol": "MES.v.0",
                "latency_ms": 1.0,
                "error": None,
                "skip_reason": None,
                "hypotheses": [
                    {"hypothesis_id": 2, "hypothesis_name": "Stop-run exhaustion fade"},
                    {"hypothesis_id": 5, "hypothesis_name": "Liquidity vacuum continuation"},
                ],
            }
        ],
    })

    assert stage["status"] == sc.OK
    assert stage["scope"] == "full"
    assert stage["evaluated_model_rows"] == 2
    assert stage["robustness_status"] == sc.OK
    assert stage["pbo"] == 0.12


def test_pipeline_prefers_full_m6_artifact_when_present(monkeypatch, tmp_path):
    smoke = tmp_path / "research_cards" / "universe_M6_smoke" / "universe_result.json"
    full = tmp_path / "research_cards" / "universe_M6_full" / "universe_result.json"
    _write_universe_result(smoke, cli_args={
        "lane": "cme",
        "max_events": 1,
        "event_type": "CPI",
        "symbols": "MES.v.0",
        "bands_override": "6.255764",
    })
    _write_universe_result(full, cli_args=_full_universe_cli_args(tmp_path))
    monkeypatch.setattr(paths, "REPO", tmp_path)
    monkeypatch.setattr(paths, "STAGE_B_RESULT", smoke)
    monkeypatch.setattr(paths, "M6_RESULT", smoke)
    monkeypatch.setattr(paths, "M6_FULL_RESULT", full)
    _point_non_universe_pipeline_paths(monkeypatch, tmp_path)
    monkeypatch.setattr(pipeline_agg, "_latency_evidence", lambda **_: {"status": sc.OK, "live_readiness_status": sc.STALE})

    p = pipeline_agg.build()

    gauntlet = next(s for s in p["stages"] if s["id"] == "gauntlet_b")
    m6 = next(s for s in p["stages"] if s["id"] == "m6_gate")
    assert gauntlet["artifact"].replace("\\", "/") == "research_cards/universe_M6_full/universe_result.json"
    assert m6["artifact"].replace("\\", "/") == "research_cards/universe_M6_full/universe_result.json"
    assert gauntlet["scope"] == "full"
    assert m6["scope"] == "full"


def test_pipeline_falls_back_to_smoke_when_full_m6_absent(monkeypatch, tmp_path):
    smoke = tmp_path / "research_cards" / "universe_M6_smoke" / "universe_result.json"
    full = tmp_path / "research_cards" / "universe_M6_full" / "universe_result.json"
    _write_universe_result(smoke, cli_args={
        "lane": "cme",
        "max_events": 1,
        "event_type": "CPI",
        "symbols": "MES.v.0",
        "bands_override": "6.255764",
    })
    monkeypatch.setattr(paths, "REPO", tmp_path)
    monkeypatch.setattr(paths, "STAGE_B_RESULT", smoke)
    monkeypatch.setattr(paths, "M6_RESULT", smoke)
    monkeypatch.setattr(paths, "M6_FULL_RESULT", full)
    _point_non_universe_pipeline_paths(monkeypatch, tmp_path)
    monkeypatch.setattr(pipeline_agg, "_latency_evidence", lambda **_: {"status": sc.OK, "live_readiness_status": sc.STALE})

    p = pipeline_agg.build()

    gauntlet = next(s for s in p["stages"] if s["id"] == "gauntlet_b")
    assert gauntlet["artifact"].replace("\\", "/") == "research_cards/universe_M6_smoke/universe_result.json"
    assert gauntlet["status"] == sc.STALE
    assert gauntlet["scope"] == "smoke"


def test_pipeline_active_sweep_masks_only_universe_placeholders(monkeypatch, tmp_path):
    smoke = tmp_path / "research_cards" / "universe_M6_smoke" / "universe_result.json"
    full = tmp_path / "research_cards" / "universe_M6_full" / "universe_result.json"
    _write_universe_result(smoke, cli_args={
        "lane": "cme",
        "max_events": 1,
        "event_type": "CPI",
        "symbols": "MES.v.0",
        "bands_override": "6.255764",
    })
    capture = tmp_path / "runtime" / "chi404" / "baseline" / "latest_capture.json"
    feature = tmp_path / "runtime" / "workbench" / "feature_fabric_manifest.json"
    stage_a = tmp_path / "research_cards" / "stage_a_full" / "stage_a_result.json"
    survivors = tmp_path / "research_cards" / "stage_a_full" / "stage_a_survivors.json"
    capture.parent.mkdir(parents=True, exist_ok=True)
    feature.parent.mkdir(parents=True, exist_ok=True)
    stage_a.parent.mkdir(parents=True, exist_ok=True)
    capture.write_text(json.dumps({"host_id": "CHI404", "captured_at": paths.now_iso(), "known_gaps": [], "drift_warnings": []}), encoding="utf-8")
    feature.write_text(json.dumps({"generated_at_utc": paths.now_iso(), "row_count": 1, "rejected_count": 0}), encoding="utf-8")
    stage_a.write_text(json.dumps({"units_run": 1, "units_errored": 0, "units_skipped": 0, "cells": [], "certification_stamp": {"status": "GREEN"}}), encoding="utf-8")
    survivors.write_text(json.dumps([{"hypothesis_id": 2}]), encoding="utf-8")
    job_dir = tmp_path / "runtime" / "lifecycle" / "jobs" / "running"
    job_dir.mkdir(parents=True, exist_ok=True)
    (job_dir / "cme_m6_universe_sweep_cockpit_1.json").write_text(
        json.dumps({"job_id": "cme_m6_universe_sweep_cockpit_1", "model_id": "cme_m6_universe_sweep"}),
        encoding="utf-8",
    )
    monkeypatch.setattr(paths, "REPO", tmp_path)
    monkeypatch.setattr(paths, "CAPTURE_BASELINE", capture)
    monkeypatch.setattr(paths, "FEATURE_FABRIC", feature)
    monkeypatch.setattr(paths, "STAGE_A_RESULT", stage_a)
    monkeypatch.setattr(paths, "STAGE_A_SURVIVORS", survivors)
    monkeypatch.setattr(paths, "STAGE_B_RESULT", smoke)
    monkeypatch.setattr(paths, "M6_RESULT", smoke)
    monkeypatch.setattr(paths, "M6_FULL_RESULT", full)
    monkeypatch.setattr(paths, "ALPHA_CME_SPEC", tmp_path / "missing.md")
    loaders._cache.clear()
    monkeypatch.setattr(pipeline_agg, "_latency_evidence", lambda **_: {"status": sc.OK, "live_readiness_status": sc.STALE})

    p = pipeline_agg.build()

    assert p["health"] == sc.GREEN
    assert {s["id"] for s in p["stages"] if s["status"] != sc.OK} == {"gauntlet_b", "m6_gate"}


def test_pipeline_active_sweep_does_not_hide_missing_prerequisites(monkeypatch, tmp_path):
    job_dir = tmp_path / "runtime" / "lifecycle" / "jobs" / "running"
    job_dir.mkdir(parents=True, exist_ok=True)
    (job_dir / "cme_m6_universe_sweep_cockpit_1.json").write_text(
        json.dumps({"job_id": "cme_m6_universe_sweep_cockpit_1", "model_id": "cme_m6_universe_sweep"}),
        encoding="utf-8",
    )
    monkeypatch.setattr(paths, "REPO", tmp_path)
    _point_non_universe_pipeline_paths(monkeypatch, tmp_path)
    monkeypatch.setattr(paths, "STAGE_B_RESULT", tmp_path / "missing_stageb.json")
    monkeypatch.setattr(paths, "M6_RESULT", tmp_path / "missing_m6.json")
    monkeypatch.setattr(paths, "M6_FULL_RESULT", tmp_path / "missing_full.json")
    monkeypatch.setattr(paths, "ALPHA_CME_SPEC", tmp_path / "missing.md")
    loaders._cache.clear()
    monkeypatch.setattr(pipeline_agg, "_latency_evidence", lambda **_: {"status": sc.OK, "live_readiness_status": sc.STALE})

    p = pipeline_agg.build()

    assert p["health"] == sc.AMBER
    assert any(s["id"] == "capture" and s["status"] == sc.MISSING for s in p["stages"])


def test_pipeline_latency_evidence_preserves_unmeasured_defensive_ack(monkeypatch, tmp_path):
    _write_latency_evidence_files(tmp_path)
    monkeypatch.setattr(paths, "REPO", tmp_path)
    _point_latency_paths(monkeypatch, tmp_path)

    evidence = pipeline_agg._latency_evidence()

    assert evidence["status"] == sc.OK
    assert evidence["ack_p99_us"] == 6255.764
    assert evidence["m6_band_ms"] == 6.255764
    assert evidence["offensive_engine_us"] == 15.3
    assert evidence["offensive_baseline_tick_to_send_us"] == 23.314
    assert evidence["offensive_latest_decision_to_send_p99_us"] == 38.693
    assert evidence["defensive_cancel_to_send_us"] == 14.677
    assert evidence["defensive_cancel_ack_status"] == "UNMEASURED"
    assert evidence["live_readiness_status"] == sc.STALE


def test_pipeline_latency_gate_is_non_green_when_defensive_ack_required(monkeypatch, tmp_path):
    _write_latency_evidence_files(tmp_path)
    monkeypatch.setattr(paths, "REPO", tmp_path)
    _point_latency_paths(monkeypatch, tmp_path)

    evidence = pipeline_agg._latency_evidence(defensive_ack_required=True)

    assert evidence["status"] == sc.STALE
    assert evidence["detail"] == "defensive cancel ack required but unmeasured"


def test_alerts_quiet_when_healthy(monkeypatch, tmp_path):
    # Repoint every alert source at empty/missing → alert feed must be quiet.
    for attr in ("SLOW_TIER_PROBLEMS", "CERT_REGISTRY", "LATENCY_SUMMARY", "CAPTURE_BASELINE"):
        monkeypatch.setattr(paths, attr, tmp_path / f"{attr}.json")
    _write_options_spec(tmp_path, "**FIXED**")
    monkeypatch.setattr(paths, "REPO", tmp_path)
    report_path = tmp_path / "data_doctor_report.json"
    report_path.write_text(
        json.dumps({
            "run_utc": paths.now_iso(),
            "checks": _options_ok_checks(),
            "options_lane": {"name": "options_lane", "status": "OK", "detail": "ok"},
        }),
        encoding="utf-8",
    )
    monkeypatch.setattr(paths, "DATA_DOCTOR_REPORT", report_path)
    a = ZONES["alerts"]()
    assert a["count"] == 0
    assert a["health"] == "green"


# --- API + auth -------------------------------------------------------------

def test_api_requires_token_when_configured(monkeypatch):
    monkeypatch.setenv("COCKPIT_VIEW_TOKEN", "secret-view")
    monkeypatch.delenv("COCKPIT_CONTROL_TOKEN", raising=False)
    client = TestClient(app)  # no context => watcher/lifespan not started
    # No token from non-loopback testclient → 401.
    assert client.get("/api/pipeline").status_code == 401
    # Correct bearer → 200.
    r = client.get("/api/pipeline", headers={"Authorization": "Bearer secret-view"})
    assert r.status_code == 200
    assert r.json()["zone"] == "pipeline"


def test_health_open():
    client = TestClient(app)
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_spa_fallback_for_client_routes():
    client = TestClient(app)
    for route in ("/chat", "/models", "/lifecycle"):
        r = client.get(route)
        assert r.status_code == 200, route
        assert "text/html" in r.headers.get("content-type", ""), route
        assert '<div id="root">' in r.text, route
    # API + WS routes are NOT shadowed by the SPA catch-all
    h = client.get("/api/health")
    assert h.status_code == 200 and h.json()["status"] == "ok"
    # The GET-only catch-all must not capture the POST /api/chat route either
    # (no view token configured here → require_view 401, never an HTML body).
    chat = client.post("/api/chat", json={"query": "x"})
    assert "text/html" not in chat.headers.get("content-type", "")


def test_spa_catch_all_blocks_path_traversal():
    # The SPA fallback must never serve a file outside dist. URL-encoded `../`
    # is NOT normalized by the client, so it reaches the handler verbatim — the
    # resolve()+containment guard must reject it (else: arbitrary file read of
    # backend source / a .env with credentials).
    client = TestClient(app)
    evil = [
        "/..%2f..%2fbackend%2fvault_rag.py",
        "/..%2f..%2fbackend%2fmain.py",
        "/..%2f..%2f..%2f..%2f.env",
    ]
    for path in evil:
        r = client.get(path)
        # Either a clean 404 (no dist) or the index.html SPA fallback — never
        # the contents of a backend source / secrets file.
        assert "Keyword retrieval over the Obsidian vault" not in r.text, path
        assert "FastAPI aggregation service" not in r.text, path
        if r.status_code == 200:
            assert "text/html" in r.headers.get("content-type", ""), path


def test_rate_limit_ignores_xff_without_trusted_proxy(monkeypatch):
    monkeypatch.setattr(cockpit_main, "_RL_TRUST_PROXY", True)
    monkeypatch.setattr(cockpit_main, "_RL_TRUSTED_PROXIES", set())
    ip = cockpit_main._client_ip_for_rate_limit("testclient", "1.2.3.4")
    assert ip == "testclient"


def test_rate_limit_honors_xff_only_from_allowlisted_proxy(monkeypatch):
    monkeypatch.setattr(cockpit_main, "_RL_TRUST_PROXY", True)
    monkeypatch.setattr(cockpit_main, "_RL_TRUSTED_PROXIES", {"127.0.0.1"})
    assert cockpit_main._client_ip_for_rate_limit("127.0.0.1", "1.2.3.4, 127.0.0.1") == "1.2.3.4"
    assert cockpit_main._client_ip_for_rate_limit("testclient", "1.2.3.4, testclient") == "testclient"


def test_control_rejects_remote_origin():
    # TestClient origin is non-loopback ("testclient") → control forbidden.
    client = TestClient(app)
    r = client.post("/api/control/job", json={"name": "feature_rebuild", "confirm": True})
    assert r.status_code == 403


def test_control_status_lists_cme_m6_sweep(monkeypatch):
    from apps.cockpit.backend import auth

    monkeypatch.setattr(auth, "_LOOPBACK", {"testclient"})
    client = TestClient(app)
    r = client.get("/api/control/status")
    assert r.status_code == 200
    assert "cme_m6_universe_sweep" in r.json()["jobs"]


def test_control_status_keeps_active_sweep_visible(monkeypatch):
    from apps.cockpit.backend import auth

    active = {
        "job_id": "cme_m6_universe_sweep_cockpit_1",
        "model_id": "cme_m6_universe_sweep",
        "host": "laptop",
        "state": "pending",
    }
    old_done = [
        {"job_id": f"old_{i}", "model_id": "old", "host": "laptop", "state": "done"}
        for i in range(30)
    ]
    monkeypatch.setattr(auth, "_LOOPBACK", {"testclient"})
    monkeypatch.setattr(control, "_all_jobs", lambda: [active, *old_done])
    client = TestClient(app)

    r = client.get("/api/control/status")

    assert r.status_code == 200
    tracked = r.json()["tracked_jobs"]
    assert any(j["job_id"] == active["job_id"] for j in tracked)


def test_control_rejects_m6_sweep_when_exec_off(monkeypatch):
    from apps.cockpit.backend import auth

    monkeypatch.setattr(auth, "_LOOPBACK", {"testclient"})
    monkeypatch.setattr(control, "_exec_enabled", lambda: False)
    client = TestClient(app)

    r = client.post("/api/control/job", json={"name": "cme_m6_universe_sweep", "confirm": True})

    assert r.status_code == 403
    assert "COCKPIT_CONTROL_EXEC=1" in r.json()["detail"]


def test_control_rejects_duplicate_active_m6_sweep(monkeypatch):
    from apps.cockpit.backend import auth
    from lifecycle_orchestrator.src import job_runner

    active = {
        "job_id": "cme_m6_universe_sweep_cockpit_1",
        "model_id": "cme_m6_universe_sweep",
        "host": "laptop",
        "state": "running",
    }
    def duplicate(*_args, **_kwargs):
        raise job_runner.DuplicateActiveJob(active)

    monkeypatch.setattr(auth, "_LOOPBACK", {"testclient"})
    monkeypatch.setattr(control, "_exec_enabled", lambda: True)
    monkeypatch.setattr(job_runner, "enqueue_singleton", duplicate)
    client = TestClient(app)

    r = client.post("/api/control/job", json={"name": "cme_m6_universe_sweep", "confirm": True})

    assert r.status_code == 409
    assert active["job_id"] in r.json()["detail"]


def test_control_cme_m6_sweep_command_is_full_scope():
    spec = control._job_cmd()["cme_m6_universe_sweep"]
    args = spec["command"]["args"]

    assert spec["host"] == "laptop"
    assert spec["command"]["entry"].endswith("run_event_universe.py")
    assert args == [
        "--lane", "cme",
        "--bands", "6.255764",
        "--symbols", "MES.v.0,MNQ.v.0,ES.v.0,NQ.v.0,ZN.v.0,ZB.v.0,RTY.v.0",
        "--events-csv", "packages/data_system/config/events.csv",
        "--from-stage-a", "research_cards/stage_a_full/stage_a_survivors.json",
        "--out", "research_cards/universe_M6_full",
        "--workers", "12",
    ]
    assert "--max-events" not in args
    assert "--event-type" not in args
    assert "--cells" not in args
    assert "--shard" not in args


# --- notifier (push) --------------------------------------------------------

def test_push_notifies_only_on_new_problem(monkeypatch, tmp_path):
    from apps.cockpit.backend import push

    monkeypatch.setattr(paths, "ALERT_STATE", tmp_path / "alert_state.json")
    # No channel configured → notify is a no-op but diff/persist still works.
    monkeypatch.delenv("NTFY_URL", raising=False)
    monkeypatch.delenv("COCKPIT_NOTIFY_WEBHOOK", raising=False)

    zone = {"alerts": [{"id": "cert-red", "severity": "crit", "source": "certification", "message": "RED"}]}
    first = push.process_alerts(zone)
    assert first == ["cert-red"]          # new problem detected
    second = push.process_alerts(zone)
    assert second == []                    # same standing problem → no re-notify
    # Cleared then recurs → notifies again.
    push.process_alerts({"alerts": []})
    third = push.process_alerts(zone)
    assert third == ["cert-red"]


def test_lifecycle_zone_empty(monkeypatch, tmp_path):
    monkeypatch.setattr(paths, "MODEL_LIFECYCLE", tmp_path / "absent.json")
    z = ZONES["lifecycle"]()
    assert z["registered"] is False
    assert z["health"] == "green"
    assert z["total_models"] == 0


def test_lifecycle_zone_populated_and_alerts(monkeypatch, tmp_path):
    import json
    reg = tmp_path / "model_lifecycle.json"
    reg.write_text(json.dumps({
        "models": {
            "MES_X": {"current_state": "LIVE", "hypothesis_id": 1, "symbol": "MES", "current_state_since": "2026-06-12T00:00:00+00:00"},
            "MGC_Y": {"current_state": "QUARANTINED", "hypothesis_id": 35, "symbol": "MGC",
                       "demotion": {"reason": "feature_training_domain"}, "current_state_since": "2026-06-12T01:00:00+00:00"},
            "MCL_Z": {"current_state": "DEGRADED", "hypothesis_id": 7, "symbol": "MCL",
                       "reentry_routing": {"route": "param_tweak"}, "current_state_since": "2026-06-12T02:00:00+00:00"},
        }
    }))
    monkeypatch.setattr(paths, "MODEL_LIFECYCLE", reg)
    z = ZONES["lifecycle"]()
    assert z["total_models"] == 3 and z["live"] == 1
    assert z["funnel"]["QUARANTINED"] == 1 and z["funnel"]["DEGRADED"] == 1
    assert z["health"] == "red"  # a QUARANTINED model
    # alerts feed surfaces the quarantine (crit) + degraded (warn)
    a = ZONES["alerts"]()
    ids = {al["id"] for al in a["alerts"]}
    assert "lifecycle-quar-MGC_Y" in ids
    assert any(al["severity"] == "crit" and al["source"] == "lifecycle" for al in a["alerts"])


def test_autonomy_zone_disabled_by_default(monkeypatch):
    monkeypatch.delenv("HFT3_AUTONOMY_ENABLED", raising=False)
    monkeypatch.delenv("HFT3_AUTONOMY_KILL", raising=False)
    z = ZONES["autonomy"]()
    assert z["available"] is True
    assert z["master_enabled"] is False     # two-key OFF by default
    assert z["can_arm_live"] is False
    assert z["health"] in ("green", "amber")  # green when unfrozen + chain ok


def test_push_no_channel_returns_false(monkeypatch):
    from apps.cockpit.backend import push

    monkeypatch.delenv("NTFY_URL", raising=False)
    monkeypatch.delenv("COCKPIT_NOTIFY_WEBHOOK", raising=False)
    assert push.channel() is None
    assert push.notify("t", "m") is False


# --- Lanes block ------------------------------------------------------------

def test_lanes_registered_contains_cme_options():
    """system zone lanes.registered must include 'cme_options'; its capability profile
    must be research_only and model_id_prefixes must contain a 'FOPT_' entry."""
    z = ZONES["system"]()
    lanes = z.get("lanes", {})
    assert "cme_options" in lanes.get("registered", []), \
        f"cme_options missing from registered: {lanes.get('registered')}"
    items = {it["lane"]: it for it in lanes.get("items", [])}
    cme_opts = items.get("cme_options", {})
    cp = cme_opts.get("capability_profile", {})
    assert cp.get("research_only") is True, f"cme_options research_only not True: {cp}"
    prefixes = cme_opts.get("model_id_prefixes", [])
    assert any("FOPT_" in p for p in prefixes), \
        f"FOPT_ prefix not in model_id_prefixes: {prefixes}"


def test_lanes_options_defect_ledger_open_blocks_shadow_live_only():
    z = ZONES["system"]()
    defects = z.get("lanes", {}).get("cme_options_defects", {})
    assert defects.get("status") == "fail"
    assert defects.get("open_count", 0) >= 1
    assert "o-a" in set(defects.get("open_ids", []))
    assert z.get("health") == "green"
    assert z.get("health_scope") == "research_replay"
    assert z.get("shadow_live_blockers", {}).get("cme_options_defects") == "fail"


def test_lanes_missing_data_doctor_report_is_graceful(monkeypatch, tmp_path):
    """Pointing DATA_DOCTOR_REPORT at a nonexistent file -> cme_options_data.status==missing;
    system zone research/replay health remains green while the options card stays red."""
    monkeypatch.setattr(paths, "DATA_DOCTOR_REPORT", tmp_path / "no_report.json")
    z = ZONES["system"]()
    lanes = z.get("lanes", {})
    cod = lanes.get("cme_options_data", {})
    from apps.cockpit.backend import schemas as sc
    assert cod.get("status") == sc.MISSING, f"expected missing, got {cod.get('status')}"
    assert z.get("health") == sc.GREEN
    _json_roundtrip(z)


def test_lanes_options_warn_is_not_ok(monkeypatch, tmp_path):
    report_path = tmp_path / "data_doctor_report.json"
    report = {
        "run_utc": paths.now_iso(),
        "checks": [
            {"name": "options-fixing-mbo", "status": "OK", "detail": "10 files"},
            {"name": "options-statistics", "status": "WARN", "detail": "missing statistics"},
        ],
        "options_lane": {"name": "options_lane", "status": "WARN", "detail": "statistics missing"},
        "failed": 0,
        "warned": 1,
    }
    report_path.write_text(json.dumps(report), encoding="utf-8")
    monkeypatch.setattr(paths, "DATA_DOCTOR_REPORT", report_path)
    z = ZONES["system"]()
    cod = z.get("lanes", {}).get("cme_options_data", {})
    from apps.cockpit.backend import schemas as sc
    assert cod.get("status") in {sc.MISSING, sc.FAIL}
    assert "options-datasets" in cod.get("missing_checks", [])
    assert z.get("health") == sc.GREEN


def test_lanes_partial_options_report_missing_mandatory_checks(monkeypatch, tmp_path):
    lake = tmp_path / "options"
    lake.mkdir(parents=True)
    report_path = tmp_path / "data_doctor_report.json"
    present = [
        {"name": "options-datasets", "status": "OK", "detail": "ok"},
        {"name": "options-fixing-mbo", "status": "OK", "detail": "ok"},
        {"name": "options-fixing-coverage", "status": "OK", "detail": "ok"},
    ]
    report_path.write_text(
        json.dumps({
            "run_utc": paths.now_iso(),
            "checks": present,
            "options_lane": {"name": "options_lane", "status": "OK", "detail": "ok"},
        }),
        encoding="utf-8",
    )
    monkeypatch.setattr(paths, "DATA_DOCTOR_REPORT", report_path)
    monkeypatch.setattr(paths, "OPTIONS_LAKE_ROOT", lake)

    z = ZONES["system"]()
    cod = z.get("lanes", {}).get("cme_options_data", {})

    from apps.cockpit.backend import schemas as sc
    assert cod.get("status") == sc.MISSING
    assert cod.get("missing_checks") == ["options-ohlcv", "options-definitions", "options-statistics"]


def test_lanes_synthetic_data_doctor_report(monkeypatch, tmp_path):
    """A synthetic data_doctor report with options-* checks (incl. one FAIL) and an
    options_lane summary -> cme_options_data status==fail, summary lifted, gap data visible."""
    report_path = tmp_path / "data_doctor_report.json"
    report = {
        "run_utc": paths.now_iso(),
        "checks": [
            {"name": "options-fixing-coverage", "status": "FAIL",
             "detail": "missing 2 expiry windows", "gap_count": 2, "stale_gap_count": 1},
            {"name": "options-fixing-mbo", "status": "OK", "detail": "10 quotes + 5 trades"},
            {"name": "options-ohlcv", "status": "OK", "detail": "42 files"},
        ],
        "options_lane": {"name": "options_lane", "status": "OK", "detail": "options_lane summary"},
        "failed": 1,
        "warned": 0,
    }
    report_path.write_text(json.dumps(report), encoding="utf-8")
    monkeypatch.setattr(paths, "DATA_DOCTOR_REPORT", report_path)
    z = ZONES["system"]()
    lanes = z.get("lanes", {})
    cod = lanes.get("cme_options_data", {})
    from apps.cockpit.backend import schemas as sc
    assert cod.get("status") == sc.FAIL, f"expected fail, got {cod.get('status')}"
    # summary block must be lifted
    summary = cod.get("summary")
    assert summary is not None and summary.get("name") == "options_lane", \
        f"summary not lifted: {summary}"
    # options- checks present
    checks = cod.get("checks", [])
    assert any(c["name"] == "options-fixing-coverage" for c in checks), \
        f"options-fixing-coverage not in checks: {checks}"
    # gap detail accessible
    gap_check = next((c for c in checks if c.get("name") == "options-fixing-coverage"), None)
    assert gap_check is not None and gap_check.get("gap_count") == 2, \
        f"gap check missing or no detail: {gap_check}"
    _json_roundtrip(z)


def test_system_view_reads_real_options_gap_summary():
    src = (paths.REPO / "apps/cockpit/frontend/src/views/SystemView.tsx").read_text(encoding="utf-8")
    assert "summary[\"expiry_coverage\"]" in src
    assert "expiryCoverage?.[\"gap_count\"]" in src


def test_system_view_renders_options_defect_details_and_budget_status():
    src = (paths.REPO / "apps/cockpit/frontend/src/views/SystemView.tsx").read_text(encoding="utf-8")
    assert 'g(defects, "open_ids")' in src
    assert 'join(", ")' in src
    assert '["defect ids", openIds]' in src
    assert '["defect artifact", defectArtifact]' in src
    assert '["defect reason", defectReason]' in src
    assert '<Card title="Databento" status={String(g(db, "status") ?? "unknown")}' in src


def test_databento_manifest_missing_is_not_ok(monkeypatch, tmp_path):
    monkeypatch.setattr(paths, "DATABENTO_MANIFEST", tmp_path / "missing_manifest.parquet")
    monkeypatch.setattr(paths, "DATABENTO_RECEIPT", tmp_path / "missing_receipt.json")
    z = ZONES["system"]()
    db = z["databento"]
    from apps.cockpit.backend import schemas as sc
    assert db["status"] == sc.MISSING
    assert db["total_used"] is None
    assert db["remaining"] is None
    assert db["remaining_authoritative"] is False
    assert z["health"] in {sc.AMBER, sc.RED}


def test_alerts_missing_data_doctor_report(monkeypatch, tmp_path):
    monkeypatch.setattr(paths, "DATA_DOCTOR_REPORT", tmp_path / "no_report.json")
    for attr in ("SLOW_TIER_PROBLEMS", "CERT_REGISTRY", "LATENCY_SUMMARY",
                 "CAPTURE_BASELINE", "MODEL_LIFECYCLE"):
        monkeypatch.setattr(paths, attr, tmp_path / f"{attr}.json")
    a = ZONES["alerts"]()
    ids = {al["id"] for al in a["alerts"]}
    assert "lake-data-doctor-missing" in ids


def test_alerts_stale_data_doctor_report(monkeypatch, tmp_path):
    report_path = tmp_path / "data_doctor_report.json"
    report_path.write_text(json.dumps({"run_utc": "2020-01-01T00:00:00+00:00", "checks": []}),
                           encoding="utf-8")
    monkeypatch.setattr(paths, "DATA_DOCTOR_REPORT", report_path)
    for attr in ("SLOW_TIER_PROBLEMS", "CERT_REGISTRY", "LATENCY_SUMMARY",
                 "CAPTURE_BASELINE", "MODEL_LIFECYCLE"):
        monkeypatch.setattr(paths, attr, tmp_path / f"{attr}.json")
    a = ZONES["alerts"]()
    ids = {al["id"] for al in a["alerts"]}
    assert "lake-data-doctor-stale" in ids


def test_alerts_options_warn_check_alert(monkeypatch, tmp_path):
    report_path = tmp_path / "data_doctor_report.json"
    report = {
        "run_utc": paths.now_iso(),
        "checks": [
            {"name": "options-statistics", "status": "WARN",
             "detail": "statistics pending"},
        ],
        "failed": 0,
        "warned": 1,
    }
    report_path.write_text(json.dumps(report), encoding="utf-8")
    monkeypatch.setattr(paths, "DATA_DOCTOR_REPORT", report_path)
    for attr in ("SLOW_TIER_PROBLEMS", "CERT_REGISTRY", "LATENCY_SUMMARY",
                 "CAPTURE_BASELINE", "MODEL_LIFECYCLE"):
        monkeypatch.setattr(paths, attr, tmp_path / f"{attr}.json")
    a = ZONES["alerts"]()
    ids = {al["id"] for al in a["alerts"]}
    assert "lake-options-statistics" in ids
    assert any(
        al["severity"] == "crit" and al["source"] == "cme_options_backfill"
        for al in a["alerts"]
    )


def test_alerts_missing_mandatory_options_checks(monkeypatch, tmp_path):
    report_path = tmp_path / "data_doctor_report.json"
    report = {
        "run_utc": paths.now_iso(),
        "checks": [
            {"name": "options-datasets", "status": "OK", "detail": "ok"},
            {"name": "options-fixing-mbo", "status": "OK", "detail": "ok"},
        ],
        "options_lane": {"name": "options_lane", "status": "OK", "detail": "ok"},
    }
    report_path.write_text(json.dumps(report), encoding="utf-8")
    monkeypatch.setattr(paths, "DATA_DOCTOR_REPORT", report_path)
    for attr in ("SLOW_TIER_PROBLEMS", "CERT_REGISTRY", "LATENCY_SUMMARY",
                 "CAPTURE_BASELINE", "MODEL_LIFECYCLE"):
        monkeypatch.setattr(paths, attr, tmp_path / f"{attr}.json")
    a = ZONES["alerts"]()
    ids = {al["id"] for al in a["alerts"]}
    assert "lake-options-datasets-missing" not in ids
    assert "lake-options-fixing-coverage-missing" in ids
    assert "lake-options-ohlcv-missing" in ids
    assert "lake-options-definitions-missing" in ids
    assert "lake-options-statistics-missing" in ids
    assert all(al["severity"] == "crit" for al in a["alerts"])


def test_alerts_options_defect_ledger_open_is_not_runtime_alert():
    a = ZONES["alerts"]()
    ids = {al["id"] for al in a["alerts"]}
    assert "options-defect-ledger-open" not in ids


def test_alerts_options_fixing_coverage_alert(monkeypatch, tmp_path):
    """alerts zone with a failing options-fixing-coverage check -> alert id
    'lake-options-fixing-coverage' present in the alerts feed."""
    report_path = tmp_path / "data_doctor_report.json"
    report = {
        "run_utc": paths.now_iso(),
        "checks": [
            {"name": "options-fixing-coverage", "status": "FAIL",
             "detail": "missing 3 expiry windows"},
        ],
        "failed": 1,
        "warned": 0,
    }
    report_path.write_text(json.dumps(report), encoding="utf-8")
    monkeypatch.setattr(paths, "DATA_DOCTOR_REPORT", report_path)
    # silence unrelated alert sources
    for attr in ("SLOW_TIER_PROBLEMS", "CERT_REGISTRY", "LATENCY_SUMMARY",
                 "CAPTURE_BASELINE", "MODEL_LIFECYCLE"):
        monkeypatch.setattr(paths, attr, tmp_path / f"{attr}.json")
    a = ZONES["alerts"]()
    ids = {al["id"] for al in a["alerts"]}
    assert "lake-options-fixing-coverage" in ids, \
        f"lake-options-fixing-coverage not in alert ids: {ids}"
