#!/usr/bin/env python3
"""Phase 3D: audit latency_truth.json component bands through ontology gate.

Writes runtime/reports/ontology_gate_latency_truth_validation.json.
Spec: docs/vault/HFTBACKTEST_LATENCY_ONTOLOGY.md; plan Phase 3D.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from hft3_bootstrap import setup_repo_paths

setup_repo_paths()

from backtest_pipeline.src.ontology_gate import (  # noqa: E402
    validate_fable_entry_checklist,
    run_gate,
    VERDICT_REJECT,
    VERDICT_PASS,
)

_OUT = _REPO / "runtime" / "reports" / "ontology_gate_latency_truth_validation.json"
_DEFAULT_TRUTH = _REPO / "runtime" / "latency_reports" / "latency_truth.json"

_CITATIONS = [
    {
        "paper_id": "none",
        "spec_ref": "HFTBACKTEST_REALISM_ENGINE_SPEC.md",
        "tool_doc_ref": "none",
    },
    {
        "paper_id": "none",
        "spec_ref": "LATENCY.md",
        "tool_doc_ref": "none",
    },
]

_HFTBACKTEST_CRITICAL = (
    "feed_latency_us",
    "new_send_to_exchange_us",
    "new_exchange_to_ack_us",
    "cancel_send_to_exchange_us",
    "cancel_exchange_to_ack_us",
)


def _load_truth(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _band_summary(payload: dict[str, Any]) -> dict[str, Any]:
    bands = payload.get("component_bands") or {}
    by_status: dict[str, list[str]] = {}
    for name, row in bands.items():
        status = str((row or {}).get("measurement_status") or "UNKNOWN")
        by_status.setdefault(status, []).append(name)
    critical = {
        name: (bands.get(name) or {}).get("measurement_status")
        for name in _HFTBACKTEST_CRITICAL
        if name in bands
    }
    return {
        "band_count": len(bands),
        "by_status": by_status,
        "hftbacktest_critical_status": critical,
        "open_or_unmeasured_critical": [
            name
            for name, status in critical.items()
            if status in {"OPEN", "UNMEASURED"}
        ],
    }


def _fable() -> object:
    return validate_fable_entry_checklist(
        grounded=True,
        vault_read=True,
        authority_located=True,
        no_assumptions=True,
        fable_active=True,
    )


def _run_scenario(
    *,
    scenario_id: str,
    summary: str,
    expected_verdict: str,
    drift_text: str,
    invariant_results: dict[str, str] | None,
    invariant_findings: list[str] | None,
    scope_flags: dict[str, bool] | None = None,
) -> dict[str, Any]:
    flags = scope_flags or {}
    verdict = run_gate(
        fable_checklist=_fable(),
        citations=_CITATIONS,
        area="backtest_pipeline",
        invariant_results=invariant_results,
        invariant_findings=invariant_findings,
        drift_text=drift_text,
        subset_pytest_claimed_as_scope_green=bool(flags.get("subset_pytest_claimed_as_scope_green")),
        waived_verify_claimed_as_done=bool(flags.get("waived_verify_claimed_as_done")),
        plan_todo_theater=bool(flags.get("plan_todo_theater")),
        scope_green_without_exit_code=bool(flags.get("scope_green_without_exit_code")),
        missing_verify_tail=bool(flags.get("missing_verify_tail")),
    )
    return {
        "id": scenario_id,
        "summary": summary,
        "expected_verdict": expected_verdict,
        "actual_verdict": verdict.verdict,
        "scenario_ok": verdict.verdict == expected_verdict,
        "gate_red_count": verdict.red_count,
        "gate_reasons": list(verdict.reasons),
    }


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Validate latency_truth through ontology gate.")
    parser.add_argument(
        "--truth-path",
        type=Path,
        default=_DEFAULT_TRUTH,
        help="Path to latency_truth.json",
    )
    args = parser.parse_args(argv)

    truth_path = args.truth_path
    if not truth_path.is_file():
        print(f"error: missing {truth_path}", file=sys.stderr)
        return 2

    payload = _load_truth(truth_path)
    summary = _band_summary(payload)
    open_critical = summary["open_or_unmeasured_critical"]

    scenarios = [
        _run_scenario(
            scenario_id="dishonest_all_bands_measured",
            summary="Claim all HftBacktest component bands MEASURED for promotion",
            expected_verdict=VERDICT_REJECT,
            drift_text=(
                "All hftbacktest latency component bands are MEASURED and promotion-ready; "
                "cancel_send_to_exchange_us and cancel_exchange_to_ack_us closed."
            ),
            invariant_results={
                "B1": "na",
                "B2": "na",
                "B3": "na",
                "B4": "na",
                "B5": "fail",
                "B6": "na",
                "B7": "na",
                "B8": "na",
            },
            invariant_findings=[
                f"latency_truth_open_critical_bands:{','.join(open_critical) or 'none'}"
            ],
            scope_flags={"missing_verify_tail": True},
        ),
        _run_scenario(
            scenario_id="honest_research_only_open_bands",
            summary="Honest research-only posture with OPEN/UNMEASURED cancel bands documented",
            expected_verdict=VERDICT_PASS,
            drift_text=(
                "latency_truth.json documents OPEN cancel bands; "
                "research-only backtest allowed; promotion blocked until CC-4 closes bands."
            ),
            invariant_results={
                "B1": "pass",
                "B2": "pass",
                "B3": "na",
                "B4": "na",
                "B5": "pass",
                "B6": "na",
                "B7": "pass",
                "B8": "na",
            },
            invariant_findings=[],
        ),
    ]

    report = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "authority": "docs/vault/HFTBACKTEST_LATENCY_ONTOLOGY.md",
        "phase": "3D_latency_truth_cc_campaigns",
        "truth_path": str(truth_path.relative_to(_REPO)),
        "band_summary": summary,
        "scenarios": scenarios,
        "dishonest_claim_rejected": scenarios[0]["scenario_ok"],
        "honest_posture_passes": scenarios[1]["scenario_ok"],
    }
    _OUT.parent.mkdir(parents=True, exist_ok=True)
    _OUT.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(_OUT.relative_to(_REPO)), "report": report}, indent=2))

    if not report["dishonest_claim_rejected"] or not report["honest_posture_passes"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
