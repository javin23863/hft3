"""Unified research pipeline stage registry — chronological order, ontology, lifecycle spine.

Authority: docs/vault/UNIFIED_RESEARCH_PIPELINE.md
Vault: library/14 Model Lifecycle and Governance.md, library/13 Robust Backtesting and Multiple Testing.md
Literature: docs/references/Ultimate_Quantitative_Finance_Researcher.pdf (walk-forward, MC);
  docs/references/chicago_cme_microstructure_mathematical_model.pdf (execution realism);
  docs/references/algorithmic_trading_strategy_development.pdf (structural models, separate lane).

Stages are strict chronological order. Each downstream stage consumes upstream artifacts only;
honest ``not_run`` sentinels document what is intentionally skipped (e.g. pilot VBT has no full MC/WF).
Trade-manager behavior tracking (`packages/trade_manager/model_behavior.py`) and lifecycle registry
(`packages/model_metrics/lifecycle.py`) are the runtime spine from Stage 5 onward.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Mapping, MutableMapping, Optional, Sequence

# --- Stage IDs (chronological) ------------------------------------------------

STAGE_0_ONTOLOGY = "stage_0_ontology"
STAGE_1_VECTORBT_SCREEN = "stage_1_vectorbt_screen"
STAGE_2_PROMOTED_AGGREGATION = "stage_2_promoted_aggregation"
STAGE_3_HFTBACKTEST_REALISM = "stage_3_hftbacktest_realism"
STAGE_4_WORKBENCH_ROBUSTNESS = "stage_4_workbench_robustness"
STAGE_5_LIFECYCLE_BEHAVIOR = "stage_5_lifecycle_behavior_tracking"
STAGE_6_PROMOTION_CERTIFICATION = "stage_6_promotion_certification"
STAGE_7_LIVE_PAPER_CHI404 = "stage_7_live_paper_chi404"

CHRONOLOGICAL_STAGE_IDS: Sequence[str] = (
    STAGE_0_ONTOLOGY,
    STAGE_1_VECTORBT_SCREEN,
    STAGE_2_PROMOTED_AGGREGATION,
    STAGE_3_HFTBACKTEST_REALISM,
    STAGE_4_WORKBENCH_ROBUSTNESS,
    STAGE_5_LIFECYCLE_BEHAVIOR,
    STAGE_6_PROMOTION_CERTIFICATION,
    STAGE_7_LIVE_PAPER_CHI404,
)

UNIFIED_PIPELINE_DOC = "docs/vault/UNIFIED_RESEARCH_PIPELINE.md"


@dataclass(frozen=True)
class PipelineStage:
    stage_id: str
    ordinal: int
    name: str
    lifecycle_state: str
    vault_notes: tuple[str, ...]
    literature_refs: tuple[str, ...]
    code_entrypoints: tuple[str, ...]
    input_artifact: str
    output_artifact: str
    not_run_at_stage: tuple[str, ...]
    trade_manager_hook: str


PIPELINE_STAGES: dict[str, PipelineStage] = {
    STAGE_0_ONTOLOGY: PipelineStage(
        stage_id=STAGE_0_ONTOLOGY,
        ordinal=0,
        name="Ontology and literature grounding",
        lifecycle_state="(pre-registry)",
        vault_notes=(
            "wiki/hot.md",
            "Memory Stack.md",
            "library/Ontology.md",
            "library/System Implications.md",
            "library/14 Model Lifecycle and Governance.md",
        ),
        literature_refs=(
            "docs/references/Ultimate_Quantitative_Finance_Researcher.pdf",
            "docs/project/FEATURE_LITERATURE_TRACEABILITY_MATRIX.md",
        ),
        code_entrypoints=(
            "scripts/vault_gate.ps1",
            "scripts/vault_pre_edit.ps1",
            "packages/backtest_pipeline/src/hft_campaign/ontology.py",
        ),
        input_artifact="(task query + vault consult stamp)",
        output_artifact="runtime/vault-gate/.last-vault-gate.json",
        not_run_at_stage=("VectorBT screen", "HftBacktest replay", "Monte Carlo", "live orders"),
        trade_manager_hook="none — governance prep only",
    ),
    STAGE_1_VECTORBT_SCREEN: PipelineStage(
        stage_id=STAGE_1_VECTORBT_SCREEN,
        ordinal=1,
        name="Feature families + VectorBT paid/pilot screen",
        lifecycle_state="CANDIDATE → SCREENING",
        vault_notes=(
            "decisions/2026-06-17 Feature-complete research authority correction.md",
            "pipelines/unified-research-pipeline.md",
        ),
        literature_refs=(
            "docs/project/VECTORBT_SCREENING_ENGINE_SPEC.md",
            "docs/project/OPPORTUNITY_RESEARCH_SPEC.md",
            "docs/references/dev_instructions.pdf",
        ),
        code_entrypoints=(
            "scripts/run_paid_screen.py",
            "scripts/run_pipeline.py --vectorbt",
            "packages/backtest_pipeline/src/vectorbt_adapter.py",
            "packages/backtest_pipeline/src/feature_plane.py",
            "packages/backtest_pipeline/src/fs_v1_screen_path.py",
        ),
        input_artifact="events.csv × model registry × NPZ/feature store",
        output_artifact="research_cards/pipeline_runs/<run_id>/screening_artifact.json",
        not_run_at_stage=(
            "full Monte Carlo",
            "full walk-forward fold matrix (pilot: pilot_summary_only)",
            "official HftBacktest replay",
            "live/paper execution",
        ),
        trade_manager_hook="annotate research_card_links on SCREENING enrollment (optional HFT3_PIPELINE_LIFECYCLE_ENROLL=1)",
    ),
    STAGE_2_PROMOTED_AGGREGATION: PipelineStage(
        stage_id=STAGE_2_PROMOTED_AGGREGATION,
        ordinal=2,
        name="Promoted candidate aggregation + promotion gate",
        lifecycle_state="SCREENING",
        vault_notes=("library/13 Robust Backtesting and Multiple Testing.md",),
        literature_refs=(
            "docs/references/Ultimate_Quantitative_Finance_Researcher.pdf §multiple testing",
            "docs/project/ROBUSTNESS_TESTING_SPEC.md",
        ),
        code_entrypoints=(
            "packages/backtest_pipeline/src/promotion_gate.py",
            "packages/backtest_pipeline/src/vectorbt_adapter.py::apply_promotion_gates",
            "packages/backtest_pipeline/src/recipe_hash_gate.py",
        ),
        input_artifact="screening_artifact.json promoted[] rows",
        output_artifact="PromotedCandidate records + promoted_ids in screening_artifact.json",
        not_run_at_stage=(
            "execution-realistic replay",
            "workbench MC/Bonferroni pack",
            "certification envelope mint",
        ),
        trade_manager_hook="promoted rows carry hypothesis_id for lifecycle registry slug",
    ),
    STAGE_3_HFTBACKTEST_REALISM: PipelineStage(
        stage_id=STAGE_3_HFTBACKTEST_REALISM,
        ordinal=3,
        name="HftBacktest execution realism (promoted-only)",
        lifecycle_state="SCREENING → GAUNTLET",
        vault_notes=(
            "docs/vault/HFTBACKTEST_LATENCY_ONTOLOGY.md",
            "library/14 Model Lifecycle and Governance.md",
        ),
        literature_refs=(
            "docs/references/chicago_cme_microstructure_mathematical_model.pdf",
            "docs/project/HFTBACKTEST_REALISM_ENGINE_SPEC.md",
            "docs/references/rithmic_trial_hftbacktest_pipeline_prompt.pdf",
        ),
        code_entrypoints=(
            "scripts/run_hftbacktest_realism.py",
            "scripts/run_pipeline.py --hftbacktest-realism",
            "packages/backtest_pipeline/src/hftbacktest_realism.py",
            "packages/backtest_pipeline/src/hft_campaign/",
        ),
        input_artifact="screening_artifact.json (screen-passed, recipe_hash match)",
        output_artifact="research_cards/hftbacktest_realism/<run_id>/replay_summary.json",
        not_run_at_stage=(
            "VectorBT broad rescreen",
            "workbench full MC matrix",
            "autonomous promotion MC/WF gates (unless workbench evidence wired)",
        ),
        trade_manager_hook="replay order lifecycle → trade_manager order_state audit; lifecycle annotate GAUNTLET handoff",
    ),
    STAGE_4_WORKBENCH_ROBUSTNESS: PipelineStage(
        stage_id=STAGE_4_WORKBENCH_ROBUSTNESS,
        ordinal=4,
        name="Workbench robustness pack (MC, WF, purged CV, Bonferroni)",
        lifecycle_state="GAUNTLET",
        vault_notes=("library/13 Robust Backtesting and Multiple Testing.md",),
        literature_refs=(
            "docs/references/Ultimate_Quantitative_Finance_Researcher.pdf",
            "docs/vault/BACKTESTER_CERTIFICATION.md",
            "docs/project/ROBUSTNESS_TESTING_SPEC.md",
        ),
        code_entrypoints=(
            "apps/workbench/src/robustness/pack.py",
            "python -m workbench run",
            "python -m workbench campaign",
            "packages/hft3/research/run_autonomous.py::stage_robustness_and_wf",
        ),
        input_artifact="HBT-realism metrics or workbench campaign summary",
        output_artifact="research_cards/workbench_runs/<run_id>/ + robustness_gates.json",
        not_run_at_stage=(
            "VectorBT pilot lightweight _simulate_walk_forward only scope",
            "live Rithmic orders",
        ),
        trade_manager_hook="robustness evidence feeds ModelBehaviorEnvelope inputs at certify",
    ),
    STAGE_5_LIFECYCLE_BEHAVIOR: PipelineStage(
        stage_id=STAGE_5_LIFECYCLE_BEHAVIOR,
        ordinal=5,
        name="Trade manager lifecycle enrollment + behavior tracking",
        lifecycle_state="GAUNTLET → CERTIFIED (envelope freeze)",
        vault_notes=("library/14 Model Lifecycle and Governance.md",),
        literature_refs=(
            "docs/references/Ultimate_Quantitative_Finance_Researcher.pdf",
            "specs/MODEL_LIFECYCLE.md",
        ),
        code_entrypoints=(
            "packages/model_metrics/lifecycle.py",
            "packages/trade_manager/model_behavior.py",
            "packages/model_metrics/decay_detector.py",
            "packages/trade_manager/risk_layer.py",
            "packages/lifecycle_orchestrator/src/run_lifecycle_eval.py",
        ),
        input_artifact="robustness + HBT artifacts + promoted candidate metadata",
        output_artifact="runtime/lifecycle/model_lifecycle.json + envelopes/<id>.json",
        not_run_at_stage=("VectorBT screen", "broad discovery rescan"),
        trade_manager_hook="ModelBehaviorRuleEngine + submit_gate enforce GREEN/YELLOW/RED on LIVE path",
    ),
    STAGE_6_PROMOTION_CERTIFICATION: PipelineStage(
        stage_id=STAGE_6_PROMOTION_CERTIFICATION,
        ordinal=6,
        name="Promotion and certification gates",
        lifecycle_state="CERTIFIED → SHADOW → LIVE",
        vault_notes=("decisions/", "validation/"),
        literature_refs=(
            "docs/VALIDATION_HONESTY.md",
            "docs/REVIEWER_CHARTER.md",
            "packages/hft3/validation/promotion_gate.py",
        ),
        code_entrypoints=(
            "packages/model_metrics/certify.py",
            "packages/hft3/validation/promotion_gate.py",
            "packages/lifecycle_orchestrator/src/rearm.py",
            "packages/autonomy/gates.py",
        ),
        input_artifact="frozen behavior envelope + robustness gate artifacts",
        output_artifact="certification stamp + transitions.jsonl audit",
        not_run_at_stage=("workstation live capture", "autonomy arm without defect-ledger clean"),
        trade_manager_hook="submit_gate blocks DEGRADED-RED; kill_switch separate from autonomy kill",
    ),
    STAGE_7_LIVE_PAPER_CHI404: PipelineStage(
        stage_id=STAGE_7_LIVE_PAPER_CHI404,
        ordinal=7,
        name="Live/paper lane (CHI404 CME only)",
        lifecycle_state="LIVE (+ DEGRADED routes)",
        vault_notes=("docs/vault/CHI404_CANONICAL_ENTRYPOINTS.md", "BLUEPRINT.md §4"),
        literature_refs=(
            "docs/references/chicago_cme_a_plus_production_implementation_prompt.pdf",
            "docs/rithmic_trial/README.md",
        ),
        code_entrypoints=(
            "scripts/chi404_run_trial_live.sh",
            "data_system/rithmic_trial/pipeline.py",
            "packages/trade_manager/execution_boundary.py",
        ),
        input_artifact="LIVE lifecycle state + submit_gate pass + CHI404 latency evidence",
        output_artifact="runtime/replay_audits/*_order_lifecycle.jsonl + trial quarantine paths",
        not_run_at_stage=("workstation Windows capture", "VectorBT rescreen", "workbench-only paths"),
        trade_manager_hook="execution_boundary + risk_layer pre-trade; decay_detector → lifecycle eval loop",
    ),
}


def get_stage(stage_id: str) -> PipelineStage:
    if stage_id not in PIPELINE_STAGES:
        raise KeyError(f"unknown pipeline stage: {stage_id}")
    return PIPELINE_STAGES[stage_id]


def pipeline_stage_stamp(stage_id: str) -> dict[str, Any]:
    """Return canonical pipeline metadata fields for artifact stamping."""
    stage = get_stage(stage_id)
    return {
        "research_pipeline_stage_id": stage.stage_id,
        "research_pipeline_stage_ordinal": stage.ordinal,
        "research_pipeline_stage_name": stage.name,
        "research_pipeline_lifecycle_state": stage.lifecycle_state,
        "research_pipeline_ontology_doc": UNIFIED_PIPELINE_DOC,
        "research_pipeline_vault_notes": list(stage.vault_notes),
        "research_pipeline_literature_refs": list(stage.literature_refs),
        "research_pipeline_trade_manager_hook": stage.trade_manager_hook,
    }


def stamp_artifact(artifact: MutableMapping[str, Any], stage_id: str) -> MutableMapping[str, Any]:
    """Merge pipeline stage metadata into an artifact dict (in place)."""
    artifact.update(pipeline_stage_stamp(stage_id))
    return artifact


def record_lifecycle_pipeline_handoff(
    model_lifecycle_id: str,
    stage_id: str,
    *,
    artifact_path: Optional[str] = None,
    actor: str = "research_pipeline",
    reason: Optional[str] = None,
) -> bool:
    """Annotate an existing lifecycle record with pipeline stage artifact links.

    Does not create models or change lifecycle state. Returns True when annotated.
    """
    if not model_lifecycle_id:
        return False
    try:
        from model_metrics import lifecycle
    except ImportError:
        return False
    rec = lifecycle.get_record(model_lifecycle_id)
    if rec is None:
        return False
    merged_links = dict(rec.research_card_links or {})
    merged_links[f"pipeline_stage_{stage_id}"] = {
        "stage_id": stage_id,
        "artifact_path": artifact_path,
        "ontology_doc": UNIFIED_PIPELINE_DOC,
    }
    lifecycle.annotate(
        model_lifecycle_id,
        {"research_card_links": merged_links},
        reason=reason or f"pipeline handoff {stage_id}",
        actor=actor,
    )
    return True


def maybe_enroll_lifecycle_transition(
    model_lifecycle_id: str,
    stage_id: str,
    *,
    hypothesis_id: Optional[str] = None,
    symbol: Optional[str] = None,
    actor: str = "research_pipeline",
) -> bool:
    """Optional lifecycle state advance when HFT3_PIPELINE_LIFECYCLE_ENROLL=1.

    Fail-closed: unknown models are not auto-created unless enrolling from stage 1.
    """
    if os.environ.get("HFT3_PIPELINE_LIFECYCLE_ENROLL", "").strip() not in ("1", "true", "yes"):
        return False
    try:
        from model_metrics import lifecycle
    except ImportError:
        return False
    rec = lifecycle.get_record(model_lifecycle_id)
    target_by_stage = {
        STAGE_1_VECTORBT_SCREEN: lifecycle.SCREENING,
        STAGE_3_HFTBACKTEST_REALISM: lifecycle.GAUNTLET,
    }
    target = target_by_stage.get(stage_id)
    if target is None:
        return False
    if rec is None:
        if stage_id != STAGE_1_VECTORBT_SCREEN:
            return False
        lifecycle.apply_transition(
            model_lifecycle_id,
            lifecycle.CANDIDATE,
            trigger="pipeline_enroll",
            reason=f"vectorbt screen handoff ({stage_id})",
            actor=actor,
            create=True,
            initial={"hypothesis_id": hypothesis_id, "symbol": symbol},
        )
        rec = lifecycle.get_record(model_lifecycle_id)
    if rec is None:
        return False
    if rec.current_state == target:
        return True
    try:
        lifecycle.apply_transition(
            model_lifecycle_id,
            target,
            trigger="pipeline_handoff",
            reason=f"research pipeline stage {stage_id}",
            actor=actor,
        )
    except lifecycle.IllegalTransitionError:
        return False
    return True


def annotate_promoted_screening_handoffs(
    artifact: Mapping[str, Any],
    *,
    artifact_path: Optional[str] = None,
) -> None:
    """Record lifecycle links for promoted hypothesis ids after VectorBT screen."""
    promoted = artifact.get("promoted") or []
    if not isinstance(promoted, list):
        return
    stage_id = (
        STAGE_2_PROMOTED_AGGREGATION
        if int(artifact.get("promoted_count") or len(promoted)) > 0
        else STAGE_1_VECTORBT_SCREEN
    )
    for row in promoted:
        if not isinstance(row, Mapping):
            continue
        model_id = str(row.get("hypothesis_id") or row.get("model_id") or "").strip()
        if not model_id:
            continue
        maybe_enroll_lifecycle_transition(
            model_id,
            stage_id,
            hypothesis_id=model_id,
            symbol=str(row.get("symbol") or ""),
        )
        record_lifecycle_pipeline_handoff(
            model_id,
            stage_id,
            artifact_path=artifact_path,
        )


__all__ = [
    "CHRONOLOGICAL_STAGE_IDS",
    "PIPELINE_STAGES",
    "PipelineStage",
    "STAGE_0_ONTOLOGY",
    "STAGE_1_VECTORBT_SCREEN",
    "STAGE_2_PROMOTED_AGGREGATION",
    "STAGE_3_HFTBACKTEST_REALISM",
    "STAGE_4_WORKBENCH_ROBUSTNESS",
    "STAGE_5_LIFECYCLE_BEHAVIOR",
    "STAGE_6_PROMOTION_CERTIFICATION",
    "STAGE_7_LIVE_PAPER_CHI404",
    "UNIFIED_PIPELINE_DOC",
    "annotate_promoted_screening_handoffs",
    "get_stage",
    "maybe_enroll_lifecycle_transition",
    "pipeline_stage_stamp",
    "record_lifecycle_pipeline_handoff",
    "stamp_artifact",
]
