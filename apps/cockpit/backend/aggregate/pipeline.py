"""Pipeline zone — start-to-end stage state machine.

Current research prefilter: VectorBT paid screen on Vast (events.csv × active
model registry × discovery_confirmation). HftBacktest realism runs on VectorBT
promoted outputs only. Historical M6 path remains visible: Stage A survivors
-> run_event_universe gauntlet (labeled explicitly when surfaced).

Capture -> Feature Build -> VectorBT Screen (Vast) -> [Stage A historical] ->
Q001 Data Inventory -> Gauntlet B (M6 historical) -> M6 Gate -> Promote.

Each stage is derived purely from the artifact it writes; a stage whose
artifact is absent renders as MISSING ("not yet run"), never an error.
"""
from __future__ import annotations

import json
import math
import os
import re
import time
from pathlib import Path
from typing import Any, Optional

from .. import loaders, paths, schemas
from .system import _q001_inventory

try:
    from backtest_pipeline.src.vectorbt_adapter import (
        ScreeningArtifactError,
        validate_screening_artifact,
    )
except Exception:  # pragma: no cover - cockpit must stay up if package import breaks
    ScreeningArtifactError = ValueError
    validate_screening_artifact = None

try:
    from backtest_pipeline.src.hftbacktest_realism import (
        REPLAY_SUMMARY_REQUIRED_FIELDS as HBT_REPLAY_SUMMARY_REQUIRED_FIELDS,
        validate_replay_summary,
    )
except Exception:  # pragma: no cover - cockpit must stay up if package import breaks
    HBT_REPLAY_SUMMARY_REQUIRED_FIELDS = None
    validate_replay_summary = None

try:
    from backtest_pipeline.src.recipe_hash_gate import (
        extract_feature_recipe_hash_from_promoted_row,
        validate_feature_recipe_hash_handoff,
    )
except Exception:  # pragma: no cover - cockpit must stay up if package import breaks
    extract_feature_recipe_hash_from_promoted_row = None
    validate_feature_recipe_hash_handoff = None


def _stage(id_: str, label: str, status: str, **extra) -> dict:
    return {"id": id_, "label": label, "status": status, **extra}


_REPLAY_SUMMARY_REQUIRED_FIELDS = HBT_REPLAY_SUMMARY_REQUIRED_FIELDS or (
    "run_id",
    "created_at_utc",
    "hft3_commit",
    "screening_artifact_hash",
    "candidate_id",
    "model_id",
    "symbol",
    "research_clock",
    "event_or_session_scope",
    "hftbacktest_source_lock_hash",
    "data_validation_status",
    "latency_model_family",
    "exchange_model",
    "queue_model",
    "queue_model_source",
    "fill_model_scope",
    "partial_fill_policy",
    "time_in_force_policy",
    "accelerated_mode",
    "accuracy_tradeoff_declared",
    "queue_position_modeled",
    "order_response_latency_modeled",
    "full_replay_comparison_hash_or_not_run",
    "certification_allowed",
    "market_impact_mode",
    "orders_intended",
    "orders_submitted",
    "orders_acknowledged",
    "orders_cancelled",
    "fills_count",
    "partial_fills_count",
    "unfilled_count",
    "fill_rate",
    "avg_queue_position_or_not_available",
    "latency_p50_ms",
    "latency_p90_ms",
    "latency_p99_ms",
    "tick_size",
    "lot_size",
    "minimum_order_qty",
    "maker_fees",
    "taker_fees",
    "gross_pnl",
    "net_pnl",
    "execution_adjusted_expectancy",
    "max_drawdown",
    "adverse_selection_markout",
    "spread_capture_or_cost",
    "official_hftbacktest_replay_status",
    "official_replay_artifact_hash",
    "discrepancy_comparison_status",
    "discrepancy_comparison_artifact_hash",
    "certification_feedback_status",
    "replay_realism_status",
    "fail_closed_reasons",
)


def _active_run_context() -> dict[str, Any]:
    active = paths.read_json(paths.REPO / "runtime" / "workbench" / "active_run.json")
    return active if isinstance(active, dict) else {}


def _active_run_artifact_mismatch(path: Path, data: dict) -> dict[str, Any] | None:
    active = _active_run_context()
    policy = active.get("artifact_reuse_policy")
    if policy != "active_run_id_only":
        return None
    active_run_id = str(active.get("run_id") or "")
    observed_run_id = str(data.get("run_id") or "")
    if active_run_id and observed_run_id == active_run_id:
        return None
    return {
        "status": schemas.STALE,
        "detail": (
            f"artifact run_id={observed_run_id or 'missing'} "
            f"!= active_run_id={active_run_id or 'missing'}"
        ),
        "active_run_id": active_run_id or None,
        "artifact_reuse_policy": policy,
        "observed_run_id": observed_run_id or None,
        "artifact": path.relative_to(paths.REPO).as_posix(),
    }


def _survivor_payload_run_id(payload: Any) -> str:
    if isinstance(payload, dict):
        return str(payload.get("run_id") or "")
    if isinstance(payload, list):
        observed: set[str] = set()
        for row in payload:
            if not isinstance(row, dict):
                return ""
            row_run_id = str(row.get("run_id") or "")
            if not row_run_id:
                return ""
            observed.add(row_run_id)
        return observed.pop() if len(observed) == 1 else ""
    return ""


def _survivor_artifact_mismatch(payload: Any) -> dict[str, Any] | None:
    observed = _survivor_payload_run_id(payload)
    return _active_run_artifact_mismatch(paths.STAGE_A_SURVIVORS, {"run_id": observed})


def _rel(path: Path) -> str:
    try:
        return path.relative_to(paths.REPO).as_posix()
    except ValueError:
        return path.as_posix()


def _artifact_time(path: Path, data: Any, semantic_keys: tuple[str, ...]) -> tuple[float, str | None, str] | None:
    if isinstance(data, dict):
        for key in semantic_keys:
            dt = schemas.parse_iso(data.get(key) if isinstance(data.get(key), str) else None)
            if dt is not None:
                return dt.timestamp(), dt.isoformat(), key
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return None
    return mtime, paths.mtime_iso(path), "mtime"


def _latest_json_artifact(root: Path, filename: str, semantic_keys: tuple[str, ...]) -> dict[str, Any] | None:
    if not root.is_dir():
        return None
    latest: tuple[float, Path, Any, str | None, str] | None = None
    for artifact in root.glob(f"*/{filename}"):
        data = paths.read_json(artifact)
        observed = _artifact_time(artifact, data, semantic_keys)
        if observed is None:
            continue
        sort_time, observed_at, time_source = observed
        if latest is None or sort_time > latest[0]:
            latest = (sort_time, artifact, data, observed_at, time_source)
    if latest is None:
        return None
    _, artifact, data, observed_at, time_source = latest
    return {
        "path": artifact,
        "data": data,
        "observed_at": observed_at,
        "time_source": time_source,
    }


def _screening_rows(data: dict, key: str) -> list[dict[str, Any]]:
    rows = data.get(key)
    if not isinstance(rows, list):
        return []
    return [row for row in rows if isinstance(row, dict)]


def _screening_status(data: dict) -> str:
    if data.get("screening_backend") != "vectorbt":
        return schemas.STALE
    promoted = _screening_rows(data, "promoted")
    if promoted:
        return str(promoted[0].get("screening_status") or "pass")
    rejected = _screening_rows(data, "rejected")
    if rejected:
        return str(rejected[0].get("screening_status") or "rejected")
    rejected_ids = data.get("rejected_ids")
    promoted_ids = data.get("promoted_ids")
    if isinstance(rejected_ids, list) and rejected_ids and not promoted_ids:
        return "rejected"
    return str(data.get("screening_status") or schemas.UNKNOWN)


def _selected_screening_row(data: dict) -> dict[str, Any] | None:
    promoted = _screening_rows(data, "promoted")
    if promoted:
        return promoted[0]
    rejected = _screening_rows(data, "rejected")
    if rejected:
        return rejected[0]
    return None


def _screening_candidate_id_from_data(data: dict) -> str | None:
    row = _selected_screening_row(data)
    if row is None:
        return None
    candidate_id = str(row.get("candidate_id") or "").strip()
    return candidate_id or None


def _screening_robustness_status(data: dict) -> tuple[str, str | None, str | None]:
    row = _selected_screening_row(data)
    if row is None:
        return schemas.UNKNOWN, "no screening candidate row", None
    statuses = {
        "wfc_status": row.get("wfc_status"),
        "dsr_status": row.get("dsr_status"),
        "pbo_status": row.get("pbo_status"),
        "cscv_status": row.get("cscv_status"),
        "robustness_artifact_staleness": row.get("robustness_artifact_staleness"),
    }
    required = {
        "wfc_status": "pass",
        "dsr_status": "pass",
        "pbo_status": "pass",
        "cscv_status": "pass",
        "robustness_artifact_staleness": "fresh",
    }
    if all(str(statuses[key]) == expected for key, expected in required.items()):
        return "pass", None, str(row.get("candidate_id") or "") or None
    missing = [key for key, value in statuses.items() if value in (None, "")]
    if missing:
        return schemas.MISSING, "missing " + ", ".join(missing), str(row.get("candidate_id") or "") or None
    not_run = [key for key, value in statuses.items() if str(value) == "not_run"]
    if not_run:
        return "not_run", "not_run " + ", ".join(not_run), str(row.get("candidate_id") or "") or None
    failed = [f"{key}={value}" for key, value in statuses.items() if str(value) != required[key]]
    return "fail", ", ".join(failed), str(row.get("candidate_id") or "") or None


def _screening_surface_fields(data: dict) -> dict[str, Any]:
    row = _selected_screening_row(data)
    if row is None:
        return {
            "surface_stability_status": schemas.UNKNOWN,
            "surface_stability_detail": "no screening candidate row",
            "surface_formula_authority_status": schemas.UNKNOWN,
        }
    surface = row.get("surface_stability_metrics")
    if not isinstance(surface, dict):
        return {
            "surface_stability_status": schemas.MISSING,
            "surface_stability_detail": "surface_stability_metrics missing",
            "surface_formula_authority_status": schemas.MISSING,
        }
    return {
        "surface_stability_status": surface.get("status") or schemas.UNKNOWN,
        "surface_stability_detail": surface.get("reason") or surface.get("detail"),
        "surface_formula_authority_status": (
            surface.get("formula_authority_status") or schemas.UNKNOWN
        ),
    }


def _feature_recipe_hash_handoff_errors(
    replay_data: dict,
    screening_data: dict,
    *,
    require_evidence: bool,
    promoted_row: dict[str, Any] | None = None,
) -> list[str]:
    if extract_feature_recipe_hash_from_promoted_row is None or validate_feature_recipe_hash_handoff is None:
        if require_evidence:
            return ["feature_recipe_hash_handoff_validator_unavailable"]
        return []
    row = promoted_row or _selected_screening_row(screening_data)
    if row is None:
        return ["no_screening_candidate_row"] if require_evidence else []
    promoted_hash = extract_feature_recipe_hash_from_promoted_row(row)
    replay_hash = str(replay_data.get("feature_recipe_hash") or "").strip()
    if require_evidence and not promoted_hash and not replay_hash:
        return ["feature_recipe_hash_handoff_missing"]
    if require_evidence and not promoted_hash:
        return ["promoted_feature_recipe_hash_missing"]
    return validate_feature_recipe_hash_handoff(
        scenario_feature_recipe_hash=replay_hash,
        promoted_row=row,
    )


def _screening_replay_eligibility_fields(data: dict) -> dict[str, Any]:
    row = _selected_screening_row(data)
    if row is None:
        return {
            "replay_eligibility_status": schemas.UNKNOWN,
            "replay_eligibility_detail": "no screening candidate row",
        }
    status = row.get("replay_eligibility_status") or schemas.UNKNOWN
    detail = row.get("rejection_reason_or_null")
    if status == "eligible":
        if extract_feature_recipe_hash_from_promoted_row is None:
            return {
                "replay_eligibility_status": schemas.STALE,
                "replay_eligibility_detail": "feature_recipe_hash_handoff_validator_unavailable",
            }
        if not extract_feature_recipe_hash_from_promoted_row(row):
            return {
                "replay_eligibility_status": schemas.STALE,
                "replay_eligibility_detail": "feature_recipe_hash_handoff_missing",
            }
    return {
        "replay_eligibility_status": status,
        "replay_eligibility_detail": detail,
    }


def _count_or_len(data: dict, count_key: str, rows_key: str, ids_key: str) -> int | None:
    value = data.get(count_key)
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    rows = data.get(rows_key)
    if isinstance(rows, list):
        return len(rows)
    ids = data.get(ids_key)
    if isinstance(ids, list):
        return len(ids)
    return None


def _missing_vbt5_visibility_fields() -> dict[str, Any]:
    return {
        "screening_status": schemas.MISSING,
        "screening_artifact": None,
        "robustness_status": schemas.MISSING,
        "robustness_artifact": None,
        "surface_stability_status": schemas.MISSING,
        "surface_formula_authority_status": schemas.MISSING,
        "replay_eligibility_status": schemas.MISSING,
        "replay_status": schemas.MISSING,
        "replay_artifact": None,
    }


def _latest_screening_fields(run_id: str | None = None) -> dict[str, Any]:
    missing = _missing_vbt5_visibility_fields()
    missing.pop("replay_status", None)
    missing.pop("replay_artifact", None)
    if run_id:
        scoped_path = paths.pipeline_runs_root() / run_id / "screening_artifact.json"
        if not scoped_path.is_file():
            return {
                **missing,
                "screening_detail": f"no screening_artifact.json for tracking run_id={run_id}",
            }
        data = paths.read_json(scoped_path)
        if isinstance(data, dict):
            artifact_run_id = str(data.get("run_id") or "")
            if artifact_run_id and artifact_run_id != run_id:
                detail = (
                    f"screening_artifact.json run_id={artifact_run_id} "
                    f"!= tracking run_id={run_id}"
                )
                return {
                    **missing,
                    "screening_artifact": _rel(scoped_path),
                    "robustness_artifact": _rel(scoped_path),
                    "screening_status": schemas.STALE,
                    "screening_detail": detail,
                    "robustness_status": schemas.STALE,
                    "robustness_detail": detail,
                }
        observed = _artifact_time(scoped_path, data, ("created_at_utc", "generated_utc"))
        if observed is None:
            return {
                **missing,
                "screening_detail": f"screening_artifact.json unreadable for run_id={run_id}",
            }
        _, observed_at, time_source = observed
        latest = {
            "path": scoped_path,
            "data": data,
            "observed_at": observed_at,
            "time_source": time_source,
        }
    else:
        latest = _latest_json_artifact(
            paths.pipeline_runs_root(),
            "screening_artifact.json",
            ("created_at_utc", "generated_utc"),
        )
        if latest is None:
            return missing
    path = latest["path"]
    data = latest["data"]
    common = {
        "screening_artifact": _rel(path),
        "screening_created_at_utc": latest["observed_at"],
        "screening_time_source": latest["time_source"],
        "robustness_artifact": _rel(path),
    }
    if not isinstance(data, dict):
        return {
            **common,
            "screening_status": schemas.UNKNOWN,
            "screening_detail": "screening_artifact.json is missing or malformed",
            "robustness_status": schemas.UNKNOWN,
            "robustness_detail": "screening_artifact.json is missing or malformed",
            "surface_stability_status": schemas.UNKNOWN,
            "surface_stability_detail": "screening_artifact.json is missing or malformed",
            "surface_formula_authority_status": schemas.UNKNOWN,
            "replay_eligibility_status": schemas.UNKNOWN,
            "replay_eligibility_detail": "screening_artifact.json is missing or malformed",
        }
    mismatch = None if run_id else _active_run_artifact_mismatch(path, data)
    if mismatch:
        return {
            **common,
            "screening_status": schemas.STALE,
            "screening_detail": mismatch["detail"],
            "screening_run_id": data.get("run_id"),
            "screening_scope": data.get("screening_scope"),
            "screening_artifact_hash": data.get("screening_artifact_hash"),
            "screening_promoted_count": _count_or_len(data, "promoted_count", "promoted", "promoted_ids"),
            "screening_rejected_count": _count_or_len(data, "rejected_count", "rejected", "rejected_ids"),
            "robustness_status": schemas.STALE,
            "robustness_detail": mismatch["detail"],
            "robustness_artifact": _rel(path),
            "robustness_candidate_id": _screening_candidate_id_from_data(data),
            **_screening_surface_fields(data),
            **_screening_replay_eligibility_fields(data),
        }
    if validate_screening_artifact is None:
        return {
            **common,
            "screening_status": schemas.STALE,
            "screening_detail": "VectorBT artifact validator unavailable",
            "screening_artifact_hash": data.get("screening_artifact_hash"),
            "robustness_status": schemas.STALE,
            "robustness_detail": "VectorBT artifact validator unavailable",
            "robustness_artifact": _rel(path),
            "robustness_candidate_id": _screening_candidate_id_from_data(data),
            **_screening_surface_fields(data),
            **_screening_replay_eligibility_fields(data),
        }
    try:
        validate_screening_artifact(data)
        screening_detail = None
    except ScreeningArtifactError as exc:
        screening_detail = str(exc)
        return {
            **common,
            "screening_status": schemas.STALE,
            "screening_detail": screening_detail,
            "screening_run_id": data.get("run_id"),
            "screening_scope": data.get("screening_scope"),
            "screening_artifact_hash": data.get("screening_artifact_hash"),
            "screening_promoted_count": _count_or_len(data, "promoted_count", "promoted", "promoted_ids"),
            "screening_rejected_count": _count_or_len(data, "rejected_count", "rejected", "rejected_ids"),
            "robustness_status": schemas.STALE,
            "robustness_detail": "screening artifact validation failed",
            "robustness_artifact": _rel(path),
            "robustness_candidate_id": _screening_candidate_id_from_data(data),
            **_screening_surface_fields(data),
            **_screening_replay_eligibility_fields(data),
        }
    robustness_status, robustness_detail, robustness_candidate_id = _screening_robustness_status(data)
    return {
        **common,
        "screening_status": _screening_status(data),
        "screening_detail": screening_detail,
        "screening_run_id": data.get("run_id"),
        "screening_scope": data.get("screening_scope"),
        "screening_artifact_hash": data.get("screening_artifact_hash"),
        "screening_promoted_count": _count_or_len(data, "promoted_count", "promoted", "promoted_ids"),
        "screening_rejected_count": _count_or_len(data, "rejected_count", "rejected", "rejected_ids"),
        "robustness_status": robustness_status,
        "robustness_detail": robustness_detail,
        "robustness_candidate_id": robustness_candidate_id,
        **_screening_surface_fields(data),
        **_screening_replay_eligibility_fields(data),
    }


def _selected_screening_candidate_id(fields: dict[str, Any]) -> str | None:
    value = fields.get("robustness_candidate_id")
    if isinstance(value, str) and value:
        return value
    screening_data = _load_screening_artifact_data(fields)
    if screening_data is None:
        return None
    return _screening_candidate_id_from_data(screening_data)


def _replay_source_lock(path: Path) -> dict[str, Any] | None:
    lock = paths.read_json(path.with_name("hftbacktest_source_lock.json"))
    return lock if isinstance(lock, dict) else None


def _load_screening_artifact_data(screening_fields: dict[str, Any]) -> dict | None:
    artifact_rel = screening_fields.get("screening_artifact")
    if not isinstance(artifact_rel, str) or not artifact_rel:
        return None
    raw = paths.read_json(paths.REPO / artifact_rel)
    return raw if isinstance(raw, dict) else None


def _replay_summary_errors(
    data: dict,
    screening_fields: dict[str, Any],
    source_lock: dict[str, Any] | None,
    screening_data: dict | None = None,
    *,
    promoted_row: dict[str, Any] | None = None,
) -> list[str]:
    errors: list[str] = []
    for field in _REPLAY_SUMMARY_REQUIRED_FIELDS:
        if field not in data or data.get(field) in ("", None):
            errors.append(f"missing:{field}")
    if validate_replay_summary is None:
        errors.append("hftbacktest_replay_validator_unavailable")
    else:
        errors.extend(validate_replay_summary(data, source_lock=source_lock))
    if data.get("replay_realism_status") != "pass":
        errors.append("replay_realism_status_not_pass")
    if data.get("official_hftbacktest_replay_status") != "pass":
        errors.append("official_hftbacktest_replay_status_not_pass")
    if screening_fields.get("screening_status") != "pass":
        errors.append("screening_status_not_pass")
    if screening_fields.get("robustness_status") != "pass":
        errors.append("robustness_status_not_pass")
    if screening_fields.get("surface_stability_status") != "pass":
        errors.append("surface_stability_status_not_pass")
    if screening_fields.get("surface_formula_authority_status") not in {"defined", "pass"}:
        errors.append("surface_formula_authority_missing")
    if screening_fields.get("replay_eligibility_status") != "eligible":
        errors.append("replay_eligibility_status_not_eligible")
    expected_hash = screening_fields.get("screening_artifact_hash")
    if expected_hash and data.get("screening_artifact_hash") != expected_hash:
        errors.append("screening_artifact_hash_mismatch")
    expected_candidate = _selected_screening_candidate_id(screening_fields)
    if expected_candidate and data.get("candidate_id") != expected_candidate:
        errors.append("candidate_id_mismatch")
    pairing_established = not (
        (expected_hash and data.get("screening_artifact_hash") != expected_hash)
        or (expected_candidate and data.get("candidate_id") != expected_candidate)
    )
    require_recipe_evidence = (
        screening_fields.get("replay_eligibility_status") == "eligible"
        and pairing_established
    )
    if screening_data is not None:
        errors.extend(
            _feature_recipe_hash_handoff_errors(
                data,
                screening_data,
                require_evidence=require_recipe_evidence,
                promoted_row=promoted_row,
            )
        )
    elif require_recipe_evidence:
        errors.append("feature_recipe_hash_handoff_missing")
    return list(dict.fromkeys(errors))


def _paired_replay_for_candidate(
    screening_fields: dict[str, Any],
    candidate_id: str,
) -> tuple[dict[str, Any] | None, str | None]:
    root = paths.hftbacktest_realism_root()
    if not root.is_dir():
        return None, "hftbacktest_realism_root_missing"
    expected_hash = screening_fields.get("screening_artifact_hash")
    if not expected_hash:
        return None, "screening_hash_unavailable"
    if not candidate_id:
        return None, "candidate_id_unavailable"
    artifacts: list[dict[str, Any]] = []
    for artifact in root.glob("*/replay_summary.json"):
        data = paths.read_json(artifact)
        if not isinstance(data, dict):
            continue
        if data.get("screening_artifact_hash") != expected_hash:
            continue
        if data.get("candidate_id") != candidate_id:
            continue
        observed = _artifact_time(artifact, data, ("generated_utc", "created_at_utc"))
        if observed is None:
            continue
        sort_time, observed_at, time_source = observed
        artifacts.append(
            {
                "path": artifact,
                "data": data,
                "observed_at": observed_at,
                "time_source": time_source,
                "sort_time": sort_time,
            }
        )
    if not artifacts:
        return None, "no_paired_replay_summary_for_screening_hash_and_candidate"
    artifacts.sort(key=lambda item: item["sort_time"], reverse=True)
    return artifacts[0], None


def _latest_paired_replay_artifact(screening_fields: dict[str, Any]) -> tuple[dict[str, Any] | None, str | None]:
    expected_candidate = _selected_screening_candidate_id(screening_fields)
    if not expected_candidate:
        root = paths.hftbacktest_realism_root()
        if not root.is_dir():
            return None, None
        artifacts: list[dict[str, Any]] = []
        for artifact in root.glob("*/replay_summary.json"):
            data = paths.read_json(artifact)
            observed = _artifact_time(artifact, data, ("generated_utc", "created_at_utc"))
            if observed is None:
                continue
            sort_time, observed_at, time_source = observed
            artifacts.append(
                {
                    "path": artifact,
                    "data": data,
                    "observed_at": observed_at,
                    "time_source": time_source,
                    "sort_time": sort_time,
                }
            )
        if not artifacts:
            return None, None
        artifacts.sort(key=lambda item: item["sort_time"], reverse=True)
        return artifacts[0], "screening_hash_or_candidate_unavailable"
    paired, pair_error = _paired_replay_for_candidate(screening_fields, expected_candidate)
    if paired is not None:
        return paired, pair_error
    root = paths.hftbacktest_realism_root()
    if not root.is_dir():
        return None, None
    artifacts: list[dict[str, Any]] = []
    for artifact in root.glob("*/replay_summary.json"):
        data = paths.read_json(artifact)
        observed = _artifact_time(artifact, data, ("generated_utc", "created_at_utc"))
        if observed is None:
            continue
        sort_time, observed_at, time_source = observed
        artifacts.append(
            {
                "path": artifact,
                "data": data,
                "observed_at": observed_at,
                "time_source": time_source,
                "sort_time": sort_time,
            }
        )
    if not artifacts:
        return None, None
    artifacts.sort(key=lambda item: item["sort_time"], reverse=True)
    return artifacts[0], pair_error or "no_paired_replay_summary_for_screening_hash_and_candidate"


def _validated_promoted_candidate_count(screening_fields: dict[str, Any]) -> int:
    screening_data = _load_screening_artifact_data(screening_fields)
    if not screening_data:
        return 0
    promoted_rows = _screening_rows(screening_data, "promoted")
    if not promoted_rows:
        return 0
    validated = 0
    for row in promoted_rows:
        candidate_id = str(row.get("candidate_id") or "").strip()
        if not candidate_id:
            continue
        paired, pair_error = _paired_replay_for_candidate(screening_fields, candidate_id)
        if pair_error or paired is None:
            continue
        path = paired["path"]
        data = paired["data"]
        if not isinstance(data, dict):
            continue
        per_candidate_fields = {**screening_fields, "robustness_candidate_id": candidate_id}
        source_lock = _replay_source_lock(path)
        errors = _replay_summary_errors(
            data,
            per_candidate_fields,
            source_lock,
            screening_data,
            promoted_row=row,
        )
        if not errors:
            validated += 1
    return validated


def _latest_replay_fields(screening_fields: dict[str, Any] | None = None) -> dict[str, Any]:
    screening_fields = screening_fields or {}
    latest, pair_error = _latest_paired_replay_artifact(screening_fields)
    if latest is None:
        return {
            "replay_status": schemas.MISSING,
            "replay_artifact": None,
        }
    path = latest["path"]
    data = latest["data"]
    common = {
        "replay_artifact": _rel(path),
        "replay_created_at_utc": latest["observed_at"],
        "replay_time_source": latest["time_source"],
    }
    if not isinstance(data, dict):
        return {
            **common,
            "replay_status": schemas.UNKNOWN,
            "replay_detail": "replay_summary.json is missing or malformed",
        }
    if pair_error:
        return {
            **common,
            "replay_status": schemas.STALE,
            "replay_detail": pair_error,
            "replay_run_id": data.get("run_id"),
            "replay_candidate_id": data.get("candidate_id"),
            "replay_screening_artifact_hash": data.get("screening_artifact_hash"),
            "official_hftbacktest_replay_status": data.get("official_hftbacktest_replay_status"),
        }
    source_lock = _replay_source_lock(path)
    screening_data = _load_screening_artifact_data(screening_fields)
    errors = _replay_summary_errors(data, screening_fields, source_lock, screening_data)
    if errors:
        return {
            **common,
            "replay_status": schemas.STALE,
            "replay_detail": ", ".join(errors),
            "replay_run_id": data.get("run_id"),
            "replay_candidate_id": data.get("candidate_id"),
            "replay_screening_artifact_hash": data.get("screening_artifact_hash"),
            "official_hftbacktest_replay_status": data.get("official_hftbacktest_replay_status"),
        }
    return {
        **common,
        "replay_status": str(data.get("replay_realism_status") or schemas.UNKNOWN),
        "replay_run_id": data.get("run_id"),
        "replay_candidate_id": data.get("candidate_id"),
        "replay_screening_artifact_hash": data.get("screening_artifact_hash"),
        "official_hftbacktest_replay_status": data.get("official_hftbacktest_replay_status"),
        "hftbacktest_source_lock_hash": data.get("hftbacktest_source_lock_hash"),
    }


def _vbt5_visibility_fields() -> dict[str, Any]:
    screening_fields = _latest_screening_fields()
    return {**screening_fields, **_latest_replay_fields(screening_fields)}


def _promote_scoped_tracking_status(
    base_status: str,
    tracking: dict[str, Any],
) -> tuple[str, str | None]:
    """Fail-closed promote gate when scoped to a VectorBT paid-screen tracking run."""
    state = tracking.get("state") or "idle"
    anomalies = tracking.get("anomalies")
    anomaly_list = anomalies if isinstance(anomalies, list) else []
    if state == "complete" and not anomaly_list:
        return base_status, None

    detail_parts: list[str] = []
    if state != "complete":
        detail_parts.append(f"tracking_state={state}")
    detail_parts.extend(anomaly_list)
    tracking_detail = "; ".join(detail_parts)

    if base_status == schemas.OK:
        if state == "idle":
            return schemas.MISSING, tracking_detail
        return schemas.STALE, tracking_detail
    return base_status, tracking_detail


def _vbt5_promote_status(base_status: str, vbt5: dict[str, Any]) -> tuple[str, str | None]:
    if base_status != schemas.OK:
        return base_status, None
    required = {
        "screening_status": "pass",
        "robustness_status": "pass",
        "surface_stability_status": "pass",
        "replay_eligibility_status": "eligible",
        "replay_status": "pass",
    }
    for key, expected in required.items():
        if vbt5.get(key) != expected:
            return schemas.STALE, f"{key}_not_{expected}"
    if vbt5.get("surface_formula_authority_status") not in {"defined", "pass"}:
        return schemas.STALE, "surface_formula_authority_missing"
    return schemas.OK, None


def _capture_stage() -> dict:
    data = paths.read_json(paths.CAPTURE_BASELINE)
    if not isinstance(data, dict):
        return _stage("capture", "Capture", schemas.MISSING,
                      detail="no baseline on this host (live manifest is colo-side)")
    gaps = data.get("known_gaps") or []
    drift = data.get("drift_warnings") or []
    status = schemas.FAIL if gaps else (schemas.AMBER if drift else schemas.OK)
    if status == schemas.AMBER:  # zone-health amber maps to a non-fatal warn dot
        status = schemas.STALE
    return _stage(
        "capture", "Capture", status,
        host=data.get("host_id"),
        captured_at=data.get("captured_at"),
        known_gaps=len(gaps),
        drift_warnings=len(drift),
        cpu_layout=data.get("cpu_layout"),
        artifact=str(paths.CAPTURE_BASELINE.relative_to(paths.REPO)),
        note="hardware/capture baseline; live per-symbol manifest lives on CHI404",
    )


def _feature_stage() -> dict:
    data = paths.read_json(paths.FEATURE_FABRIC)
    if not isinstance(data, dict):
        return _stage("feature_build", "Feature Build", schemas.MISSING)
    mismatch = _active_run_artifact_mismatch(paths.FEATURE_FABRIC, data)
    if mismatch:
        return _stage("feature_build", "Feature Build", **mismatch)
    return _stage(
        "feature_build", "Feature Build", schemas.OK,
        generated_at=data.get("generated_at_utc"),
        row_count=data.get("row_count"),
        rejected_count=data.get("rejected_count"),
        schema_version=data.get("schema_version"),
        artifact=str(paths.FEATURE_FABRIC.relative_to(paths.REPO)),
    )


def _stage_a_stage() -> dict:
    raw = loaders.stage_a_raw()
    if not isinstance(raw, dict):
        return _stage(
            "stage_a", "Stage A (historical M6)", schemas.MISSING,
            historical=True,
            note="Historical M6 cell screen; not a VectorBT paid-screen prerequisite.",
        )
    mismatch = _active_run_artifact_mismatch(paths.STAGE_A_RESULT, raw)
    if mismatch:
        return _stage("stage_a", "Stage A (historical M6)", historical=True, **mismatch)
    units_run = raw.get("units_run", 0) or 0
    errored = raw.get("units_errored", 0) or 0
    survivors = paths.read_json(paths.STAGE_A_SURVIVORS)
    survivor_mismatch = _survivor_artifact_mismatch(survivors)
    if survivor_mismatch:
        return _stage("stage_a", "Stage A (historical M6)", historical=True, **survivor_mismatch)
    n_survivors: Optional[int] = None
    if isinstance(survivors, list):
        n_survivors = len(survivors)
    elif isinstance(survivors, dict):
        for k in ("survivors", "hypothesis_ids", "ids"):
            if isinstance(survivors.get(k), list):
                n_survivors = len(survivors[k])
                break
    status = schemas.OK if units_run > 0 and errored == 0 else (
        schemas.FAIL if errored > 0 else schemas.MISSING)
    stamp = raw.get("certification_stamp", {}) if isinstance(raw.get("certification_stamp"), dict) else {}
    return _stage(
        "stage_a", "Stage A (historical M6)", status,
        run_end=raw.get("run_end_utc"),
        units_run=units_run,
        units_skipped=raw.get("units_skipped", 0),
        units_errored=errored,
        band_ms=raw.get("band_ms"),
        n_cells=len(raw.get("cells", [])),
        survivors=n_survivors,
        cert_status=stamp.get("status"),
        promotion_label=raw.get("promotion_label"),
        artifact=str(paths.STAGE_A_RESULT.relative_to(paths.REPO)),
        historical=True,
        note="Historical M6 cell screen; not a VectorBT paid-screen prerequisite.",
    )


_SMOKE_SCOPE_KEYS = ("max_events", "event_type", "cells", "shard")
_FULL_SCOPE_KEYS = (
    "lane",
    "bands_override",
    "event_type",
    "symbols",
    "events_csv",
    "workers",
    "max_events",
    "from_stage_a",
    "cells",
    "shard",
)
_FULL_SYMBOL_SCOPE = {"MES.v.0", "MNQ.v.0", "ES.v.0", "NQ.v.0", "ZN.v.0", "ZB.v.0", "RTY.v.0"}
_M6_FULL_ARTIFACT_ID = "research_cards/universe_M6_full/universe_result.json"
_M6_BAND_MS = 6.255764
_FULL_STAGE_A = "research_cards/stage_a_full/stage_a_survivors.json"
_Q001_MBO_PILOT_MANIFEST = "packages/data_system/config/mbo_pilot_basket_20260605_manifest.json"
_Q001_ACCEPTED_SKIP_REASONS = {"no_market_data", "symbol_absent_in_raw_after_redownload"}
_MALFORMED_SKIP_REASON_COUNTS = "malformed_skip_reason_counts"
_Q001_STAGE_FIELDS = (
    "q001_status",
    "artifact",
    "missing_or_unavailable_slots",
    "data_doctor_status",
    "strict_mbo_gap_count",
    "strict_mbo_stale_gap_count",
)
_DSR_CDF_MIN = 0.95
_DSR_EVIDENCE_KEYS = ("dsr_cdf", "dsr", "deflated_sharpe", "value")


def _q001_stage() -> dict:
    q001 = _q001_inventory()
    status = q001.get("status") or schemas.UNKNOWN
    gaps = q001.get("gaps")
    gap_count = len(gaps) if isinstance(gaps, list) else 0
    extra = {key: q001.get(key) for key in _Q001_STAGE_FIELDS}
    artifact = extra.get("artifact")
    if isinstance(artifact, str):
        extra["artifact"] = artifact.replace("\\", "/")
    extra["gap_count"] = gap_count
    if status != schemas.OK:
        extra["detail"] = (
            f"Q001 inventory {status}: "
            f"q001_status={q001.get('q001_status')}, gap_count={gap_count}"
        )
    return _stage("q001_inventory", "Q001 Data Inventory", status, **extra)


def _preferred_universe_path(fallback: Path) -> Path:
    full = getattr(paths, "M6_FULL_RESULT", paths.REPO / _M6_FULL_ARTIFACT_ID)
    return full if full.is_file() else fallback


def _nested(data: Any, *keys: str) -> Any:
    cur = data
    for key in keys:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(key)
    return cur


def _finite_float(value: Any) -> Optional[float]:
    if isinstance(value, bool):
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _round(value: Optional[float], digits: int) -> Optional[float]:
    return None if value is None else round(value, digits)


def _jsonl_metric(path: Path, field: str) -> Optional[float]:
    text = paths.read_text(path)
    if not text:
        return None
    for line in text.splitlines():
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        value = _finite_float(row.get(field)) if isinstance(row, dict) else None
        if value is not None:
            return value
    return None


_COMPONENT_BAND_ORDER = (
    "feed_latency_us",
    "new_send_to_exchange_us",
    "new_exchange_to_ack_us",
    "cancel_send_to_exchange_us",
    "cancel_exchange_to_ack_us",
)
_RE_UNIVERSE_PROGRESS = re.compile(r"\[(\d+)/(\d+)\]")
_RE_WORK_UNITS = re.compile(
    r"Work units:\s*(\d+)\s+reused:\s*(\d+)\s+remaining:\s*(\d+)\s+skipped:\s*(\d+)"
)
_RE_VBT_UNIT_PROGRESS = re.compile(r"\[unit\]\s+(\S+)\s+->\s+(\S+)")
_RE_VBT_DRAIN_PROGRESS = re.compile(
    r"\[drain\]\s+batch=\S+\s+units=(\d+)\s+ok=(\d+)\s+failed=(\d+)\s+collected=(\d+)/(\d+)"
)
_RENTED_WORKER_THRESHOLD = 64
_VBT_RENTED_WORKER_THRESHOLD = 64
_VBT_DEFAULT_RESEARCH_SPLIT = "discovery_confirmation"  # BLUEPRINT §8 canonical VectorBT prefilter split
_DECL_RUN_ID_KEYS = ("run_id", "vbt_full_run_id")
_DECL_SOURCE_LOCK_KEYS = (
    "source_lock",
    "hftbacktest_source_lock_hash",
    "source_lock_hash",
)
_HISTORICAL_STAGE_IDS = frozenset({"stage_a", "gauntlet_b", "m6_gate"})


def _declaration_run_id(decl: dict[str, Any]) -> str | None:
    for key in _DECL_RUN_ID_KEYS:
        value = decl.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _manifest_events_csv_hash(manifest: dict[str, Any]) -> str:
    return str(
        manifest.get("events_csv_hash")
        or manifest.get("events_csv_hash_or_not_applicable")
        or ""
    ).strip()


def _manifest_lake_manifest_hash(manifest: dict[str, Any]) -> str:
    return str(manifest.get("lake_manifest_hash") or "").strip()


def _declaration_expected_work_units_aligned(
    decl: dict[str, Any],
    manifest: dict[str, Any],
) -> bool:
    decl_expected = _as_int(decl.get("expected_work_units"), -1)
    man_expected = _as_int(manifest.get("expected_work_units"), -1)
    if decl_expected > 0 and man_expected > 0 and decl_expected != man_expected:
        return False
    return True


def _declaration_matches_manifest(
    decl: dict[str, Any],
    *,
    run_id: str | None,
    manifest: dict[str, Any],
) -> bool:
    if not decl:
        return False
    decl_run_id = _declaration_run_id(decl)
    if isinstance(run_id, str) and run_id:
        if decl_run_id == run_id:
            return _declaration_expected_work_units_aligned(decl, manifest)
        if decl_run_id:
            return False
    decl_events = str(decl.get("events_csv_hash") or "").strip()
    decl_lake = _manifest_lake_manifest_hash(decl)
    man_events = _manifest_events_csv_hash(manifest)
    man_lake = _manifest_lake_manifest_hash(manifest)
    if decl_events and man_events and decl_events != man_events:
        return False
    if decl_lake and man_lake and decl_lake != man_lake:
        return False
    if decl_events and decl_lake:
        if not (man_events and man_lake):
            return False
        if decl_events != man_events or decl_lake != man_lake:
            return False
        return _declaration_expected_work_units_aligned(decl, manifest)
    for key in _DECL_SOURCE_LOCK_KEYS:
        decl_val = decl.get(key)
        manifest_val = manifest.get(key)
        if decl_val and manifest_val and decl_val == manifest_val:
            return _declaration_expected_work_units_aligned(decl, manifest)
    return False


def _pipeline_health_stages(
    stages: list[dict[str, Any]],
    *,
    scoped_run: bool,
) -> list[dict[str, Any]]:
    if not scoped_run:
        return stages
    return [
        stage
        for stage in stages
        if not (
            stage.get("historical")
            or stage.get("id") in _HISTORICAL_STAGE_IDS
        )
    ]


def _component_band_rows(component_bands: Any) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    bands = component_bands if isinstance(component_bands, dict) else {}
    for name in _COMPONENT_BAND_ORDER:
        band = bands.get(name)
        if not isinstance(band, dict):
            rows.append(
                {
                    "name": name,
                    "measurement_status": "MISSING",
                    "p99_us": None,
                    "note": None,
                    "source_run_id": None,
                }
            )
            continue
        dist = band.get("distribution_us")
        p99_us = dist.get("p99_us") if isinstance(dist, dict) else None
        rows.append(
            {
                "name": name,
                "measurement_status": str(band.get("measurement_status") or "UNKNOWN"),
                "p99_us": _finite_float(p99_us),
                "note": band.get("note"),
                "source_run_id": band.get("source_run_id"),
            }
        )
    return rows


def _live_placement_summary(latency_truth: Any) -> dict[str, Any] | None:
    live = _nested(latency_truth, "live_placement") if isinstance(latency_truth, dict) else None
    if not isinstance(live, dict):
        return None
    samples = live.get("samples") if isinstance(live.get("samples"), dict) else {}
    offensive = live.get("offensive_us") if isinstance(live.get("offensive_us"), dict) else {}
    defensive = live.get("defensive_us") if isinstance(live.get("defensive_us"), dict) else {}
    capability = paths.read_json(paths.LATENCY_LIVE_PLACEMENT_CAPABILITY)
    capability_status = None
    if isinstance(capability, dict):
        capability_status = capability.get("status") or capability.get("verdict")
    return {
        "measured_utc": live.get("measured_utc"),
        "run_id": live.get("run_id"),
        "host": live.get("host"),
        "system": live.get("system"),
        "gateway": live.get("gateway"),
        "hot_path_language": live.get("hot_path_language"),
        "paired_new_ack": samples.get("paired_new_ack"),
        "paired_cancel_submit": samples.get("paired_cancel_submit"),
        "cancel_ack": samples.get("cancel_ack"),
        "tick_to_send_p99_us": _finite_float(offensive.get("tick_to_send_p99")),
        "decision_to_send_p99_us": _finite_float(offensive.get("decision_to_send_p99")),
        "cancel_to_send_p99_us": _finite_float(defensive.get("cancel_to_send_p99")),
        "cancel_to_ack_p99_us": _finite_float(defensive.get("cancel_to_ack_p99")),
        "capability_status": capability_status,
        "capability_artifact": live.get("capability_artifact"),
        "baseline_artifact": live.get("baseline_artifact"),
    }


def _execution_realism_flags(latency_truth: Any) -> dict[str, Any]:
    if not isinstance(latency_truth, dict):
        return {"hftbacktest_regimes_present": False, "cc_component_ingest_present": False}
    regimes = latency_truth.get("hftbacktest_regimes")
    ingest = latency_truth.get("cc_component_ingest")
    regime_names = regimes.get("regimes") if isinstance(regimes, dict) else None
    return {
        "hftbacktest_regimes_present": isinstance(regimes, dict),
        "hftbacktest_regime_count": len(regime_names) if isinstance(regime_names, list) else 0,
        "hftbacktest_regimes_artifact": (
            regimes.get("artifacts_dir") if isinstance(regimes, dict) else None
        ),
        "cc_component_ingest_present": isinstance(ingest, dict),
        "cc_component_ingest_utc": ingest.get("last_ingest_utc") if isinstance(ingest, dict) else None,
    }


def _latest_universe_log() -> Path | None:
    candidates = [
        *paths.REPO.glob("runtime/universe_M6*.log"),
        *paths.REPO.glob("runtime/universe_M6*.log.*"),
    ]
    latest: tuple[float, Path] | None = None
    for path in candidates:
        if not path.is_file() or path.suffix == ".err":
            continue
        try:
            mtime = path.stat().st_mtime
        except OSError:
            continue
        if latest is None or mtime > latest[0]:
            latest = (mtime, path)
    return latest[1] if latest else None


def _universe_sweep_host_label(workers: Any) -> tuple[str, str, str | None]:
    override = os.environ.get("HFT3_UNIVERSE_SWEEP_HOST", "").strip()
    if override:
        return "external", override, None
    if isinstance(workers, int) and not isinstance(workers, bool):
        if workers >= _RENTED_WORKER_THRESHOLD:
            return "rented", "vast-or-rented", None
        if workers >= 12:
            note = (
                f"Detected laptop-class run (workers={workers}). "
                "Authoritative M6 recovery should use Vast/rented host with high worker count."
            )
            return "local", "laptop", note
    return "unknown", "unknown", None


def _tail_log_text(path: Path, *, max_bytes: int = 65536) -> str:
    try:
        size = path.stat().st_size
        with open(path, "rb") as fh:
            if size > max_bytes:
                fh.seek(size - max_bytes)
            return fh.read().decode("utf-8", errors="replace")
    except OSError:
        return paths.read_text(path) or ""


def _universe_sweep_tracking() -> dict[str, Any]:
    context = paths.read_json(paths.M6_FULL_CHECKPOINT)
    workers = _nested(context, "cli_args", "workers") if isinstance(context, dict) else None
    host_kind, host_label, host_note = _universe_sweep_host_label(workers)
    log_path = _latest_universe_log()
    progress: dict[str, Any] = {}
    log_tail = ""
    state = "idle"
    age_s: float | None = None
    if log_path is not None:
        log_text = _tail_log_text(log_path)
        lines = [line for line in log_text.splitlines() if line.strip()]
        log_tail = "\n".join(lines[-25:])
        work_units = _RE_WORK_UNITS.search(log_text)
        if work_units:
            progress = {
                "total": int(work_units.group(1)),
                "reused": int(work_units.group(2)),
                "remaining": int(work_units.group(3)),
                "skipped": int(work_units.group(4)),
            }
        progress_lines = [line for line in lines if _RE_UNIVERSE_PROGRESS.search(line)]
        if progress_lines:
            match = _RE_UNIVERSE_PROGRESS.search(progress_lines[-1])
            if match:
                progress["current_unit"] = int(match.group(1))
                progress["current_total"] = int(match.group(2))
        try:
            age_s = max(0.0, time.time() - log_path.stat().st_mtime)
        except OSError:
            age_s = None
        current_unit = progress.get("current_unit")
        current_total = progress.get("current_total")
        if paths.M6_FULL_RESULT.is_file():
            state = "complete"
        elif isinstance(current_unit, int) and isinstance(current_total, int) and current_total > 0:
            if current_unit >= current_total:
                state = "complete"
            elif age_s is not None and age_s < 300:
                state = "running"
            else:
                state = "stalled"
        elif progress.get("remaining") and age_s is not None and age_s < 300:
            state = "running"
        elif progress_lines or progress.get("remaining"):
            state = "stalled"
        else:
            state = "observed"
    detail = host_note
    if host_kind in {"rented", "external"} and log_path and age_s is not None and age_s > 300:
        sync_note = "Mirror Vast log/checkpoint to repo (see runtime/monitor/universe_M6_full_watch.md)."
        detail = (detail + " " + sync_note if detail else sync_note)
    if state == "stalled" and host_kind == "local":
        detail = (detail + " Local run appears stalled/killed." if detail else "Local run appears stalled/killed.")
    return {
        "state": state,
        "host_kind": host_kind,
        "host_label": host_label,
        "workers": workers,
        "git_commit": context.get("git_commit") if isinstance(context, dict) else None,
        "log_artifact": _rel(log_path) if log_path else None,
        "checkpoint_artifact": _rel(paths.M6_FULL_CHECKPOINT) if paths.M6_FULL_CHECKPOINT.is_file() else None,
        "output_artifact": _rel(paths.M6_FULL_RESULT) if paths.M6_FULL_RESULT.is_file() else None,
        "progress": progress or None,
        "log_tail": log_tail[-2400:] if log_tail else None,
        "detail": detail,
        "repo_state_doc": "docs/REPO_STATE.md",
        "monitor_doc": (
            _rel(paths.UNIVERSE_MONITOR_DOC)
            if paths.UNIVERSE_MONITOR_DOC.is_file()
            else "runtime/monitor/universe_M6_full_watch.md"
        ),
        "tracking_mode": "read_only_external",
    }


def _latest_paid_screen_manifest() -> dict[str, Any] | None:
    root = paths.pipeline_runs_root()
    if not root.is_dir():
        return None
    latest: tuple[float, Path, Any, str | None, str] | None = None
    for manifest_path in root.glob("*/paid_screen_run_manifest.json"):
        data = paths.read_json(manifest_path)
        observed = _artifact_time(
            manifest_path,
            data,
            ("finished_at_utc", "started_at_utc"),
        )
        if observed is None:
            continue
        sort_time, observed_at, time_source = observed
        if latest is None or sort_time > latest[0]:
            latest = (sort_time, manifest_path, data, observed_at, time_source)
    if latest is None:
        return None
    _, path, data, observed_at, time_source = latest
    return {
        "path": path,
        "data": data if isinstance(data, dict) else {},
        "observed_at": observed_at,
        "time_source": time_source,
        "run_id": path.parent.name,
    }


def _manifest_out_dir_allowed(manifest: dict[str, Any], out_dir: str) -> bool:
    run_id = manifest.get("run_id")
    if isinstance(run_id, str) and run_id:
        expected = paths.pipeline_runs_root() / run_id
        try:
            if Path(out_dir).resolve() == expected.resolve():
                return True
        except OSError:
            pass
    manifest_path = manifest.get("path")
    if isinstance(manifest_path, Path):
        try:
            if Path(out_dir).resolve() == manifest_path.parent.resolve():
                return True
        except OSError:
            pass
    return False


def _latest_vbt_orchestrator_log(manifest: dict[str, Any] | None) -> Path | None:
    if manifest is not None:
        data = manifest.get("data") if isinstance(manifest.get("data"), dict) else {}
        out_dir = data.get("out_dir")
        if (
            isinstance(out_dir, str)
            and out_dir
            and _manifest_out_dir_allowed(manifest, out_dir)
        ):
            candidate = Path(out_dir) / "orchestrator.log"
            if candidate.is_file():
                return candidate
        run_id = manifest.get("run_id")
        if isinstance(run_id, str) and run_id:
            candidate = paths.pipeline_runs_root() / run_id / "orchestrator.log"
            if candidate.is_file():
                return candidate
        return None
    root = paths.pipeline_runs_root()
    if not root.is_dir():
        return None
    latest: tuple[float, Path] | None = None
    for log_path in root.glob("*/orchestrator.log"):
        if not log_path.is_file():
            continue
        try:
            mtime = log_path.stat().st_mtime
        except OSError:
            continue
        if latest is None or mtime > latest[0]:
            latest = (mtime, log_path)
    return latest[1] if latest else None


def _count_jsonl_lines(path: Path) -> int | None:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return sum(1 for line in fh if line.strip())
    except OSError:
        return None


def _vbt_host_label(workers: Any) -> tuple[str, str, str | None]:
    override = os.environ.get("HFT3_VBT_SWEEP_HOST", "").strip()
    if override:
        return "external", override, None
    if isinstance(workers, int) and not isinstance(workers, bool):
        if workers >= _VBT_RENTED_WORKER_THRESHOLD:
            return "rented", "vast-or-rented", None
        if workers >= 12:
            note = (
                f"Detected laptop-class VectorBT run (workers={workers}). "
                "Authoritative full rent should use Vast/rented host with high worker count."
            )
            return "local", "laptop", note
    return "unknown", "unknown", None


def _first_present(data: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in data and data.get(key) not in (None, ""):
            return data.get(key)
    return None


def _status_path_value(value: Any) -> Any:
    if not isinstance(value, str) or not value:
        return value
    path = Path(value)
    try:
        if path.is_absolute():
            if value.startswith("/"):
                stripped = value.replace("\\", "/")
                for prefix in ("/root/hft3/repo/", "/root/hft3/"):
                    if stripped.startswith(prefix):
                        return stripped[len(prefix):]
                return value
            return _rel(path)
        return path.as_posix()
    except (OSError, ValueError):
        return value


def _status_anomalies(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value if item not in (None, "")]
    if isinstance(value, str) and value:
        return [value]
    return []


def _status_count(source: dict[str, Any], *aliases: str) -> tuple[bool, int | None, bool]:
    value = _first_present(source, *aliases)
    if value is None:
        return False, None, False
    parsed = _as_int(value, -1)
    return parsed >= 0, parsed if parsed >= 0 else None, True


def _overlay_vbt_status_json(tracking: dict[str, Any]) -> dict[str, Any]:
    try:
        paths.VBT_FULL_STATUS.resolve().relative_to(paths.REPO.resolve())
    except (OSError, ValueError):
        return tracking
    status = paths.read_json(paths.VBT_FULL_STATUS)
    if not isinstance(status, dict):
        return tracking

    out = dict(tracking)
    progress = status.get("progress") if isinstance(status.get("progress"), dict) else {}
    source = {**progress, **status}
    int_fields = {
        "workers": ("workers", "worker_count", "workers_requested"),
        "expected_work_units": ("expected_work_units", "expected", "total_work_units", "total_units"),
        "completed_work_units": ("completed_work_units", "completed", "units_completed", "completed_units"),
        "collected_batches": ("collected_batches", "batches_collected"),
        "expected_batches": ("expected_batches", "total_batches"),
    }
    for field, aliases in int_fields.items():
        value = _first_present(source, *aliases)
        if value is not None:
            parsed = _as_int(value, -1)
            if parsed >= 0:
                out[field] = parsed

    for field, aliases in {
        "state": ("state", "status"),
        "run_id": ("run_id", "run"),
        "last_sync_utc": ("last_sync_utc", "last_sync", "synced_at_utc", "updated_at_utc"),
        "tmux_session": ("tmux_session", "tmux"),
        "ssh_host": ("ssh_host", "host"),
        "host_label": ("host_label", "host_name", "ssh_host"),
        "eta_utc": ("eta_utc", "estimated_completion_utc"),
    }.items():
        value = _first_present(source, *aliases)
        if value is not None:
            out[field] = value

    units_per_hour = _finite_float(_first_present(source, "units_per_hour", "rate_units_per_hour"))
    if units_per_hour is not None:
        out["units_per_hour"] = units_per_hour
    eta_seconds = _finite_float(_first_present(source, "eta_seconds", "remaining_seconds"))
    if eta_seconds is not None:
        out["eta_seconds"] = eta_seconds

    artifact_fields = {
        "log_artifact": ("log_artifact", "log_path"),
        "manifest_artifact": ("manifest_artifact", "manifest_path"),
        "artifact": ("artifact", "output_artifact", "output_path"),
    }
    for field, aliases in artifact_fields.items():
        value = _first_present(source, *aliases)
        if value is not None:
            out[field] = _status_path_value(value)

    anomalies: list[str] = []
    for anomaly in _status_anomalies(tracking.get("anomalies")) + _status_anomalies(source.get("anomalies")):
        if anomaly not in anomalies:
            anomalies.append(anomaly)
    failed_known, failed_value, failed_present = _status_count(
        source,
        "failed_work_units",
        "failed",
        "units_failed",
        "failed_units",
    )
    completed_known, completed_value, completed_present = _status_count(
        source,
        "completed_work_units",
        "completed",
        "units_completed",
        "completed_units",
    )
    skipped_known, skipped_value, skipped_present = _status_count(
        source,
        "skipped_work_units",
        "skipped",
        "units_skipped",
        "skipped_units",
    )
    for field, status_known, status_value in (
        ("failed_work_units", failed_known, failed_value),
        ("skipped_work_units", skipped_known, skipped_value),
        ("completed_work_units", completed_known, completed_value),
    ):
        tracking_value = tracking.get(field)
        tracking_known = tracking_value is not None
        counts: list[int] = []
        if tracking_known:
            tracking_count = _as_int(tracking_value, -1)
            if tracking_count >= 0:
                counts.append(tracking_count)
        if status_known and status_value is not None:
            counts.append(status_value)
        if counts:
            out[field] = max(counts)
        else:
            out.pop(field, None)

    failed = _as_int(out.get("failed_work_units"), 0)
    if failed > 0 and f"failed_work_units={failed}" not in anomalies:
        anomalies.append(f"failed_work_units={failed}")
    skipped = _as_int(out.get("skipped_work_units"), 0)
    if skipped > 0 and f"skipped_work_units={skipped}" not in anomalies:
        anomalies.append(f"skipped_work_units={skipped}")
    status_state = str(out.get("state") or "").lower()
    if status_state == "complete":
        expected = _as_int(out.get("expected_work_units"), -1)
        completed = _as_int(out.get("completed_work_units"), 0)
        for field, known, present in (
            ("failed_work_units", failed_known, failed_present),
            ("skipped_work_units", skipped_known, skipped_present),
        ):
            if known:
                continue
            reason = "unknown" if present else "missing"
            anomaly = f"status json state=complete but {field} {reason}"
            if anomaly not in anomalies:
                anomalies.append(anomaly)
        if expected <= 0:
            anomaly = "status json state=complete but expected_work_units missing or zero"
            if anomaly not in anomalies:
                anomalies.append(anomaly)
        elif failed_known and skipped_known:
            accounted = completed + failed + skipped
            anomaly = f"accounted_work_units={accounted} != expected_work_units={expected}"
            if accounted != expected and anomaly not in anomalies:
                anomalies.append(anomaly)
        clean_complete = (
            expected > 0
            and completed_known
            and failed_known
            and skipped_known
            and failed == 0
            and skipped == 0
            and completed + failed + skipped == expected
        )
        if not clean_complete or anomalies:
            out["state"] = "partial_failed" if failed > 0 or skipped > 0 else "stalled"
    out["anomalies"] = anomalies or None
    out["status_artifact"] = _rel(paths.VBT_FULL_STATUS)
    out["tracking_mode"] = "read_only_external_status_json"
    if out.get("ssh_host") and out.get("host_kind") in (None, "unknown"):
        out["host_kind"] = "external"
    return out


def _vectorbt_paid_screen_tracking() -> dict[str, Any]:
    declaration = paths.read_json(paths.VBT_FULL_RUN_DECLARATION)
    decl: dict[str, Any] = declaration if isinstance(declaration, dict) else {}
    manifest_entry = _latest_paid_screen_manifest()
    manifest = manifest_entry.get("data") if isinstance(manifest_entry, dict) else {}
    if not isinstance(manifest, dict):
        manifest = {}
    run_id = manifest_entry.get("run_id") if isinstance(manifest_entry, dict) else None
    has_manifest = isinstance(manifest_entry, dict)
    declaration_matched = bool(decl) and (
        not has_manifest
        or _declaration_matches_manifest(decl, run_id=run_id, manifest=manifest)
    )

    if declaration_matched:
        workers = _as_int(decl.get("workers_requested"), -1)
        if workers < 0 and manifest:
            workers = _as_int(manifest.get("workers"), -1)
        expected = _as_int(decl.get("expected_work_units"), -1)
        if expected < 0 and manifest:
            expected = _as_int(manifest.get("expected_work_units"), -1)
    elif manifest:
        workers = _as_int(manifest.get("workers"), -1)
        expected = _as_int(manifest.get("expected_work_units"), -1)
    else:
        workers = _as_int(decl.get("workers_requested"), -1)
        expected = _as_int(decl.get("expected_work_units"), -1)

    units_path = paths.VBT_FULL_UNITS_JSONL
    units_line_count = _count_jsonl_lines(units_path) if units_path.is_file() else None
    units_jsonl_scope = "declaration" if declaration_matched else "global"

    def _manifest_count_known(key: str) -> bool:
        return isinstance(manifest, dict) and key in manifest and _as_int(manifest.get(key), -1) >= 0

    completed_known = _manifest_count_known("completed_work_units")
    failed_known = _manifest_count_known("failed_work_units")
    skipped_known = _manifest_count_known("skipped_work_units")
    completed = _as_int(manifest.get("completed_work_units"), 0) if isinstance(manifest, dict) else 0
    failed = _as_int(manifest.get("failed_work_units"), 0) if isinstance(manifest, dict) else 0
    skipped = _as_int(manifest.get("skipped_work_units"), 0) if isinstance(manifest, dict) else 0

    log_path = _latest_vbt_orchestrator_log(manifest_entry)
    log_tail = ""
    log_completed = 0
    log_failed = 0
    log_drain_ok = 0
    log_drain_failed = 0
    log_accounting_known = False
    age_s: float | None = None
    if log_path is not None:
        log_text = _tail_log_text(log_path)
        lines = [line for line in log_text.splitlines() if line.strip()]
        log_tail = "\n".join(lines[-25:])
        for line in lines:
            drain_match = _RE_VBT_DRAIN_PROGRESS.search(line)
            if drain_match:
                log_accounting_known = True
                log_drain_ok += _as_int(drain_match.group(2), 0)
                log_drain_failed += _as_int(drain_match.group(3), 0)
                continue
            match = _RE_VBT_UNIT_PROGRESS.search(line)
            if not match:
                continue
            log_accounting_known = True
            status = match.group(2).upper()
            if status in {"OK", "OK_CACHED"}:
                log_completed += 1
            elif status == "ERROR":
                log_failed += 1
        try:
            age_s = max(0.0, time.time() - log_path.stat().st_mtime)
        except OSError:
            age_s = None
        manifest_status_lower = str(manifest.get("status") or "").lower() if isinstance(manifest, dict) else ""
        if completed <= 0 and isinstance(manifest, dict):
            manifest_completed = _as_int(manifest.get("completed_work_units"), 0)
            if manifest_completed > 0 and manifest_status_lower in {"running", "partial_failed", "aborted"}:
                completed = manifest_completed
            elif log_drain_ok > 0:
                completed = log_drain_ok
            elif log_completed > 0:
                completed = log_completed
        if failed <= 0:
            manifest_failed = _as_int(manifest.get("failed_work_units"), 0) if isinstance(manifest, dict) else 0
            if manifest_failed > 0 and manifest_status_lower in {"running", "partial_failed", "aborted"}:
                failed = manifest_failed
            elif log_drain_failed > 0:
                failed = log_drain_failed
            elif log_failed > 0:
                failed = log_failed
        completed_known = completed_known or log_accounting_known
        failed_known = failed_known or log_accounting_known

    accounted = completed + failed + skipped

    host_kind, host_label, host_note = _vbt_host_label(workers if workers >= 0 else None)
    state = "idle"
    log_stale = False
    manifest_status = str(manifest.get("status") or "").lower() if isinstance(manifest, dict) else ""
    reject_counts_known = failed_known and skipped_known
    accounting_complete = expected > 0 and reject_counts_known and accounted == expected
    log_complete_by_progress = log_path is not None and expected > 0 and completed >= expected
    if isinstance(manifest, dict) and manifest_status == "complete":
        state = "complete" if accounting_complete else "stalled"
    elif isinstance(manifest, dict) and manifest_status == "running":
        state = "running"
    elif log_path is not None:
        if accounting_complete and log_complete_by_progress:
            state = "complete"
        elif age_s is not None and age_s < 300:
            state = "running"
        elif completed > 0 or log_completed > 0 or log_drain_ok > 0:
            state = "stalled"
            log_stale = True
        else:
            state = "observed"
    elif decl or units_path.is_file():
        state = "declared"

    anomalies: list[str] = []
    if decl and has_manifest and not declaration_matched:
        decl_run_id = _declaration_run_id(decl)
        if decl_run_id and run_id:
            anomalies.append(
                f"declaration run_id={decl_run_id} != manifest run_id={run_id} (declaration ignored)"
            )
        else:
            anomalies.append(
                "declaration not matched to manifest run (declaration ignored for workers/expected/split)"
            )
    if (
        declaration_matched
        and expected > 0
        and units_line_count is not None
        and units_line_count != expected
    ):
        anomalies.append(
            f"declaration expected_work_units={expected} != units_jsonl_lines={units_line_count}"
        )
    if failed > 0:
        anomalies.append(f"failed_work_units={failed}")
    if skipped > 0:
        anomalies.append(f"skipped_work_units={skipped}")
    if manifest_status == "complete" and expected <= 0:
        anomalies.append("manifest status=complete but expected_work_units missing or zero")
    if manifest_status == "complete" or log_complete_by_progress:
        count_source = "manifest status=complete" if manifest_status == "complete" else "orchestrator log complete"
        if not failed_known:
            anomalies.append(f"{count_source} but failed_work_units missing")
        if not skipped_known:
            anomalies.append(f"{count_source} but skipped_work_units missing")
    if expected > 0 and accounted != expected:
        anomalies.append(
            f"accounted_work_units={accounted} != expected_work_units={expected}"
        )
    if log_stale:
        anomalies.append("orchestrator log stale (>300s without progress)")
    if host_kind == "local" and expected > 1000:
        anomalies.append("full-scope unit count on laptop-class worker topology")

    detail = host_note
    if host_kind in {"rented", "external"} and log_path and age_s is not None and age_s > 300:
        sync_note = "Mirror Vast orchestrator log/manifest to repo for cockpit tracking."
        detail = (detail + " " + sync_note if detail else sync_note)

    research_split: str | None = None
    if declaration_matched:
        research_split = decl.get("research_split")
        if not research_split and manifest:
            research_split = manifest.get("research_split")
    elif manifest:
        research_split = manifest.get("research_split")
    elif decl:
        research_split = decl.get("research_split")
    if not research_split:
        research_split = _VBT_DEFAULT_RESEARCH_SPLIT
    if research_split != _VBT_DEFAULT_RESEARCH_SPLIT:
        anomalies.append(f"noncanonical_research_split={research_split}")

    tracking = {
        "state": state,
        "host_kind": host_kind,
        "host_label": host_label,
        "workers": workers if workers >= 0 else None,
        "expected_work_units": expected if expected > 0 else None,
        "completed_work_units": completed if completed_known else None,
        "failed_work_units": failed if failed_known else None,
        "skipped_work_units": skipped if skipped_known else None,
        "units_jsonl_lines": units_line_count,
        "units_jsonl_scope": units_jsonl_scope,
        "research_split": research_split,
        "run_id": run_id,
        "declaration_matched": declaration_matched if decl else None,
        "declaration_artifact": _rel(paths.VBT_FULL_RUN_DECLARATION) if paths.VBT_FULL_RUN_DECLARATION.is_file() else None,
        "units_jsonl_artifact": _rel(units_path) if units_path.is_file() else None,
        "ready_gate_artifact": _rel(paths.VBT_READY_GATE) if paths.VBT_READY_GATE.is_file() else None,
        "manifest_artifact": (
            _rel(manifest_entry["path"])
            if isinstance(manifest_entry, dict) and manifest_entry.get("path") is not None
            else None
        ),
        "log_artifact": _rel(log_path) if log_path else None,
        "log_tail": log_tail[-2400:] if log_tail else None,
        "anomalies": anomalies or None,
        "detail": detail,
        "scope_doc": (
            _rel(paths.VBT_PAID_SCREEN_DOC)
            if paths.VBT_PAID_SCREEN_DOC.is_file()
            else "docs/project/VBT_PAID_SCREEN_UNIT_SCOPE.md"
        ),
        "tracking_mode": "read_only_external",
        "workflow_note": (
            "VectorBT on Vast is the broad cheap prefilter; "
            "HftBacktest realism is downstream on promoted outputs only."
        ),
    }
    return _overlay_vbt_status_json(tracking)


def _latency_evidence(*, defensive_ack_required: bool = False) -> dict:
    ack_dist = paths.read_json(paths.ORDER_ACK_DISTRIBUTION)
    latency_summary = paths.read_json(paths.LATENCY_SUMMARY)
    latency_truth = paths.read_json(paths.LATENCY_TRUTH)
    component_bands = _nested(latency_truth, "component_bands") if isinstance(latency_truth, dict) else None
    critical_measured = None
    if isinstance(component_bands, dict):
        critical_measured = all(
            (component_bands.get(name) or {}).get("measurement_status") == "MEASURED"
            for name in (
                "feed_latency_us",
                "new_send_to_exchange_us",
                "new_exchange_to_ack_us",
                "cancel_send_to_exchange_us",
                "cancel_exchange_to_ack_us",
            )
        )
    current_baseline = paths.read_json(paths.LATENCY_CURRENT_BASELINE)
    live_baseline = paths.read_json(paths.LATENCY_LIVE_BASELINE)
    latest_order = paths.read_json(paths.LATENCY_LATEST_ORDER_SUMMARY)

    ack_p99 = _finite_float(_nested(ack_dist, "percentiles", "p99"))
    if ack_p99 is None:
        ack_p99 = _finite_float(_nested(latency_summary, "native_probe_orders", "order_submit_to_ack_us", "p99_us"))
    live_ack_p99_ms = _finite_float(_nested(latency_summary, "live_order_ack_p99_ms"))
    new_send_block = latency_summary.get("new_send_to_ack_ms") if isinstance(latency_summary, dict) else None
    new_send_p99_ms = None
    if isinstance(new_send_block, dict):
        ms = new_send_block.get("ms")
        if isinstance(ms, dict):
            new_send_p99_ms = _finite_float(ms.get("p99_ms"))
    if live_ack_p99_ms is None and new_send_p99_ms is not None:
        live_ack_p99_ms = new_send_p99_ms
    if live_ack_p99_ms is not None:
        live_ack_p99_us = live_ack_p99_ms * 1000.0
    else:
        live_ack_p99_us = None
    offensive_engine = _finite_float(_nested(latency_truth, "compute", "tick_to_decision_ns"))
    if offensive_engine is not None:
        offensive_engine /= 1000.0
    baseline_tick_to_send = _finite_float(_nested(current_baseline, "metrics", "tick_to_send_us", "p99_us"))
    baseline_decision_to_send = _finite_float(_nested(current_baseline, "metrics", "decision_to_send_us", "p99_us"))
    live_tick_to_send = _finite_float(_nested(live_baseline, "metrics", "tick_to_send_us", "p99_us"))
    live_cancel_to_send = _finite_float(_nested(live_baseline, "metrics", "cancel_to_send_us", "p99_us"))
    latest_decision_to_send_p99 = _finite_float(_nested(latest_order, "metrics", "decision_to_send_us", "p99_us"))
    latest_decision_to_send_p50 = _finite_float(_nested(latest_order, "metrics", "decision_to_send_us", "p50_us"))
    defensive_cancel_to_send = live_cancel_to_send
    if defensive_cancel_to_send is None:
        defensive_cancel_to_send = _jsonl_metric(paths.LATENCY_DEFENSIVE_CANCEL_SAMPLE, "cancel_to_send_us")
    defensive_cancel_to_ack = _finite_float(_nested(live_baseline, "metrics", "cancel_to_ack_us", "p99_us"))
    if defensive_cancel_to_ack is None:
        defensive_cancel_to_ack = _jsonl_metric(paths.LATENCY_DEFENSIVE_CANCEL_SAMPLE, "cancel_to_ack_us")

    missing = []
    required = {
        "ack_p99_us": ack_p99,
        "offensive_engine_us": offensive_engine,
        "offensive_baseline_tick_to_send_us": baseline_tick_to_send,
        "offensive_baseline_decision_to_send_us": baseline_decision_to_send,
        "offensive_live_tick_to_send_us": live_tick_to_send,
        "offensive_latest_decision_to_send_p99_us": latest_decision_to_send_p99,
        "defensive_cancel_to_send_us": defensive_cancel_to_send,
    }
    for key, value in required.items():
        if value is None:
            missing.append(key)

    defensive_ack_status = "MEASURED" if defensive_cancel_to_ack is not None else "UNMEASURED"
    status = schemas.OK
    detail = "latency evidence observed; defensive ack not required for research sweep"
    if missing:
        status = schemas.STALE
        detail = "missing latency evidence: " + ", ".join(missing)
    elif defensive_ack_required and defensive_ack_status == "UNMEASURED":
        status = schemas.STALE
        detail = "defensive cancel ack required but unmeasured"

    return {
        "status": status,
        "detail": detail,
        "ack_p99_us": _round(ack_p99, 3),
        "live_ack_p99_us": _round(live_ack_p99_us, 3),
        "m6_band_ms": _round(ack_p99 / 1000.0 if ack_p99 is not None else None, 6),
        "live_m6_band_ms": _round(live_ack_p99_ms, 6),
        "offensive_engine_us": _round(offensive_engine, 3),
        "offensive_baseline_tick_to_send_us": _round(baseline_tick_to_send, 3),
        "offensive_baseline_decision_to_send_us": _round(baseline_decision_to_send, 3),
        "offensive_live_tick_to_send_us": _round(live_tick_to_send, 3),
        "offensive_latest_decision_to_send_p50_us": _round(latest_decision_to_send_p50, 3),
        "offensive_latest_decision_to_send_p99_us": _round(latest_decision_to_send_p99, 3),
        "defensive_cancel_to_send_us": _round(defensive_cancel_to_send, 3),
        "defensive_live_cancel_to_send_us": _round(live_cancel_to_send, 3),
        "defensive_cancel_ack_status": defensive_ack_status,
        "defensive_cancel_ack_required": defensive_ack_required,
        "hftbacktest_critical_bands_measured": critical_measured,
        "component_bands": _component_band_rows(component_bands),
        "live_placement": _live_placement_summary(latency_truth),
        "execution_realism": _execution_realism_flags(latency_truth),
        "live_readiness_status": schemas.STALE if defensive_ack_status == "UNMEASURED" else schemas.OK,
        "sources": {
            "ack_p99": str(paths.ORDER_ACK_DISTRIBUTION.relative_to(paths.REPO)),
            "offensive_engine": str(paths.LATENCY_TRUTH.relative_to(paths.REPO)),
            "offensive_baseline": str(paths.LATENCY_CURRENT_BASELINE.relative_to(paths.REPO)),
            "offensive_live": str(paths.LATENCY_LIVE_BASELINE.relative_to(paths.REPO)),
            "offensive_latest": str(paths.LATENCY_LATEST_ORDER_SUMMARY.relative_to(paths.REPO)),
            "defensive_cancel": str(paths.LATENCY_LIVE_BASELINE.relative_to(paths.REPO)),
            "live_placement_capability": str(paths.LATENCY_LIVE_PLACEMENT_CAPABILITY.relative_to(paths.REPO)),
        },
    }


def _pbo_max() -> Optional[float]:
    try:
        from model_metrics.config import load_metrics_config, thresholds_for

        thresholds = thresholds_for(load_metrics_config(paths.REPO))
    except Exception:
        return None
    try:
        if "maximum_pbo" not in thresholds:
            return None
        max_pbo = float(thresholds["maximum_pbo"])
    except (TypeError, ValueError):
        return None
    if not (math.isfinite(max_pbo) and 0.0 <= max_pbo < 0.5):
        return None
    return max_pbo


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _is_filled(value: Any) -> bool:
    if value is None:
        return False
    if value == "":
        return False
    if isinstance(value, (list, tuple, set, dict)) and not value:
        return False
    return True


def _parse_symbols(value: Any) -> set[str]:
    if not isinstance(value, str):
        return set()
    return {symbol.strip() for symbol in value.split(",") if symbol.strip()}


def _vectorbt_screen_stage() -> dict:
    tracking = _vectorbt_paid_screen_tracking()
    run_id = tracking.get("run_id")
    if isinstance(run_id, str) and run_id:
        screening = _latest_screening_fields(run_id=run_id)
    else:
        screening = {
            "screening_status": schemas.MISSING,
            "screening_artifact": None,
            "screening_promoted_count": None,
        }
    state = tracking.get("state") or "idle"
    anomalies = tracking.get("anomalies")
    screening_status = screening.get("screening_status")
    screening_detail = screening.get("screening_detail")
    if state == "complete" and screening_status == "pass" and not anomalies:
        status = schemas.OK
    elif state == "running":
        status = schemas.RUNNING
    elif state in {"declared", "observed"}:
        status = schemas.STALE
    elif state == "stalled" or anomalies:
        status = schemas.STALE
    elif state == "idle":
        status = schemas.MISSING
    elif state == "complete":
        status = schemas.MISSING if screening_status == schemas.MISSING else schemas.STALE
    else:
        status = schemas.UNKNOWN
    detail = tracking.get("detail")
    if screening_detail:
        detail = (detail + " " + screening_detail if detail else screening_detail)
    if anomalies and isinstance(anomalies, list):
        anomaly_text = "; ".join(anomalies)
        detail = (detail + " " + anomaly_text if detail else anomaly_text)
    return _stage(
        "vectorbt_screen",
        "VectorBT Screen (Vast)",
        status,
        detail=detail,
        tracking_state=state,
        workers=tracking.get("workers"),
        expected_work_units=tracking.get("expected_work_units"),
        completed_work_units=tracking.get("completed_work_units"),
        failed_work_units=tracking.get("failed_work_units"),
        skipped_work_units=tracking.get("skipped_work_units"),
        research_split=tracking.get("research_split"),
        run_id=tracking.get("run_id"),
        screening_status=screening_status,
        screening_detail=screening_detail,
        screening_promoted_count=screening.get("screening_promoted_count"),
        screening_artifact=screening.get("screening_artifact"),
        manifest_artifact=tracking.get("manifest_artifact"),
        declaration_artifact=tracking.get("declaration_artifact"),
        workflow_note=tracking.get("workflow_note"),
    )


def _scope_from_cli_args(data: dict) -> tuple[str, list[str], Optional[str]]:
    """Classify M6 universe run scope. ``from_stage_a`` requirement applies to
    historical M6 ``run_event_universe`` only — not VectorBT paid-screen units.
    """
    cli_args = data.get("cli_args")
    if not isinstance(cli_args, dict):
        return "unknown", [], "missing cli_args scope metadata"
    bounded = [key for key in _SMOKE_SCOPE_KEYS if _is_filled(cli_args.get(key))]
    if bounded:
        symbols = _parse_symbols(cli_args.get("symbols"))
        if symbols != _FULL_SYMBOL_SCOPE:
            bounded.append("symbols")
        return "smoke", bounded, None
    missing = [key for key in _FULL_SCOPE_KEYS if key not in cli_args]
    if missing:
        return "unknown", [], "missing cli_args keys: " + ", ".join(missing)
    if cli_args.get("lane") != "cme":
        return "unknown", [], "non-cme lane scope"
    band = _finite_float(cli_args.get("bands_override"))
    if band is None or abs(band - _M6_BAND_MS) > 1e-9:
        return "unknown", [], "non-canonical M6 band scope"
    if _as_int(cli_args.get("workers"), -1) != 12:
        return "unknown", [], "non-canonical worker scope"
    from_stage_a = str(cli_args.get("from_stage_a") or "").replace("\\", "/")
    if from_stage_a != _FULL_STAGE_A:
        return "unknown", [], "non-canonical Stage A survivor scope"
    events_csv = cli_args.get("events_csv")
    try:
        csv_path = Path(str(events_csv))
        if not csv_path.is_absolute():
            csv_path = paths.REPO / csv_path
        canonical_csv = (paths.REPO / "packages" / "data_system" / "config" / "events.csv").resolve()
        if csv_path.resolve() != canonical_csv:
            return "unknown", [], "non-canonical events_csv scope"
    except (OSError, ValueError, TypeError):
        return "unknown", [], "unreadable events_csv scope"
    symbols = _parse_symbols(cli_args.get("symbols"))
    if not symbols:
        return "unknown", [], "missing explicit symbol scope"
    if symbols != _FULL_SYMBOL_SCOPE:
        return "smoke", ["symbols"], None
    return "full", [], None


def _hypothesis_label(hypothesis: Any) -> Optional[str]:
    if not isinstance(hypothesis, dict):
        return None
    hyp_id = (
        hypothesis.get("hypothesis_id")
        or hypothesis.get("id")
        or hypothesis.get("model_id")
    )
    name = (
        hypothesis.get("hypothesis_name")
        or hypothesis.get("name")
        or hypothesis.get("model_name")
    )
    if hyp_id is not None and name:
        return f"{hyp_id}: {name}"
    if hyp_id is not None:
        return str(hyp_id)
    if name:
        return str(name)
    return None


def _evaluated_models(data: dict) -> tuple[int, list[str]]:
    unit_results = data.get("unit_results")
    if not isinstance(unit_results, list):
        return 0, []
    rows = 0
    models: list[str] = []
    seen: set[str] = set()
    for unit in unit_results:
        if not isinstance(unit, dict):
            continue
        hypotheses = unit.get("hypotheses")
        if unit.get("error") or unit.get("skip_reason") or not isinstance(hypotheses, list) or not hypotheses:
            continue
        rows += len(hypotheses)
        for hypothesis in hypotheses:
            label = _hypothesis_label(hypothesis)
            if label and label not in seen:
                seen.add(label)
                models.append(label)
    return rows, models


def _robustness_state(data: dict) -> tuple[str, Optional[str], Optional[float]]:
    robustness = data.get("robustness")
    if not isinstance(robustness, dict):
        return schemas.MISSING, "robustness block missing", None
    pbo_block = robustness.get("pbo")
    if not isinstance(pbo_block, dict):
        return schemas.STALE, "pbo robustness block missing", None
    reason = pbo_block.get("reason")
    reason_text = str(reason) if reason else None
    if reason_text and "insufficient" in reason_text.lower():
        return schemas.STALE, reason_text, None
    pbo = pbo_block.get("pbo")
    if isinstance(pbo, (int, float)) and not isinstance(pbo, bool):
        pbo_value = float(pbo)
        if not (math.isfinite(pbo_value) and 0.0 <= pbo_value <= 1.0):
            return schemas.STALE, f"pbo invalid: {pbo}", None
        max_pbo = _pbo_max()
        if (
            not isinstance(max_pbo, (int, float))
            or isinstance(max_pbo, bool)
            or not (math.isfinite(float(max_pbo)) and 0.0 <= float(max_pbo) < 0.5)
        ):
            return schemas.STALE, "maximum_pbo threshold invalid", None
        max_pbo = float(max_pbo)
        if pbo_value > max_pbo:
            return schemas.STALE, f"pbo {pbo_value} > maximum_pbo {max_pbo}", None
        n_configs = _as_int(pbo_block.get("n_configs"), -1)
        if n_configs < 2:
            return schemas.STALE, f"pbo n_configs insufficient: {n_configs} < 2", None
        n_partitions = _as_int(pbo_block.get("n_partitions"), -1)
        if n_partitions <= 0:
            return schemas.STALE, f"pbo n_partitions unavailable: {n_partitions}", None
        gate_status, gate_detail = _gauntlet_gate_state(data)
        if gate_status != schemas.OK:
            return gate_status, gate_detail, pbo_value
        return schemas.OK, f"pbo={pbo_value} <= maximum_pbo {max_pbo}; {gate_detail}", pbo_value
    if reason_text:
        return schemas.STALE, reason_text, None
    return schemas.STALE, "pbo unavailable", None


def _cell_base_slug(slug: str) -> str:
    parts = slug.split("_")
    try:
        event_idx = parts.index("band") + 2
    except ValueError:
        return slug
    return "_".join(parts[:event_idx]) if event_idx <= len(parts) else slug


def _passed_holm_slugs(data: dict) -> set[str]:
    corrections = data.get("corrections")
    if not isinstance(corrections, dict):
        return set()
    out: set[str] = set()
    for block in corrections.values():
        if not isinstance(block, dict):
            continue
        candidates = [block]
        for key in ("holm", "holm_010", "holm_005"):
            child = block.get(key)
            if isinstance(child, dict):
                candidates.append(child)
        for candidate in candidates:
            for key in ("passed_slugs", "passed", "survivors"):
                values = candidate.get(key)
                if isinstance(values, (list, tuple, set)):
                    out.update(str(value) for value in values)
    return out


def _robust_numeric(cell: dict, *keys: str) -> Optional[float]:
    for key in keys:
        value = cell.get(key)
        if isinstance(value, dict):
            nested = _finite_float(_nested(value, "value"))
            if nested is not None:
                return nested
        numeric = _finite_float(value)
        if numeric is not None:
            return numeric
    return None


def _robust_bool(cell: dict, *keys: str) -> Optional[bool]:
    for key in keys:
        if key not in cell:
            continue
        value = cell.get(key)
        if isinstance(value, bool):
            return value
        return None
    return None


def _dsr_pass_gate_reason(dsr_cell: dict) -> Optional[str]:
    pending = [dsr_cell]
    while pending:
        cell = pending.pop()
        if "dsr_pass" in cell and cell.get("dsr_pass") is not True:
            return f"dsr_pass {cell.get('dsr_pass')} is not True"
        for key in _DSR_EVIDENCE_KEYS:
            value = cell.get(key)
            if isinstance(value, dict):
                pending.append(value)
    return None


def _dsr_gate_reason(dsr_cell: dict) -> Optional[str]:
    pass_reason = _dsr_pass_gate_reason(dsr_cell)
    if pass_reason:
        return pass_reason

    has_dsr_cdf = "dsr_cdf" in dsr_cell
    if has_dsr_cdf:
        dsr_cdf = _robust_numeric(dsr_cell, "dsr_cdf")
        if dsr_cdf is None or dsr_cdf < 0.0 or dsr_cdf > 1.0:
            return f"dsr_cdf {dsr_cdf} outside [0.0, 1.0]"
        if dsr_cdf < _DSR_CDF_MIN:
            return f"dsr_cdf {dsr_cdf} < {_DSR_CDF_MIN}"
        return None

    dsr = _robust_numeric(dsr_cell, "dsr", "deflated_sharpe", "value")
    if dsr is None or dsr <= 0.0:
        if dsr_cell.get("dsr_pass") is True:
            return "dsr_pass True without dsr_cdf or signed dsr"
        return f"dsr {dsr} <= 0.0"
    return None


def _gauntlet_gate_state(data: dict) -> tuple[str, str]:
    robustness = data.get("robustness")
    if not isinstance(robustness, dict):
        return schemas.STALE, "robustness block missing"
    dsr_by_cell = robustness.get("dsr_by_cell")
    bootstrap_by_cell = robustness.get("bootstrap_by_cell")
    fee_stress_by_cell = robustness.get("fee_stress_by_cell")
    if not isinstance(dsr_by_cell, dict) or not dsr_by_cell:
        return schemas.STALE, "dsr_by_cell missing"
    if not isinstance(bootstrap_by_cell, dict) or not bootstrap_by_cell:
        return schemas.STALE, "bootstrap_by_cell missing"
    if not isinstance(fee_stress_by_cell, dict) or not fee_stress_by_cell:
        return schemas.STALE, "fee_stress_by_cell missing"
    holm_passed = _passed_holm_slugs(data)
    if not holm_passed:
        return schemas.STALE, "holm survivors missing"

    evaluated = 0
    passed = 0
    last_reasons: list[str] = []
    for slug, dsr_cell in dsr_by_cell.items():
        if not isinstance(slug, str) or not isinstance(dsr_cell, dict):
            continue
        evaluated += 1
        base_slug = _cell_base_slug(slug)
        reasons: list[str] = []
        if slug not in holm_passed and base_slug not in holm_passed:
            reasons.append("not holm survivor")
        dsr_reason = _dsr_gate_reason(dsr_cell)
        if dsr_reason:
            reasons.append(dsr_reason)
        boot_cell = bootstrap_by_cell.get(slug) or bootstrap_by_cell.get(base_slug)
        ci_lower = _robust_numeric(boot_cell, "ci_lower", "ci_lo_95", "lower", "ci_low") if isinstance(boot_cell, dict) else None
        if ci_lower is None or ci_lower <= 0.0:
            reasons.append(f"bootstrap ci_lower {ci_lower} <= 0.0")
        fee_cell = fee_stress_by_cell.get(slug) or fee_stress_by_cell.get(base_slug)
        fee_x2 = _robust_bool(fee_cell, "fee_x2_pass", "fee_2x_pass", "stress_pass", "passed") if isinstance(fee_cell, dict) else None
        if fee_x2 is not True:
            reasons.append("fee-x2 stress fail")
        if reasons:
            last_reasons = reasons
        else:
            passed += 1

    if evaluated <= 0:
        return schemas.STALE, "no gauntlet cells evaluated"
    if passed <= 0:
        return schemas.STALE, "gauntlet gates failed: " + ", ".join(last_reasons or ["no survivor"])
    return schemas.OK, f"gauntlet survivors={passed}/{evaluated}"


def _counts_from_skipped_rows(data: dict) -> tuple[dict[str, int], int]:
    counts: dict[str, int] = {}
    counted = 0
    skipped = data.get("skipped")
    if isinstance(skipped, list):
        for row in skipped:
            if not isinstance(row, dict):
                continue
            reason = str(row.get("reason") or "unspecified")
            counts[reason] = counts.get(reason, 0) + 1
            counted += 1
    return counts, counted


def _counts_from_runtime_skips(data: dict) -> dict[str, int]:
    counts: dict[str, int] = {}
    unit_results = data.get("unit_results")
    if isinstance(unit_results, list):
        for unit in unit_results:
            if not isinstance(unit, dict):
                continue
            reason = unit.get("skip_reason")
            if reason:
                reason_text = str(reason)
                counts[reason_text] = counts.get(reason_text, 0) + 1
    return counts


def _skip_reason_counts(data: dict) -> dict[str, int]:
    declared_counts = data.get("skip_reason_counts")
    if isinstance(declared_counts, dict):
        counts: dict[str, int] = {}
        valid = True
        for reason, count in declared_counts.items():
            if isinstance(count, bool) or type(count) is not int:
                valid = False
                break
            if count < 0:
                valid = False
                break
            counts[str(reason)] = counts.get(str(reason), 0) + count
        declared_skips = data.get("units_skipped")
        if declared_skips is not None and _as_int(declared_skips, -1) != sum(counts.values()):
            valid = False
        skipped_counts, skipped_rows = _counts_from_skipped_rows(data)
        if skipped_rows > 0 and skipped_counts != counts:
            valid = False
        if valid:
            return dict(sorted(counts.items()))
        return {_MALFORMED_SKIP_REASON_COUNTS: 1}

    counts, counted_from_skipped = _counts_from_skipped_rows(data)
    declared_skips = _as_int(data.get("units_skipped"))
    if declared_skips > counted_from_skipped:
        counts["unspecified"] = counts.get("unspecified", 0) + (declared_skips - counted_from_skipped)
    if not counts:
        counts.update(_counts_from_runtime_skips(data))
    return counts


def _q001_gap_reason_map() -> dict[tuple[str, str | None], str]:
    manifest = paths.read_json(paths.REPO / _Q001_MBO_PILOT_MANIFEST)
    if not isinstance(manifest, dict):
        return {}
    out: dict[tuple[str, str | None], str] = {}
    for event_id in manifest.get("no_market_windows") or []:
        out[(str(event_id), None)] = "no_market_data"
    for window in manifest.get("partial_windows") or []:
        if not isinstance(window, dict):
            continue
        event_id = str(window.get("event_id") or "")
        reason = str(window.get("reason") or "symbol_absent_in_raw_after_redownload")
        missing_symbols = window.get("missing_symbols") or []
        if not event_id or not isinstance(missing_symbols, list):
            continue
        for symbol in missing_symbols:
            out[(event_id, str(symbol))] = reason
    return out


def _q001_skip_rows_are_ledger_backed(data: dict) -> bool:
    q001_rows = []
    skipped = data.get("skipped")
    if not isinstance(skipped, list):
        return False
    for row in skipped:
        if isinstance(row, dict) and row.get("reason") in _Q001_ACCEPTED_SKIP_REASONS:
            q001_rows.append(row)
    if not q001_rows:
        return False
    gap_reasons = _q001_gap_reason_map()
    if not gap_reasons:
        return False
    for row in q001_rows:
        event_id = str(row.get("event_id") or "")
        symbol = str(row.get("symbol") or "")
        reason = str(row.get("reason") or "")
        if reason == "no_market_data":
            if gap_reasons.get((event_id, None)) != reason:
                return False
        elif gap_reasons.get((event_id, symbol)) != reason:
            return False
    return True


def _accepted_non_blocking_skip_reasons(data: dict, skip_counts: dict[str, int]) -> set[str]:
    accepted = {"embargo_2026"}
    if not any(reason in skip_counts for reason in _Q001_ACCEPTED_SKIP_REASONS):
        return accepted
    q001 = _q001_inventory()
    if q001.get("status") == schemas.OK and q001.get("available_data_scope_accepted") is True:
        if _q001_skip_rows_are_ledger_backed(data):
            accepted.update(_Q001_ACCEPTED_SKIP_REASONS)
    return accepted


def _blocking_skip_detail(data: dict, skip_counts: dict[str, int]) -> Optional[str]:
    non_blocking = _accepted_non_blocking_skip_reasons(data, skip_counts)
    blocking = {
        reason: count
        for reason, count in skip_counts.items()
        if count > 0 and reason not in non_blocking
    }
    if not blocking:
        return None
    parts = [f"{reason}={blocking[reason]}" for reason in sorted(blocking)]
    return "coverage skips: " + ", ".join(parts)


def _certification_state(data: dict) -> tuple[str, Optional[str], dict[str, Any]]:
    stamp = data.get("certification_stamp")
    if not isinstance(stamp, dict):
        return schemas.STALE, "certification_stamp missing", {}
    status = stamp.get("status")
    stale = stamp.get("stale")
    eligible = stamp.get("promotion_eligible")
    if status != "GREEN":
        return schemas.STALE, f"certification_stamp status={status}", stamp
    if stale is not False:
        return schemas.STALE, f"certification_stamp stale={stale}", stamp
    if eligible is not True:
        return schemas.STALE, f"certification_stamp promotion_eligible={eligible}", stamp
    return schemas.OK, None, stamp


def _universe_stage(id_: str, label: str, path, *, historical: bool = False) -> dict:
    data = paths.read_json(path)
    if not isinstance(data, dict):
        stage = _stage(id_, label, schemas.MISSING)
        if historical:
            stage["historical"] = True
        return stage
    mismatch = _active_run_artifact_mismatch(path, data)
    if mismatch:
        stage = _stage(id_, label, **mismatch)
        if historical:
            stage["historical"] = True
        return stage
    errored = _as_int(data.get("units_errored"))
    run = _as_int(data.get("units_run"))
    evaluated_rows, evaluated_models = _evaluated_models(data)
    scope, scope_detail, scope_issue = _scope_from_cli_args(data)
    robustness_status, robustness_detail, pbo = _robustness_state(data)
    skip_counts = _skip_reason_counts(data)
    blocking_skip_detail = _blocking_skip_detail(data, skip_counts)
    certification_status, certification_detail, stamp = _certification_state(data)

    detail = None
    data_status = data.get("status")
    has_abort_reason = "abort_reason" in data and data.get("abort_reason") is not None
    abort_reason = data.get("abort_reason") if has_abort_reason else None
    if str(data_status).upper() == "ABORTED_NO_PROGRESS" or has_abort_reason:
        status = schemas.FAIL
        detail = str(abort_reason or data_status or "abort_reason present")
    elif errored > 0:
        status = schemas.FAIL
        detail = f"{errored} unit(s) errored"
    elif run <= 0:
        status = schemas.MISSING
        detail = "no units run"
    elif evaluated_rows == 0:
        status = schemas.STALE
        detail = "no model hypotheses evaluated"
    elif scope == "smoke":
        status = schemas.STALE
        detail = "bounded/smoke scope: " + ", ".join(scope_detail)
    elif scope_issue:
        status = schemas.STALE
        detail = scope_issue
    elif blocking_skip_detail:
        status = schemas.STALE
        detail = blocking_skip_detail
    elif certification_status != schemas.OK:
        status = schemas.STALE
        detail = certification_detail
    elif robustness_status != schemas.OK:
        status = schemas.STALE
        detail = robustness_detail
    else:
        status = schemas.OK

    stage = _stage(
        id_, label, status,
        detail=detail,
        run_end=data.get("run_end_utc"),
        units_run=run,
        units_skipped=data.get("units_skipped", 0),
        units_errored=errored,
        evaluated_model_rows=evaluated_rows,
        evaluated_model_count=len(evaluated_models),
        scope=scope,
        skip_reason_counts=skip_counts,
        cert_status=stamp.get("status"),
        certification_stale=stamp.get("stale"),
        promotion_eligible=stamp.get("promotion_eligible"),
        promotion_label=stamp.get("promotion_label"),
        robustness_status=robustness_status,
        robustness_detail=robustness_detail,
        latency_bands_ms=data.get("latency_bands_ms"),
        artifact=str(path.relative_to(paths.REPO)),
    )
    if evaluated_models:
        stage["evaluated_models"] = evaluated_models
    if scope_detail:
        stage["scope_detail"] = scope_detail
    if pbo is not None:
        stage["pbo"] = pbo
    if historical:
        stage["historical"] = True
    return stage



_M6_LINE = re.compile(r"^.*\bM6\b.*$", re.MULTILINE)


def _gate_stage(latency_evidence: Optional[dict] = None) -> dict:
    base = _universe_stage(
        "m6_gate",
        "M6 Gate (historical M6)",
        _preferred_universe_path(paths.M6_RESULT),
        historical=True,
    )
    if isinstance(latency_evidence, dict):
        base["latency_evidence_status"] = latency_evidence.get("status")
        base["latency_live_readiness_status"] = latency_evidence.get("live_readiness_status")
        if latency_evidence.get("status") != schemas.OK and base.get("status") == schemas.OK:
            base["status"] = schemas.STALE
            base["detail"] = latency_evidence.get("detail") or "latency evidence unavailable"
    spec = paths.read_text(paths.ALPHA_CME_SPEC)
    if spec:
        m = _M6_LINE.search(spec)
        if m:
            base["spec_line"] = m.group(0).strip()[:240]
    return base


def _promote_stage() -> dict:
    tracking = _vectorbt_paid_screen_tracking()
    run_id = tracking.get("run_id")
    scoped_run = isinstance(run_id, str) and bool(run_id)
    promote_validation_detail: str | None = None
    validated_candidates = 0
    if scoped_run:
        screening_fields = _latest_screening_fields(run_id=run_id)
        vbt5 = {**screening_fields, **_latest_replay_fields(screening_fields)}
        promoted_count = vbt5.get("screening_promoted_count")
        candidate_source = "vectorbt_promoted"
        if isinstance(promoted_count, int) and not isinstance(promoted_count, bool):
            screening_promoted_count = promoted_count
        else:
            screening_promoted_count = 0
        validated_candidates = _validated_promoted_candidate_count(screening_fields)
        if screening_promoted_count > 0 and validated_candidates == screening_promoted_count:
            base_status = schemas.OK
        elif screening_promoted_count == 0:
            base_status = schemas.MISSING
        else:
            base_status = schemas.STALE
            promote_validation_detail = (
                f"validated_candidates={validated_candidates} "
                f"!= screening_promoted_count={screening_promoted_count}"
            )
        base_status, tracking_detail = _promote_scoped_tracking_status(base_status, tracking)
        if tracking_detail:
            promote_validation_detail = (
                tracking_detail
                if not promote_validation_detail
                else f"{promote_validation_detail}; {tracking_detail}"
            )
        candidates = validated_candidates
    else:
        vbt5 = _missing_vbt5_visibility_fields()
        candidate_source = "stage_a_survivors_historical"
        screening_promoted_count = None
        n = 0
        survivors = paths.read_json(paths.STAGE_A_SURVIVORS)
        survivor_mismatch = _survivor_artifact_mismatch(survivors)
        if survivor_mismatch:
            mismatch = {k: v for k, v in survivor_mismatch.items() if k != "status"}
            mismatch["candidates"] = 0
            mismatch["candidate_source"] = candidate_source
            mismatch["note"] = (
                "research-only; promotion gate is DSR/PBO/Holm/fee-stress gated; "
                "VectorBT promoted rows are the canonical HftBacktest handoff source"
            )
            return _stage(
                "promote",
                "Promote",
                schemas.STALE,
                **_missing_vbt5_visibility_fields(),
                **mismatch,
            )
        if isinstance(survivors, list):
            n = len(survivors)
        elif isinstance(survivors, dict):
            for k in ("survivors", "hypothesis_ids", "ids"):
                if isinstance(survivors.get(k), list):
                    n = len(survivors[k])
                    break
        candidates = n
        base_status = schemas.OK if n > 0 else schemas.MISSING
    status, vbt5_detail = _vbt5_promote_status(base_status, vbt5)
    if promote_validation_detail:
        vbt5_detail = (
            promote_validation_detail
            if not vbt5_detail
            else f"{promote_validation_detail}; {vbt5_detail}"
        )
    stage_kwargs: dict[str, Any] = {
        "candidates": candidates,
        "candidate_source": candidate_source,
        "note": (
            "research-only; promotion gate is DSR/PBO/Holm/fee-stress gated; "
            "HftBacktest handoff requires feature_recipe_hash equality from VectorBT promoted rows"
        ),
        "vbt5_evidence_detail": vbt5_detail,
        **vbt5,
    }
    if scoped_run:
        stage_kwargs["validated_candidates"] = validated_candidates
        stage_kwargs["screening_promoted_count"] = screening_promoted_count
    return _stage(
        "promote", "Promote", status,
        **stage_kwargs,
    )


def build() -> dict:
    latency_evidence = _latency_evidence()
    tracking = _vectorbt_paid_screen_tracking()
    run_id = tracking.get("run_id")
    scoped_run = isinstance(run_id, str) and bool(run_id)
    stages = [
        _capture_stage(),
        _feature_stage(),
        _vectorbt_screen_stage(),
        _stage_a_stage(),
        _q001_stage(),
        _universe_stage(
            "gauntlet_b",
            "Gauntlet B (historical M6)",
            _preferred_universe_path(paths.STAGE_B_RESULT),
            historical=True,
        ),
        _gate_stage(latency_evidence),
        _promote_stage(),
    ]
    health_stages = _pipeline_health_stages(stages, scoped_run=scoped_run)
    non_ok = [s for s in health_stages if s.get("status") != schemas.OK]
    if any(s["status"] == schemas.FAIL for s in health_stages):
        health = schemas.RED
    elif any(
        s["status"] in (schemas.STALE, schemas.MISSING, schemas.UNKNOWN, schemas.RUNNING)
        for s in health_stages
    ):
        health = schemas.AMBER
    else:
        health = schemas.GREEN
    return {
        "zone": "pipeline",
        "generated_utc": paths.now_iso(),
        "health": health,
        "latency_evidence": latency_evidence,
        "universe_sweep_tracking": _universe_sweep_tracking(),
        "vectorbt_paid_screen_tracking": _vectorbt_paid_screen_tracking(),
        "stages": stages,
    }
