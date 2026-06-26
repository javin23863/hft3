#!/usr/bin/env python3
"""Fail-closed gate: pilot + smoke must pass before full paid VectorBT screen on Vast."""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from hft3_bootstrap import setup_repo_paths

setup_repo_paths()

_PAID_SCOPES = {
    "paid-compute",
    "paid",
    "broad-screen",
    "broad",
    "all-models",
    "paid_compute",
    "broad_screen",
    "all_model",
    "all_models",
}
_MIN_POSITIVE_TRADE_ROWS = 1


def _validate_screening_artifact(payload: Dict[str, Any]) -> None:
    from backtest_pipeline.src.vectorbt_adapter import validate_screening_artifact

    validate_screening_artifact(payload)


def _load_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _validate_screening_file(path: Path, errors: List[str], label: str) -> Dict[str, Any]:
    if not path.is_file():
        errors.append(f"{label}:missing_file:{path}")
        return {}
    try:
        payload = _load_json(path)
        _validate_screening_artifact(payload)
    except Exception as exc:
        errors.append(f"{label}:validate_failed:{path}:{exc}")
        return {}
    if not payload.get("no_lookahead_signal_shift_proof"):
        errors.append(f"{label}:missing_no_lookahead_signal_shift_proof")
    return payload


def _hash_fields(payload: Dict[str, Any]) -> Dict[str, str]:
    return {
        "events_csv_hash": str(
            payload.get("events_csv_hash_or_not_applicable")
            or payload.get("events_csv_hash")
            or ""
        ),
        "lake_manifest_hash": str(payload.get("lake_manifest_hash") or ""),
        "screening_scope": str(payload.get("screening_scope") or ""),
        "vectorbt_engine": str(payload.get("vectorbt_engine") or ""),
    }


def _positive_trade_rows(payload: Dict[str, Any]) -> int:
    total = 0
    for section in ("promoted", "rejected"):
        rows = payload.get(section) or []
        if not isinstance(rows, list):
            continue
        for row in rows:
            if not isinstance(row, dict):
                continue
            metric_values = row.get("metric_values") or {}
            stats = metric_values.get("vbt_stats") or row.get("vectorbt_results") or {}
            if not isinstance(stats, dict):
                continue
            try:
                trades = float(stats.get("Total Trades"))
            except (TypeError, ValueError):
                continue
            if trades > 0:
                total += 1
    return total


def _promoted_id_count(payload: Dict[str, Any]) -> int:
    promoted_ids = payload.get("promoted_ids") or []
    if isinstance(promoted_ids, list) and promoted_ids:
        return len(promoted_ids)
    promoted = payload.get("promoted") or []
    if isinstance(promoted, list) and promoted:
        return len(promoted)
    try:
        return int(payload.get("promoted_count") or 0)
    except (TypeError, ValueError):
        return 0


def _validate_smoke_manifest(manifest_path: Path, errors: List[str]) -> Dict[str, Any]:
    if not manifest_path.is_file():
        errors.append(f"smoke_manifest:missing:{manifest_path}")
        return {}
    manifest = _load_json(manifest_path)
    expected = int(manifest.get("expected_work_units") or 0)
    completed = int(manifest.get("completed_work_units") or 0)
    skipped = int(manifest.get("skipped_work_units") or 0)
    failed = int(manifest.get("failed_work_units") or 0)
    if expected <= 0:
        errors.append("smoke_manifest:expected_work_units_not_positive")
    if completed + skipped + failed != expected:
        errors.append(
            f"smoke_manifest:unit_count_mismatch:"
            f"expected={expected} completed={completed} skipped={skipped} failed={failed}"
        )
    if failed > 0:
        errors.append(f"smoke_manifest:failed_work_units={failed}")
    out_dir = Path(str(manifest.get("out_dir") or manifest_path.parent))
    unit_results = manifest.get("unit_results") or []
    if not unit_results:
        errors.append("smoke_manifest:empty_unit_results")
    validated = 0
    paid_scope_artifacts = 0
    paid_scope_positive_promotions = 0
    paid_scope_positive_trade_rows = 0
    feature_plane_status_counts: Dict[str, int] = {}
    bar_construction_id_counts: Dict[str, int] = {}
    for row in unit_results:
        if row.get("status") != "OK":
            continue
        rel = row.get("screening_artifact_relpath")
        if not rel:
            errors.append(f"smoke_unit:missing_relpath:{row.get('unit_id')}")
            continue
        artifact_path = out_dir / rel
        payload = _validate_screening_file(artifact_path, errors, f"smoke_unit:{row.get('unit_id')}")
        if payload:
            validated += 1
            scope = str(payload.get("screening_scope") or manifest.get("vectorbt_scope") or "")
            feature_plane_status = str(payload.get("feature_plane_status") or "")
            bar_construction_id = str(payload.get("bar_construction_id") or "")
            feature_set_id = str(payload.get("feature_set_id") or "")
            if feature_plane_status:
                feature_plane_status_counts[feature_plane_status] = (
                    feature_plane_status_counts.get(feature_plane_status, 0) + 1
                )
            if bar_construction_id:
                bar_construction_id_counts[bar_construction_id] = (
                    bar_construction_id_counts.get(bar_construction_id, 0) + 1
                )
            # Per Codex review finding 10: include the underscore-variant scope
            # names alongside the hyphen-variant names so the rust-engine
            # requirement is enforced for all canonical spellings.
            if scope in _PAID_SCOPES:
                paid_scope_artifacts += 1
                engine = str(payload.get("vectorbt_engine") or "").lower()
                if engine != "rust":
                    errors.append(
                        f"smoke_unit:{row.get('unit_id')}:paid_scope_requires_rust_engine:got={engine}"
                    )
                if feature_plane_status == "bar_stub_research_only":
                    errors.append(
                        f"smoke_unit:{row.get('unit_id')}:paid_scope_bar_stub_research_only"
                    )
                if feature_set_id == "fs_v1_pilot_unknown":
                    errors.append(
                        f"smoke_unit:{row.get('unit_id')}:paid_scope_unknown_feature_set"
                    )
                if bar_construction_id == "ohlcv_1m_from_npz_or_supplied_array":
                    errors.append(
                        f"smoke_unit:{row.get('unit_id')}:paid_scope_npz_bar_fallback"
                    )
                paid_scope_positive_promotions += _promoted_id_count(payload)
                paid_scope_positive_trade_rows += _positive_trade_rows(payload)
    if validated == 0:
        errors.append("smoke_manifest:no_validated_unit_artifacts")
    if paid_scope_artifacts == 0:
        errors.append("smoke_manifest:no_paid_scope_unit_artifacts")
    elif paid_scope_positive_promotions <= 0:
        errors.append("smoke_manifest:no_paid_scope_positive_promotions")
    if paid_scope_artifacts > 0 and paid_scope_positive_trade_rows < _MIN_POSITIVE_TRADE_ROWS:
        errors.append("smoke_manifest:no_paid_scope_positive_trade_rows")
    manifest["validated_unit_artifacts"] = validated
    manifest["paid_scope_unit_artifacts"] = paid_scope_artifacts
    manifest["paid_scope_positive_promotions"] = paid_scope_positive_promotions
    manifest["paid_scope_positive_trade_rows"] = paid_scope_positive_trade_rows
    manifest["feature_plane_status_counts"] = feature_plane_status_counts
    manifest["bar_construction_id_counts"] = bar_construction_id_counts
    manifest["out_dir"] = str(out_dir)
    return manifest


def _run_lookahead_pytest(repo_root: Path, errors: List[str]) -> str:
    cmd = [
        sys.executable,
        "-m",
        "pytest",
        "tests/test_vectorbt_adapter.py::TestFilterCandidates::test_same_close_jump_signal_does_not_enter_on_jump_close",
        "-q",
        "--tb=no",
    ]
    proc = subprocess.run(
        cmd,
        cwd=repo_root,
        capture_output=True,
        text=True,
        timeout=120,
    )
    tail = (proc.stdout or "") + (proc.stderr or "")
    if proc.returncode != 0:
        errors.append(f"lookahead_pytest:exit_{proc.returncode}")
    return tail.strip()[-500:]


def evaluate_gate(
    *,
    pilot_artifact: Path,
    smoke_manifest: Path,
    repo_root: Path,
    run_pytest: bool,
) -> Dict[str, Any]:
    errors: List[str] = []
    pilot_payload: Dict[str, Any] = {}
    if pilot_artifact.is_file():
        try:
            pilot_payload = _load_json(pilot_artifact)
        except (OSError, json.JSONDecodeError):
            pilot_payload = {}
    pilot = _validate_screening_file(pilot_artifact, errors, "pilot")
    smoke = _validate_smoke_manifest(smoke_manifest, errors)
    pytest_tail = ""
    if run_pytest:
        pytest_tail = _run_lookahead_pytest(repo_root, errors)

    pilot_hashes = _hash_fields(pilot or pilot_payload)
    smoke_unit_hashes: Dict[str, str] = {}
    for row in smoke.get("unit_results") or []:
        if row.get("status") not in {"OK", "OK_CACHED"}:
            continue
        rel = row.get("screening_artifact_relpath")
        if not rel:
            continue
        out_dir = Path(str(smoke.get("out_dir") or smoke_manifest.parent))
        payload = _load_json(out_dir / rel)
        smoke_unit_hashes = _hash_fields(payload)
        break

    if pilot_hashes.get("events_csv_hash") and smoke_unit_hashes.get("events_csv_hash"):
        if pilot_hashes["events_csv_hash"] != smoke_unit_hashes["events_csv_hash"]:
            errors.append("hash_mismatch:events_csv_hash")
    if pilot_hashes.get("lake_manifest_hash") and smoke_unit_hashes.get("lake_manifest_hash"):
        if pilot_hashes["lake_manifest_hash"] != smoke_unit_hashes["lake_manifest_hash"]:
            errors.append("hash_mismatch:lake_manifest_hash")

    feature_family_summary: Dict[str, Any] = {}
    ff_source = pilot if pilot else pilot_payload
    if ff_source:
        from backtest_pipeline.src.feature_family_status import evaluate_feature_family_paid_gate

        ff_errors, feature_family_summary = evaluate_feature_family_paid_gate(
            ff_source,
            repo_root=repo_root,
        )
        errors.extend(ff_errors)

    ready = len(errors) == 0
    result = {
        "ready_for_full_run": ready,
        "evaluated_at_utc": datetime.now(timezone.utc).isoformat(),
        "pilot_artifact": str(pilot_artifact),
        "smoke_manifest": str(smoke_manifest),
        "feature_family_gate": feature_family_summary,
        "pilot_hashes": pilot_hashes,
        "smoke_manifest_summary": {
            "expected_work_units": smoke.get("expected_work_units"),
            "completed_work_units": smoke.get("completed_work_units"),
            "failed_work_units": smoke.get("failed_work_units"),
            "units_per_hour": smoke.get("units_per_hour"),
            "validated_unit_artifacts": smoke.get("validated_unit_artifacts"),
            "paid_scope_unit_artifacts": smoke.get("paid_scope_unit_artifacts"),
            "paid_scope_positive_promotions": smoke.get("paid_scope_positive_promotions"),
            "paid_scope_positive_trade_rows": smoke.get("paid_scope_positive_trade_rows"),
            "feature_plane_status_counts": smoke.get("feature_plane_status_counts"),
            "bar_construction_id_counts": smoke.get("bar_construction_id_counts"),
        },
        "errors": errors,
        "lookahead_pytest_tail": pytest_tail,
    }
    return result


def write_gate_result(out: Path, result: Dict[str, Any]) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")


def watch_manifest_stall(manifest_path: Path, stall_minutes: int) -> int:
    if not manifest_path.is_file():
        print(f"STALL_WATCH: manifest missing: {manifest_path}", file=sys.stderr)
        return 2
    manifest = _load_json(manifest_path)
    completed = int(manifest.get("completed_work_units") or 0)
    last = int(manifest.get("_stall_watch_completed") or completed)
    manifest["_stall_watch_completed"] = completed
    manifest["_stall_watch_at_utc"] = datetime.now(timezone.utc).isoformat()
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    if completed == last:
        print(
            f"STALL_WATCH: no progress (completed={completed}); "
            f"investigate if elapsed > {stall_minutes}m",
            file=sys.stderr,
        )
        return 1
    print(f"STALL_WATCH: progress completed={completed}")
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Paid VectorBT screen ready gate")
    parser.add_argument("--pilot-artifact", type=Path, help="Pilot screening_artifact.json")
    parser.add_argument("--smoke-manifest", type=Path, help="Smoke paid_screen_run_manifest.json")
    parser.add_argument("--out", type=Path, default=_REPO / "runtime" / "reports" / "paid_screen_ready_gate.json")
    parser.add_argument("--repo-root", type=Path, default=_REPO)
    parser.add_argument("--skip-pytest", action="store_true")
    parser.add_argument("--watch-manifest", type=Path, default=None, help="Stall-watch mode")
    parser.add_argument("--stall-minutes", type=int, default=30)
    args = parser.parse_args(argv)

    if args.watch_manifest:
        return watch_manifest_stall(args.watch_manifest, args.stall_minutes)

    if not args.pilot_artifact or not args.smoke_manifest:
        parser.error("--pilot-artifact and --smoke-manifest required unless --watch-manifest")

    pilot = args.pilot_artifact if args.pilot_artifact.is_absolute() else _REPO / args.pilot_artifact
    smoke = args.smoke_manifest if args.smoke_manifest.is_absolute() else _REPO / args.smoke_manifest
    repo_root = args.repo_root if args.repo_root.is_absolute() else _REPO / args.repo_root

    result = evaluate_gate(
        pilot_artifact=pilot,
        smoke_manifest=smoke,
        repo_root=repo_root,
        run_pytest=not args.skip_pytest,
    )
    out = args.out if args.out.is_absolute() else _REPO / args.out
    write_gate_result(out, result)
    print(json.dumps({"ready_for_full_run": result["ready_for_full_run"], "errors": result["errors"]}))
    return 0 if result["ready_for_full_run"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
