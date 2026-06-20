#!/usr/bin/env python3
"""Deterministic three-generation autoresearch acceptance (assignment §21).

Uses fake/minimal runners — no live VectorBT or HftBacktest compute required.
Writes runtime/reports/autoresearch_three_gen_acceptance_20260620.md.
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from research_pipeline.elite_refinement import propose_next_candidates
from research_pipeline.feature_recipe import attach_feature_recipe_to_candidate, compute_feature_recipe_hash
from research_pipeline.generation_gate_chain import (
    FINAL_HFT_REJECTED,
    FINAL_ONTOLOGY_REJECTED,
    FINAL_PASS,
    FINAL_REGULAR_WF_REJECTED,
    FINAL_STATISTICAL_REJECTED,
    FINAL_SURFACE_REJECTED,
    FINAL_VECTORBT_REJECTED,
    FINAL_WFC_REJECTED,
)
from research_pipeline.generation_loop import AutoresearchConfig, run_autoresearch_loop
from research_pipeline.generation_state import generation_dir, load_manifest
from research_pipeline.types import CandidateModel, ParsedHypothesis

REPORT_PATH = _REPO_ROOT / "runtime" / "reports" / "autoresearch_three_gen_acceptance_20260620.md"

REJECT_STATUS_MAP = {
    FINAL_ONTOLOGY_REJECTED: "ontology",
    FINAL_VECTORBT_REJECTED: "vectorbt",
    FINAL_SURFACE_REJECTED: "surface",
    FINAL_REGULAR_WF_REJECTED: "regular_wf",
    FINAL_WFC_REJECTED: "wfc",
    FINAL_STATISTICAL_REJECTED: "statistical",
    FINAL_HFT_REJECTED: "hft",
}


@dataclass
class _FilterResult:
    payload: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return dict(self.payload)


def _parsed_hypothesis() -> ParsedHypothesis:
    return ParsedHypothesis(
        thesis="Fade spread blowout after macro surprise (acceptance dry-run)",
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

    grid = {(r, c): {"net_return": 0.10, "trade_count": 50} for r in range(3) for c in range(3)}
    return compute_surface_stability(grid)


def _statistical_evidence(*, fail_component: str | None = None) -> dict[str, Any]:
    passed = {"status": "pass"}
    out = {
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
    if fail_component == "statistical":
        out["dsr_status"] = "fail"
        out["dsr_or_not_run"] = {"status": "fail"}
    return out


def _acceptance_filter(*, candidates, parsed, event_id, repo_root, gates, screening_scope, run_budget=None, **kwargs):
    promoted: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    surface = _passing_surface_metrics()

    for cand in candidates:
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
        cid = attached.candidate_id
        recipe = dict(attached.feature_recipe or {})
        recipe_hash = attached.feature_recipe_hash

        if cid.startswith("block_ontology"):
            continue
        if cid.startswith("reject_vbt"):
            rejected.append({"candidate_id": cid, "reason": "vectorbt_screen_reject"})
            continue

        fail_component: str | None = None
        if cid.startswith("reject_stat"):
            fail_component = "statistical"

        statistical = _statistical_evidence(fail_component=fail_component)
        row = {
            "candidate_id": cid,
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
                "feature_recipe": recipe,
                "surface_stability_metrics": surface,
                **statistical,
            },
            "surface_stability_metrics": surface,
            **statistical,
        }
        if cid.startswith("reject_surface"):
            row["surface_stability_metrics"] = {"status": "fail", "reason": "planted_surface_fail"}
            row["vectorbt_results"]["surface_stability_metrics"] = row["surface_stability_metrics"]
        promoted.append(row)

    return _FilterResult(
        {
            "screening_backend": "vectorbt",
            "screening_scope": screening_scope,
            "event_id": event_id,
            "promoted": promoted,
            "promoted_ids": [p["candidate_id"] for p in promoted],
            "rejected": rejected,
            "feature_plane_status": "scheduled_event_only",
        }
    )


def _acceptance_persist(artifact, path: Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = dict(artifact)
    payload.setdefault("screening_artifact_hash", "three_gen_acceptance")
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return path


def _acceptance_robustness_fn(repo_root: Path):
    def _run(**kwargs):
        cid = str(kwargs.get("candidate_id") or "")
        out = repo_root / "runtime" / "reports" / "three_gen_rob" / cid
        out.mkdir(parents=True, exist_ok=True)
        wf_status = "PASS"
        wfc_status = "PASS"
        if cid.startswith("reject_wfc"):
            wfc_status = "FAIL"
        if cid.startswith("reject_wf"):
            wf_status = "FAIL"
        campaign_summary = {
            "status": wf_status,
            "wfc_status": wfc_status,
            "robustness_passed": wf_status == "PASS" and wfc_status == "PASS",
            "periods": [
                {"name": "Discovery", "gate_pass": wf_status == "PASS"},
                {"name": "Holdout", "gate_pass": wf_status == "PASS", "evaluate_only": True},
                {"name": "Recent holdout", "gate_pass": wf_status == "PASS", "evaluate_only": True},
            ],
            "wfc": {"pearson": 0.5 if wfc_status == "PASS" else -0.1, "spearman": 0.4 if wfc_status == "PASS" else -0.1, "wfc_status": wfc_status},
            "wfc_matrix_rows": [{"parameter_hash": f"acceptance_{cid}", "fold": 0}] if wfc_status == "PASS" else [],
            "metrics": {},
        }
        (out / "summary.json").write_text(json.dumps(campaign_summary), encoding="utf-8")
        return {
            "robustness_pass": campaign_summary["robustness_passed"],
            "regular_walk_forward_pass": wf_status == "PASS",
            "wfc_pass": wfc_status == "PASS",
            "metrics": {},
            "campaign_id": f"rob_{cid}",
            "campaign_summary": campaign_summary,
            "artifact_dir": str(out),
        }

    return _run


def _acceptance_hft_fn(repo_root: Path):
    from types import SimpleNamespace

    def _manifest_for_candidate(candidate_id: str, config) -> dict[str, Any]:
        if not config:
            return {}
        manifests_path = Path(config.out_dir).parent / "candidate_manifests.jsonl"
        if manifests_path.is_file():
            for line in manifests_path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                row = json.loads(line)
                if str(row.get("candidate_id")) == candidate_id:
                    return row
        return {}

    def _screening_artifact_hash(config) -> str:
        if not config:
            return ""
        for path in sorted(Path(config.out_dir).parent.glob("*/screening_artifact.json")):
            artifact = json.loads(path.read_text(encoding="utf-8"))
            if artifact.get("screening_artifact_hash"):
                return str(artifact["screening_artifact_hash"])
        return ""

    def _robustness_artifact_hash(candidate_id: str) -> str:
        from backtest_pipeline.src.hft_campaign._hashing import sha256_hex

        summary_path = repo_root / "runtime" / "reports" / "three_gen_rob" / candidate_id / "summary.json"
        if not summary_path.is_file():
            return ""
        return sha256_hex(json.loads(summary_path.read_text(encoding="utf-8")))

    def _run(**kwargs):
        cid = str((kwargs.get("scenarios") or [{}])[0].candidate_id if kwargs.get("scenarios") else kwargs.get("candidate_id") or "")
        if not cid and kwargs.get("scenarios"):
            cid = str(getattr(kwargs["scenarios"][0], "candidate_id", ""))
        scenarios = kwargs.get("scenarios") or []
        config = kwargs.get("config")
        out = repo_root / "runtime" / "reports" / "three_gen_hft" / (cid or "unknown")
        out.mkdir(parents=True, exist_ok=True)
        status = "completed"
        cert = "scheduled_event_replay_not_full_feature_plane"
        if str(cid).startswith("reject_hft"):
            status = "failed"
            cert = "fail"
        screening_hash = _screening_artifact_hash(config)
        return SimpleNamespace(
            status="pass" if status == "completed" else "fail",
            summary={"status": "pass" if status == "completed" else "fail"},
            scenario_results=[
                SimpleNamespace(
                    scenario_id=str(getattr(s, "scenario_id", "s1")),
                    status=status,
                    replay_result={
                        "candidate_id": str(getattr(s, "candidate_id", cid)),
                        "manifest_hash": str(
                            getattr(s, "manifest_hash", "")
                            or _manifest_for_candidate(str(getattr(s, "candidate_id", cid)), config).get("manifest_hash")
                            or ""
                        ),
                        "feature_recipe_hash": str(
                            getattr(s, "feature_recipe_hash", "")
                            or _manifest_for_candidate(str(getattr(s, "candidate_id", cid)), config).get("feature_recipe_hash")
                            or ""
                        ),
                        "screening_artifact_hash": str(getattr(s, "upstream_screening_artifact_hash", "") or screening_hash),
                        "robustness_artifact_hash": _robustness_artifact_hash(str(getattr(s, "candidate_id", cid))),
                        "certification_status": cert,
                    },
                    artifact_dir=str(out),
                )
                for s in scenarios
            ],
        )

    return _run


def _seed_candidates_for_generation(
    *,
    parsed: ParsedHypothesis,
    generation_index: int,
    prior_summary: dict[str, Any] | None,
    tested_hashes: set[str],
) -> list[CandidateModel]:
    if generation_index == 0:
        seeds = [
            CandidateModel("pass_elite", "SPREAD_BLOWOUT_RECOMPRESSION", {"signal_threshold": 0.15, "holding_period_bars": 15}, parsed.thesis, {}),
            CandidateModel("reject_vbt_1", "SPREAD_BLOWOUT_RECOMPRESSION", {"signal_threshold": 0.20, "holding_period_bars": 15}, parsed.thesis, {}),
            CandidateModel("block_ontology_1", "SPREAD_BLOWOUT_RECOMPRESSION", {"signal_threshold": 0.25, "holding_period_bars": 15}, parsed.thesis, {"ontology_citations": [{"paper_id": "fake-2099", "spec_ref": "FAKE.md"}]}),
            CandidateModel("reject_wfc_1", "SPREAD_BLOWOUT_RECOMPRESSION", {"signal_threshold": 0.30, "holding_period_bars": 15}, parsed.thesis, {}),
        ]
        return [
            attach_feature_recipe_to_candidate(c, parsed=parsed, target_event_id="CPI_2024_09_11_TIGHT", target_symbol="MES")
            for c in seeds
        ]
    assert prior_summary is not None
    return propose_next_candidates(
        parsed=parsed,
        generation_summary=prior_summary,
        tested_hashes=tested_hashes,
        max_candidates=6,
        exploration_fraction=0.0,
        family_search_enabled=True,
        family_search_fraction=0.6,
        target_event_id="CPI_2024_09_11_TIGHT",
        target_symbol="MES",
    )


def _manifest_recipe_hashes(gen_dir_path: Path) -> dict[str, str]:
    """Map candidate_id → feature_recipe_hash from frozen manifests."""
    out: dict[str, str] = {}
    manifests_path = gen_dir_path / "candidate_manifests.jsonl"
    if not manifests_path.is_file():
        return out
    for line in manifests_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        cid = str(row.get("candidate_id") or "")
        if cid:
            out[cid] = str(row.get("feature_recipe_hash") or "")
    return out


def _parent_child_recipe_changes(
    gen_dir: Path,
    prior_summaries: dict[int, dict[str, Any]],
) -> list[dict[str, Any]]:
    manifests_path = gen_dir / "candidate_manifests.jsonl"
    if not manifests_path.is_file():
        return []
    gen_idx = int(gen_dir.name.split("_")[-1])
    prior = prior_summaries.get(gen_idx - 1) or {}
    prior_gen_dir = gen_dir.parent / f"generation_{gen_idx - 1:03d}"
    prior_manifest_hashes = _manifest_recipe_hashes(prior_gen_dir)
    parent_recipes = {
        str(r.get("candidate_id")): dict(r.get("feature_recipe") or {})
        for r in prior.get("candidates") or []
    }
    parent_hashes = {
        str(r.get("candidate_id")): str(r.get("feature_recipe_hash") or "")
        for r in prior.get("candidates") or []
    }
    parent_hashes.update(prior_manifest_hashes)
    changes: list[dict[str, Any]] = []
    for line in manifests_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        meta = dict(row.get("metadata") or {})
        parent = str(row.get("parent_candidate_id") or row.get("elite_parent") or meta.get("elite_parent") or "")
        child_recipe = dict(row.get("feature_recipe") or {})
        parent_recipe = parent_recipes.get(parent) or {}
        child_hash = str(row.get("feature_recipe_hash") or "")
        parent_hash = parent_hashes.get(parent, "")
        proposal_reason = str(row.get("proposal_reason") or meta.get("proposal_reason") or "")
        recipe_changed = bool(parent) and (
            (parent_recipe and child_recipe != parent_recipe)
            or (parent_hash and child_hash and child_hash != parent_hash)
        )
        changes.append(
            {
                "candidate_id": row.get("candidate_id"),
                "parent_id": parent or None,
                "proposal_reason": proposal_reason or row.get("proposal_reason"),
                "feature_recipe_hash": row.get("feature_recipe_hash"),
                "recipe_dimension_changed": recipe_changed,
                "family_variant_id": meta.get("family_variant_id"),
            }
        )
    return changes


def _summarize_generation(gen_dir: Path, gen_idx: int) -> dict[str, Any]:
    summary_path = gen_dir / "generation_summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    proposed_path = gen_dir / "proposed_candidates.json"
    proposed_count = 0
    if proposed_path.is_file():
        proposed_count = len(json.loads(proposed_path.read_text(encoding="utf-8")))
    else:
        proposed_count = int(summary.get("proposed_candidate_count") or len(summary.get("candidates") or []))

    reject_counts: Counter[str] = Counter()
    final_pass = 0
    for row in summary.get("candidates") or []:
        status = str(row.get("final_status") or "")
        if status == FINAL_PASS:
            final_pass += 1
        elif status in REJECT_STATUS_MAP:
            reject_counts[REJECT_STATUS_MAP[status]] += 1

    return {
        "generation_index": gen_idx,
        "proposed_count": proposed_count,
        "reject_counts": dict(reject_counts),
        "final_pass_count": final_pass,
        "summary": summary,
    }


def run_three_gen_acceptance(
    *,
    repo_root: Path | None = None,
    report_path: Path | None = None,
) -> dict[str, Any]:
    """Run deterministic three-generation campaign; return structured report payload."""
    repo_root = Path(repo_root or _REPO_ROOT)
    report_path = Path(report_path or REPORT_PATH)
    parsed = _parsed_hypothesis()

    npz = repo_root / "runtime" / "reports" / "three_gen_replay.npz"
    lat = repo_root / "runtime" / "reports" / "three_gen_latency.json"
    queue = repo_root / "runtime" / "reports" / "three_gen_queue.json"
    for path, content in (
        (npz, b""),
        (lat, '{"schema":"latency_model.v1","order_latency_ms":{"p50":1.0}}'),
        (queue, '{"schema":"fill_queue_model.v1"}'),
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.suffix == ".npz":
            from backtest_pipeline.src.replay_npz_fixture import build_minimal_mbo_npz

            build_minimal_mbo_npz(path)
        else:
            path.write_text(content, encoding="utf-8")

    cfg = AutoresearchConfig(
        max_generations=3,
        max_candidates_per_generation=6,
        exploration_fraction=0.0,
        family_search_enabled=True,
        family_search_fraction=0.6,
        run_robustness=True,
        run_hft_campaign=True,
        stop_no_improvement_generations=0,
        hft_source_npz=npz,
        hft_latency_model=lat,
        hft_fill_queue_model=queue,
    )

    import research_pipeline.generation_loop as gl

    def gen0_wrapper(*args, **kwargs):
        return _seed_candidates_for_generation(
            parsed=parsed,
            generation_index=0,
            prior_summary=None,
            tested_hashes=set(),
        )

    original_propose = gl.propose_next_candidates
    original_generate = gl.generate_candidates
    original_generate_scenario_manifest = gl.generate_scenario_manifest
    original_load_scenarios_from_manifest = gl.load_scenarios_from_manifest

    def propose_wrapper(**kwargs):
        cands = list(original_propose(**kwargs))
        prior = kwargs.get("generation_summary") or {}
        gen_idx = int(prior.get("generation_index") or 0) + 1
        if gen_idx >= 1:
            cands.append(
                attach_feature_recipe_to_candidate(
                    CandidateModel(
                        candidate_id=f"reject_stat_planted_g{gen_idx}",
                        model_id="SPREAD_BLOWOUT_RECOMPRESSION",
                        strategy_params={"signal_threshold": 0.35, "holding_period_bars": 20},
                        thesis=parsed.thesis,
                    ),
                    parsed=parsed,
                    target_event_id="CPI_2024_09_11_TIGHT",
                )
            )
        return cands

    scenario_store: dict[str, list[Any]] = {"scenarios": []}

    def fake_generate_scenario_manifest(mcfg):
        scenarios = [
            type(
                "Scenario",
                (),
                {
                    "scenario_id": f"sc_{cid}",
                    "candidate_id": cid,
                    "to_dict": lambda self, _cid=cid: {"scenario_id": f"sc_{cid}", "candidate_id": _cid},
                },
            )()
            for cid in (getattr(mcfg, "candidate_ids", None) or ["unknown"])
        ]
        scenario_store["scenarios"] = scenarios
        return scenarios, []

    gl.generate_candidates = gen0_wrapper
    gl.propose_next_candidates = propose_wrapper
    gl.generate_scenario_manifest = fake_generate_scenario_manifest
    gl.load_scenarios_from_manifest = lambda _path: scenario_store["scenarios"]
    try:
        code, loop_report = run_autoresearch_loop(
            repo_root=repo_root,
            thesis=parsed.thesis,
            event_id="CPI_2024_09_11_TIGHT",
            cfg=cfg,
            parsed=parsed,
            no_llm=True,
            filter_fn=_acceptance_filter,
            persist_fn=_acceptance_persist,
            robustness_fn=_acceptance_robustness_fn(repo_root),
            hft_fn=_acceptance_hft_fn(repo_root),
        )
    finally:
        gl.propose_next_candidates = original_propose
        gl.generate_candidates = original_generate
        gl.generate_scenario_manifest = original_generate_scenario_manifest
        gl.load_scenarios_from_manifest = original_load_scenarios_from_manifest

    campaign_id = loop_report["campaign_id"]
    manifest = load_manifest(repo_root, campaign_id)
    dedup_count = len(manifest.get("tested_parameter_hashes") or [])

    generation_summaries: list[dict[str, Any]] = []
    recipe_changes: dict[int, list[dict[str, Any]]] = {}
    prior_summaries: dict[int, dict[str, Any]] = {}

    for gen_idx in range(loop_report.get("generations_run") or 0):
        gen_dir = generation_dir(repo_root, campaign_id, gen_idx)
        gen_summary = _summarize_generation(gen_dir, gen_idx)
        generation_summaries.append(gen_summary)
        prior_summaries[gen_idx] = gen_summary["summary"]
        if gen_idx >= 1:
            recipe_changes[gen_idx] = _parent_child_recipe_changes(gen_dir, prior_summaries)

    recipe_dimension_change_gen2 = False
    if recipe_changes.get(2):
        recipe_dimension_change_gen2 = any(r.get("recipe_dimension_changed") for r in recipe_changes[2])

    payload = {
        "mode": "fixture_dry_run",
        "live_data_required": False,
        "exit_code": code,
        "campaign_id": campaign_id,
        "generations_run": loop_report.get("generations_run"),
        "stop_reason": loop_report.get("stop_reason") or "max_generations_reached",
        "deduplication_tested_hash_count": dedup_count,
        "generations": generation_summaries,
        "recipe_changes": recipe_changes,
        "recipe_dimension_change_gen2": recipe_dimension_change_gen2,
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(_format_report(payload), encoding="utf-8")
    payload["report_path"] = str(report_path)
    return payload


def _format_report(payload: dict[str, Any]) -> str:
    lines = [
        "# Autoresearch three-generation acceptance (2026-06-20)",
        "",
        f"- **Mode:** {payload['mode']} (no live VectorBT/HftBacktest compute)",
        f"- **Exit code:** {payload['exit_code']}",
        f"- **Campaign ID:** `{payload['campaign_id']}`",
        f"- **Generations run:** {payload['generations_run']}",
        f"- **Stop reason:** {payload['stop_reason']}",
        f"- **Deduplication (tested_parameter_hashes):** {payload['deduplication_tested_hash_count']}",
        f"- **Gen-2 real feature-recipe dimension change:** {payload['recipe_dimension_change_gen2']}",
        "",
    ]
    for gen in payload["generations"]:
        idx = gen["generation_index"]
        lines.append(f"## Generation {idx}")
        lines.append("")
        lines.append(f"- Proposed candidates: **{gen['proposed_count']}**")
        lines.append(f"- FINAL_PASS: **{gen['final_pass_count']}**")
        rejects = gen["reject_counts"]
        if rejects:
            lines.append("- Gate rejects by type:")
            for gate, count in sorted(rejects.items()):
                lines.append(f"  - {gate}: {count}")
        else:
            lines.append("- Gate rejects by type: none")
        lines.append("")

    for gen_idx, changes in sorted(payload.get("recipe_changes", {}).items()):
        lines.append(f"## Generation {gen_idx} parent-child recipe changes")
        lines.append("")
        if not changes:
            lines.append("_No manifest rows._")
        else:
            for ch in changes:
                lines.append(
                    f"- `{ch['candidate_id']}` parent=`{ch.get('parent_id')}` "
                    f"reason=`{ch.get('proposal_reason')}` "
                    f"recipe_changed={ch.get('recipe_dimension_changed')} "
                    f"variant=`{ch.get('family_variant_id')}`"
                )
        lines.append("")

    lines.extend(
        [
            "## Blockers / honesty",
            "",
            "This acceptance run uses planted fake runners and minimal NPZ fixtures.",
            "It does **not** certify live paid-screen throughput or CHI404 replay latency.",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    payload = run_three_gen_acceptance()
    print(f"Wrote {payload['report_path']}")
    return int(payload["exit_code"])


if __name__ == "__main__":
    raise SystemExit(main())
