#!/usr/bin/env python3
"""Audit prior session handoff claims through ontology gate drift + scope honesty.

Writes runtime/reports/ontology_gate_prior_claims_audit.json (fail-closed record).
Spec: docs/project/ONTOLOGY_GATE_AGENT_SPEC.md §7
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from hft3_bootstrap import setup_repo_paths

setup_repo_paths()

from backtest_pipeline.src.ontology_gate import (  # noqa: E402
    check_drift,
    check_scope_honesty,
    validate_fable_entry_checklist,
    run_gate,
)

_OUT = _REPO / "runtime" / "reports" / "ontology_gate_prior_claims_audit.json"

_PRIOR_CLAIMS: list[dict[str, str]] = [
    {
        "id": "vast_bare_rescan",
        "summary": "Vast M6 launched run_event_universe --rescan without VectorBT screening artifact",
        "drift_text": (
            "Full HftBacktest universe rescan on Vast without vectorbt screening artifact. "
            "Treat vectorbt screening as hft execution realism evidence."
        ),
        "scope_flags": {
            "subset_pytest_claimed_as_scope_green": False,
            "waived_verify_claimed_as_done": False,
            "plan_todo_theater": False,
            "scope_green_without_exit_code": True,
            "missing_verify_tail": True,
        },
    },
    {
        "id": "hyp5_two_slots_as_full_truth",
        "summary": "HYP_5 evaluate() two slots described as complete feature-family coverage",
        "drift_text": (
            "Feature lake existence equals model consumption. "
            "spread_stress and book_slope are the full feature plane for all hypotheses."
        ),
        "scope_flags": {},
        "drift_artifact": {
            "lake_existence_as_feature_usage": True,
        },
    },
    {
        "id": "merge_ready_theater",
        "summary": "merge-ready claimed with 0/285840 units and instance down",
        "drift_text": "merge-ready yes scope-green without pytest exit code",
        "scope_flags": {
            "subset_pytest_claimed_as_scope_green": True,
            "missing_verify_tail": True,
            "scope_green_without_exit_code": True,
        },
    },
    {
        "id": "parallel_vbt_scope_doc",
        "summary": "VBT_RESEARCH_PRODUCT_SCOPE as canonical authority",
        "drift_text": (
            "New source-of-truth VBT_RESEARCH_PRODUCT_SCOPE product scope document "
            "instead of OPPORTUNITY_RESEARCH_SPEC and VECTORBT_SCREENING_ENGINE_SPEC."
        ),
        "scope_flags": {},
        "drift_artifact": {"creates_parallel_authority_docs": True},
    },
]


def _audit_one(entry: dict) -> dict:
    drift = check_drift(
        text=entry.get("drift_text"),
        artifact=entry.get("drift_artifact"),
    )
    flags = entry.get("scope_flags") or {}
    scope = check_scope_honesty(
        subset_pytest_claimed_as_scope_green=bool(flags.get("subset_pytest_claimed_as_scope_green")),
        waived_verify_claimed_as_done=bool(flags.get("waived_verify_claimed_as_done")),
        plan_todo_theater=bool(flags.get("plan_todo_theater")),
        scope_green_without_exit_code=bool(flags.get("scope_green_without_exit_code")),
        missing_verify_tail=bool(flags.get("missing_verify_tail")),
    )
    fable = validate_fable_entry_checklist(
        grounded=True,
        vault_read=True,
        authority_located=True,
        no_assumptions=True,
        fable_active=True,
    )
    gate = run_gate(
        fable_checklist=fable,
        area="backtest_pipeline",
        drift_text=entry.get("drift_text"),
        drift_artifact=entry.get("drift_artifact"),
        subset_pytest_claimed_as_scope_green=bool(flags.get("subset_pytest_claimed_as_scope_green")),
        waived_verify_claimed_as_done=bool(flags.get("waived_verify_claimed_as_done")),
        plan_todo_theater=bool(flags.get("plan_todo_theater")),
        scope_green_without_exit_code=bool(flags.get("scope_green_without_exit_code")),
        missing_verify_tail=bool(flags.get("missing_verify_tail")),
    )
    return {
        "id": entry["id"],
        "summary": entry["summary"],
        "drift": {
            "clean": drift.clean,
            "detected_patterns": list(drift.detected_patterns),
            "findings": list(drift.findings),
            "severity": drift.severity,
        },
        "scope_honesty": {
            "honest": scope.honest,
            "issues": list(scope.issues),
            "severity": scope.severity,
        },
        "gate_verdict": gate.verdict,
        "gate_red_count": gate.red_count,
        "gate_reasons": list(gate.reasons),
        "formal_status": "REJECTED_BY_ONTOLOGY_GATE" if gate.verdict == "REJECT" else "ACCEPTED_BY_ONTOLOGY_GATE",
    }


def main() -> int:
    results = [_audit_one(entry) for entry in _PRIOR_CLAIMS]
    report = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "authority": "docs/project/ONTOLOGY_GATE_AGENT_SPEC.md",
        "claims_audited": len(results),
        "all_rejected": all(r["gate_verdict"] == "REJECT" for r in results),
        "results": results,
    }
    _OUT.parent.mkdir(parents=True, exist_ok=True)
    _OUT.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(_OUT), "all_rejected": report["all_rejected"]}, indent=2))
    return 0 if report["all_rejected"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
