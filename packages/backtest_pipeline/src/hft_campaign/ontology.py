"""Vault ontology gates for HftBacktest campaign (fail-closed)."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from backtest_pipeline.src.hftbacktest_realism import _source_lock_has_hash_backed_native_hot_path_evidence

FEATURE_PLANE_STATUSES = frozenset(
    {
        "feature_complete_pit_declared",
        "scheduled_event_only",
        "bar_stub_research_only",
        "incomplete_feature_plane",
    }
)

CANONICAL_ONTOLOGY_DOCS = (
    "docs/project/FEATURE_LITERATURE_TRACEABILITY_MATRIX.md",
    "docs/cockpit/MACRO_CONTEXT_VIX_OPTIONS_CHECKLIST.md",
    "docs/project/HFTBACKTEST_CAMPAIGN_ARCHITECTURE.md",
    "docs/project/VECTORBT_TO_HFTBACKTEST_HANDOFF.md",
    "docs/vault/HFTBACKTEST_LATENCY_ONTOLOGY.md",
)

VAULT_GATE_STAMP = Path("runtime/vault-gate/.last-vault-gate.json")
VAULT_GATE_MAX_AGE_MINUTES = 240


def validate_feature_plane_status(status: str) -> list[str]:
    if status not in FEATURE_PLANE_STATUSES:
        return [f"feature_plane_status_invalid:{status}"]
    return []


def validate_screening_feature_plane(screening: Mapping[str, Any]) -> list[str]:
    reasons: list[str] = []
    status = str(screening.get("feature_plane_status", "scheduled_event_only"))
    reasons.extend(validate_feature_plane_status(status))
    if status == "feature_complete_pit_declared":
        proof = screening.get("feature_plane_pit_proof")
        if not isinstance(proof, Mapping) or str(proof.get("status", "")).lower() != "pass":
            reasons.append("feature_complete_pit_declared_missing_proof")
    return reasons


def load_vault_gate_receipt(repo_root: Path) -> tuple[dict[str, Any], list[str]]:
    reasons: list[str] = []
    stamp_path = Path(repo_root) / VAULT_GATE_STAMP
    if not stamp_path.is_file():
        return {}, ["vault_gate_stamp_missing:run_scripts_vault_gate_ps1"]
    try:
        raw = stamp_path.read_text(encoding="utf-8-sig")
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return {}, ["vault_gate_stamp_invalid_json"]
    ts_raw = payload.get("timestamp_utc")
    if not ts_raw:
        reasons.append("vault_gate_stamp_missing_timestamp")
    else:
        try:
            stamp_ts = datetime.fromisoformat(str(ts_raw).replace("Z", "+00:00"))
        except ValueError:
            reasons.append("vault_gate_stamp_invalid_timestamp")
            stamp_ts = None
        if stamp_ts is not None:
            if stamp_ts.tzinfo is None:
                stamp_ts = stamp_ts.replace(tzinfo=timezone.utc)
            age_min = (datetime.now(timezone.utc) - stamp_ts).total_seconds() / 60.0
            if age_min > VAULT_GATE_MAX_AGE_MINUTES:
                reasons.append(f"vault_gate_stamp_stale:{age_min:.0f}m")
    hot = str(payload.get("hot_excerpt", ""))
    if len(hot) < 40:
        reasons.append("vault_gate_hot_excerpt_missing")
    return payload, reasons


def assert_campaign_ontology_prerequisites(repo_root: Path) -> list[str]:
    """Fail-closed campaign prerequisites from vault ontology."""
    reasons: list[str] = []
    _, vault_reasons = load_vault_gate_receipt(repo_root)
    reasons.extend(vault_reasons)
    for rel in CANONICAL_ONTOLOGY_DOCS:
        if not (Path(repo_root) / rel).is_file():
            reasons.append(f"canonical_ontology_doc_missing:{rel}")
    return list(dict.fromkeys(reasons))


def resolve_certification_status(
    *,
    scenario_feature_plane: str,
    replay_tier: str,
    transitional_handoff: bool,
    accelerated_mode: bool,
    source_lock_reasons: list[str],
    replay_passed: bool,
    source_lock: Mapping[str, Any] | None = None,
) -> str:
    if transitional_handoff or accelerated_mode:
        return "accelerated_not_certifying"
    if not replay_passed:
        return "fail"
    if replay_tier == "stage1_minimal":
        return "integration_smoke_not_production"
    if source_lock_reasons or not _source_lock_has_hash_backed_native_hot_path_evidence(source_lock or {}):
        return "missing_native_hot_path_evidence"
    if scenario_feature_plane != "feature_complete_pit_declared":
        return "scheduled_event_replay_not_full_feature_plane"
    return "full_fidelity_declared"


def scenarios_for_config_stages(
    scenarios: list[Any],
    stages: tuple[int, ...],
) -> tuple[list[Any], list[Any]]:
    """Return (runnable, skipped) scenarios based on configured stages."""
    if not stages:
        return scenarios, []
    tier_by_stage = {
        1: "stage1_minimal",
        2: "stage2_individual",
        3: "stage3_stress",
        4: "stage4_combined",
        5: "stage5_discrepancy",
    }
    allowed = {tier_by_stage[s] for s in stages if s in tier_by_stage}
    runnable = [s for s in scenarios if getattr(s, "replay_tier", "") in allowed]
    skipped = [s for s in scenarios if getattr(s, "replay_tier", "") not in allowed]
    return runnable, skipped
