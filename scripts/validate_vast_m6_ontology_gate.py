#!/usr/bin/env python3
"""Validate Vast M6 pipeline posture through ontology gate (expect REJECT until VBT path).

Writes runtime/reports/ontology_gate_vast_m6_validation.json.
See docs/project/ONTOLOGY_GATE_VAST_M6_PATH.md for required citations and correct order.
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

_OUT = _REPO / "runtime" / "reports" / "ontology_gate_vast_m6_validation.json"

_REQUIRED_CITATIONS = [
    {
        "paper_id": "none",
        "spec_ref": "VECTORBT_SCREENING_ENGINE_SPEC.md::Screening Artifact Contract",
        "tool_doc_ref": "Portfolio.from_signals::1.0.0",
    },
    {
        "paper_id": "none",
        "spec_ref": "OPPORTUNITY_RESEARCH_SPEC.md",
        "tool_doc_ref": "none",
    },
]

_BARE_RESCAN_DRIFT = (
    "run_event_universe.py --rescan full matrix on Vast without vectorbt "
    "screening_artifact.json or --from-stage-a survivors. "
    "Claims hft execution realism from bare rescan."
)


def main() -> int:
    fable = validate_fable_entry_checklist(
        grounded=True,
        vault_read=True,
        authority_located=True,
        no_assumptions=True,
        fable_active=True,
    )

    # Current bad posture: no screening artifact (empty payload fails schema).
    verdict = run_gate(
        fable_checklist=fable,
        citations=_REQUIRED_CITATIONS,
        area="backtest_pipeline",
        artifact={},
        invariant_results={
            "B1": "pass",
            "B2": "pass",
            "B3": "pass",
            "B4": "pass",
            "B5": "fail",
            "B6": "na",
            "B7": "pass",
            "B8": "na",
        },
        invariant_findings=["missing_vectorbt_screening_artifact_before_hft_universe_sweep"],
        drift_text=_BARE_RESCAN_DRIFT,
    )

    report = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "scenario": "vast_m6_bare_rescan_without_vbt_artifact",
        "expected_verdict": VERDICT_REJECT,
        "actual_verdict": verdict.verdict,
        "expected_until": [
            "screening_artifact.json from VectorBT paid screen (rust engine for paid-compute scope)",
            "run_event_universe --from-stage-a with stage_a_survivors.json",
            "feature_plane_status declared on artifact (not lake_existence_as_usage)",
            "ontology citation block in handoff per ONTOLOGY_GATE_AGENT_SPEC.md §5",
        ],
        "required_citations": _REQUIRED_CITATIONS,
        "gate": verdict.as_dict(),
        "unblocks_when": "gate.verdict == PASS with valid screening artifact + survivors path",
    }
    _OUT.parent.mkdir(parents=True, exist_ok=True)
    _OUT.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(_OUT), "verdict": verdict.verdict}, indent=2))

    # Exit 0 when gate correctly rejects bad posture; exit 1 if it wrongly passes.
    return 0 if verdict.verdict == VERDICT_REJECT else 1


if __name__ == "__main__":
    raise SystemExit(main())
