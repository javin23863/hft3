#!/usr/bin/env python3
"""CLI for the deterministic Ontology Gate Agent (ontology_gate.run_gate).

Spec: docs/project/ONTOLOGY_GATE_AGENT_SPEC.md
Exits 0 on PASS, 1 on REJECT.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Mapping

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from hft3_bootstrap import setup_repo_paths

setup_repo_paths()

from backtest_pipeline.src.ontology_gate import (  # noqa: E402
    validate_fable_entry_checklist,
    run_gate,
    VERDICT_PASS,
)


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _parse_fable(payload: Mapping[str, Any]) -> Any:
    """Accept GROUNDED-style or grounded-style keys."""
    key_map = {
        "GROUNDED": "grounded",
        "VAULT_READ": "vault_read",
        "AUTHORITY_LOCATED": "authority_located",
        "NO_ASSUMPTIONS": "no_assumptions",
        "FABLE_ACTIVE": "fable_active",
    }
    kwargs: dict[str, bool] = {}
    for upper, lower in key_map.items():
        if upper in payload:
            kwargs[lower] = bool(payload[upper])
        elif lower in payload:
            kwargs[lower] = bool(payload[lower])
    missing = set(key_map.values()) - set(kwargs)
    if missing:
        raise ValueError(f"fable checklist missing keys: {sorted(missing)}")
    return validate_fable_entry_checklist(**kwargs)


def _parse_invariants(raw: str | None) -> dict[str, str] | None:
    if not raw:
        return None
    raw = raw.strip()
    if raw.startswith("{"):
        parsed = json.loads(raw)
        if not isinstance(parsed, dict):
            raise ValueError("invariant-results JSON must be an object")
        return {str(k): str(v) for k, v in parsed.items()}
    out: dict[str, str] = {}
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        if "=" not in part:
            raise ValueError(f"invalid invariant token (expected B1=pass): {part!r}")
        key, value = part.split("=", 1)
        out[key.strip()] = value.strip()
    return out


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Run ontology gate (deterministic, fail-closed).")
    p.add_argument(
        "--fable-json",
        type=Path,
        help="JSON file with 5 Fable entry checkboxes (GROUNDED/VAULT_READ/... or snake_case).",
    )
    p.add_argument(
        "--fable-all-true",
        action="store_true",
        help="Use all-true Fable checklist (agent already grounded).",
    )
    p.add_argument("--citations-json", type=Path, help="JSON array of citation claim objects.")
    p.add_argument("--artifact", type=Path, help="Screening or replay artifact JSON file.")
    p.add_argument(
        "--artifact-type",
        default="screening",
        choices=("screening", "replay"),
        help="Artifact schema type (default: screening).",
    )
    p.add_argument("--area", default="backtest_pipeline", help="Code area for B1-B8 applicability.")
    p.add_argument(
        "--invariant-results",
        help="Comma list B1=pass,B2=na,... or JSON object.",
    )
    p.add_argument("--invariant-findings", type=Path, help="JSON array of invariant finding strings.")
    p.add_argument("--call-sites-json", type=Path, help="JSON array of tool usage call-site objects.")
    p.add_argument("--drift-text", help="Prose/PR body to scan for drift patterns.")
    p.add_argument("--drift-artifact-json", type=Path, help="Structured drift flags as JSON object.")
    p.add_argument("--output", type=Path, help="Write GateVerdict JSON here.")
    p.add_argument("--subset-as-scope-green", action="store_true")
    p.add_argument("--waived-as-done", action="store_true")
    p.add_argument("--plan-todo-theater", action="store_true")
    p.add_argument("--scope-green-no-exit", action="store_true")
    p.add_argument("--missing-verify-tail", action="store_true")
    return p


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)

    if args.fable_json:
        fable = _parse_fable(_load_json(args.fable_json))
    elif args.fable_all_true:
        fable = validate_fable_entry_checklist(
            grounded=True,
            vault_read=True,
            authority_located=True,
            no_assumptions=True,
            fable_active=True,
        )
    else:
        print("error: supply --fable-json or --fable-all-true", file=sys.stderr)
        return 2

    citations = _load_json(args.citations_json) if args.citations_json else None
    artifact = _load_json(args.artifact) if args.artifact else None
    call_sites = _load_json(args.call_sites_json) if args.call_sites_json else None
    drift_artifact = _load_json(args.drift_artifact_json) if args.drift_artifact_json else None
    invariant_findings = None
    if args.invariant_findings:
        try:
            invariant_findings = _load_json(args.invariant_findings)
        except (json.JSONDecodeError, OSError, ValueError) as exc:
            print(f"error: invalid --invariant-findings: {exc}", file=sys.stderr)
            return 2
        if not isinstance(invariant_findings, list):
            print("error: --invariant-findings must be a JSON array", file=sys.stderr)
            return 2

    verdict = run_gate(
        fable_checklist=fable,
        citations=citations,
        area=args.area,
        invariant_results=_parse_invariants(args.invariant_results),
        invariant_findings=invariant_findings,
        call_sites=call_sites,
        artifact=artifact,
        artifact_type=args.artifact_type,
        drift_text=args.drift_text,
        drift_artifact=drift_artifact,
        subset_pytest_claimed_as_scope_green=args.subset_as_scope_green,
        waived_verify_claimed_as_done=args.waived_as_done,
        plan_todo_theater=args.plan_todo_theater,
        scope_green_without_exit_code=args.scope_green_no_exit,
        missing_verify_tail=args.missing_verify_tail,
    )

    payload = verdict.as_dict()
    text = json.dumps(payload, indent=2, sort_keys=True)
    print(text)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n", encoding="utf-8")

    return 0 if verdict.verdict == VERDICT_PASS else 1


if __name__ == "__main__":
    raise SystemExit(main())
