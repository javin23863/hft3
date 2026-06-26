#!/usr/bin/env python3
"""Audit VectorBT paid-screen progress, feature_set_id uniqueness, and ETA.

Writes runtime/reports/vbt_run_progress_audit.json by default.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from hft3_bootstrap import setup_repo_paths

setup_repo_paths()

feature_plane_validation_errors = None
validate_screening_artifact = None
_VALIDATORS_LOADED = False
_FAST_ARTIFACT_SAMPLE_LIMIT = 20


def _ensure_validators_loaded() -> None:
    global _VALIDATORS_LOADED, feature_plane_validation_errors, validate_screening_artifact
    if _VALIDATORS_LOADED:
        return
    try:
        from backtest_pipeline.src.feature_plane import (  # noqa: PLC0415
            feature_plane_validation_errors as imported_feature_plane_validation_errors,
        )

        feature_plane_validation_errors = imported_feature_plane_validation_errors
    except ImportError:
        feature_plane_validation_errors = None

    try:
        from backtest_pipeline.src.vectorbt_adapter import (  # noqa: PLC0415
            validate_screening_artifact as imported_validate_screening_artifact,
        )

        validate_screening_artifact = imported_validate_screening_artifact
    except ImportError:
        validate_screening_artifact = None

    _VALIDATORS_LOADED = True


def _minimal_artifact_checks(payload: dict[str, Any]) -> list[str]:
    """Stdlib-only checks when Vast tree lacks feature_plane imports."""
    errors: list[str] = []
    fs = str(payload.get("feature_set_id") or "")
    fps = str(payload.get("feature_plane_status") or "")
    if not fs:
        errors.append("missing_feature_set_id")
    if fs == "fs_v1_pilot_unknown":
        errors.append("pilot_unknown_feature_set_not_production")
    if fps == "bar_stub_research_only":
        errors.append("bar_stub_not_feature_complete")
    if payload.get("vectorbt_engine") != "rust" and str(payload.get("screening_scope", "")).lower() in {
        "paid-compute",
        "paid_compute",
    }:
        errors.append("paid_compute_requires_rust_engine")
    return errors


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _utc_z(dt: datetime | None = None) -> str:
    return (dt or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _normalize_utc_z(value: str) -> str:
    return value[:-6] + "Z" if value.endswith("+00:00") else value


def _repo_artifact(path: Path | str | None) -> str | None:
    if path in (None, ""):
        return None
    p = Path(path)
    try:
        return p.resolve().relative_to(_REPO).as_posix()
    except (OSError, ValueError):
        return str(path)


def _repo_display_path(path: Path) -> str:
    try:
        return path.resolve().relative_to(_REPO).as_posix()
    except (OSError, ValueError):
        return str(path)


def _env_first(*names: str) -> str | None:
    for name in names:
        value = os.environ.get(name)
        if value:
            return value
    return None


def _as_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= 0 else None


def _as_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= 0 else None


def _first_present(payload: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in payload:
            return payload[key]
    return None


def _accounted_units(
    completed: int | None,
    failed: int | None,
    skipped: int | None,
) -> int | None:
    counts = (completed, failed, skipped)
    if all(value is None for value in counts):
        return None
    return sum(value for value in counts if value is not None)


def _positive_trade_rows(payload: dict[str, Any]) -> int:
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


def _sample_artifact_health(run_dir: Path, limit: int = _FAST_ARTIFACT_SAMPLE_LIMIT) -> dict[str, Any]:
    units_dir = run_dir / "units"
    sample_count = 0
    promoted_total = 0
    positive_trade_rows = 0
    feature_plane_statuses: Counter[str] = Counter()
    bar_construction_ids: Counter[str] = Counter()
    errors: list[str] = []
    if not units_dir.is_dir() or limit <= 0:
        return {
            "sample_artifact_count": sample_count,
            "sample_promoted_ids": promoted_total,
            "sample_positive_trade_rows": positive_trade_rows,
            "sample_feature_plane_status_counts": {},
            "sample_bar_construction_id_counts": {},
            "sample_validation_errors": errors,
        }
    for art in units_dir.glob("*/screening_artifact.json"):
        if sample_count >= limit:
            break
        try:
            payload = _load_json(art)
        except Exception as exc:  # noqa: BLE001 — audit should report and continue
            errors.append(f"{art.relative_to(run_dir)}:{exc}")
            sample_count += 1
            continue
        sample_count += 1
        promoted_total += len(payload.get("promoted_ids") or [])
        positive_trade_rows += _positive_trade_rows(payload)
        fps = str(payload.get("feature_plane_status") or "MISSING")
        feature_plane_statuses[fps] += 1
        bar_id = str(payload.get("bar_construction_id") or "MISSING")
        bar_construction_ids[bar_id] += 1

    if sample_count >= 10 and promoted_total == 0:
        errors.append(f"zero_promoted_ids_in_artifact_sample:n={sample_count}")
    if sample_count >= 10 and positive_trade_rows == 0:
        errors.append(f"zero_positive_trade_rows_in_artifact_sample:n={sample_count}")
    bar_stub_count = feature_plane_statuses.get("bar_stub_research_only", 0)
    if bar_stub_count:
        errors.append(f"bar_stub_research_only_in_artifact_sample:n={bar_stub_count}")
    npz_fallback_count = bar_construction_ids.get("ohlcv_1m_from_npz_or_supplied_array", 0)
    if npz_fallback_count:
        errors.append(f"npz_bar_fallback_in_artifact_sample:n={npz_fallback_count}")

    return {
        "sample_artifact_count": sample_count,
        "sample_promoted_ids": promoted_total,
        "sample_positive_trade_rows": positive_trade_rows,
        "sample_feature_plane_status_counts": dict(sorted(feature_plane_statuses.items())),
        "sample_bar_construction_id_counts": dict(sorted(bar_construction_ids.items())),
        "sample_validation_errors": errors,
    }


def _scan_run_dir(run_dir: Path) -> dict[str, Any]:
    _ensure_validators_loaded()
    manifest_path = run_dir / "paid_screen_run_manifest.json"
    units_dir = run_dir / "units"
    artifacts: list[Path] = []
    if units_dir.is_dir():
        artifacts = sorted(units_dir.glob("*/screening_artifact.json"))
    manifest = _load_json(manifest_path) if manifest_path.is_file() else None

    feature_set_ids: list[str] = []
    validation_errors: list[str] = []
    promoted_total = 0
    feature_plane_statuses: Counter[str] = Counter()
    for art in artifacts:
        try:
            payload = _load_json(art)
            if validate_screening_artifact is not None:
                validate_screening_artifact(payload)
            if feature_plane_validation_errors is not None:
                fp_errors = feature_plane_validation_errors(payload)
            else:
                fp_errors = _minimal_artifact_checks(payload)
            if fp_errors:
                validation_errors.extend([f"{art.name}:{e}" for e in fp_errors[:3]])
            fs = str(payload.get("feature_set_id") or "")
            if fs:
                feature_set_ids.append(fs)
            fps = str(payload.get("feature_plane_status") or "MISSING")
            feature_plane_statuses[fps] += 1
            promoted_total += len(payload.get("promoted_ids") or [])
        except Exception as exc:  # noqa: BLE001 — audit aggregates failures
            validation_errors.append(f"{art.relative_to(run_dir)}:{exc}")

    fs_counts = Counter(feature_set_ids)
    unique_fs = len(fs_counts)
    duplicate_fs = {k: v for k, v in fs_counts.items() if v > 1}
    if artifact_count >= 10 and promoted_total == 0:
        validation_errors.append(f"zero_promoted_ids_in_artifacts:n={artifact_count}")
    if artifact_count >= 10 and feature_plane_statuses.get("bar_stub_research_only", 0):
        validation_errors.append(
            "bar_stub_research_only_in_artifacts:"
            f"n={feature_plane_statuses.get('bar_stub_research_only', 0)}"
        )

    manifest_payload = manifest or {}
    expected = _as_int(manifest_payload.get("expected_work_units"))
    if expected is None:
        units_jsonl = _REPO / "runtime" / "reports" / "vbt_full_units.jsonl"
        if units_jsonl.is_file():
            try:
                with units_jsonl.open("r", encoding="utf-8") as fh:
                    expected = sum(1 for line in fh if line.strip())
            except OSError:
                pass
    completed = _as_int(manifest_payload.get("completed_work_units"))
    failed = _as_int(manifest_payload.get("failed_work_units"))
    skipped = _as_int(manifest_payload.get("skipped_work_units"))
    workers = _as_int(manifest_payload.get("workers"))
    collected_batches = _as_int(
        _first_present(manifest_payload, "collected_batches", "batches_collected")
    )
    expected_batches = _as_int(manifest_payload.get("expected_batches"))
    artifact_count = len(artifacts)
    accounted = _accounted_units(completed, failed, skipped)
    done_units = accounted if accounted is not None else artifact_count

    eta_seconds = None
    units_per_hour = _as_float(manifest_payload.get("units_per_hour"))
    remaining = max(0, expected - done_units) if expected is not None else None
    if remaining and units_per_hour and float(units_per_hour) > 0:
        eta_seconds = int(remaining / float(units_per_hour) * 3600)

    return {
        "run_id": run_dir.name,
        "manifest_path": str(manifest_path) if manifest_path.is_file() else None,
        "manifest_artifact": _repo_artifact(manifest_path) if manifest_path.is_file() else None,
        "manifest_status": manifest_payload.get("status"),
        "artifact": _repo_artifact(manifest_payload.get("out_dir") or run_dir),
        "workers": workers,
        "expected_work_units": expected,
        "completed_work_units": completed,
        "failed_work_units": failed,
        "skipped_work_units": skipped,
        "collected_batches": collected_batches,
        "expected_batches": expected_batches,
        "artifact_files_on_disk": artifact_count,
        "done_units_estimate": done_units,
        "remaining_units_estimate": remaining,
        "units_per_hour": units_per_hour,
        "eta_seconds": eta_seconds,
        "promoted_ids_in_artifacts": promoted_total,
        "unique_feature_set_ids": unique_fs,
        "feature_set_id_counts": dict(sorted(fs_counts.items())),
        "duplicate_feature_set_ids": duplicate_fs,
        "feature_plane_status_counts": dict(sorted(feature_plane_statuses.items())),
        "production_feature_set_ok": unique_fs >= 1 and "fs_v1_pilot_unknown" not in fs_counts,
        "feature_plane_production_ok": feature_plane_statuses.get("feature_complete_pit_declared", 0) > 0
        and feature_plane_statuses.get("bar_stub_research_only", 0) == 0,
        "validation_errors": validation_errors[:20],
        "validation_error_count": len(validation_errors),
    }


def _scan_run_dir_fast(run_dir: Path) -> dict[str, Any]:
    manifest_path = run_dir / "paid_screen_run_manifest.json"
    manifest = _load_json(manifest_path) if manifest_path.is_file() else None
    manifest_payload = manifest or {}

    expected = _as_int(manifest_payload.get("expected_work_units"))
    completed = _as_int(manifest_payload.get("completed_work_units"))
    failed = _as_int(manifest_payload.get("failed_work_units"))
    skipped = _as_int(manifest_payload.get("skipped_work_units"))
    workers = _as_int(manifest_payload.get("workers"))
    collected_batches = _as_int(
        _first_present(manifest_payload, "collected_batches", "batches_collected")
    )
    expected_batches = _as_int(manifest_payload.get("expected_batches"))
    units_per_hour = _as_float(manifest_payload.get("units_per_hour"))

    done_units = _accounted_units(completed, failed, skipped)
    remaining = (
        max(0, expected - done_units)
        if expected is not None and done_units is not None
        else None
    )
    eta_seconds = None
    if remaining and units_per_hour and float(units_per_hour) > 0:
        eta_seconds = int(remaining / float(units_per_hour) * 3600)
    sample = _sample_artifact_health(run_dir)
    validation_errors = list(sample["sample_validation_errors"])
    sample_artifact_count = sample["sample_artifact_count"]

    return {
        "run_id": run_dir.name,
        "manifest_path": str(manifest_path) if manifest_path.is_file() else None,
        "manifest_artifact": _repo_artifact(manifest_path) if manifest_path.is_file() else None,
        "manifest_status": manifest_payload.get("status"),
        "artifact": _repo_artifact(manifest_payload.get("out_dir") or run_dir),
        "workers": workers,
        "expected_work_units": expected,
        "completed_work_units": completed,
        "failed_work_units": failed,
        "skipped_work_units": skipped,
        "collected_batches": collected_batches,
        "expected_batches": expected_batches,
        "artifact_files_on_disk": completed,
        "done_units_estimate": done_units,
        "remaining_units_estimate": remaining,
        "units_per_hour": units_per_hour,
        "eta_seconds": eta_seconds,
        "promoted_ids_in_artifacts": None,
        "sample_artifact_count": sample_artifact_count,
        "sample_promoted_ids": sample["sample_promoted_ids"],
        "sample_positive_trade_rows": sample["sample_positive_trade_rows"],
        "unique_feature_set_ids": 0,
        "feature_set_id_counts": {},
        "duplicate_feature_set_ids": {},
        "feature_plane_status_counts": sample["sample_feature_plane_status_counts"],
        "bar_construction_id_counts": sample["sample_bar_construction_id_counts"],
        "production_feature_set_ok": None,
        "feature_plane_production_ok": None,
        "validation_error_count": len(validation_errors),
        "audit_mode": "fast_status",
        "artifact_audit_mode": "sampled" if sample_artifact_count else "skipped",
        "artifact_audit_skipped": sample_artifact_count == 0,
        "validation_errors": validation_errors[:20],
    }


def _status_state(best: dict[str, Any] | None) -> str:
    if not best:
        return "idle"
    status = str(best.get("manifest_status") or "").lower()
    expected = _as_int(best.get("expected_work_units"))
    completed = _as_int(best.get("completed_work_units")) or 0
    failed = _as_int(best.get("failed_work_units"))
    skipped = _as_int(best.get("skipped_work_units"))
    failed_count = failed or 0
    skipped_count = skipped or 0
    accounted = completed + failed_count + skipped_count
    has_rejected_units = failed_count > 0 or skipped_count > 0
    clean_rejections_if_known = failed == 0 and skipped == 0
    validation_error_count = _as_int(best.get("validation_error_count")) or 0
    has_validation_errors = (
        bool(best.get("validation_errors"))
        or bool(best.get("anomalies"))
        or validation_error_count > 0
    )
    if status == "complete":
        if (
            expected is not None
            and expected > 0
            and accounted == expected
            and clean_rejections_if_known
            and not has_validation_errors
        ):
            return "complete"
        if has_rejected_units:
            return "partial_failed"
        return "stalled"
    if status in {"running", "partial_failed", "aborted"}:
        return status
    if expected is not None and expected > 0 and accounted == expected:
        if has_validation_errors:
            return "partial_failed" if has_rejected_units else "stalled"
        if not has_rejected_units and not clean_rejections_if_known:
            return "stalled"
        return "partial_failed" if has_rejected_units else "complete"
    if accounted:
        return "running"
    return "observed"


def _build_status(best: dict[str, Any] | None, *, generated_at_utc: str) -> dict[str, Any]:
    generated_at_utc = _normalize_utc_z(generated_at_utc)
    eta_seconds = _as_int((best or {}).get("eta_seconds"))
    eta_utc = None
    if eta_seconds is not None:
        eta_utc = _utc_z(datetime.now(timezone.utc) + timedelta(seconds=eta_seconds))

    expected = (best or {}).get("expected_work_units")
    completed = (best or {}).get("completed_work_units")
    failed = (best or {}).get("failed_work_units")
    skipped = (best or {}).get("skipped_work_units")
    remaining = (best or {}).get("remaining_units_estimate")
    progress = {
        "expected": expected,
        "total": expected,
        "completed": completed,
        "failed": failed,
        "skipped": skipped,
        "remaining": remaining,
        "collected_batches": (best or {}).get("collected_batches"),
    }
    if (best or {}).get("expected_batches") is not None:
        progress["expected_batches"] = (best or {}).get("expected_batches")
    anomalies = list((best or {}).get("validation_errors") or [])
    failed_count = _as_int(failed)
    skipped_count = _as_int(skipped)
    if failed_count is not None and failed_count > 0:
        anomalies.append(f"failed_work_units={failed_count}")
    if skipped_count is not None and skipped_count > 0:
        anomalies.append(f"skipped_work_units={skipped_count}")
    if str((best or {}).get("manifest_status") or "").lower() == "complete":
        if "failed_work_units" not in (best or {}) or failed in (None, ""):
            anomalies.append("failed_work_units missing")
        if "skipped_work_units" not in (best or {}) or skipped in (None, ""):
            anomalies.append("skipped_work_units missing")

    status = {
        "state": _status_state(best),
        "status": _status_state(best),
        "run_id": (best or {}).get("run_id"),
        "workers": (best or {}).get("workers"),
        "expected": expected,
        "completed": completed,
        "failed": failed,
        "skipped": skipped,
        "expected_work_units": expected,
        "completed_work_units": completed,
        "failed_work_units": failed,
        "skipped_work_units": skipped,
        "collected_batches": (best or {}).get("collected_batches"),
        "units_per_hour": (best or {}).get("units_per_hour"),
        "eta_seconds": eta_seconds,
        "eta_utc": eta_utc,
        "last_sync_utc": generated_at_utc,
        "generated_at_utc": generated_at_utc,
        "manifest_artifact": (best or {}).get("manifest_artifact"),
        "manifest_path": (best or {}).get("manifest_path"),
        "artifact": (best or {}).get("artifact"),
        "output_path": (best or {}).get("artifact"),
        "sample_artifact_count": (best or {}).get("sample_artifact_count"),
        "sample_promoted_ids": (best or {}).get("sample_promoted_ids"),
        "sample_positive_trade_rows": (best or {}).get("sample_positive_trade_rows"),
        "feature_plane_status_counts": (best or {}).get("feature_plane_status_counts"),
        "bar_construction_id_counts": (best or {}).get("bar_construction_id_counts"),
        "progress": progress,
        "anomalies": anomalies or None,
        "host_label": _env_first("VBT_HOST_LABEL", "HOST_LABEL"),
        "ssh_host": _env_first("VAST_SSH_HOST", "SSH_HOST"),
        "tmux_session": _env_first("VBT_TMUX_SESSION", "TMUX_SESSION"),
    }
    if (best or {}).get("expected_batches") is not None:
        status["expected_batches"] = (best or {}).get("expected_batches")
    return status


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Audit VBT run progress and feature uniqueness.")
    parser.add_argument("--run-dir", type=Path, default=None, help="Single pipeline_runs/<id> directory")
    parser.add_argument("--pattern", default="paid_full_", help="Run dir name prefix when scanning")
    parser.add_argument(
        "--out",
        type=Path,
        default=_REPO / "runtime" / "reports" / "vbt_run_progress_audit.json",
    )
    parser.add_argument(
        "--status-out",
        type=Path,
        default=_REPO / "runtime" / "reports" / "vbt_full_status.json",
    )
    parser.add_argument(
        "--fast-status",
        action="store_true",
        help="Read only the latest matching run manifest and write status JSON without crawling unit artifacts.",
    )
    args = parser.parse_args(argv)

    runs_root = _REPO / "research_cards" / "pipeline_runs"
    if args.run_dir:
        targets = [args.run_dir if args.run_dir.is_absolute() else _REPO / args.run_dir]
    elif args.fast_status:
        candidates = [
            p
            for p in runs_root.iterdir()
            if p.is_dir()
            and p.name.startswith(args.pattern)
            and (p / "paid_screen_run_manifest.json").is_file()
        ]
        targets = sorted(
            candidates,
            key=lambda p: (p / "paid_screen_run_manifest.json").stat().st_mtime,
            reverse=True,
        )[:1]
    else:
        targets = sorted(
            [p for p in runs_root.iterdir() if p.is_dir() and p.name.startswith(args.pattern)],
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )

    scanner = _scan_run_dir_fast if args.fast_status else _scan_run_dir
    audits = [scanner(p) for p in targets]
    best = audits[0] if audits else None
    generated_at_utc = _utc_z()
    report = {
        "generated_at_utc": generated_at_utc,
        "runs_scanned": len(audits),
        "audit_mode": "fast_status" if args.fast_status else "full_artifact_audit",
        "latest_run": best,
        "all_runs": audits,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    args.status_out.parent.mkdir(parents=True, exist_ok=True)
    status_payload = _build_status(best, generated_at_utc=generated_at_utc)
    args.status_out.write_text(
        json.dumps(status_payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    if best:
        pct = 0.0
        if best.get("expected_work_units") and best.get("done_units_estimate"):
            pct = 100.0 * best["done_units_estimate"] / best["expected_work_units"]
        print(
            f"run={best['run_id']} artifacts={best['artifact_files_on_disk']} "
            f"progress={pct:.1f}% unique_feature_sets={best['unique_feature_set_ids']} "
            f"prod_fs_ok={best.get('production_feature_set_ok')} "
            f"fp_prod_ok={best.get('feature_plane_production_ok')} eta_s={best['eta_seconds']}"
        )
    print(
        json.dumps(
            {
                "status_output": _repo_display_path(args.status_out),
                "output": _repo_display_path(args.out),
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
