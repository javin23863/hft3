"""Central leakage detector for Workbench active runs."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable

from workbench.src.run.evidence_snapshot import load_run_evidence, read_json
from workbench.src.run.fresh_start import (
    GENERATED_TARGETS,
    _tracked_generated_artifact_rows,
    _tracked_paths,
)


LEAKAGE_SCHEMA_VERSION = "workbench_leakage_detection_v1"
LEAKAGE_ARTIFACTS = ("leakage_detection.json", "leakage_detection.md")
STALE_SOURCE_NAMES = ("workbench_campaign", "autonomous", "crypto_lane")
ACTIVE_RUN_ARTIFACTS = (
    "plan.json",
    "summary.json",
    "rejected_stale_artifacts.json",
    "feature_fabric/feature_fabric_manifest.json",
    "feature_fabric/feature_lineage.json",
    "feature_fabric/feature_pit_audit.json",
    "feature_fabric/rejected_features.json",
)


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _relative(repo: Path, path: Path) -> str:
    return path.resolve().relative_to(repo.resolve()).as_posix()


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_markdown(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Workbench Leakage Detection",
        "",
        f"- Run ID: `{payload.get('run_id', '')}`",
        f"- Status: `{payload.get('status', '')}`",
        f"- Generated: `{payload.get('generated_at_utc', '')}`",
        "",
        "## Checks",
        "",
    ]
    for check in payload.get("checks", []):
        lines.append(f"- `{check.get('status', '')}` `{check.get('name', '')}` - {check.get('reason', '')}")
    if payload.get("blocking"):
        lines.extend(["", "## Blocking Issues", ""])
        for blocker in payload["blocking"]:
            lines.append(f"- `{blocker.get('gate', '')}` {blocker.get('reason', '')}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _check(name: str, status: str, reason: str, **extra: Any) -> dict[str, Any]:
    payload = {"name": name, "status": status, "reason": reason}
    payload.update(extra)
    return payload


def _blocking_from_checks(checks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    blocking_statuses = {"FAIL", "BLOCKING", "STALE", "MISSING"}
    blockers: list[dict[str, Any]] = []
    for check in checks:
        if str(check.get("status") or "").upper() in blocking_statuses:
            blockers.append(
                {
                    "gate": check.get("name", ""),
                    "status": check.get("status", ""),
                    "reason": check.get("reason", ""),
                }
            )
    return blockers


def _active_run(repo: Path, requested_run_id: str | None) -> tuple[str, dict[str, Any]]:
    active = read_json(repo / "runtime" / "workbench" / "active_run.json")
    run_id = str(requested_run_id or active.get("run_id") or "")
    return run_id, active


def _run_dir(repo: Path, run_id: str) -> Path:
    return repo / "runtime" / "workbench" / "all_lanes" / run_id


def _generated_root_check(repo: Path, tracked: set[str], stale_rows: list[dict[str, Any]]) -> dict[str, Any]:
    stale_paths = {str(row.get("path") or "").replace("\\", "/") for row in stale_rows}
    contaminated: list[dict[str, Any]] = []
    quarantined: list[str] = []
    for rel_target in GENERATED_TARGETS:
        root = repo / rel_target
        if not root.exists():
            continue
        if root.is_file():
            paths = [root]
        else:
            paths = [path for path in root.rglob("*") if path.is_file()]
        for path in paths:
            rel = _relative(repo, path)
            if rel in tracked and rel in stale_paths:
                quarantined.append(rel)
                continue
            contaminated.append({"path": rel, "tracked": rel in tracked})
    if contaminated:
        return _check(
            "generated_artifact_roots_clean",
            "FAIL",
            "Generated artifact roots contain files outside the active all-lane run boundary.",
            contaminated_paths=contaminated[:50],
            contaminated_count=len(contaminated),
            quarantined_tracked_count=len(quarantined),
        )
    return _check(
        "generated_artifact_roots_clean",
        "PASS",
        "Generated artifact roots are clean or tracked stale artifacts are quarantined.",
        quarantined_tracked_count=len(quarantined),
    )


def _stale_ledger_check(repo: Path, run_dir: Path, tracked: set[str]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    expected_rows = _tracked_generated_artifact_rows(tracked)
    payload = read_json(run_dir / "rejected_stale_artifacts.json")
    rows = [row for row in payload.get("rows", []) if isinstance(row, dict)]
    actual_paths = {str(row.get("path") or "").replace("\\", "/") for row in rows}
    expected_paths = {str(row.get("path") or "").replace("\\", "/") for row in expected_rows}
    rejected_status_ok = all(str(row.get("status") or "") == "REJECTED" for row in rows)
    if actual_paths != expected_paths or not rejected_status_ok:
        return (
            _check(
                "tracked_stale_artifacts_quarantined",
                "FAIL",
                "Tracked generated artifacts are not fully represented in the rejected stale-artifact ledger.",
                expected_count=len(expected_paths),
                actual_count=len(actual_paths),
                missing_paths=sorted(expected_paths - actual_paths)[:50],
                unexpected_paths=sorted(actual_paths - expected_paths)[:50],
            ),
            rows,
        )
    return (
        _check(
            "tracked_stale_artifacts_quarantined",
            "PASS",
            "Tracked generated artifacts are explicitly quarantined before the active run.",
            rejected_count=len(rows),
        ),
        rows,
    )


def _artifact_identity_check(run_dir: Path, run_id: str) -> dict[str, Any]:
    issues: list[dict[str, str]] = []
    for rel_path in ACTIVE_RUN_ARTIFACTS:
        path = run_dir / rel_path
        if not path.is_file():
            continue
        payload = read_json(path)
        observed = str(payload.get("run_id") or "")
        if observed and observed != run_id:
            issues.append({"artifact": rel_path, "observed_run_id": observed, "expected_run_id": run_id})
    if issues:
        return _check(
            "active_artifact_run_identity",
            "STALE",
            "One or more active-run artifacts declare a different run id.",
            issue_count=len(issues),
            issues=issues,
        )
    return _check(
        "active_artifact_run_identity",
        "PASS",
        "Active-run artifacts either declare the active run id or do not declare a run id.",
    )


def _feature_fabric_checks(snapshot: Any) -> list[dict[str, Any]]:
    fabric = ((getattr(snapshot, "diagnostics", {}) or {}).get("feature_fabric")) or {}
    gates = fabric.get("blocking_gates") or []
    checks = [
        _check(
            "feature_fabric_identity",
            "PASS" if fabric.get("gate_status") == "PASS" else "FAIL",
            "Feature-fabric artifacts belong to the active run and have no blocking gates."
            if fabric.get("gate_status") == "PASS"
            else "Feature-fabric artifacts failed their Workbench evidence gate.",
            blocking_gates=gates,
            gate_status=fabric.get("gate_status", ""),
        ),
        _check(
            "feature_fabric_pit_validation",
            "PASS" if fabric.get("pit_validation_status") == "PASS" else "FAIL",
            "Cross-lane feature rows pass point-in-time validation."
            if fabric.get("pit_validation_status") == "PASS"
            else "Cross-lane feature rows are missing or failed point-in-time validation.",
            pit_validation_status=fabric.get("pit_validation_status", ""),
            issue_count=fabric.get("pit_issue_count", 0),
        ),
    ]
    return checks


def _legacy_source_checks(repo: Path) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    for source in STALE_SOURCE_NAMES:
        snapshot = load_run_evidence(repo, source)
        if getattr(snapshot, "current_stage", "") == "stale_source_blocked":
            checks.append(
                _check(
                    f"{source}_stale_source_block",
                    "PASS",
                    f"{source} is blocked from becoming active evidence while an all-lane run is active.",
                )
            )
        else:
            checks.append(
                _check(
                    f"{source}_stale_source_block",
                    "FAIL",
                    f"{source} can still surface as active evidence during an all-lane run.",
                    observed_stage=getattr(snapshot, "current_stage", ""),
                    observed_run_id=getattr(snapshot, "run_id", ""),
                )
            )
    return checks


def run_leakage_detection(
    repo: Path,
    run_id: str | None = None,
    *,
    write_artifacts: bool = True,
    tracked_paths_fn: Callable[[Path], set[str]] | None = None,
) -> dict[str, Any]:
    """Run the active Workbench leakage detector and optionally persist artifacts."""

    repo = repo.resolve()
    selected_run_id, active = _active_run(repo, run_id)
    run_dir = _run_dir(repo, selected_run_id) if selected_run_id else repo / "runtime" / "workbench" / "all_lanes" / "_missing_active_run"
    tracked = tracked_paths_fn(repo) if tracked_paths_fn else _tracked_paths(repo)
    checks: list[dict[str, Any]] = []

    if not selected_run_id or not active:
        checks.append(
            _check(
                "active_run_manifest",
                "MISSING",
                "No active all-lane run manifest exists. Run fresh-start before model execution.",
            )
        )
    elif str(active.get("run_id") or "") != selected_run_id:
        checks.append(
            _check(
                "active_run_manifest",
                "STALE",
                "Requested run id does not match the active all-lane run manifest.",
                active_run_id=active.get("run_id", ""),
                requested_run_id=selected_run_id,
            )
        )
    else:
        checks.append(
            _check(
                "active_run_manifest",
                "PASS",
                "Active all-lane run manifest is present and matches the selected run.",
            )
        )

    ledger_check, stale_rows = _stale_ledger_check(repo, run_dir, tracked)
    checks.append(ledger_check)
    checks.append(_generated_root_check(repo, tracked, stale_rows))
    checks.append(_artifact_identity_check(run_dir, selected_run_id))

    try:
        snapshot = load_run_evidence(repo, "all_lanes")
        checks.extend(_feature_fabric_checks(snapshot))
        checks.extend(_legacy_source_checks(repo))
    except Exception as exc:
        checks.append(
            _check(
                "workbench_snapshot_leakage_gate",
                "FAIL",
                "Workbench evidence snapshot failed during leakage detection.",
                error=str(exc),
            )
        )

    blocking = _blocking_from_checks(checks)
    status = "FAIL" if blocking else "PASS"
    artifact_paths = {
        "json": str(run_dir / LEAKAGE_ARTIFACTS[0]),
        "markdown": str(run_dir / LEAKAGE_ARTIFACTS[1]),
    }
    payload = {
        "schema_version": LEAKAGE_SCHEMA_VERSION,
        "run_id": selected_run_id,
        "generated_at_utc": _utc_now(),
        "status": status,
        "active_run": active,
        "checks": checks,
        "blocking": blocking,
        "artifact_paths": artifact_paths,
        "policy": {
            "artifact_reuse": "active_run_id_only",
            "legacy_sources": list(STALE_SOURCE_NAMES),
            "generated_roots": list(GENERATED_TARGETS),
        },
    }
    if write_artifacts and selected_run_id:
        _write_json(run_dir / LEAKAGE_ARTIFACTS[0], payload)
        _write_markdown(run_dir / LEAKAGE_ARTIFACTS[1], payload)
    return payload
