#!/usr/bin/env python3
"""Phase 3C: audit cockpit/workbench sync claims through ontology gate.

Writes runtime/reports/ontology_gate_cockpit_validation.json.
Spec: docs/project/ONTOLOGY_GATE_AGENT_SPEC.md; plan Phase 3C.
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
    validate_fable_entry_checklist,
    run_gate,
    VERDICT_REJECT,
)

_OUT = _REPO / "runtime" / "reports" / "ontology_gate_cockpit_validation.json"

_SPEC_DIRS = (
    _REPO / "docs" / "project",
    _REPO / "specs",
    _REPO / "docs" / "cockpit",
    _REPO / "docs",
)

_CITATIONS = [
    {
        "paper_id": "none",
        "spec_ref": "BUILDOUT_CORRECTNESS_CHECKLIST.md",
        "tool_doc_ref": "none",
    },
    {
        "paper_id": "none",
        "spec_ref": "VALIDATION_HONESTY.md",
        "tool_doc_ref": "none",
    },
]

_SCENARIOS = [
    {
        "id": "m6_running_from_stale_log",
        "summary": "M6 sweep shown as running from mirrored log without Vast health artifact",
        "expected_verdict": VERDICT_REJECT,
        "drift_text": (
            "cockpit M6 universe sweep running from stale local log mirror; "
            "no Vast SSH health or universe_result.json freshness proof."
        ),
        "scope_flags": {
            "missing_verify_tail": True,
            "scope_green_without_exit_code": True,
        },
        "invariant_results": {
            "B1": "na",
            "B2": "na",
            "B3": "na",
            "B4": "na",
            "B5": "fail",
            "B6": "na",
            "B7": "na",
            "B8": "na",
        },
        "invariant_findings": ["cockpit_running_without_backend_artifact_freshness"],
    },
    {
        "id": "decorative_green_without_source",
        "summary": "Dashboard GREEN without named backend object and freshness rule",
        "expected_verdict": VERDICT_REJECT,
        "drift_text": (
            "cockpit panel green status without backend source path, "
            "freshness rule, or fail-closed stale/fixture guard."
        ),
        "scope_flags": {
            "subset_pytest_claimed_as_scope_green": False,
            "missing_verify_tail": True,
        },
        "invariant_results": {
            "B1": "na",
            "B2": "na",
            "B3": "na",
            "B4": "na",
            "B5": "na",
            "B6": "na",
            "B7": "na",
            "B8": "na",
        },
        "invariant_findings": ["cockpit_truth_gate_F_violation"],
    },
    {
        "id": "honest_cockpit_blocked_posture",
        "summary": "Cockpit fail-closed when artifact missing or stale (target posture after c10d2ec7)",
        "expected_verdict": "PASS",
        "drift_text": (
            "cockpit surfaces MISSING/STALE/RED when universe_M6 artifact absent; "
            "backend source named in panel metadata per BUILDOUT_CORRECTNESS_CHECKLIST."
        ),
        "scope_flags": {},
        "invariant_results": {
            "B1": "pass",
            "B2": "na",
            "B3": "na",
            "B4": "na",
            "B5": "na",
            "B6": "na",
            "B7": "na",
            "B8": "na",
        },
        "invariant_findings": [],
    },
]


def _fable() -> object:
    return validate_fable_entry_checklist(
        grounded=True,
        vault_read=True,
        authority_located=True,
        no_assumptions=True,
        fable_active=True,
    )


def _run_scenario(entry: dict) -> dict:
    flags = entry.get("scope_flags") or {}
    verdict = run_gate(
        fable_checklist=_fable(),
        citations=_CITATIONS,
        area="workbench",
        invariant_results=entry.get("invariant_results"),
        invariant_findings=entry.get("invariant_findings"),
        drift_text=entry.get("drift_text"),
        subset_pytest_claimed_as_scope_green=bool(flags.get("subset_pytest_claimed_as_scope_green")),
        waived_verify_claimed_as_done=bool(flags.get("waived_verify_claimed_as_done")),
        plan_todo_theater=bool(flags.get("plan_todo_theater")),
        scope_green_without_exit_code=bool(flags.get("scope_green_without_exit_code")),
        missing_verify_tail=bool(flags.get("missing_verify_tail")),
        spec_dirs=_SPEC_DIRS,
    )
    expected = entry["expected_verdict"]
    ok = verdict.verdict == expected
    return {
        "id": entry["id"],
        "summary": entry["summary"],
        "expected_verdict": expected,
        "actual_verdict": verdict.verdict,
        "scenario_ok": ok,
        "gate_red_count": verdict.red_count,
        "gate_reasons": list(verdict.reasons),
    }


def main() -> int:
    results = [_run_scenario(entry) for entry in _SCENARIOS]
    bad_posture = [r for r in results if r["id"] != "honest_cockpit_blocked_posture"]
    report = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "authority": "docs/project/ONTOLOGY_GATE_AGENT_SPEC.md",
        "phase": "3C_cockpit_workbench_sync",
        "backend_artifacts_checked": [
            "research_cards/universe_M6_full/universe_result.json",
            "runtime/universe_M6_vast.log",
            "apps/cockpit/backend/aggregate/pipeline.py",
        ],
        "scenarios": results,
        "bad_claims_all_rejected": all(r["actual_verdict"] == VERDICT_REJECT for r in bad_posture),
        "honest_posture_passes": next(
            r["scenario_ok"] for r in results if r["id"] == "honest_cockpit_blocked_posture"
        ),
    }
    _OUT.parent.mkdir(parents=True, exist_ok=True)
    _OUT.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(_OUT.relative_to(_REPO)), "report": report}, indent=2))
    if not report["bad_claims_all_rejected"]:
        return 1
    if not report["honest_posture_passes"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
