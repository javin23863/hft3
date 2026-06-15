"""Cockpit backend tests — aggregator shape, graceful-missing, API auth.

Run from repo root:  python -m pytest apps/cockpit/backend/tests -q
"""
import importlib.util
import json
import os
import subprocess
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from apps.cockpit.backend import loaders, paths
from apps.cockpit.backend import control
from apps.cockpit.backend import schemas as sc
from apps.cockpit.backend import main as cockpit_main
from apps.cockpit.backend.aggregate import ZONES
from apps.cockpit.backend.aggregate import alerts as alerts_agg
from apps.cockpit.backend.aggregate import pipeline as pipeline_agg
from apps.cockpit.backend.aggregate import system as system_agg
from apps.cockpit.backend.main import app

VIEW_KEYS = {"zone", "generated_utc", "health"}
_REPO_ROOT = Path(__file__).resolve().parents[4]


def _load_run_event_universe():
    script = _REPO_ROOT / "scripts" / "run_event_universe.py"
    spec = importlib.util.spec_from_file_location("run_event_universe_contract", script)
    if spec is None or spec.loader is None:
        raise AssertionError(f"unable to load {script}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


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


def _stub_q001_ok(monkeypatch) -> None:
    payload = {
        "status": sc.OK,
        "q001_status": "INVENTORIED",
        "artifact": "runtime/data_audits/paid_data_inventory.json",
        "gaps": [],
    }
    monkeypatch.setattr(
        system_agg,
        "_q001_inventory",
        lambda: payload,
    )
    monkeypatch.setattr(alerts_agg, "_q001_inventory", lambda: payload)
    monkeypatch.setattr(pipeline_agg, "_q001_inventory", lambda: payload)


def _point_options_zone_ok(monkeypatch, root: Path) -> Path:
    _write_options_spec(root, "**FIXED**")
    lake = root / "options"
    lake.mkdir(parents=True)
    report_path = root / "data_doctor_report.json"
    report_path.write_text(
        json.dumps({
            "run_utc": paths.now_iso(),
            "checks": _options_ok_checks(),
            "options_lane": {"name": "options_lane", "status": "OK", "detail": "ok"},
        }),
        encoding="utf-8",
    )
    monkeypatch.setattr(paths, "REPO", root)
    monkeypatch.setattr(paths, "DATA_DOCTOR_REPORT", report_path)
    monkeypatch.setattr(paths, "OPTIONS_LAKE_ROOT", lake)
    return report_path


def _silence_alert_sources(monkeypatch, root: Path) -> None:
    for attr in ("SLOW_TIER_PROBLEMS", "CERT_REGISTRY", "LATENCY_SUMMARY",
                 "CAPTURE_BASELINE", "MODEL_LIFECYCLE"):
        monkeypatch.setattr(paths, attr, root / f"{attr}.json")


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


def test_pipeline_has_seven_stages():
    p = ZONES["pipeline"]()
    ids = [s["id"] for s in p["stages"]]
    assert ids == [
        "capture",
        "feature_build",
        "stage_a",
        "q001_inventory",
        "gauntlet_b",
        "m6_gate",
        "promote",
    ]
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


def test_universe_declared_skip_reason_counts_prevent_runtime_double_count(monkeypatch, tmp_path):
    stage = _read_universe_stage(monkeypatch, tmp_path, {
        "units_skipped": 1,
        "skip_reason_counts": {"empty_npz": 1},
        "skipped": [
            {
                "event_id": "EMPTY_EVT",
                "event_type": "CPI",
                "release_date": "2024-04-10",
                "symbol": "MES.v.0",
                "latency_ms": 1.0,
                "reason": "empty_npz",
            },
        ],
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
            },
            {
                "event_id": "EMPTY_EVT",
                "symbol": "MES.v.0",
                "latency_ms": 1.0,
                "error": None,
                "skip_reason": "empty_npz",
                "hypotheses": [],
            },
        ],
    })

    assert stage["skip_reason_counts"] == {"empty_npz": 1}
    assert stage["detail"] == "coverage skips: empty_npz=1"


def test_universe_malformed_declared_skip_reason_counts_are_stale(monkeypatch, tmp_path):
    stage = _read_universe_stage(monkeypatch, tmp_path, {
        "units_skipped": 1,
        "skip_reason_counts": {"empty_npz": True},
        "skipped": [{"reason": "embargo_2026"}],
    })

    assert stage["status"] == sc.STALE
    assert stage["skip_reason_counts"] == {"malformed_skip_reason_counts": 1}
    assert stage["detail"] == "coverage skips: malformed_skip_reason_counts=1"


def test_universe_embargo_only_skips_do_not_block_full_ok(monkeypatch, tmp_path):
    stage = _read_universe_stage(monkeypatch, tmp_path, {
        "units_skipped": 1,
        "skipped": [{"reason": "embargo_2026"}],
    })

    assert stage["status"] == sc.OK
    assert stage["skip_reason_counts"] == {"embargo_2026": 1}


def test_universe_q001_accepted_gap_skips_do_not_block_full_ok(monkeypatch, tmp_path):
    manifest = tmp_path / "packages" / "data_system" / "config" / "mbo_pilot_basket_20260605_manifest.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(json.dumps({
        "no_market_windows": ["EIA_CRUDE_2024_12_25_TIGHT"],
        "partial_windows": [{
            "event_id": "FED_H41_2024_06_19_TIGHT",
            "missing_symbols": ["ES.v.0"],
            "reason": "symbol_absent_in_raw_after_redownload",
        }],
    }), encoding="utf-8")
    monkeypatch.setattr(pipeline_agg, "_q001_inventory", lambda: {
        "status": sc.OK,
        "available_data_scope_accepted": True,
    })
    stage = _read_universe_stage(monkeypatch, tmp_path, {
        "units_skipped": 2,
        "skip_reason_counts": {
            "no_market_data": 1,
            "symbol_absent_in_raw_after_redownload": 1,
        },
        "skipped": [
            {
                "event_id": "EIA_CRUDE_2024_12_25_TIGHT",
                "symbol": "MES.v.0",
                "reason": "no_market_data",
            },
            {
                "event_id": "FED_H41_2024_06_19_TIGHT",
                "symbol": "ES.v.0",
                "reason": "symbol_absent_in_raw_after_redownload",
            },
        ],
    })

    assert stage["status"] == sc.OK
    assert stage["skip_reason_counts"] == {
        "no_market_data": 1,
        "symbol_absent_in_raw_after_redownload": 1,
    }


def test_runner_universe_result_writer_feeds_cockpit_universe_stage(monkeypatch, tmp_path):
    manifest = tmp_path / "packages" / "data_system" / "config" / "mbo_pilot_basket_20260605_manifest.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(json.dumps({
        "no_market_windows": ["EIA_CRUDE_2024_12_25_TIGHT"],
        "partial_windows": [{
            "event_id": "FED_H41_2024_06_19_TIGHT",
            "missing_symbols": ["ES.v.0"],
            "reason": "symbol_absent_in_raw_after_redownload",
        }],
    }), encoding="utf-8")
    writer = _load_run_event_universe().write_universe_result
    artifact = writer(
        tmp_path / "research_cards" / "universe_contract",
        unit_results=[{
            "event_id": "CPI_2024_09_11_TIGHT",
            "event_type": "CPI",
            "release_date": "2024-09-11",
            "symbol": "MES.v.0",
            "latency_ms": 6.255764,
            "error": None,
            "skip_reason": None,
            "hypotheses": [
                {"hypothesis_id": 2, "hypothesis_name": "Stop-run exhaustion fade"}
            ],
        }],
        skipped=[
            {
                "event_id": "EIA_CRUDE_2024_12_25_TIGHT",
                "event_type": "EIA_CRUDE",
                "release_date": "2024-12-25",
                "symbol": "MES.v.0",
                "latency_ms": 6.255764,
                "reason": "no_market_data",
            },
            {
                "event_id": "FED_H41_2024_06_19_TIGHT",
                "event_type": "FED_H41",
                "release_date": "2024-06-19",
                "symbol": "ES.v.0",
                "latency_ms": 6.255764,
                "reason": "symbol_absent_in_raw_after_redownload",
            },
        ],
        aggregated={},
        corrections={"CPI": {"holm": {"passed_slugs": ["hyp_2_band_6.255764"]}}},
        robustness={
            "dsr_by_cell": {"hyp_2_band_6.255764_CPI": {"dsr": 0.8}},
            "pbo": {"pbo": 0.12, "n_configs": 2, "n_partitions": 16},
            "bootstrap_by_cell": {"hyp_2_band_6.255764_CPI": {"ci_lower": 1.5}},
            "fee_stress_by_cell": {"hyp_2_band_6.255764_CPI": {"fee_x2_pass": True}},
        },
        latency_bands=[6.255764],
        cli_args=_full_universe_cli_args(tmp_path),
        stamp={
            "status": "GREEN",
            "stale": False,
            "promotion_eligible": True,
            "promotion_label": "PROMOTION_ELIGIBLE_FROM_BACKTESTER_SIDE",
        },
        run_start_utc="2026-06-15T00:00:00+00:00",
        run_end_utc="2026-06-15T00:00:01+00:00",
        total_elapsed_s=1.0,
    )
    monkeypatch.setattr(paths, "REPO", tmp_path)
    monkeypatch.setattr(pipeline_agg, "_q001_inventory", lambda: {
        "status": sc.STALE,
        "available_data_scope_accepted": False,
    })

    stale_stage = pipeline_agg._universe_stage("gauntlet_b", "Gauntlet B", artifact)

    assert stale_stage["status"] == sc.STALE
    assert stale_stage["detail"] == (
        "coverage skips: no_market_data=1, symbol_absent_in_raw_after_redownload=1"
    )

    monkeypatch.setattr(pipeline_agg, "_q001_inventory", lambda: {
        "status": sc.OK,
        "available_data_scope_accepted": True,
    })

    stage = pipeline_agg._universe_stage("gauntlet_b", "Gauntlet B", artifact)

    assert stage["status"] == sc.OK
    assert stage["scope"] == "full"
    assert stage["skip_reason_counts"] == {
        "no_market_data": 1,
        "symbol_absent_in_raw_after_redownload": 1,
    }
    assert stage["units_skipped"] == 2
    assert stage["evaluated_model_rows"] == 1
    assert stage["robustness_status"] == sc.OK


def test_universe_q001_reason_strings_without_accepted_scope_stay_stale(monkeypatch, tmp_path):
    manifest = tmp_path / "packages" / "data_system" / "config" / "mbo_pilot_basket_20260605_manifest.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(json.dumps({
        "no_market_windows": ["EIA_CRUDE_2024_12_25_TIGHT"],
        "partial_windows": [],
    }), encoding="utf-8")
    monkeypatch.setattr(pipeline_agg, "_q001_inventory", lambda: {
        "status": sc.STALE,
        "available_data_scope_accepted": False,
    })
    stage = _read_universe_stage(monkeypatch, tmp_path, {
        "units_skipped": 1,
        "skip_reason_counts": {"no_market_data": 1},
        "skipped": [{
            "event_id": "EIA_CRUDE_2024_12_25_TIGHT",
            "symbol": "MES.v.0",
            "reason": "no_market_data",
        }],
    })

    assert stage["status"] == sc.STALE
    assert stage["detail"] == "coverage skips: no_market_data=1"


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
    monkeypatch.setattr(
        pipeline_agg,
        "_q001_inventory",
        lambda: {
            "status": sc.STALE,
            "q001_status": "INVENTORIED_WITH_WARNINGS",
            "artifact": "runtime/data_audits/paid_data_inventory.json",
            "missing_or_unavailable_slots": 211,
            "data_doctor_status": "WARN",
            "strict_mbo_gap_count": 507,
            "strict_mbo_stale_gap_count": 503,
            "gaps": [{"source": "mbo_pilot_manifest", "severity": "WARN"}],
        },
    )

    p = pipeline_agg.build()

    q001 = next(s for s in p["stages"] if s["id"] == "q001_inventory")
    assert p["health"] == sc.AMBER
    assert q001["status"] == sc.STALE
    assert q001["q001_status"] == "INVENTORIED_WITH_WARNINGS"
    assert q001["artifact"] == "runtime/data_audits/paid_data_inventory.json"
    assert q001["missing_or_unavailable_slots"] == 211
    assert q001["data_doctor_status"] == "WARN"
    assert q001["strict_mbo_gap_count"] == 507
    assert q001["strict_mbo_stale_gap_count"] == 503
    assert q001["gap_count"] == 1
    assert "q001_status=INVENTORIED_WITH_WARNINGS" in q001["detail"]
    assert {s["id"] for s in p["stages"] if s["status"] != sc.OK} == {
        "q001_inventory",
        "gauntlet_b",
        "m6_gate",
    }

    _stub_q001_ok(monkeypatch)
    p = pipeline_agg.build()

    assert p["health"] == sc.GREEN
    assert {s["id"] for s in p["stages"] if s["status"] != sc.OK} == {"gauntlet_b", "m6_gate"}

    monkeypatch.setattr(
        pipeline_agg,
        "_q001_inventory",
        lambda: {
            "status": sc.UNKNOWN,
            "q001_status": "INVENTORIED",
            "artifact": "runtime/data_audits/paid_data_inventory.json",
            "gaps": [],
        },
    )
    p = pipeline_agg.build()

    assert p["health"] == sc.AMBER
    assert {s["id"] for s in p["stages"] if s["status"] != sc.OK} == {
        "q001_inventory",
        "gauntlet_b",
        "m6_gate",
    }


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
    _stub_q001_ok(monkeypatch)
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


def test_lanes_options_defect_ledger_open_blocks_shadow_live_only(monkeypatch):
    _stub_q001_ok(monkeypatch)
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
    _stub_q001_ok(monkeypatch)
    monkeypatch.setattr(paths, "DATA_DOCTOR_REPORT", tmp_path / "no_report.json")
    z = ZONES["system"]()
    lanes = z.get("lanes", {})
    cod = lanes.get("cme_options_data", {})
    from apps.cockpit.backend import schemas as sc
    assert cod.get("status") == sc.MISSING, f"expected missing, got {cod.get('status')}"
    assert z.get("health") == sc.GREEN
    _json_roundtrip(z)


def test_lanes_options_warn_is_not_ok(monkeypatch, tmp_path):
    _stub_q001_ok(monkeypatch)
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


def test_system_q001_inventory_warnings_are_non_green(monkeypatch, tmp_path):
    artifact = tmp_path / "runtime" / "data_audits" / "paid_data_inventory.json"
    artifact.parent.mkdir(parents=True)
    artifact.write_text(
        json.dumps({
            "q001_cme_data_inventory": {
                "status": "INVENTORIED_WITH_WARNINGS",
                "futures": {
                    "mbo_pilot_basket": {
                        "missing_or_unavailable_slots": 211,
                    },
                },
                "options": {
                    "data_doctor_status": "WARN",
                    "warn_checks": [{"name": "options-fixing-mbo-coverage", "status": "WARN"}],
                    "fail_checks": [],
                    "options_lane": {
                        "expiry_coverage": {
                            "strict_mbo_gap_count": 507,
                            "strict_mbo_stale_gap_count": 503,
                        },
                    },
                },
                "gaps": [
                    {"source": "mbo_pilot_manifest", "severity": "WARN"},
                    {"source": "data_doctor", "severity": "WARN"},
                ],
            },
        }),
        encoding="utf-8",
    )
    monkeypatch.setattr(paths, "REPO", tmp_path)
    monkeypatch.setattr(system_agg, "_latency", lambda: {"status": sc.OK, "live_arm_status": sc.OK})
    monkeypatch.setattr(system_agg, "_slow_tier", lambda: {"status": sc.OK})
    monkeypatch.setattr(system_agg, "_certification", lambda: {"status": sc.OK})
    monkeypatch.setattr(system_agg, "_databento", lambda: {"status": sc.OK})
    monkeypatch.setattr(system_agg, "_capture", lambda: {"status": sc.OK})
    monkeypatch.setattr(
        system_agg,
        "_lanes",
        lambda: {
            "status": sc.OK,
            "cme_options_data": {"status": sc.OK},
            "cme_options_defects": {"status": sc.OK},
        },
    )

    z = system_agg.build()
    q001 = z["q001_inventory"]

    assert z["health"] == sc.AMBER
    assert q001["status"] == sc.STALE
    assert q001["status"] != sc.OK
    assert q001["q001_status"] == "INVENTORIED_WITH_WARNINGS"
    assert q001["artifact"].replace("\\", "/") == "runtime/data_audits/paid_data_inventory.json"
    assert q001["missing_or_unavailable_slots"] == 211
    assert q001["data_doctor_status"] == "WARN"
    assert q001["strict_mbo_gap_count"] == 507
    assert q001["strict_mbo_stale_gap_count"] == 503
    assert len(q001["gaps"]) == 2
    _json_roundtrip(z)


def test_system_q001_owner_accepted_available_data_scope_is_green_with_skips(monkeypatch, tmp_path):
    decision = tmp_path / "docs" / "project" / "q001_owner_decision.json"
    decision.parent.mkdir(parents=True)
    decision.write_text(
        json.dumps({
            "status": "ACCEPTED_AVAILABLE_DATA_SCOPE",
            "schema_version": 1,
            "question_id": "Q001",
            "decision_date": "2026-06-15",
            "mbo_gap_ledger": "ACCEPTED_NON_BLOCKING_INVENTORY_SCOPE",
            "options_strict_mbo_warning_ledger": "ACCEPTED_NON_BLOCKING_INVENTORY_SCOPE",
            "available_data_research_allowed": True,
            "accepted_evidence": {
                "missing_or_unavailable_slots": 211,
                "strict_mbo_gap_count": 507,
                "strict_mbo_stale_gap_count": 503,
                "options_warn_checks": ["options-fixing-mbo-coverage"],
            },
            "model_gap_policy": {
                "missing_mbo_required_models": "SIDELINE_UNTIL_DATA_FILLED",
                "strict_options_quote_required_models": "SIDELINE_UNTIL_DATA_FILLED",
                "available_data_models": "RUN_WITH_EXPLICIT_COVERAGE",
                "must_emit_skip_or_rejection_reasons": True,
            },
        }),
        encoding="utf-8",
    )
    artifact = tmp_path / "runtime" / "data_audits" / "paid_data_inventory.json"
    artifact.parent.mkdir(parents=True)
    artifact.write_text(
        json.dumps({
            "q001_cme_data_inventory": {
                "status": "INVENTORIED_WITH_WARNINGS",
                "event_catalog": {"status": "OK"},
                "futures": {
                    "active_npz_manifest": {"status": "OK"},
                    "mbo_pilot_basket": {
                        "status": "completed_with_gaps",
                        "missing_or_unavailable_slots": 211,
                    },
                },
                "options": {
                    "data_doctor_status": "WARN",
                    "warn_checks": [{"name": "options-fixing-mbo-coverage", "status": "WARN"}],
                    "fail_checks": [],
                    "options_lane": {
                        "expiry_coverage": {
                            "strict_mbo_gap_count": 507,
                            "strict_mbo_stale_gap_count": 503,
                        },
                    },
                },
                "gaps": [
                    {"source": "mbo_pilot_manifest", "severity": "WARN"},
                    {"source": "data_doctor", "severity": "WARN"},
                ],
            },
        }),
        encoding="utf-8",
    )
    monkeypatch.setattr(paths, "REPO", tmp_path)
    monkeypatch.setattr(system_agg, "_latency", lambda: {"status": sc.OK, "live_arm_status": sc.OK})
    monkeypatch.setattr(system_agg, "_slow_tier", lambda: {"status": sc.OK})
    monkeypatch.setattr(system_agg, "_certification", lambda: {"status": sc.OK})
    monkeypatch.setattr(system_agg, "_databento", lambda: {"status": sc.OK})
    monkeypatch.setattr(system_agg, "_capture", lambda: {"status": sc.OK})
    monkeypatch.setattr(
        system_agg,
        "_lanes",
        lambda: {
            "status": sc.OK,
            "cme_options_data": {"status": sc.OK},
            "cme_options_defects": {"status": sc.OK},
        },
    )

    z = system_agg.build()
    q001 = z["q001_inventory"]

    assert z["health"] == sc.GREEN
    assert q001["status"] == sc.OK
    assert q001["available_data_scope_accepted"] is True
    assert q001["owner_decision_status"] == "ACCEPTED_AVAILABLE_DATA_SCOPE"
    assert q001["missing_or_unavailable_slots"] == 211
    assert q001["strict_mbo_gap_count"] == 507
    assert q001["accepted_evidence"]["strict_mbo_stale_gap_count"] == 503
    assert q001["model_gap_policy"]["available_data_models"] == "RUN_WITH_EXPLICIT_COVERAGE"
    _json_roundtrip(z)


def test_system_q001_owner_acceptance_rejects_accepted_evidence_drift():
    owner_decision = {
        "accepted_available_data_scope": True,
        "accepted_evidence": {
            "missing_or_unavailable_slots": 211,
            "strict_mbo_gap_count": 507,
            "strict_mbo_stale_gap_count": 503,
            "options_warn_checks": ["options-fixing-mbo-coverage"],
        },
    }
    q001_values = {
        "event_catalog_status": "OK",
        "active_npz_manifest_status": "OK",
        "mbo_pilot_basket_status": "completed_with_gaps",
        "missing_or_unavailable_slots": 211,
        "data_doctor_status": "WARN",
        "options_warn_checks": [{"name": "options-fixing-mbo-coverage", "status": "WARN"}],
        "options_fail_checks": [],
        "strict_mbo_gap_count": 508,
        "strict_mbo_stale_gap_count": 503,
    }

    status = system_agg._q001_accepted_available_data_status(
        "INVENTORIED_WITH_WARNINGS",
        [{"source": "mbo_pilot_manifest", "severity": "WARN"}],
        q001_values,
        owner_decision,
    )

    assert status == sc.STALE


def _accepted_q001_values() -> dict:
    return {
        "event_catalog_status": "OK",
        "active_npz_manifest_status": "OK",
        "mbo_pilot_basket_status": "completed_with_gaps",
        "missing_or_unavailable_slots": 211,
        "data_doctor_status": "WARN",
        "options_warn_checks": [{"name": "options-fixing-mbo-coverage", "status": "WARN"}],
        "options_fail_checks": [],
        "strict_mbo_gap_count": 507,
        "strict_mbo_stale_gap_count": 503,
    }


def _accepted_owner_decision() -> dict:
    return {
        "accepted_available_data_scope": True,
        "accepted_evidence": {
            "missing_or_unavailable_slots": 211,
            "strict_mbo_gap_count": 507,
            "strict_mbo_stale_gap_count": 503,
            "options_warn_checks": ["options-fixing-mbo-coverage"],
        },
    }


def test_system_q001_owner_acceptance_requires_explicit_gap_evidence():
    status = system_agg._q001_accepted_available_data_status(
        "INVENTORIED_WITH_WARNINGS",
        None,
        _accepted_q001_values(),
        _accepted_owner_decision(),
    )

    assert status == sc.UNKNOWN


def test_system_q001_owner_acceptance_rejects_unknown_gap_evidence():
    status = system_agg._q001_accepted_available_data_status(
        "INVENTORIED_WITH_WARNINGS",
        [
            {"source": "mbo_pilot_manifest", "severity": "WARN"},
            {"source": "data_doctor", "severity": "OK"},
        ],
        _accepted_q001_values(),
        _accepted_owner_decision(),
    )

    assert status == sc.UNKNOWN


def test_system_q001_owner_acceptance_rejects_options_fail_checks_despite_summary_ok():
    values = _accepted_q001_values()
    values["data_doctor_status"] = "OK"
    values["options_fail_checks"] = [{"name": "options-fixing-coverage", "status": "FAIL"}]

    status = system_agg._q001_accepted_available_data_status(
        "INVENTORIED_WITH_WARNINGS",
        [
            {"source": "mbo_pilot_manifest", "severity": "WARN"},
            {"source": "data_doctor", "severity": "WARN"},
        ],
        values,
        _accepted_owner_decision(),
    )

    assert status == sc.FAIL


def test_system_q001_owner_decision_requires_schema_policy_and_evidence(monkeypatch, tmp_path):
    decision = tmp_path / "docs" / "project" / "q001_owner_decision.json"
    decision.parent.mkdir(parents=True)
    decision.write_text(
        json.dumps({
            "status": "ACCEPTED_AVAILABLE_DATA_SCOPE",
            "mbo_gap_ledger": "ACCEPTED_NON_BLOCKING_INVENTORY_SCOPE",
            "options_strict_mbo_warning_ledger": "ACCEPTED_NON_BLOCKING_INVENTORY_SCOPE",
            "available_data_research_allowed": True,
        }),
        encoding="utf-8",
    )
    monkeypatch.setattr(paths, "REPO", tmp_path)

    owner_decision = system_agg._q001_owner_decision()

    assert owner_decision["accepted_available_data_scope"] is False
    assert set(owner_decision["validation_errors"]) == {
        "accepted_evidence",
        "schema_version",
        "question_id",
        "decision_date",
        "model_gap_policy",
    }


def test_system_q001_owner_acceptance_rejects_unscoped_options_warning(monkeypatch, tmp_path):
    decision = tmp_path / "docs" / "project" / "q001_owner_decision.json"
    decision.parent.mkdir(parents=True)
    decision.write_text(
        json.dumps({
            "status": "ACCEPTED_AVAILABLE_DATA_SCOPE",
            "schema_version": 1,
            "question_id": "Q001",
            "decision_date": "2026-06-15",
            "mbo_gap_ledger": "ACCEPTED_NON_BLOCKING_INVENTORY_SCOPE",
            "options_strict_mbo_warning_ledger": "ACCEPTED_NON_BLOCKING_INVENTORY_SCOPE",
            "available_data_research_allowed": True,
            "accepted_evidence": {
                "missing_or_unavailable_slots": 211,
                "strict_mbo_gap_count": 507,
                "strict_mbo_stale_gap_count": 503,
                "options_warn_checks": ["options-fixing-mbo-coverage"],
            },
            "model_gap_policy": {
                "missing_mbo_required_models": "SIDELINE_UNTIL_DATA_FILLED",
                "strict_options_quote_required_models": "SIDELINE_UNTIL_DATA_FILLED",
                "available_data_models": "RUN_WITH_EXPLICIT_COVERAGE",
                "must_emit_skip_or_rejection_reasons": True,
            },
        }),
        encoding="utf-8",
    )
    artifact = tmp_path / "runtime" / "data_audits" / "paid_data_inventory.json"
    artifact.parent.mkdir(parents=True)
    artifact.write_text(
        json.dumps({
            "q001_cme_data_inventory": {
                "status": "INVENTORIED_WITH_WARNINGS",
                "event_catalog": {"status": "OK"},
                "futures": {
                    "active_npz_manifest": {"status": "OK"},
                    "mbo_pilot_basket": {
                        "status": "completed_with_gaps",
                        "missing_or_unavailable_slots": 211,
                    },
                },
                "options": {
                    "data_doctor_status": "WARN",
                    "warn_checks": [{"name": "options-definitions", "status": "WARN"}],
                    "fail_checks": [],
                    "options_lane": {
                        "expiry_coverage": {
                            "strict_mbo_gap_count": 507,
                            "strict_mbo_stale_gap_count": 503,
                        },
                    },
                },
                "gaps": [
                    {"source": "mbo_pilot_manifest", "severity": "WARN"},
                    {"source": "data_doctor", "severity": "WARN"},
                ],
            },
        }),
        encoding="utf-8",
    )
    monkeypatch.setattr(paths, "REPO", tmp_path)

    q001 = system_agg._q001_inventory()

    assert q001["status"] == sc.STALE
    assert q001["available_data_scope_accepted"] is True
    assert q001["options_warn_checks"] == [{"name": "options-definitions", "status": "WARN"}]
    _json_roundtrip(q001)


def test_system_q001_inventoried_with_warning_evidence_is_non_green(monkeypatch, tmp_path):
    artifact = tmp_path / "runtime" / "data_audits" / "paid_data_inventory.json"
    artifact.parent.mkdir(parents=True)
    artifact.write_text(
        json.dumps({
            "q001_cme_data_inventory": {
                "status": "INVENTORIED",
                "event_catalog": {"status": "OK"},
                "futures": {
                    "active_npz_manifest": {"status": "OK"},
                    "mbo_pilot_basket": {
                        "status": "OK",
                        "missing_or_unavailable_slots": 211,
                    },
                },
                "options": {
                    "data_doctor_status": "WARN",
                    "options_lane": {
                        "expiry_coverage": {
                            "strict_mbo_gap_count": 507,
                            "strict_mbo_stale_gap_count": 503,
                        },
                    },
                },
                "gaps": [
                    {"source": "mbo_pilot_manifest", "severity": "WARN"},
                ],
            },
        }),
        encoding="utf-8",
    )
    monkeypatch.setattr(paths, "REPO", tmp_path)
    monkeypatch.setattr(system_agg, "_latency", lambda: {"status": sc.OK, "live_arm_status": sc.OK})
    monkeypatch.setattr(system_agg, "_slow_tier", lambda: {"status": sc.OK})
    monkeypatch.setattr(system_agg, "_certification", lambda: {"status": sc.OK})
    monkeypatch.setattr(system_agg, "_databento", lambda: {"status": sc.OK})
    monkeypatch.setattr(system_agg, "_capture", lambda: {"status": sc.OK})
    monkeypatch.setattr(
        system_agg,
        "_lanes",
        lambda: {
            "status": sc.OK,
            "cme_options_data": {"status": sc.OK},
            "cme_options_defects": {"status": sc.OK},
        },
    )

    z = system_agg.build()
    q001 = z["q001_inventory"]

    assert z["health"] == sc.AMBER
    assert q001["status"] == sc.STALE
    assert q001["status"] != sc.OK
    assert q001["q001_status"] == "INVENTORIED"
    assert q001["missing_or_unavailable_slots"] == 211
    assert q001["data_doctor_status"] == "WARN"
    assert q001["strict_mbo_gap_count"] == 507
    assert q001["strict_mbo_stale_gap_count"] == 503
    _json_roundtrip(z)


def test_system_q001_blocked_with_warning_evidence_stays_red(monkeypatch, tmp_path):
    artifact = tmp_path / "runtime" / "data_audits" / "paid_data_inventory.json"
    artifact.parent.mkdir(parents=True)
    artifact.write_text(
        json.dumps({
            "q001_cme_data_inventory": {
                "status": "BLOCKED",
                "futures": {
                    "mbo_pilot_basket": {
                        "missing_or_unavailable_slots": 211,
                    },
                },
                "options": {
                    "data_doctor_status": "WARN",
                    "options_lane": {
                        "expiry_coverage": {
                            "strict_mbo_gap_count": 507,
                            "strict_mbo_stale_gap_count": 503,
                        },
                    },
                },
                "gaps": [
                    {"source": "mbo_pilot_manifest", "severity": "WARN"},
                ],
            },
        }),
        encoding="utf-8",
    )
    monkeypatch.setattr(paths, "REPO", tmp_path)
    monkeypatch.setattr(system_agg, "_latency", lambda: {"status": sc.OK, "live_arm_status": sc.OK})
    monkeypatch.setattr(system_agg, "_slow_tier", lambda: {"status": sc.OK})
    monkeypatch.setattr(system_agg, "_certification", lambda: {"status": sc.OK})
    monkeypatch.setattr(system_agg, "_databento", lambda: {"status": sc.OK})
    monkeypatch.setattr(system_agg, "_capture", lambda: {"status": sc.OK})
    monkeypatch.setattr(
        system_agg,
        "_lanes",
        lambda: {
            "status": sc.OK,
            "cme_options_data": {"status": sc.OK},
            "cme_options_defects": {"status": sc.OK},
        },
    )

    z = system_agg.build()
    q001 = z["q001_inventory"]

    assert z["health"] == sc.RED
    assert q001["status"] == sc.FAIL
    assert q001["q001_status"] == "BLOCKED"
    assert q001["missing_or_unavailable_slots"] == 211
    assert q001["data_doctor_status"] == "WARN"
    assert q001["strict_mbo_gap_count"] == 507
    assert q001["strict_mbo_stale_gap_count"] == 503
    _json_roundtrip(z)


@pytest.mark.parametrize("q001_status", ["BLOCKED_WITH_WARNINGS", "FAIL", "ERROR", "FAILED"])
def test_system_q001_top_level_hard_fail_tokens_stay_red_with_clean_evidence(
    monkeypatch, tmp_path, q001_status
):
    artifact = tmp_path / "runtime" / "data_audits" / "paid_data_inventory.json"
    artifact.parent.mkdir(parents=True)
    artifact.write_text(
        json.dumps({
            "q001_cme_data_inventory": {
                "status": q001_status,
                "event_catalog": {"status": "OK"},
                "futures": {
                    "active_npz_manifest": {"status": "OK"},
                    "mbo_pilot_basket": {
                        "status": "completed",
                        "missing_or_unavailable_slots": 0,
                    },
                },
                "options": {
                    "data_doctor_status": "OK",
                    "options_lane": {
                        "expiry_coverage": {
                            "strict_mbo_gap_count": 0,
                            "strict_mbo_stale_gap_count": 0,
                        },
                    },
                },
                "gaps": [],
            },
        }),
        encoding="utf-8",
    )
    monkeypatch.setattr(paths, "REPO", tmp_path)
    monkeypatch.setattr(system_agg, "_latency", lambda: {"status": sc.OK, "live_arm_status": sc.OK})
    monkeypatch.setattr(system_agg, "_slow_tier", lambda: {"status": sc.OK})
    monkeypatch.setattr(system_agg, "_certification", lambda: {"status": sc.OK})
    monkeypatch.setattr(system_agg, "_databento", lambda: {"status": sc.OK})
    monkeypatch.setattr(system_agg, "_capture", lambda: {"status": sc.OK})
    monkeypatch.setattr(
        system_agg,
        "_lanes",
        lambda: {
            "status": sc.OK,
            "cme_options_data": {"status": sc.OK},
            "cme_options_defects": {"status": sc.OK},
        },
    )

    z = system_agg.build()
    q001 = z["q001_inventory"]

    assert z["health"] == sc.RED
    assert q001["status"] == sc.FAIL
    assert q001["status"] != sc.OK
    assert q001["q001_status"] == q001_status
    assert q001["event_catalog_status"] == "OK"
    assert q001["active_npz_manifest_status"] == "OK"
    assert q001["mbo_pilot_basket_status"] == "completed"
    assert q001["missing_or_unavailable_slots"] == 0
    assert q001["data_doctor_status"] == "OK"
    assert q001["strict_mbo_gap_count"] == 0
    assert q001["strict_mbo_stale_gap_count"] == 0
    assert q001["gaps"] == []
    _json_roundtrip(z)


def test_system_q001_inventoried_with_missing_evidence_is_non_green(monkeypatch, tmp_path):
    artifact = tmp_path / "runtime" / "data_audits" / "paid_data_inventory.json"
    artifact.parent.mkdir(parents=True)
    artifact.write_text(
        json.dumps({
            "q001_cme_data_inventory": {
                "status": "INVENTORIED",
                "gaps": [],
            },
        }),
        encoding="utf-8",
    )
    monkeypatch.setattr(paths, "REPO", tmp_path)
    monkeypatch.setattr(system_agg, "_latency", lambda: {"status": sc.OK, "live_arm_status": sc.OK})
    monkeypatch.setattr(system_agg, "_slow_tier", lambda: {"status": sc.OK})
    monkeypatch.setattr(system_agg, "_certification", lambda: {"status": sc.OK})
    monkeypatch.setattr(system_agg, "_databento", lambda: {"status": sc.OK})
    monkeypatch.setattr(system_agg, "_capture", lambda: {"status": sc.OK})
    monkeypatch.setattr(
        system_agg,
        "_lanes",
        lambda: {
            "status": sc.OK,
            "cme_options_data": {"status": sc.OK},
            "cme_options_defects": {"status": sc.OK},
        },
    )

    z = system_agg.build()
    q001 = z["q001_inventory"]

    assert z["health"] == sc.AMBER
    assert q001["status"] == sc.UNKNOWN
    assert q001["status"] != sc.OK
    assert q001["q001_status"] == "INVENTORIED"
    assert q001["gaps"] == []
    assert "missing_or_unavailable_slots" not in q001
    assert "data_doctor_status" not in q001
    assert "strict_mbo_gap_count" not in q001
    assert "strict_mbo_stale_gap_count" not in q001
    _json_roundtrip(z)


def test_system_q001_inventoried_with_source_status_failures_is_non_green(
    monkeypatch, tmp_path
):
    artifact = tmp_path / "runtime" / "data_audits" / "paid_data_inventory.json"
    artifact.parent.mkdir(parents=True)
    artifact.write_text(
        json.dumps({
            "q001_cme_data_inventory": {
                "status": "INVENTORIED",
                "event_catalog": {"status": "MISSING"},
                "futures": {
                    "active_npz_manifest": {"status": "FAIL"},
                    "mbo_pilot_basket": {
                        "status": "OK",
                        "missing_or_unavailable_slots": 0,
                    },
                },
                "options": {
                    "data_doctor_status": "OK",
                    "options_lane": {
                        "expiry_coverage": {
                            "strict_mbo_gap_count": 0,
                            "strict_mbo_stale_gap_count": 0,
                        },
                    },
                },
                "gaps": [],
            },
        }),
        encoding="utf-8",
    )
    monkeypatch.setattr(paths, "REPO", tmp_path)
    monkeypatch.setattr(system_agg, "_latency", lambda: {"status": sc.OK, "live_arm_status": sc.OK})
    monkeypatch.setattr(system_agg, "_slow_tier", lambda: {"status": sc.OK})
    monkeypatch.setattr(system_agg, "_certification", lambda: {"status": sc.OK})
    monkeypatch.setattr(system_agg, "_databento", lambda: {"status": sc.OK})
    monkeypatch.setattr(system_agg, "_capture", lambda: {"status": sc.OK})
    monkeypatch.setattr(
        system_agg,
        "_lanes",
        lambda: {
            "status": sc.OK,
            "cme_options_data": {"status": sc.OK},
            "cme_options_defects": {"status": sc.OK},
        },
    )

    z = system_agg.build()
    q001 = z["q001_inventory"]

    assert z["health"] == sc.RED
    assert q001["status"] == sc.FAIL
    assert q001["status"] != sc.OK
    assert q001["q001_status"] == "INVENTORIED"
    assert q001["event_catalog_status"] == "MISSING"
    assert q001["active_npz_manifest_status"] == "FAIL"
    assert q001["mbo_pilot_basket_status"] == "OK"
    assert q001["missing_or_unavailable_slots"] == 0
    assert q001["data_doctor_status"] == "OK"
    assert q001["strict_mbo_gap_count"] == 0
    assert q001["strict_mbo_stale_gap_count"] == 0
    _json_roundtrip(z)


def test_system_q001_runtime_completed_with_gaps_is_stale(monkeypatch, tmp_path):
    artifact = tmp_path / "runtime" / "data_audits" / "paid_data_inventory.json"
    artifact.parent.mkdir(parents=True)
    artifact.write_text(
        json.dumps({
            "q001_cme_data_inventory": {
                "status": "INVENTORIED_WITH_WARNINGS",
                "event_catalog": {"status": "OK"},
                "futures": {
                    "active_npz_manifest": {"status": "OK"},
                    "mbo_pilot_basket": {
                        "status": "completed_with_gaps",
                        "missing_or_unavailable_slots": 211,
                    },
                },
                "options": {
                    "data_doctor_status": "WARN",
                    "options_lane": {
                        "expiry_coverage": {
                            "strict_mbo_gap_count": 507,
                            "strict_mbo_stale_gap_count": 503,
                        },
                    },
                },
                "gaps": [],
            },
        }),
        encoding="utf-8",
    )
    monkeypatch.setattr(paths, "REPO", tmp_path)
    monkeypatch.setattr(system_agg, "_latency", lambda: {"status": sc.OK, "live_arm_status": sc.OK})
    monkeypatch.setattr(system_agg, "_slow_tier", lambda: {"status": sc.OK})
    monkeypatch.setattr(system_agg, "_certification", lambda: {"status": sc.OK})
    monkeypatch.setattr(system_agg, "_databento", lambda: {"status": sc.OK})
    monkeypatch.setattr(system_agg, "_capture", lambda: {"status": sc.OK})
    monkeypatch.setattr(
        system_agg,
        "_lanes",
        lambda: {
            "status": sc.OK,
            "cme_options_data": {"status": sc.OK},
            "cme_options_defects": {"status": sc.OK},
        },
    )

    z = system_agg.build()
    q001 = z["q001_inventory"]

    assert z["health"] == sc.AMBER
    assert q001["status"] == sc.STALE
    assert q001["status"] != sc.UNKNOWN
    assert q001["status"] != sc.OK
    assert q001["q001_status"] == "INVENTORIED_WITH_WARNINGS"
    assert q001["event_catalog_status"] == "OK"
    assert q001["active_npz_manifest_status"] == "OK"
    assert q001["mbo_pilot_basket_status"] == "completed_with_gaps"
    assert q001["missing_or_unavailable_slots"] == 211
    assert q001["data_doctor_status"] == "WARN"
    assert q001["strict_mbo_gap_count"] == 507
    assert q001["strict_mbo_stale_gap_count"] == 503
    _json_roundtrip(z)


@pytest.mark.parametrize("mbo_status", ["OK", "GREEN", "PASS"])
def test_system_q001_inventoried_generic_mbo_status_is_non_green(
    monkeypatch, tmp_path, mbo_status
):
    artifact = tmp_path / "runtime" / "data_audits" / "paid_data_inventory.json"
    artifact.parent.mkdir(parents=True)
    artifact.write_text(
        json.dumps({
            "q001_cme_data_inventory": {
                "status": "INVENTORIED",
                "event_catalog": {"status": "OK"},
                "futures": {
                    "active_npz_manifest": {"status": "OK"},
                    "mbo_pilot_basket": {
                        "status": mbo_status,
                        "missing_or_unavailable_slots": 0,
                    },
                },
                "options": {
                    "data_doctor_status": "OK",
                    "options_lane": {
                        "expiry_coverage": {
                            "strict_mbo_gap_count": 0,
                            "strict_mbo_stale_gap_count": 0,
                        },
                    },
                },
                "gaps": [],
            },
        }),
        encoding="utf-8",
    )
    monkeypatch.setattr(paths, "REPO", tmp_path)
    monkeypatch.setattr(system_agg, "_latency", lambda: {"status": sc.OK, "live_arm_status": sc.OK})
    monkeypatch.setattr(system_agg, "_slow_tier", lambda: {"status": sc.OK})
    monkeypatch.setattr(system_agg, "_certification", lambda: {"status": sc.OK})
    monkeypatch.setattr(system_agg, "_databento", lambda: {"status": sc.OK})
    monkeypatch.setattr(system_agg, "_capture", lambda: {"status": sc.OK})
    monkeypatch.setattr(
        system_agg,
        "_lanes",
        lambda: {
            "status": sc.OK,
            "cme_options_data": {"status": sc.OK},
            "cme_options_defects": {"status": sc.OK},
        },
    )

    z = system_agg.build()
    q001 = z["q001_inventory"]

    assert z["health"] != sc.GREEN
    assert q001["status"] == sc.UNKNOWN
    assert q001["status"] != sc.OK
    assert q001["q001_status"] == "INVENTORIED"
    assert q001["event_catalog_status"] == "OK"
    assert q001["active_npz_manifest_status"] == "OK"
    assert q001["mbo_pilot_basket_status"] == mbo_status
    assert q001["missing_or_unavailable_slots"] == 0
    assert q001["data_doctor_status"] == "OK"
    assert q001["strict_mbo_gap_count"] == 0
    assert q001["strict_mbo_stale_gap_count"] == 0
    assert q001["gaps"] == []
    _json_roundtrip(z)


@pytest.mark.parametrize("source_field", ["event_catalog", "active_npz_manifest", "data_doctor"])
@pytest.mark.parametrize("source_status", ["GREEN", "PASS", "PASSED", "COMPLETED"])
def test_system_q001_inventoried_generic_source_status_is_non_green(
    monkeypatch, tmp_path, source_field, source_status
):
    payload = {
        "q001_cme_data_inventory": {
            "status": "INVENTORIED",
            "event_catalog": {"status": "OK"},
            "futures": {
                "active_npz_manifest": {"status": "OK"},
                "mbo_pilot_basket": {
                    "status": "completed",
                    "missing_or_unavailable_slots": 0,
                },
            },
            "options": {
                "data_doctor_status": "OK",
                "options_lane": {
                    "expiry_coverage": {
                        "strict_mbo_gap_count": 0,
                        "strict_mbo_stale_gap_count": 0,
                    },
                },
            },
            "gaps": [],
        },
    }
    q001_payload = payload["q001_cme_data_inventory"]
    if source_field == "event_catalog":
        q001_payload["event_catalog"]["status"] = source_status
    elif source_field == "active_npz_manifest":
        q001_payload["futures"]["active_npz_manifest"]["status"] = source_status
    else:
        q001_payload["options"]["data_doctor_status"] = source_status

    artifact = tmp_path / "runtime" / "data_audits" / "paid_data_inventory.json"
    artifact.parent.mkdir(parents=True)
    artifact.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setattr(paths, "REPO", tmp_path)
    monkeypatch.setattr(system_agg, "_latency", lambda: {"status": sc.OK, "live_arm_status": sc.OK})
    monkeypatch.setattr(system_agg, "_slow_tier", lambda: {"status": sc.OK})
    monkeypatch.setattr(system_agg, "_certification", lambda: {"status": sc.OK})
    monkeypatch.setattr(system_agg, "_databento", lambda: {"status": sc.OK})
    monkeypatch.setattr(system_agg, "_capture", lambda: {"status": sc.OK})
    monkeypatch.setattr(
        system_agg,
        "_lanes",
        lambda: {
            "status": sc.OK,
            "cme_options_data": {"status": sc.OK},
            "cme_options_defects": {"status": sc.OK},
        },
    )

    z = system_agg.build()
    q001 = z["q001_inventory"]

    assert z["health"] != sc.GREEN
    assert q001["status"] == sc.UNKNOWN
    assert q001["status"] != sc.OK
    assert q001["q001_status"] == "INVENTORIED"
    assert q001["mbo_pilot_basket_status"] == "completed"
    assert q001["missing_or_unavailable_slots"] == 0
    assert q001["strict_mbo_gap_count"] == 0
    assert q001["strict_mbo_stale_gap_count"] == 0
    if source_field == "event_catalog":
        assert q001["event_catalog_status"] == source_status
    elif source_field == "active_npz_manifest":
        assert q001["active_npz_manifest_status"] == source_status
    else:
        assert q001["data_doctor_status"] == source_status
    _json_roundtrip(z)


def test_system_q001_clean_completed_mbo_pilot_is_green(monkeypatch, tmp_path):
    artifact = tmp_path / "runtime" / "data_audits" / "paid_data_inventory.json"
    artifact.parent.mkdir(parents=True)
    artifact.write_text(
        json.dumps({
            "q001_cme_data_inventory": {
                "status": "INVENTORIED",
                "event_catalog": {"status": "OK"},
                "futures": {
                    "active_npz_manifest": {"status": "OK"},
                    "mbo_pilot_basket": {
                        "status": "completed",
                        "missing_or_unavailable_slots": 0,
                    },
                },
                "options": {
                    "data_doctor_status": "OK",
                    "options_lane": {
                        "expiry_coverage": {
                            "strict_mbo_gap_count": 0,
                            "strict_mbo_stale_gap_count": 0,
                        },
                    },
                },
                "gaps": [],
            },
        }),
        encoding="utf-8",
    )
    monkeypatch.setattr(paths, "REPO", tmp_path)
    monkeypatch.setattr(system_agg, "_latency", lambda: {"status": sc.OK, "live_arm_status": sc.OK})
    monkeypatch.setattr(system_agg, "_slow_tier", lambda: {"status": sc.OK})
    monkeypatch.setattr(system_agg, "_certification", lambda: {"status": sc.OK})
    monkeypatch.setattr(system_agg, "_databento", lambda: {"status": sc.OK})
    monkeypatch.setattr(system_agg, "_capture", lambda: {"status": sc.OK})
    monkeypatch.setattr(
        system_agg,
        "_lanes",
        lambda: {
            "status": sc.OK,
            "cme_options_data": {"status": sc.OK},
            "cme_options_defects": {"status": sc.OK},
        },
    )

    z = system_agg.build()
    q001 = z["q001_inventory"]

    assert z["health"] == sc.GREEN
    assert q001["status"] == sc.OK
    assert q001["q001_status"] == "INVENTORIED"
    assert q001["event_catalog_status"] == "OK"
    assert q001["active_npz_manifest_status"] == "OK"
    assert q001["mbo_pilot_basket_status"] == "completed"
    assert q001["missing_or_unavailable_slots"] == 0
    assert q001["data_doctor_status"] == "OK"
    assert q001["strict_mbo_gap_count"] == 0
    assert q001["strict_mbo_stale_gap_count"] == 0
    assert q001["gaps"] == []
    _json_roundtrip(z)


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
    assert cod.get("missing_checks") == [
        "options-ohlcv",
        "options-definitions",
        "options-statistics",
    ]


def test_lanes_strict_mbo_warn_is_advisory(monkeypatch, tmp_path):
    lake = tmp_path / "options"
    lake.mkdir(parents=True)
    report_path = tmp_path / "data_doctor_report.json"
    checks = _options_ok_checks() + [
        {
            "name": "options-fixing-mbo-coverage",
            "status": "WARN",
            "detail": "mode=strict_mbo_quotes gap_count=507",
        }
    ]
    report_path.write_text(
        json.dumps({
            "run_utc": paths.now_iso(),
            "checks": checks,
            "options_lane": {"name": "options_lane", "status": "OK", "detail": "ok"},
            "failed": 0,
            "warned": 1,
        }),
        encoding="utf-8",
    )
    monkeypatch.setattr(paths, "DATA_DOCTOR_REPORT", report_path)
    monkeypatch.setattr(paths, "OPTIONS_LAKE_ROOT", lake)

    z = ZONES["system"]()
    cod = z.get("lanes", {}).get("cme_options_data", {})

    from apps.cockpit.backend import schemas as sc
    assert cod.get("status") == sc.OK
    assert cod.get("missing_checks") == []
    assert z.get("shadow_live_blockers", {}).get("cme_options_data") == sc.OK
    strict = next(
        c for c in cod.get("checks", [])
        if c.get("name") == "options-fixing-mbo-coverage"
    )
    assert strict.get("status") == "WARN"


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


def test_options_zone_exposes_independent_research_backtest_state(monkeypatch, tmp_path):
    _write_options_spec(tmp_path, "**OPEN** — blocks shadow/live arm. Research/backtest NOT blocked.")
    lake = tmp_path / "options"
    lake.mkdir(parents=True)
    report_path = tmp_path / "data_doctor_report.json"
    report_path.write_text(
        json.dumps({
            "run_utc": paths.now_iso(),
            "checks": _options_ok_checks(),
            "options_lane": {
                "as_of_utc": paths.now_iso(),
                "fixing_mbo": {"quote_files": 2, "trades_files": 3, "dates_covered": 4},
                "expiry_coverage": {"expected_dates": 4, "gap_count": 0, "stale_gap_count": 0},
                "ohlcv": {"files": 1, "names": ["ES_v0_ohlcv1m.dbn.zst"]},
                "definitions": {"files": 5, "batches": ["defs"]},
                "statistics": {"files": 0, "state": "pending_batch_delivery"},
            },
        }),
        encoding="utf-8",
    )
    monkeypatch.setattr(paths, "REPO", tmp_path)
    monkeypatch.setattr(paths, "DATA_DOCTOR_REPORT", report_path)
    monkeypatch.setattr(paths, "OPTIONS_LAKE_ROOT", lake)

    z = ZONES["options"]()

    assert z["zone"] == "options"
    assert z["lane"] == "cme_options"
    assert z["model_id_prefix"] == "FOPT_"
    assert z["research_backtest_status"] == "allowed"
    assert z["execution_status"] == "shadow_live_blocked"
    assert z["research_only"] is True
    assert z["controls"]["live_order_controls"] is False
    assert z["controls"]["paper_order_controls"] is False
    assert z["data_readiness"]["status"] == sc.OK
    assert z["defect_ledger"]["open_count"] == 1
    assert z["context_feature_coverage"]["status"] == "not_measured"
    assert z["context_feature_coverage"]["options_context_features"] == "not_measured"
    assert z["standalone_model_evidence"]["status"] == "structural_only"
    assert z["standalone_model_evidence"]["model_id_prefix"] == "FOPT_"
    assert z["standalone_model_evidence"]["real_data_backed"] is False
    assert z["standalone_model_evidence"]["robustness_status"] == "not_observed"
    assert z["shadow_live_status"] == "blocked"
    assert "shadow_live_phase_gate" in z["shadow_live_blockers"]
    assert "research_only_phase" not in z["shadow_live_blockers"]
    assert "defect_ledger_open" in z["shadow_live_blockers"]
    assert z["health"] == sc.AMBER
    _json_roundtrip(z)


def test_options_context_coverage_absent_summary_preserves_not_measured(monkeypatch, tmp_path):
    _point_options_zone_ok(monkeypatch, tmp_path)
    run_dir = tmp_path / "artifacts" / "research_cards" / "workbench_runs" / "fopt_no_context"
    run_dir.mkdir(parents=True)
    (run_dir / "summary.json").write_text(
        json.dumps({
            "campaign_id": "fopt_no_context",
            "generated_utc": "2026-06-14T01:00:00+00:00",
            "status": "PASS",
            "model_id": "FOPT_ES_CALL",
            "symbol": "MES.v.0",
            "lane": "cme_options",
        }),
        encoding="utf-8",
    )

    coverage = ZONES["options"]()["context_feature_coverage"]

    assert coverage == {
        "status": "not_measured",
        "options_context_features": "not_measured",
        "options_standalone_strategy": "not_measured",
        "note": "No artifact-level options context-feature coverage is present yet.",
    }


def test_options_context_coverage_explicit_not_measured_contract(monkeypatch, tmp_path):
    _point_options_zone_ok(monkeypatch, tmp_path)
    run_dir = (
        tmp_path
        / "artifacts"
        / "research_cards"
        / "workbench_runs"
        / "legacy_options_fixture"
    )
    run_dir.mkdir(parents=True)
    (run_dir / "summary.json").write_text(
        json.dumps({
            "campaign_id": "legacy_options_fixture",
            "generated_utc": "2026-06-14T01:30:00+00:00",
            "status": "PASS",
            "model_id": "DEALER_HEDGING",
            "symbol": "MES.v.0",
            "campaign_mode": "options_lane",
            "periods": [{"net_pnl": 10.0, "num_trades": 2}],
            "promote_candidate": False,
            "context_feature_coverage": {
                "status": "not_measured",
                "options_context_features": "not_measured",
                "options_standalone_strategy": {
                    "status": "separate",
                    "evidence_field": "periods",
                },
                "standalone_strategy_separated": True,
                "missing_policy": "fail_closed_not_measured",
                "units": {"options_context_features": "not_applicable"},
                "note": (
                    "Standalone options/parity fixture profitability is reported only in periods; "
                    "no target-vs-context uplift has been measured."
                ),
            },
            "context_ablation": {
                "status": "not_measured",
                "target_only": "separate_standalone_periods",
                "target_plus_options": "not_measured",
                "uplift": "not_measured",
                "missing_policy": "fail_closed_not_measured",
                "rows": [],
            },
        }),
        encoding="utf-8",
    )

    z = ZONES["options"]()
    coverage = z["context_feature_coverage"]

    assert coverage["status"] == "not_measured"
    assert coverage["options_context_features"] == "not_measured"
    assert coverage["options_context_feature_measured"] is False
    assert coverage["options_context_feature_count"] is None
    assert coverage["options_standalone_strategy"] == {
        "status": "separate",
        "evidence_field": "periods",
    }
    assert coverage["missing_policy"] == "fail_closed_not_measured"
    assert coverage["units"] == {"options_context_features": "not_applicable"}
    assert coverage["context_ablation_row_count"] == 0
    assert coverage["missing_fields"] == []
    assert coverage["malformed_fields"] == []
    assert "no target-vs-context uplift has been measured" in coverage["note"]
    _json_roundtrip(z)


def test_options_context_coverage_not_measured_without_ablation_fails_closed(
    monkeypatch, tmp_path
):
    _point_options_zone_ok(monkeypatch, tmp_path)
    run_dir = (
        tmp_path
        / "artifacts"
        / "research_cards"
        / "workbench_runs"
        / "partial_options_fixture"
    )
    run_dir.mkdir(parents=True)
    (run_dir / "summary.json").write_text(
        json.dumps({
            "campaign_id": "partial_options_fixture",
            "generated_utc": "2026-06-14T01:31:00+00:00",
            "status": "PASS",
            "model_id": "DEALER_HEDGING",
            "symbol": "MES.v.0",
            "campaign_mode": "options_lane",
            "context_feature_coverage": {
                "status": "not_measured",
                "options_context_features": "not_measured",
                "missing_policy": "fail_closed_not_measured",
                "units": {"options_context_features": "not_applicable"},
            },
        }),
        encoding="utf-8",
    )

    z = ZONES["options"]()
    coverage = z["context_feature_coverage"]

    assert z["health"] != sc.GREEN
    assert coverage["status"] == "incomplete"
    assert coverage["options_context_features"] == "incomplete"
    assert coverage["options_context_feature_measured"] is False
    assert set(coverage["missing_fields"]) == {
        "source_ids",
        "timestamp_ids",
        "context_ablation",
        "options_context_features",
    }
    assert coverage["context_ablation_row_count"] == 0
    assert "fail-closed" in coverage["note"]
    _json_roundtrip(z)


@pytest.mark.parametrize(
    ("coverage_extra", "ablation"),
    [
        (
            {"n_events_with_options_context": 3},
            {"status": "not_measured", "rows": []},
        ),
        (
            {},
            {
                "status": "not_measured",
                "rows": [
                    {
                        "context_set": "target_plus_options",
                        "target_only_ev": 1.0,
                        "target_plus_context_ev": 1.4,
                        "delta_ev": 0.4,
                    }
                ],
            },
        ),
    ],
)
def test_options_context_coverage_not_measured_with_evidence_fails_closed(
    monkeypatch, tmp_path, coverage_extra, ablation
):
    _point_options_zone_ok(monkeypatch, tmp_path)
    run_dir = (
        tmp_path
        / "artifacts"
        / "research_cards"
        / "workbench_runs"
        / "contradictory_options_fixture"
    )
    run_dir.mkdir(parents=True)
    coverage = {
        "status": "not_measured",
        "options_context_features": "not_measured",
        "missing_policy": "fail_closed_not_measured",
        "units": {"options_context_features": "not_applicable"},
        **coverage_extra,
    }
    (run_dir / "summary.json").write_text(
        json.dumps({
            "campaign_id": "contradictory_options_fixture",
            "generated_utc": "2026-06-14T01:32:00+00:00",
            "status": "PASS",
            "model_id": "DEALER_HEDGING",
            "symbol": "MES.v.0",
            "campaign_mode": "options_lane",
            "context_feature_coverage": coverage,
            "context_ablation": ablation,
        }),
        encoding="utf-8",
    )

    z = ZONES["options"]()
    payload = z["context_feature_coverage"]

    assert z["health"] != sc.GREEN
    assert payload["status"] == "malformed"
    assert payload["options_context_features"] == "malformed"
    assert payload["options_context_feature_measured"] is False
    assert "context_not_measured_contradiction" in payload["malformed_fields"]
    assert "fail-closed" in payload["note"]
    _json_roundtrip(z)


def test_options_context_coverage_valid_fopt_artifact_is_measured(monkeypatch, tmp_path):
    _point_options_zone_ok(monkeypatch, tmp_path)
    run_dir = tmp_path / "artifacts" / "research_cards" / "workbench_runs" / "fopt_context"
    run_dir.mkdir(parents=True)
    (run_dir / "summary.json").write_text(
        json.dumps({
            "campaign_id": "fopt_context",
            "generated_utc": "2026-06-14T02:03:04+00:00",
            "status": "PASS",
            "model_id": "FOPT_ES_CALL",
            "symbol": "MES.v.0",
            "lane": "cme_options",
            "context_feature_coverage": {
                "source_ids": [
                    "C:/hft3-lake/options/statistics/ES.OPT/2025-09-10.statistics"
                ],
                "timestamp_ids": {
                    "source_timestamp_utc": "2025-09-10T13:29:00+00:00",
                    "feature_available_utc": "2025-09-10T13:29:30+00:00",
                    "target_decision_timestamp_utc": "2025-09-10T13:30:00+00:00",
                },
                "units": {"iv_rank": "pct", "quote_intensity": "quotes_per_second"},
                "missing_policy": "partial_rows_flagged",
                "options_context_features": {"status": "measured", "n_events": 7, "missing": 1},
                "options_standalone_strategy": {"status": "separate"},
            },
            "context_ablation": {
                "rows": [
                    {
                        "target_event_type": "CPI",
                        "context_set": "target_plus_options",
                        "target_only_ev": 1.0,
                        "target_plus_context_ev": 1.4,
                        "delta_ev": 0.4,
                    }
                ]
            },
        }),
        encoding="utf-8",
    )

    z = ZONES["options"]()
    coverage = z["context_feature_coverage"]

    assert z["health"] == sc.AMBER
    assert coverage["status"] == "measured"
    assert coverage["options_context_features"] == "measured"
    assert coverage["options_context_feature_measured"] is True
    assert coverage["options_context_feature_count"] == 7.0
    assert coverage["options_standalone_strategy"] == {"status": "separate"}
    assert coverage["standalone_strategy_separated"] is True
    assert coverage["standalone_evidence_field"] == "standalone_model_evidence"
    assert coverage["latest_artifact"] == (
        "artifacts/research_cards/workbench_runs/fopt_context/summary.json"
    )
    assert coverage["latest_campaign_id"] == "fopt_context"
    assert coverage["latest_model_id"] == "FOPT_ES_CALL"
    assert coverage["source_family"] == "cme_options"
    assert coverage["source_ids"] == [
        "C:/hft3-lake/options/statistics/ES.OPT/2025-09-10.statistics"
    ]
    assert coverage["timestamp_ids"]["target_decision_timestamp_utc"] == (
        "2025-09-10T13:30:00+00:00"
    )
    assert coverage["units"]["iv_rank"] == "pct"
    assert coverage["missing_policy"] == "partial_rows_flagged"
    assert coverage["context_ablation_row_count"] == 1
    assert coverage["context_ablation_rows"][0]["context_set"] == "target_plus_options"
    assert coverage["missing_fields"] == []
    assert coverage["malformed_fields"] == []
    _json_roundtrip(z)


def test_options_context_coverage_claim_missing_proof_fails_closed(monkeypatch, tmp_path):
    _point_options_zone_ok(monkeypatch, tmp_path)
    run_dir = tmp_path / "artifacts" / "research_cards" / "workbench_runs" / "fopt_context_claim"
    run_dir.mkdir(parents=True)
    (run_dir / "summary.json").write_text(
        json.dumps({
            "campaign_id": "fopt_context_claim",
            "generated_utc": "2026-06-14T03:00:00+00:00",
            "status": "PASS",
            "model_id": "FOPT_ES_CALL",
            "symbol": "MES.v.0",
            "lane": "cme_options",
            "context_feature_coverage": {"options_context_features": "measured"},
        }),
        encoding="utf-8",
    )

    z = ZONES["options"]()
    coverage = z["context_feature_coverage"]

    assert z["health"] != sc.GREEN
    assert coverage["status"] == "incomplete"
    assert coverage["options_context_features"] == "incomplete"
    assert coverage["options_context_feature_measured"] is False
    assert coverage["latest_artifact"] == (
        "artifacts/research_cards/workbench_runs/fopt_context_claim/summary.json"
    )
    assert set(coverage["missing_fields"]) == {
        "source_ids",
        "timestamp_ids",
        "context_ablation",
        "options_context_features",
        "units",
        "missing_policy",
    }
    assert coverage["context_ablation_row_count"] == 0
    assert "fail-closed" in coverage["note"]
    _json_roundtrip(z)


def test_options_context_coverage_zero_count_is_not_measured(monkeypatch, tmp_path):
    _point_options_zone_ok(monkeypatch, tmp_path)
    run_dir = tmp_path / "artifacts" / "research_cards" / "workbench_runs" / "fopt_context_zero"
    run_dir.mkdir(parents=True)
    (run_dir / "summary.json").write_text(
        json.dumps({
            "campaign_id": "fopt_context_zero",
            "generated_utc": "2026-06-14T03:30:00+00:00",
            "status": "PASS",
            "model_id": "FOPT_ES_CALL",
            "symbol": "MES.v.0",
            "lane": "cme_options",
            "context_feature_coverage": {
                "source_ids": ["C:/hft3-lake/options/statistics/ES.OPT/2025-09-10.statistics"],
                "timestamp_ids": {
                    "source_timestamp_utc": "2025-09-10T13:29:00+00:00",
                    "feature_available_utc": "2025-09-10T13:29:30+00:00",
                    "target_decision_timestamp_utc": "2025-09-10T13:30:00+00:00",
                },
                "units": {"iv_rank": "pct"},
                "missing_policy": "zero_coverage_visible",
                "options_context_features": {"status": "measured", "n_events": 0},
            },
            "context_ablation": {
                "rows": [
                    {
                        "target_event_type": "CPI",
                        "context_set": "target_plus_options",
                        "target_only_ev": 1.0,
                        "target_plus_context_ev": 1.0,
                        "delta_ev": 0.0,
                    }
                ]
            },
        }),
        encoding="utf-8",
    )

    coverage = ZONES["options"]()["context_feature_coverage"]

    assert coverage["status"] == "incomplete"
    assert coverage["options_context_feature_measured"] is False
    assert "options_context_features" in coverage["missing_fields"]


def test_options_context_coverage_future_timestamp_fails_closed(monkeypatch, tmp_path):
    _point_options_zone_ok(monkeypatch, tmp_path)
    run_dir = tmp_path / "artifacts" / "research_cards" / "workbench_runs" / "fopt_context_leak"
    run_dir.mkdir(parents=True)
    (run_dir / "summary.json").write_text(
        json.dumps({
            "campaign_id": "fopt_context_leak",
            "generated_utc": "2026-06-14T04:00:00+00:00",
            "status": "PASS",
            "model_id": "FOPT_ES_CALL",
            "symbol": "MES.v.0",
            "lane": "cme_options",
            "context_feature_coverage": {
                "source_ids": ["C:/hft3-lake/options/statistics/ES.OPT/2025-09-10.statistics"],
                "timestamp_ids": {
                    "source_timestamp_utc": "2025-09-10T13:29:00+00:00",
                    "feature_available_utc": "2025-09-10T13:30:01+00:00",
                    "target_decision_timestamp_utc": "2025-09-10T13:30:00+00:00",
                },
                "units": {"iv_rank": "pct"},
                "missing_policy": "partial_rows_flagged",
                "options_context_features": {"n_events": 3},
            },
            "context_ablation": {
                "rows": [
                    {
                        "target_event_type": "CPI",
                        "context_set": "target_plus_options",
                        "target_only_ev": 1.0,
                        "target_plus_context_ev": 1.4,
                        "delta_ev": 0.4,
                    }
                ]
            },
        }),
        encoding="utf-8",
    )

    z = ZONES["options"]()
    coverage = z["context_feature_coverage"]

    assert z["health"] == sc.RED
    assert coverage["status"] == "malformed"
    assert "timestamp_ids.feature_available_after_target_decision" in coverage["malformed_fields"]
    assert "timestamp proof" in coverage["note"]
    _json_roundtrip(z)


def test_options_context_coverage_future_source_timestamp_fails_closed(monkeypatch, tmp_path):
    _point_options_zone_ok(monkeypatch, tmp_path)
    run_dir = tmp_path / "artifacts" / "research_cards" / "workbench_runs" / "fopt_context_future_source"
    run_dir.mkdir(parents=True)
    (run_dir / "summary.json").write_text(
        json.dumps({
            "campaign_id": "fopt_context_future_source",
            "generated_utc": "2026-06-14T04:30:00+00:00",
            "status": "PASS",
            "model_id": "FOPT_ES_CALL",
            "symbol": "MES.v.0",
            "lane": "cme_options",
            "context_feature_coverage": {
                "source_ids": ["C:/hft3-lake/options/statistics/ES.OPT/2025-09-10.statistics"],
                "timestamp_ids": {
                    "source_timestamp_utc": "2025-09-10T13:29:45+00:00",
                    "feature_available_utc": "2025-09-10T13:29:30+00:00",
                    "target_decision_timestamp_utc": "2025-09-10T13:30:00+00:00",
                },
                "units": {"iv_rank": "pct"},
                "missing_policy": "partial_rows_flagged",
                "options_context_features": {"n_events": 3},
            },
            "context_ablation": {
                "rows": [{
                    "target_event_type": "CPI",
                    "context_set": "target_plus_options",
                    "target_only_ev": 1.0,
                    "target_plus_context_ev": 1.4,
                    "delta_ev": 0.4,
                }]
            },
        }),
        encoding="utf-8",
    )

    coverage = ZONES["options"]()["context_feature_coverage"]

    assert coverage["status"] == "malformed"
    assert "timestamp_ids.source_after_feature_available" in coverage["malformed_fields"]


def test_options_context_coverage_timestamp_rows_do_not_mix(monkeypatch, tmp_path):
    _point_options_zone_ok(monkeypatch, tmp_path)
    run_dir = tmp_path / "artifacts" / "research_cards" / "workbench_runs" / "fopt_context_mixed_timestamps"
    run_dir.mkdir(parents=True)
    (run_dir / "summary.json").write_text(
        json.dumps({
            "campaign_id": "fopt_context_mixed_timestamps",
            "generated_utc": "2026-06-14T05:00:00+00:00",
            "status": "PASS",
            "model_id": "FOPT_ES_CALL",
            "symbol": "MES.v.0",
            "lane": "cme_options",
            "context_feature_coverage": {
                "source_ids": ["C:/hft3-lake/options/statistics/ES.OPT/2025-09-10.statistics"],
                "timestamp_ids": [
                    {
                        "source_timestamp_utc": "2025-09-10T13:29:00+00:00",
                        "feature_available_utc": "2025-09-10T13:29:30+00:00",
                    },
                    {"target_decision_timestamp_utc": "2025-09-10T13:30:00+00:00"},
                ],
                "units": {"iv_rank": "pct"},
                "missing_policy": "partial_rows_flagged",
                "options_context_features": {"n_events": 3},
            },
            "context_ablation": {
                "rows": [{
                    "target_event_type": "CPI",
                    "context_set": "target_plus_options",
                    "target_only_ev": 1.0,
                    "target_plus_context_ev": 1.4,
                    "delta_ev": 0.4,
                }]
            },
        }),
        encoding="utf-8",
    )

    coverage = ZONES["options"]()["context_feature_coverage"]

    assert coverage["status"] == "incomplete"
    assert "timestamp_ids[0].target_decision" in coverage["missing_fields"]
    assert "timestamp_ids[1].source_timestamp" in coverage["missing_fields"]
    assert "timestamp_ids[1].feature_available" in coverage["missing_fields"]


def test_options_api_route(monkeypatch, tmp_path):
    from apps.cockpit.backend import auth

    _write_options_spec(tmp_path, "**FIXED**")
    lake = tmp_path / "options"
    lake.mkdir(parents=True)
    report_path = tmp_path / "data_doctor_report.json"
    report_path.write_text(
        json.dumps({
            "run_utc": paths.now_iso(),
            "checks": _options_ok_checks(),
            "options_lane": {"name": "options_lane", "status": "OK", "detail": "ok"},
        }),
        encoding="utf-8",
    )
    monkeypatch.setattr(paths, "REPO", tmp_path)
    monkeypatch.setattr(paths, "DATA_DOCTOR_REPORT", report_path)
    monkeypatch.setattr(paths, "OPTIONS_LAKE_ROOT", lake)
    monkeypatch.setattr(auth, "_LOOPBACK", {"testclient"})

    client = TestClient(app)
    r = client.get("/api/options")

    assert r.status_code == 200
    payload = r.json()
    assert payload["zone"] == "options"
    assert payload["health"] == sc.AMBER
    assert payload["research_backtest_status"] == "allowed"
    assert payload["execution_status"] == "shadow_live_blocked"
    assert payload["standalone_model_evidence"]["status"] == "structural_only"
    assert payload["standalone_model_evidence"]["latest_artifact_status"] == "missing"
    assert payload["shadow_live_status"] == "blocked"
    assert payload["shadow_live_blockers"] == ["shadow_live_phase_gate"]


def test_options_zone_keeps_fopt_and_legacy_fixture_evidence_separate(monkeypatch, tmp_path):
    _write_options_spec(tmp_path, "**FIXED**")
    lake = tmp_path / "options"
    lake.mkdir(parents=True)
    report_path = tmp_path / "data_doctor_report.json"
    report_path.write_text(
        json.dumps({
            "run_utc": paths.now_iso(),
            "checks": _options_ok_checks(),
            "options_lane": {"name": "options_lane", "status": "OK", "detail": "ok"},
        }),
        encoding="utf-8",
    )
    runs = tmp_path / "artifacts" / "research_cards" / "workbench_runs"
    fopt = runs / "fopt_options_run"
    explicit_cme = runs / "explicit_cme_options_run"
    legacy = runs / "latest_legacy_options_run"
    fopt.mkdir(parents=True)
    explicit_cme.mkdir(parents=True)
    legacy.mkdir(parents=True)
    (fopt / "summary.json").write_text(
        json.dumps({
            "campaign_id": "fopt_options_run",
            "status": "FAIL",
            "model_id": "FOPT_ES_CALL",
            "symbol": "MES.v.0",
            "campaign_mode": "options_lane",
            "periods": [{"name": "Options fixture"}],
        }),
        encoding="utf-8",
    )
    (explicit_cme / "summary.json").write_text(
        json.dumps({
            "campaign_id": "explicit_cme_options_run",
            "status": "PASS",
            "model_id": "HYP_5",
            "symbol": "MES.v.0",
            "lane": "cme_options",
            "campaign_mode": "options_lane",
            "periods": [{"name": "Options fixture"}],
        }),
        encoding="utf-8",
    )
    (legacy / "summary.json").write_text(
        json.dumps({
            "campaign_id": "latest_legacy_options_run",
            "status": "PASS",
            "model_id": "DEALER_HEDGING",
            "symbol": "MES.v.0",
            "campaign_mode": "options_lane",
            "real_data_backed": True,
            "periods": [{"name": "Options fixture", "num_trades": 2}],
        }),
        encoding="utf-8",
    )
    os.utime(fopt / "summary.json", (1_700_000_000, 1_700_000_000))
    os.utime(explicit_cme / "summary.json", (1_900_000_000, 1_900_000_000))
    os.utime(legacy / "summary.json", (1_800_000_000, 1_800_000_000))

    monkeypatch.setattr(paths, "REPO", tmp_path)
    monkeypatch.setattr(paths, "DATA_DOCTOR_REPORT", report_path)
    monkeypatch.setattr(paths, "OPTIONS_LAKE_ROOT", lake)

    z = ZONES["options"]()
    fopt_evidence = z["standalone_model_evidence"]
    legacy_evidence = z["legacy_options_fixture_evidence"]

    assert fopt_evidence["status"] == "fixture_only"
    assert fopt_evidence["latest_artifact"] == (
        "artifacts/research_cards/workbench_runs/explicit_cme_options_run/summary.json"
    )
    assert fopt_evidence["latest_model_id"] == "HYP_5"
    assert fopt_evidence["latest_lane"] == "cme_options"
    assert fopt_evidence["latest_summary_status"] == "PASS"
    assert fopt_evidence["fixture_backed"] is True
    assert fopt_evidence["real_data_backed"] is False
    assert fopt_evidence["structural_only"] is False
    assert fopt_evidence["robustness_status"] == "not_observed"
    assert legacy_evidence["status"] == "real_data_claim_unverified"
    assert legacy_evidence["latest_artifact"] == (
        "artifacts/research_cards/workbench_runs/latest_legacy_options_run/summary.json"
    )
    assert legacy_evidence["latest_model_id"] == "DEALER_HEDGING"
    assert legacy_evidence["real_data_backed"] is False
    assert legacy_evidence["claimed_real_data_backed"] is True
    assert "source_ids" in legacy_evidence["missing_real_data_proof"]
    assert "timestamp_ids" in legacy_evidence["missing_real_data_proof"]
    assert "robustness_pass" in legacy_evidence["missing_real_data_proof"]
    assert "claims real-data backing" in legacy_evidence["robustness_detail"]
    assert "missing required proof" in legacy_evidence["robustness_detail"]
    assert "not FOPT CME_OPTIONS evidence" in legacy_evidence["robustness_detail"]
    _json_roundtrip(z)


def test_options_zone_finds_fopt_artifact_root_env(monkeypatch, tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _write_options_spec(repo, "**FIXED**")
    lake = repo / "options"
    lake.mkdir(parents=True)
    report_path = repo / "data_doctor_report.json"
    report_path.write_text(
        json.dumps({
            "run_utc": paths.now_iso(),
            "checks": _options_ok_checks(),
            "options_lane": {"name": "options_lane", "status": "OK", "detail": "ok"},
        }),
        encoding="utf-8",
    )
    artifact_root = tmp_path / "external_artifacts"
    run_dir = artifact_root / "workbench_runs" / "fopt_external_run"
    run_dir.mkdir(parents=True)
    summary_path = run_dir / "summary.json"
    summary_path.write_text(
        json.dumps({
            "campaign_id": "fopt_external_run",
            "generated_utc": "2026-06-14T02:03:04+00:00",
            "status": "PASS",
            "model_id": "FOPT_ES_CALL",
            "symbol": "MES.v.0",
            "lane": "cme_options",
            "real_data_backed": True,
            "source_ids": ["C:/hft3-lake/options/fixing/MES/example.mbo"],
            "timestamp_ids": {
                "source_timestamp_utc": "2026-06-14T02:00:00+00:00",
                "feature_available_utc": "2026-06-14T02:01:00+00:00",
                "target_decision_timestamp_utc": "2026-06-14T02:02:00+00:00",
            },
            "num_trades": 3,
            "uses_2026_options_data": True,
            "options_2026_usage_class": "cost-calibration",
        }),
        encoding="utf-8",
    )
    (run_dir / "robustness_summary.json").write_text(json.dumps({"status": "PASS"}), encoding="utf-8")
    monkeypatch.setenv("HFT3_ARTIFACTS_ROOT", str(artifact_root))
    monkeypatch.setattr(paths, "REPO", repo)
    monkeypatch.setattr(paths, "DATA_DOCTOR_REPORT", report_path)
    monkeypatch.setattr(paths, "OPTIONS_LAKE_ROOT", lake)

    evidence = ZONES["options"]()["standalone_model_evidence"]

    assert evidence["status"] == "real_data_backed"
    assert evidence["latest_artifact"] == str(summary_path)
    assert evidence["latest_model_id"] == "FOPT_ES_CALL"
    assert evidence["real_data_backed"] is True
    assert evidence["claimed_real_data_backed"] is True
    assert evidence["missing_real_data_proof"] == []
    assert evidence["trade_count"] == 3.0
    assert evidence["latest_artifact_time_source"] == "generated_utc"
    assert evidence["fixture_backed"] is False


def test_options_zone_structural_artifact_dominates_real_data_claim(monkeypatch, tmp_path):
    _write_options_spec(tmp_path, "**FIXED**")
    lake = tmp_path / "options"
    lake.mkdir(parents=True)
    report_path = tmp_path / "data_doctor_report.json"
    report_path.write_text(
        json.dumps({
            "run_utc": paths.now_iso(),
            "checks": _options_ok_checks(),
            "options_lane": {"name": "options_lane", "status": "OK", "detail": "ok"},
        }),
        encoding="utf-8",
    )
    run_dir = tmp_path / "artifacts" / "research_cards" / "workbench_runs" / "fopt_structural"
    run_dir.mkdir(parents=True)
    (run_dir / "summary.json").write_text(
        json.dumps({
            "campaign_id": "fopt_structural",
            "status": "FAIL",
            "model_id": "FOPT_ES_CALL",
            "symbol": "MES.v.0",
            "lane": "cme_options",
            "real_data_backed": True,
            "structural_only": True,
            "degraded": True,
            "failure_notes": ["structural-only CME options adapter; no evidence backtest executed"],
            "promotable": False,
            "num_trades": 0,
        }),
        encoding="utf-8",
    )
    monkeypatch.setattr(paths, "REPO", tmp_path)
    monkeypatch.setattr(paths, "DATA_DOCTOR_REPORT", report_path)
    monkeypatch.setattr(paths, "OPTIONS_LAKE_ROOT", lake)

    evidence = ZONES["options"]()["standalone_model_evidence"]

    assert evidence["status"] == "structural_only"
    assert evidence["real_data_backed"] is False
    assert evidence["claimed_real_data_backed"] is True
    assert evidence["structural_only"] is True
    assert evidence["degraded"] is True
    assert evidence["promotable"] is False
    assert "structural-only CME options adapter" in evidence["failure_notes"][0]
    assert "not evidence for a tradable standalone options model" in evidence["robustness_detail"]


def test_options_zone_non_promotable_artifact_dominates_real_data_claim(monkeypatch, tmp_path):
    _write_options_spec(tmp_path, "**FIXED**")
    lake = tmp_path / "options"
    lake.mkdir(parents=True)
    report_path = tmp_path / "data_doctor_report.json"
    report_path.write_text(
        json.dumps({
            "run_utc": paths.now_iso(),
            "checks": _options_ok_checks(),
            "options_lane": {"name": "options_lane", "status": "OK", "detail": "ok"},
        }),
        encoding="utf-8",
    )
    run_dir = tmp_path / "artifacts" / "research_cards" / "workbench_runs" / "fopt_non_promotable"
    run_dir.mkdir(parents=True)
    (run_dir / "summary.json").write_text(
        json.dumps({
            "campaign_id": "fopt_non_promotable",
            "status": "PASS",
            "model_id": "FOPT_ES_CALL",
            "symbol": "MES.v.0",
            "lane": "cme_options",
            "real_data_backed": True,
            "source_ids": ["C:/hft3-lake/options/fixing/MES/example.mbo"],
            "timestamp_ids": {
                "source_timestamp_utc": "2026-06-14T02:00:00+00:00",
                "feature_available_utc": "2026-06-14T02:01:00+00:00",
                "target_decision_timestamp_utc": "2026-06-14T02:02:00+00:00",
            },
            "num_trades": 4,
            "promotable": False,
        }),
        encoding="utf-8",
    )
    (run_dir / "robustness_summary.json").write_text(json.dumps({"status": "PASS"}), encoding="utf-8")
    monkeypatch.setattr(paths, "REPO", tmp_path)
    monkeypatch.setattr(paths, "DATA_DOCTOR_REPORT", report_path)
    monkeypatch.setattr(paths, "OPTIONS_LAKE_ROOT", lake)

    evidence = ZONES["options"]()["standalone_model_evidence"]

    assert evidence["status"] == "artifact_degraded"
    assert evidence["real_data_backed"] is False
    assert evidence["claimed_real_data_backed"] is True
    assert evidence["missing_real_data_proof"] == []
    assert evidence["promotable"] is False
    assert "non-promotable" in evidence["robustness_detail"]


def test_options_zone_uses_semantic_artifact_time_over_mtime(monkeypatch, tmp_path):
    _write_options_spec(tmp_path, "**FIXED**")
    lake = tmp_path / "options"
    lake.mkdir(parents=True)
    report_path = tmp_path / "data_doctor_report.json"
    report_path.write_text(
        json.dumps({
            "run_utc": paths.now_iso(),
            "checks": _options_ok_checks(),
            "options_lane": {"name": "options_lane", "status": "OK", "detail": "ok"},
        }),
        encoding="utf-8",
    )
    runs = tmp_path / "artifacts" / "research_cards" / "workbench_runs"
    older = runs / "fopt_older_semantic_newer_mtime"
    newer = runs / "fopt_newer_semantic_older_mtime"
    older.mkdir(parents=True)
    newer.mkdir(parents=True)
    older_summary = older / "summary.json"
    newer_summary = newer / "summary.json"
    older_summary.write_text(
        json.dumps({
            "campaign_id": "fopt_older_semantic_newer_mtime",
            "generated_utc": "2026-01-01T00:00:00+00:00",
            "status": "PASS",
            "model_id": "FOPT_ES_CALL",
            "symbol": "MES.v.0",
            "periods": [{"name": "Options fixture"}],
        }),
        encoding="utf-8",
    )
    newer_summary.write_text(
        json.dumps({
            "campaign_id": "fopt_newer_semantic_older_mtime",
            "generated_utc": "2026-02-01T00:00:00+00:00",
            "status": "PASS",
            "model_id": "FOPT_ES_CALL",
            "symbol": "MES.v.0",
            "periods": [{"name": "Options fixture"}],
        }),
        encoding="utf-8",
    )
    os.utime(older_summary, (1_900_000_000, 1_900_000_000))
    os.utime(newer_summary, (1_700_000_000, 1_700_000_000))
    monkeypatch.setattr(paths, "REPO", tmp_path)
    monkeypatch.setattr(paths, "DATA_DOCTOR_REPORT", report_path)
    monkeypatch.setattr(paths, "OPTIONS_LAKE_ROOT", lake)

    evidence = ZONES["options"]()["standalone_model_evidence"]

    assert evidence["latest_artifact"] == (
        "artifacts/research_cards/workbench_runs/fopt_newer_semantic_older_mtime/summary.json"
    )
    assert evidence["latest_campaign_id"] == "fopt_newer_semantic_older_mtime"
    assert evidence["latest_artifact_time_source"] == "generated_utc"
    assert evidence["latest_artifact_time_utc"] == "2026-02-01T00:00:00+00:00"


def test_options_view_renders_research_backtest_allowed_not_research_blocked():
    src = (paths.REPO / "apps/cockpit/frontend/src/views/OptionsView.tsx").read_text(encoding="utf-8")
    assert "research/backtest" in src
    assert "Standalone Options Models" in src
    assert "Legacy Options/Parity Fixture" in src
    assert 'g(expiry, "gap_diagnostics")' in src
    assert "required_action" in src
    assert "invalidArtifactSummary" in src
    assert "research only" not in src.lower()


def test_system_view_reads_real_options_gap_summary():
    src = (paths.REPO / "apps/cockpit/frontend/src/views/SystemView.tsx").read_text(encoding="utf-8")
    assert "summary[\"expiry_coverage\"]" in src
    assert "expiryCoverage?.[\"gap_count\"]" in src
    assert "expiryCoverage?.[\"gap_diagnostics\"]" in src
    assert '["first gap", gapSummary(gapDiagnostics[0])]' in src


def test_pipeline_view_renders_skip_reason_counts():
    src = (paths.REPO / "apps/cockpit/frontend/src/views/PipelineView.tsx").read_text(encoding="utf-8")
    assert '"skip_reason_counts"' in src


def test_options_diagnostics_formatters_render_payloads():
    frontend = paths.REPO / "apps/cockpit/frontend"
    module_path = frontend / "src/views/optionsDiagnostics.ts"
    esbuild_path = frontend / "node_modules/esbuild/lib/main.js"
    if not esbuild_path.is_file():
        pytest.skip("frontend esbuild dependency not installed")
    script = """
const [modulePath, esbuildPath] = process.argv.slice(1);
const esbuild = require(esbuildPath);
(async () => {
  const result = await esbuild.build({
    entryPoints: [modulePath],
    bundle: true,
    write: false,
    format: "esm",
    platform: "node",
  });
  const href = "data:text/javascript;base64," + Buffer.from(result.outputFiles[0].text).toString("base64");
  const mod = await import(href);
  const invalidRow = {
    date: "2023-07-03",
    reason: "invalid_artifact",
    required_action: "replace_invalid_artifact_or_manifest_no_data_proof",
    invalid_artifacts: [{ file: "ES_fixing_2023-07-03.dbn.zst", reason: "no sample records" }],
  };
  const vendorRow = {
    date: "2026-06-12",
    reason: "missing_artifact_vendor_lag",
    required_action: "backfill_or_manifest_vendor_no_data_proof",
    invalid_artifacts: [],
  };
  console.log(JSON.stringify({
    invalidText: mod.invalidArtifactSummary(invalidRow),
    gapText: mod.gapSummary(invalidRow),
    vendorText: mod.invalidArtifactSummary(vendorRow),
    recordCount: mod.records([{ a: 1 }, null, "x", { b: 2 }]).length,
  }));
})().catch((err) => {
  console.error(err && err.stack ? err.stack : String(err));
  process.exit(1);
});
"""
    proc = subprocess.run(
        ["node", "-e", script, str(module_path), str(esbuild_path)],
        cwd=frontend,
        check=True,
        text=True,
        capture_output=True,
        timeout=15,
    )
    payload = json.loads(proc.stdout)
    assert payload["invalidText"] == "ES_fixing_2023-07-03.dbn.zst: no sample records"
    assert payload["gapText"] == (
        "2023-07-03 invalid_artifact replace_invalid_artifact_or_manifest_no_data_proof"
    )
    assert payload["vendorText"] == "-"
    assert payload["recordCount"] == 2
    assert "[object Object]" not in proc.stdout


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
    _stub_q001_ok(monkeypatch)
    monkeypatch.setattr(paths, "DATA_DOCTOR_REPORT", tmp_path / "no_report.json")
    for attr in ("SLOW_TIER_PROBLEMS", "CERT_REGISTRY", "LATENCY_SUMMARY",
                 "CAPTURE_BASELINE", "MODEL_LIFECYCLE"):
        monkeypatch.setattr(paths, attr, tmp_path / f"{attr}.json")
    a = ZONES["alerts"]()
    ids = {al["id"] for al in a["alerts"]}
    assert "lake-data-doctor-missing" in ids


def test_alerts_stale_data_doctor_report(monkeypatch, tmp_path):
    _stub_q001_ok(monkeypatch)
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
    _stub_q001_ok(monkeypatch)
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


def test_alerts_strict_mbo_warn_is_diagnostic(monkeypatch, tmp_path):
    _stub_q001_ok(monkeypatch)
    report_path = tmp_path / "data_doctor_report.json"
    report = {
        "run_utc": paths.now_iso(),
        "checks": _options_ok_checks() + [
            {
                "name": "options-fixing-mbo-coverage",
                "status": "WARN",
                "detail": "mode=strict_mbo_quotes gap_count=507",
            }
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

    assert "lake-options-fixing-mbo-coverage" not in ids


def test_alerts_q001_runtime_warning_rolls_up_without_raw_strict_mbo_alert(monkeypatch, tmp_path):
    artifact = tmp_path / "runtime" / "data_audits" / "paid_data_inventory.json"
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_text(
        json.dumps({
            "q001_cme_data_inventory": {
                "status": "INVENTORIED_WITH_WARNINGS",
                "event_catalog": {"status": "OK"},
                "futures": {
                    "active_npz_manifest": {"status": "OK"},
                    "mbo_pilot_basket": {
                        "status": "completed_with_gaps",
                        "missing_or_unavailable_slots": 211,
                    },
                },
                "options": {
                    "data_doctor_status": "WARN",
                    "options_lane": {
                        "expiry_coverage": {
                            "strict_mbo_gap_count": 507,
                            "strict_mbo_stale_gap_count": 503,
                        },
                    },
                },
                "gaps": [{"status": "WARN"}, {"status": "STALE"}],
            },
        }),
        encoding="utf-8",
    )
    report_path = tmp_path / "data_doctor_report.json"
    report_path.write_text(
        json.dumps({
            "run_utc": paths.now_iso(),
            "checks": _options_ok_checks() + [
                {
                    "name": "options-fixing-mbo-coverage",
                    "status": "WARN",
                    "detail": "mode=strict_mbo_quotes gap_count=507",
                }
            ],
            "failed": 0,
            "warned": 1,
        }),
        encoding="utf-8",
    )
    monkeypatch.setattr(paths, "REPO", tmp_path)
    monkeypatch.setattr(paths, "DATA_DOCTOR_REPORT", report_path)
    _silence_alert_sources(monkeypatch, tmp_path)

    a = ZONES["alerts"]()
    ids = {al["id"] for al in a["alerts"]}
    q001 = next(al for al in a["alerts"] if al["id"] == "q001-paid-data-inventory")

    assert a["health"] == sc.AMBER
    assert "q001-paid-data-inventory" in ids
    assert "lake-options-fixing-mbo-coverage" not in ids
    assert q001["severity"] == sc.SEV_WARN
    assert q001["source"] == "q001_inventory"
    for token in (
        "q001_status=INVENTORIED_WITH_WARNINGS",
        "artifact=runtime/data_audits/paid_data_inventory.json",
        "missing_or_unavailable_slots=211",
        "data_doctor_status=WARN",
        "strict_mbo_gap_count=507",
        "strict_mbo_stale_gap_count=503",
    ):
        assert token in q001["message"]


def test_alerts_q001_missing_artifact_warns(monkeypatch, tmp_path):
    report_path = tmp_path / "data_doctor_report.json"
    report_path.write_text(
        json.dumps({
            "run_utc": paths.now_iso(),
            "checks": _options_ok_checks(),
            "options_lane": {"name": "options_lane", "status": "OK", "detail": "ok"},
        }),
        encoding="utf-8",
    )
    monkeypatch.setattr(paths, "REPO", tmp_path)
    monkeypatch.setattr(paths, "DATA_DOCTOR_REPORT", report_path)
    _silence_alert_sources(monkeypatch, tmp_path)

    a = ZONES["alerts"]()

    assert a["health"] == sc.AMBER
    assert a["count"] == 1
    assert a["alerts"] == [
        {
            "id": "q001-paid-data-inventory",
            "severity": sc.SEV_WARN,
            "source": "q001_inventory",
            "message": (
                "Q001 paid-data inventory missing: "
                "q001_status=None, artifact=runtime/data_audits/paid_data_inventory.json"
            ),
            "ts": None,
        }
    ]


def test_alerts_q001_fail_is_crit_and_red(monkeypatch, tmp_path):
    report_path = tmp_path / "data_doctor_report.json"
    report_path.write_text(
        json.dumps({"run_utc": paths.now_iso(), "checks": _options_ok_checks()}),
        encoding="utf-8",
    )
    monkeypatch.setattr(paths, "DATA_DOCTOR_REPORT", report_path)
    _silence_alert_sources(monkeypatch, tmp_path)
    monkeypatch.setattr(
        alerts_agg,
        "_q001_inventory",
        lambda: {
            "status": sc.FAIL,
            "q001_status": "FAIL",
            "artifact": "runtime/data_audits/paid_data_inventory.json",
            "missing_or_unavailable_slots": 0,
            "data_doctor_status": "OK",
            "strict_mbo_gap_count": 0,
            "strict_mbo_stale_gap_count": 0,
            "gaps": [],
        },
    )

    a = ZONES["alerts"]()
    q001 = next(al for al in a["alerts"] if al["id"] == "q001-paid-data-inventory")

    assert a["health"] == sc.RED
    assert q001["severity"] == sc.SEV_CRIT
    assert q001["source"] == "q001_inventory"


def test_alerts_q001_ok_emits_no_q001_alert(monkeypatch, tmp_path):
    _stub_q001_ok(monkeypatch)
    report_path = tmp_path / "data_doctor_report.json"
    report_path.write_text(
        json.dumps({"run_utc": paths.now_iso(), "checks": _options_ok_checks()}),
        encoding="utf-8",
    )
    monkeypatch.setattr(paths, "DATA_DOCTOR_REPORT", report_path)
    _silence_alert_sources(monkeypatch, tmp_path)

    a = ZONES["alerts"]()
    ids = {al["id"] for al in a["alerts"]}

    assert "q001-paid-data-inventory" not in ids
    assert a["count"] == 0
    assert a["health"] == sc.GREEN


def test_alerts_missing_mandatory_options_checks(monkeypatch, tmp_path):
    _stub_q001_ok(monkeypatch)
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


def test_alerts_options_defect_ledger_open_is_not_runtime_alert(monkeypatch):
    _stub_q001_ok(monkeypatch)
    a = ZONES["alerts"]()
    ids = {al["id"] for al in a["alerts"]}
    assert "options-defect-ledger-open" not in ids


def test_alerts_options_fixing_coverage_alert(monkeypatch, tmp_path):
    """alerts zone with a failing options-fixing-coverage check -> alert id
    'lake-options-fixing-coverage' present in the alerts feed."""
    _stub_q001_ok(monkeypatch)
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
