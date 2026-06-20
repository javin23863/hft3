#!/usr/bin/env python3
"""Plan drift gate: fail if git diff touches paths outside completed plan phases.

Authority: Vast pipeline Plan v3 (implementation plan, not edited by this script).
Output: runtime/reports/plan_drift_review.json — exit 0 required before commit.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Set

_REPO = Path(__file__).resolve().parents[1]
_DEFAULT_PLAN = Path.home() / ".cursor" / "plans" / "vast_pipeline_v3_9334a188.plan.md"
_DEFAULT_OUT = _REPO / "runtime" / "reports" / "plan_drift_review.json"

PHASE_ORDER: List[str] = [
    "fable-ground",
    "stop-vast",
    "single-deploy",
    "npz-abort-wire",
    "plan-drift-gate",
    "regate-manifest",
    "vast-launch",
    "post-screen",
]

# ponytail: cumulative allow-list per phase id (repo-relative paths or prefixes ending /)
PHASE_ALLOWED: Dict[str, List[str]] = {
    "stop-vast": [],
    "single-deploy": [
        "scripts/vast_deploy_and_verify.ps1",
        "scripts/vast_remote_verify.sh",
        "scripts/vbt_paid_screen_next_steps.py",
        "docs/REPO_STATE.md",
        "runtime/_deprecated_vast_incident_20260619/",
    ],
    "npz-abort-wire": [
        "scripts/generate_vbt_paid_units_jsonl.py",
        "scripts/run_vbt_paid_screen_vast_full.sh",
        "scripts/run_vectorbt_paid_screen_v2.py",
        "scripts/run_paid_screen.py",
        "tests/test_paid_screen_v2_orchestrator.py",
        "tests/test_generate_vbt_require_runnable_npz.py",
    ],
    "plan-drift-gate": [
        "scripts/run_plan_drift_review.py",
        "tests/test_plan_drift_review.py",
        ".pre-commit-config.yaml",
    ],
    "regate-manifest": [
        "runtime/reports/paid_screen_ready_gate.json",
        "runtime/reports/vbt_full_run_declaration.json",
        "runtime/reports/vbt_full_units.jsonl",
        "runtime/reports/vbt_full_units_dryrun.jsonl",
        "runtime/reports/vbt_smoke_units.jsonl",
        "scripts/validate_paid_screen_ready_gate.py",
        "scripts/build_lake_catalog.py",
    ],
    "vast-launch": [
        "runtime/reports/",
        "research_cards/pipeline_runs/",
    ],
    "post-screen": [
        "scripts/aggregate_vbt_promoted_ids.py",
        "scripts/run_hftbacktest_realism.py",
        "runtime/reports/vbt_full_promoted_ids.json",
    ],
}

GLOBAL_ALLOWED_PREFIXES = [
    "graphify-out/",
    "runtime/vault-gate/",
    "runtime/reports/plan_drift_review.json",
]

DEPRECATED_PATTERNS = [
    re.compile(r"^runtime/vast_[^/]+\.sh$"),
    re.compile(r"^scripts/run_vectorbt_paid_screen\.py$"),
]


def _normalize_path(path: str) -> str:
    return path.replace("\\", "/").lstrip("./")


def _git_changed_files() -> List[tuple[str, str]]:
    """Working tree changes: (path, porcelain_status) including untracked."""
    changed: dict[str, str] = {}
    proc = subprocess.run(
        ["git", "status", "--porcelain", "-u"],
        cwd=_REPO,
        capture_output=True,
        text=True,
        check=False,
    )
    for line in proc.stdout.splitlines():
        if len(line) < 4:
            continue
        status = line[:2]
        path = _normalize_path(line[3:].strip().strip('"'))
        if path:
            changed[path] = status
    return sorted((p, changed[p]) for p in changed)


def _git_diff_files(base_ref: str) -> List[str]:
    proc = subprocess.run(
        ["git", "diff", "--name-only", base_ref],
        cwd=_REPO,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode not in (0, 1):
        raise RuntimeError(f"git diff failed: {proc.stderr.strip()}")
    return [_normalize_path(l) for l in proc.stdout.splitlines() if l.strip()]


def _allowed_for_phase(completed_phase: str) -> Set[str]:
    if completed_phase not in PHASE_ORDER:
        raise ValueError(f"unknown completed_phase: {completed_phase!r}")
    idx = PHASE_ORDER.index(completed_phase)
    allowed: Set[str] = set(GLOBAL_ALLOWED_PREFIXES)
    for phase in PHASE_ORDER[: idx + 1]:
        for entry in PHASE_ALLOWED.get(phase, []):
            allowed.add(entry)
    return allowed


def _path_allowed(path: str, allowed: Set[str], *, git_status: str = "") -> bool:
    if git_status.strip() == "D" and any(
        pat.match(path) for pat in DEPRECATED_PATTERNS
    ):
        return True
    for entry in allowed:
        if entry.endswith("/"):
            if path.startswith(entry):
                return True
        elif path == entry:
            return True
    return False


def _deprecated_violation(path: str, *, status: str | None = None) -> str | None:
    if status == "D":
        return None
    for pat in DEPRECATED_PATTERNS:
        if pat.match(path):
            return f"deprecated path touched: {path}"
    return None


def _parse_plan_todos(plan_path: Path) -> List[str]:
    text = plan_path.read_text(encoding="utf-8")
    todos: List[str] = []
    in_todos = False
    for line in text.splitlines():
        if line.strip() == "todos:":
            in_todos = True
            continue
        if in_todos:
            m = re.match(r"\s+- id:\s+(\S+)", line)
            if m:
                todos.append(m.group(1))
            elif line.startswith("  - ") and "id:" not in line:
                continue
            elif not line.startswith("  "):
                break
    return todos


def run_review(
    *,
    plan_path: Path,
    completed_phase: str,
    base_ref: str = "HEAD",
) -> Dict[str, Any]:
    errors: List[str] = []
    plan_todos = _parse_plan_todos(plan_path) if plan_path.is_file() else []
    if completed_phase not in plan_todos and completed_phase not in PHASE_ORDER:
        errors.append(f"completed_phase not in plan todos: {completed_phase}")

    if completed_phase in PHASE_ORDER:
        for prior in PHASE_ORDER[: PHASE_ORDER.index(completed_phase)]:
            if prior in plan_todos and plan_todos.index(prior) > plan_todos.index(completed_phase):
                errors.append(f"wrong phase order in plan: {completed_phase} before {prior}")

    try:
        allowed = _allowed_for_phase(completed_phase)
    except ValueError:
        allowed = set()
    changed = _git_diff_files(base_ref)
    out_of_scope: List[str] = []
    deprecated_hits: List[str] = []

    for path in changed:
        dep = _deprecated_violation(path)
        if dep:
            deprecated_hits.append(dep)
        if not _path_allowed(path, allowed):
            out_of_scope.append(path)

    if out_of_scope:
        errors.append(f"files outside plan scope for phase {completed_phase}: {out_of_scope}")
    if deprecated_hits:
        errors.extend(deprecated_hits)

    return {
        "evaluated_at_utc": datetime.now(timezone.utc).isoformat(),
        "plan_path": str(plan_path),
        "completed_phase": completed_phase,
        "base_ref": base_ref,
        "changed_files": changed,
        "allowed_prefixes": sorted(allowed),
        "out_of_scope": out_of_scope,
        "deprecated_violations": deprecated_hits,
        "errors": errors,
        "pass": len(errors) == 0,
    }


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Plan drift review gate (Plan v3)")
    parser.add_argument("--plan", type=Path, default=_DEFAULT_PLAN)
    parser.add_argument("--completed-phase", required=True)
    parser.add_argument("--base-ref", default="HEAD", help="Reserved for future diff-base override")
    parser.add_argument("--out", type=Path, default=_DEFAULT_OUT)
    args = parser.parse_args(argv)

    report = run_review(
        plan_path=args.plan if args.plan.is_absolute() else args.plan,
        completed_phase=args.completed_phase,
        base_ref=args.base_ref,
    )
    try:
        allowed = _allowed_for_phase(args.completed_phase)
    except ValueError:
        allowed = set()

    changed = _git_changed_files()
    out_of_scope = [
        p for p, st in changed if not _path_allowed(p, allowed, git_status=st)
    ]
    deprecated_hits = [
        d for p, st in changed if (d := _deprecated_violation(p, status=st.strip()))
    ]
    report["changed_files"] = [p for p, _ in changed]
    report["out_of_scope"] = out_of_scope
    report["deprecated_violations"] = deprecated_hits
    porcelain_errors: List[str] = []
    if out_of_scope:
        porcelain_errors.append(
            f"files outside plan scope for phase {args.completed_phase}: {out_of_scope}"
        )
    if deprecated_hits:
        porcelain_errors.extend(deprecated_hits)
    seen: Set[str] = set()
    merged_errors: List[str] = []
    for err in list(report.get("errors") or []) + porcelain_errors:
        if err not in seen:
            seen.add(err)
            merged_errors.append(err)
    report["errors"] = merged_errors
    report["pass"] = len(merged_errors) == 0

    out = args.out if args.out.is_absolute() else _REPO / args.out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"pass": report["pass"], "errors": report["errors"], "out": str(out)}))
    return 0 if report["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
