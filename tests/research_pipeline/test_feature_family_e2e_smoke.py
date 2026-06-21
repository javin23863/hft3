"""Phase 8: end-to-end feature-family research smoke (no paid compute).

Chains autoresearch → screening artifact → recipe hash → HBT manifest handoff,
plus fs_v1 bar-construction selection when a feature store exists.
"""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from apps.cockpit.backend.tests.test_cockpit import _write_screening_artifact
from backtest_pipeline.src.fs_v1_screen_path import FS_V1_BAR_CONSTRUCTION_ID
from backtest_pipeline.src.hft_campaign.manifest import ManifestGenerationConfig, generate_scenario_manifest
from backtest_pipeline.src.recipe_hash_gate import (
    extract_feature_recipe_hash_from_promoted_row,
    validate_feature_recipe_hash_handoff,
)
from backtest_pipeline.src.replay_npz_fixture import build_minimal_mbo_npz
from research_pipeline.elite_refinement import propose_next_candidates
from research_pipeline.feature_recipe import attach_feature_recipe_to_candidate
from research_pipeline.generation_loop import AutoresearchConfig, run_autoresearch_loop
from research_pipeline.generation_state import load_manifest
from research_pipeline.types import CandidateModel, ParsedHypothesis


@pytest.fixture(autouse=True)
def _pass_ontology_gate_e2e(monkeypatch):
    from research_pipeline.generation_gate_chain import GATE_ONTOLOGY, build_gate_receipt
    from research_pipeline import generation_loop as gl

    def _pass_ontology(*, manifest, repo_root):
        return build_gate_receipt(
            gate_id=GATE_ONTOLOGY,
            gate_version="1.0.0",
            candidate_id=str(manifest["candidate_id"]),
            feature_recipe_hash=str(manifest["feature_recipe_hash"]),
            manifest_hash=str(manifest.get("manifest_hash") or "pending"),
            status="PASS",
            required_checks=["fable_entry_checklist", "citation_trace"],
            required_check_count=2,
            passed_check_count=2,
        )

    monkeypatch.setattr(gl, "run_ontology_gate_for_candidate", _pass_ontology)


@dataclass
class _E2eFilterResult:
    payload: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return dict(self.payload)


def _parsed_hypothesis() -> ParsedHypothesis:
    return ParsedHypothesis(
        thesis="Fade spread blowout after macro surprise",
        instrument_universe=["MES"],
        entry_rules=[],
        exit_rules=[],
        indicators=[],
        feature_list=["SPREAD_BLOWOUT_RECOMPRESSION"],
        param_ranges={"signal_threshold": [0.1, 0.3]},
        primary_model_id="SPREAD_BLOWOUT_RECOMPRESSION",
        source="heuristic",
    )


def _passing_surface_metrics() -> dict[str, Any]:
    from backtest_pipeline.src.surface_stability import compute_surface_stability

    grid = {
        (r, c): {"net_return": 0.10, "trade_count": 50}
        for r in range(3)
        for c in range(3)
    }
    return compute_surface_stability(grid)


def _serializable_statistical_evidence() -> dict[str, Any]:
    passed = {"status": "pass"}
    return {
        "robustness_artifact_staleness": "fresh",
        "dsr_status": "pass",
        "pbo_status": "pass",
        "cscv_status": "pass",
        "bootstrap_ci_or_not_run": passed,
        "dsr_or_not_run": passed,
        "pbo_or_not_run": passed,
        "cscv_count_or_not_run": passed,
        "fee_stress_or_not_run": passed,
        "slippage_stress_or_not_run": passed,
        "latency_stress_or_not_run": passed,
        "holm_stepdown_or_not_run": passed,
        "holm_bh_or_not_run": passed,
        "null_battery_or_not_run": passed,
        "planted_alpha_or_not_run": passed,
        "adversarial_or_not_run": passed,
        "parameter_perturbation_or_not_run": passed,
    }


def _e2e_filter(*, candidates, parsed, event_id, repo_root, gates, screening_scope, run_budget=None, **kwargs):
    promoted = []
    surface = _passing_surface_metrics()
    statistical = _serializable_statistical_evidence()
    for cand in candidates[:2]:
        attached = (
            cand
            if cand.feature_recipe
            else attach_feature_recipe_to_candidate(
                cand,
                parsed=parsed or _parsed_hypothesis(),
                target_event_id=event_id,
                target_symbol="MES",
            )
        )
        recipe = dict(attached.feature_recipe or {})
        recipe_hash = attached.feature_recipe_hash
        promoted.append(
            {
                "candidate_id": attached.candidate_id,
                "hypothesis_id": attached.model_id,
                "model_id": attached.model_id,
                "param_values": dict(attached.strategy_params),
                "feature_recipe_hash": recipe_hash,
                "feature_recipe": recipe,
                "research_clock": attached.research_clock,
                "replay_eligibility_status": "eligible",
                "vectorbt_results": {
                    "oos_expectancy": 1.25,
                    "max_drawdown_pct": -4.0,
                    "num_trades": 50,
                    "hit_rate": 0.55,
                    "gross_return": 0.12,
                    "net_return": 0.10,
                    "net_pnl": 1000.0,
                    "total_fees": 50.0,
                    "total_slippage": 25.0,
                    "trade_count": 50,
                    "expectancy_per_trade": 0.02,
                    "profit_factor": 1.4,
                    "sharpe": 0.8,
                    "sortino": 1.1,
                    "max_drawdown": -0.05,
                    "turnover": 0.3,
                    "feature_recipe_hash": recipe_hash,
                    "feature_recipe": copy.deepcopy(recipe),
                    "surface_stability_metrics": surface,
                    **statistical,
                },
                "surface_stability_metrics": surface,
                "gross_return": 0.12,
                "net_return": 0.10,
                "net_pnl": 1000.0,
                "total_fees": 50.0,
                "total_slippage": 25.0,
                "trade_count": 50,
                "hit_rate": 0.55,
                "expectancy_per_trade": 0.02,
                "profit_factor": 1.4,
                "sharpe": 0.8,
                "sortino": 1.1,
                "max_drawdown": -0.05,
                "turnover": 0.3,
                **statistical,
            }
        )
    return _E2eFilterResult(
        {
            "screening_backend": "vectorbt",
            "vectorbt_engine": "rust",
            "rust_engine_available": True,
            "rust_engine_required_for_scope": False,
            "screening_scope": screening_scope,
            "research_clock": "scheduled_event",
            "event_id": event_id,
            "bar_construction_id": FS_V1_BAR_CONSTRUCTION_ID,
            "feature_plane_status": "scheduled_event_only",
            "promoted": promoted,
            "promoted_ids": [p["candidate_id"] for p in promoted],
            "rejected": [],
        }
    )


def _e2e_persist(artifact, path: Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = dict(artifact)
    payload.setdefault("screening_artifact_hash", "phase8_smoke")
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return path


def _write_latency_queue(tmp_path: Path) -> tuple[Path, Path]:
    latency = tmp_path / "latency.json"
    queue = tmp_path / "queue.json"
    latency.write_text(
        json.dumps(
            {
                "schema": "latency_model.v1",
                "order_latency_ms": {"p50": 1.0, "p95": 2.0},
                "feed_latency_ms": {"p50": 0.5, "p95": 1.0},
            }
        ),
        encoding="utf-8",
    )
    queue.write_text(
        json.dumps({"schema": "fill_queue_model.v1", "queue_position": "back"}),
        encoding="utf-8",
    )
    return latency, queue


def _e2e_robustness_fn(tmp_path: Path):
    calls: list[int] = []

    def _run(**kwargs):
        calls.append(1)
        out = tmp_path / f"rob_{len(calls)}"
        out.mkdir(parents=True, exist_ok=True)
        campaign_summary = {
            "status": "PASS",
            "wfc_status": "PASS",
            "robustness_passed": True,
            "periods": [
                {"name": "Discovery", "gate_pass": True},
                {"name": "Holdout", "gate_pass": True, "evaluate_only": True},
                {"name": "Recent holdout", "gate_pass": True, "evaluate_only": True},
            ],
            "wfc": {"pearson": 0.5, "spearman": 0.4, "wfc_status": "PASS"},
            "wfc_matrix_rows": [{"parameter_hash": "ph-e2e", "fold": 0}],
            "metrics": {},
        }
        (out / "summary.json").write_text(json.dumps(campaign_summary), encoding="utf-8")
        return {
            "robustness_pass": True,
            "regular_walk_forward_pass": True,
            "wfc_pass": True,
            "metrics": {},
            "campaign_id": f"rob_{len(calls)}",
            "campaign_summary": campaign_summary,
            "artifact_dir": str(out),
        }

    return _run


def _e2e_hft_fn(tmp_path: Path):
    from types import SimpleNamespace

    def _latest_robustness_hash() -> str:
        from backtest_pipeline.src.hft_campaign._hashing import sha256_hex

        rob_dirs = sorted(tmp_path.glob("rob_*"), key=lambda p: p.stat().st_mtime, reverse=True)
        for rob_dir in rob_dirs:
            summary_path = rob_dir / "summary.json"
            if summary_path.is_file():
                return sha256_hex(json.loads(summary_path.read_text(encoding="utf-8")))
        return "rob-smoke-fallback"

    def _manifest_for_candidate(candidate_id: str, config) -> dict[str, Any]:
        manifests_path = Path(config.out_dir).parent / "candidate_manifests.jsonl"
        if manifests_path.is_file():
            for line in manifests_path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                row = json.loads(line)
                if str(row.get("candidate_id")) == candidate_id:
                    return row
        return {"candidate_id": candidate_id, "manifest_hash": "", "feature_recipe_hash": ""}

    def _run(**kwargs):
        scenarios = kwargs.get("scenarios") or []
        config = kwargs.get("config")
        out = tmp_path / "hft_out"
        out.mkdir(parents=True, exist_ok=True)
        rob_hash = _latest_robustness_hash()
        scenario_results = []
        for s in scenarios:
            cid = str(getattr(s, "candidate_id", "unknown"))
            manifest = _manifest_for_candidate(cid, config) if config else {}
            scenario_results.append(
                SimpleNamespace(
                    scenario_id=str(getattr(s, "scenario_id", "s1")),
                    status="completed",
                    replay_result={
                        "candidate_id": cid,
                        "manifest_hash": str(manifest.get("manifest_hash") or ""),
                        "feature_recipe_hash": str(manifest.get("feature_recipe_hash") or ""),
                        "screening_artifact_hash": "phase8_smoke",
                        "robustness_artifact_hash": rob_hash,
                        "certification_status": "full_fidelity_declared",
                    },
                    artifact_dir=str(out),
                )
            )
        return SimpleNamespace(
            status="pass",
            summary={"status": "pass"},
            scenario_results=scenario_results,
        )

    return _run


def test_e2e_autoresearch_two_generations_with_family_variants(tmp_path: Path, monkeypatch) -> None:
    latency, queue = _write_latency_queue(tmp_path)
    npz = tmp_path / "replay.npz"
    build_minimal_mbo_npz(npz)
    scenario_store: dict[str, list[Any]] = {"scenarios": []}

    def fake_generate_scenario_manifest(cfg):
        scenarios = [
            type(
                "Scenario",
                (),
                {
                    "scenario_id": f"sc_{cid}",
                    "candidate_id": cid,
                    "to_dict": lambda self, _cid=cid, _sid=f"sc_{cid}": {
                        "scenario_id": _sid,
                        "candidate_id": _cid,
                    },
                },
            )()
            for cid in (getattr(cfg, "candidate_ids", None) or ["unknown"])
        ]
        scenario_store["scenarios"] = scenarios
        return scenarios, []

    monkeypatch.setattr(
        "research_pipeline.generation_loop.generate_scenario_manifest",
        fake_generate_scenario_manifest,
    )
    monkeypatch.setattr(
        "research_pipeline.generation_loop.load_scenarios_from_manifest",
        lambda _path: scenario_store["scenarios"],
    )
    cfg = AutoresearchConfig(
        max_generations=2,
        max_candidates_per_generation=6,
        exploration_fraction=0.0,
        family_search_enabled=True,
        family_search_fraction=0.5,
        run_robustness=True,
        run_hft_campaign=True,
        hft_source_npz=npz,
        hft_latency_model=latency,
        hft_fill_queue_model=queue,
    )
    code, report = run_autoresearch_loop(
        repo_root=tmp_path,
        thesis="Fade spread blowout",
        event_id="CPI_2024_09_11_TIGHT",
        cfg=cfg,
        parsed=_parsed_hypothesis(),
        no_llm=True,
        filter_fn=_e2e_filter,
        persist_fn=_e2e_persist,
        robustness_fn=_e2e_robustness_fn(tmp_path),
        hft_fn=_e2e_hft_fn(tmp_path),
    )
    assert code == 0
    assert report["generations_run"] == 2

    campaign_id = report["campaign_id"]
    gen0_summary = json.loads(
        (
            tmp_path
            / "research_cards"
            / "autoresearch"
            / campaign_id
            / "generation_000"
            / "generation_summary.json"
        ).read_text(encoding="utf-8")
    )
    elite_rows = [r for r in gen0_summary["candidates"] if r.get("final_status") == "FINAL_PASS"]
    assert elite_rows
    assert elite_rows[0].get("feature_recipe_hash")
    assert isinstance(elite_rows[0].get("feature_recipe"), dict)

    gen1_manifests = (
        tmp_path
        / "research_cards"
        / "autoresearch"
        / campaign_id
        / "generation_001"
        / "candidate_manifests.jsonl"
    ).read_text(encoding="utf-8").strip().splitlines()
    assert gen1_manifests
    gen1_rows = [json.loads(line) for line in gen1_manifests if line.strip()]
    family_proposals = [
        r for r in gen1_rows if str(r.get("proposal_reason", "")).startswith("family_variant:")
    ]
    assert family_proposals, "Gen N+1 must include family-variant frozen manifests"

    next_cands = propose_next_candidates(
        parsed=_parsed_hypothesis(),
        generation_summary=gen0_summary,
        tested_hashes=set(),
        max_candidates=6,
        exploration_fraction=0.0,
        target_event_id="CPI_2024_09_11_TIGHT",
        family_search_enabled=True,
    )
    variant_ids = {c.metadata.get("family_variant_id") for c in next_cands}
    assert variant_ids - {None}

    manifest = load_manifest(tmp_path, campaign_id)
    assert len(manifest.get("tested_parameter_hashes") or []) >= len(gen0_summary["candidates"])


def test_e2e_autoresearch_recipe_hash_to_hbt_manifest(tmp_path: Path, monkeypatch) -> None:
    """Autoresearch emits recipe hash; HBT manifest propagates it from promoted row."""
    monkeypatch.setenv("HFT3_ARTIFACTS_ROOT", str(tmp_path / "artifacts"))
    monkeypatch.setattr(
        "backtest_pipeline.src.hft_campaign.manifest.validate_screening_artifact",
        lambda _artifact: [],
    )
    monkeypatch.setattr(
        "backtest_pipeline.src.hft_campaign.manifest.validate_candidate_replay_eligibility",
        lambda _row: [],
    )
    cfg = AutoresearchConfig(
        max_generations=1,
        max_candidates_per_generation=2,
        run_robustness=False,
        run_hft_campaign=False,
    )
    code, report = run_autoresearch_loop(
        repo_root=tmp_path,
        thesis="Fade spread blowout",
        event_id="CPI_2024_09_11_TIGHT",
        cfg=cfg,
        parsed=_parsed_hypothesis(),
        no_llm=True,
        filter_fn=_e2e_filter,
        persist_fn=_e2e_persist,
    )
    assert code == 0
    gen0_summary = json.loads(
        (
            tmp_path
            / "research_cards"
            / "autoresearch"
            / report["campaign_id"]
            / "generation_000"
            / "generation_summary.json"
        ).read_text(encoding="utf-8")
    )
    upstream_hash = gen0_summary["candidates"][0]["feature_recipe_hash"]
    assert upstream_hash

    screening_path = _write_screening_artifact(
        tmp_path,
        "phase8_hbt_handoff",
        "2026-06-18T00:00:00Z",
        replay_eligible=True,
        surface_defined=True,
    )
    screening = json.loads(screening_path.read_text(encoding="utf-8"))
    promoted = dict(screening["promoted"][0])
    promoted["feature_recipe_hash"] = upstream_hash
    promoted["vectorbt_results"] = {"feature_recipe_hash": upstream_hash}
    screening["promoted"] = [promoted]
    screening_path.write_text(json.dumps(screening, indent=2) + "\n", encoding="utf-8")

    assert extract_feature_recipe_hash_from_promoted_row(promoted) == upstream_hash

    npz = tmp_path / "replay.npz"
    build_minimal_mbo_npz(npz)
    latency, queue = _write_latency_queue(tmp_path)
    scenarios, reasons = generate_scenario_manifest(
        ManifestGenerationConfig(
            screening_artifact_path=screening_path,
            repo_root=tmp_path,
            event_id="CPI_2024_09_11_TIGHT",
            source_npz_path=npz,
            latency_model_path=latency,
            fill_queue_model_path=queue,
            select_all_replay_eligible=True,
        )
    )
    assert not reasons
    assert len(scenarios) == 1
    scenario = scenarios[0]
    assert scenario.feature_recipe_hash == upstream_hash
    assert validate_feature_recipe_hash_handoff(
        scenario_feature_recipe_hash=scenario.feature_recipe_hash,
        promoted_row=promoted,
    ) == []


def test_e2e_fs_v1_path_when_feature_store_present(tmp_path: Path) -> None:
    from backtest_pipeline.src.feature_plane import FEATURE_PLANE_STATUS_BAR_STUB
    from backtest_pipeline.src.vectorbt_adapter import filter_candidates
    from data_system.src.feature_store import store_path
    from tests.backtest_pipeline.test_fs_v1_vectorbt_path import _make_feature_store_npz

    event_id = "EVT"
    sym = "MES.v.0"
    _make_feature_store_npz(store_path(tmp_path, sym, event_id))
    cand = CandidateModel(
        candidate_id="c1",
        model_id="SPREAD_BLOWOUT_RECOMPRESSION",
        strategy_params={"signal_threshold": 0.01, "holding_period_bars": 5},
        thesis="test",
        metadata={"symbol": sym},
    )
    result = filter_candidates(
        candidates=[cand],
        parsed=None,
        event_id=event_id,
        repo_root=tmp_path,
        feature_store_root=tmp_path,
        symbol=sym,
        prefer_fs_v1_path=True,
        data_loader=lambda *_: None,
        param_grid={
            "signal_threshold": [0.01],
            "holding_period_bars": [5],
            "stop_loss_pct": [None],
            "take_profit_pct": [None],
        },
    )
    artifact = result.to_dict()
    assert artifact["bar_construction_id"] == FS_V1_BAR_CONSTRUCTION_ID
    assert artifact["feature_plane_status"] != FEATURE_PLANE_STATUS_BAR_STUB
