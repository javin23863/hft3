"""Pipeline zone — start-to-end stage state machine.

Capture -> Feature Build -> Stage A -> Q001 Data Inventory -> Gauntlet B -> M6 Gate -> Promote.
Each stage is derived purely from the artifact it writes; a stage whose
artifact is absent renders as MISSING ("not yet run"), never an error.
"""
from __future__ import annotations

import json
import math
import re
from pathlib import Path
from typing import Any, Optional

from .. import loaders, paths, schemas
from .system import _q001_inventory


def _stage(id_: str, label: str, status: str, **extra) -> dict:
    return {"id": id_, "label": label, "status": status, **extra}


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
        return _stage("stage_a", "Stage A", schemas.MISSING)
    units_run = raw.get("units_run", 0) or 0
    errored = raw.get("units_errored", 0) or 0
    survivors = paths.read_json(paths.STAGE_A_SURVIVORS)
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
        "stage_a", "Stage A", status,
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
_ACTIVE_SWEEP_PLACEHOLDER_IDS = {"gauntlet_b", "m6_gate"}
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


def _active_cme_m6_sweep() -> bool:
    jobs_root = paths.REPO / "runtime" / "lifecycle" / "jobs"
    for state in ("pending", "running"):
        state_dir = jobs_root / state
        if not state_dir.is_dir():
            continue
        for job_path in state_dir.glob("*.json"):
            job = paths.read_json(job_path)
            if not isinstance(job, dict):
                continue
            if job.get("model_id") == "cme_m6_universe_sweep":
                return True
    return False


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


def _latency_evidence(*, defensive_ack_required: bool = False) -> dict:
    ack_dist = paths.read_json(paths.ORDER_ACK_DISTRIBUTION)
    latency_summary = paths.read_json(paths.LATENCY_SUMMARY)
    latency_truth = paths.read_json(paths.LATENCY_TRUTH)
    current_baseline = paths.read_json(paths.LATENCY_CURRENT_BASELINE)
    latest_order = paths.read_json(paths.LATENCY_LATEST_ORDER_SUMMARY)

    ack_p99 = _finite_float(_nested(ack_dist, "percentiles", "p99"))
    if ack_p99 is None:
        ack_p99 = _finite_float(_nested(latency_summary, "native_probe_orders", "order_submit_to_ack_us", "p99_us"))
    offensive_engine = _finite_float(_nested(latency_truth, "compute", "tick_to_decision_ns"))
    if offensive_engine is not None:
        offensive_engine /= 1000.0
    baseline_tick_to_send = _finite_float(_nested(current_baseline, "metrics", "tick_to_send_us", "p99_us"))
    baseline_decision_to_send = _finite_float(_nested(current_baseline, "metrics", "decision_to_send_us", "p99_us"))
    latest_decision_to_send_p99 = _finite_float(_nested(latest_order, "metrics", "decision_to_send_us", "p99_us"))
    latest_decision_to_send_p50 = _finite_float(_nested(latest_order, "metrics", "decision_to_send_us", "p50_us"))
    defensive_cancel_to_send = _jsonl_metric(paths.LATENCY_DEFENSIVE_CANCEL_SAMPLE, "cancel_to_send_us")
    defensive_cancel_to_ack = _jsonl_metric(paths.LATENCY_DEFENSIVE_CANCEL_SAMPLE, "cancel_to_ack_us")

    missing = []
    required = {
        "ack_p99_us": ack_p99,
        "offensive_engine_us": offensive_engine,
        "offensive_baseline_tick_to_send_us": baseline_tick_to_send,
        "offensive_baseline_decision_to_send_us": baseline_decision_to_send,
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
        "m6_band_ms": _round(ack_p99 / 1000.0 if ack_p99 is not None else None, 6),
        "offensive_engine_us": _round(offensive_engine, 3),
        "offensive_baseline_tick_to_send_us": _round(baseline_tick_to_send, 3),
        "offensive_baseline_decision_to_send_us": _round(baseline_decision_to_send, 3),
        "offensive_latest_decision_to_send_p50_us": _round(latest_decision_to_send_p50, 3),
        "offensive_latest_decision_to_send_p99_us": _round(latest_decision_to_send_p99, 3),
        "defensive_cancel_to_send_us": _round(defensive_cancel_to_send, 3),
        "defensive_cancel_ack_status": defensive_ack_status,
        "defensive_cancel_ack_required": defensive_ack_required,
        "live_readiness_status": schemas.STALE if defensive_ack_status == "UNMEASURED" else schemas.OK,
        "sources": {
            "ack_p99": str(paths.ORDER_ACK_DISTRIBUTION.relative_to(paths.REPO)),
            "offensive_engine": str(paths.LATENCY_TRUTH.relative_to(paths.REPO)),
            "offensive_baseline": str(paths.LATENCY_CURRENT_BASELINE.relative_to(paths.REPO)),
            "offensive_latest": str(paths.LATENCY_LATEST_ORDER_SUMMARY.relative_to(paths.REPO)),
            "defensive_cancel": str(paths.LATENCY_DEFENSIVE_CANCEL_SAMPLE.relative_to(paths.REPO)),
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
    if not (math.isfinite(max_pbo) and 0.0 <= max_pbo <= 1.0):
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


def _scope_from_cli_args(data: dict) -> tuple[str, list[str], Optional[str]]:
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
            or not (math.isfinite(float(max_pbo)) and 0.0 <= float(max_pbo) <= 1.0)
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
        dsr_bool = _robust_bool(dsr_cell, "dsr_pass")
        dsr = _robust_numeric(dsr_cell, "dsr", "deflated_sharpe", "dsr_cdf", "value")
        if dsr_bool is False or (dsr_bool is None and (dsr is None or dsr <= 0.0)):
            reasons.append(f"dsr {dsr} <= 0.0")
        boot_cell = bootstrap_by_cell.get(slug) or bootstrap_by_cell.get(base_slug)
        ci_lower = _robust_numeric(boot_cell, "ci_lower", "ci_lo_95", "lower", "ci_low") if isinstance(boot_cell, dict) else None
        if ci_lower is None or ci_lower <= 0.0:
            reasons.append(f"bootstrap ci_lower {ci_lower} <= 0.0")
        fee_cell = fee_stress_by_cell.get(slug) or fee_stress_by_cell.get(base_slug)
        fee_x2 = _robust_bool(fee_cell, "fee_x2_pass", "fee_2x_pass", "passed") if isinstance(fee_cell, dict) else None
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


def _universe_stage(id_: str, label: str, path) -> dict:
    data = paths.read_json(path)
    if not isinstance(data, dict):
        return _stage(id_, label, schemas.MISSING)
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
    return stage



_M6_LINE = re.compile(r"^.*\bM6\b.*$", re.MULTILINE)


def _gate_stage(latency_evidence: Optional[dict] = None) -> dict:
    base = _universe_stage("m6_gate", "M6 Gate", _preferred_universe_path(paths.M6_RESULT))
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
    survivors = paths.read_json(paths.STAGE_A_SURVIVORS)
    n = 0
    if isinstance(survivors, list):
        n = len(survivors)
    elif isinstance(survivors, dict):
        for k in ("survivors", "hypothesis_ids", "ids"):
            if isinstance(survivors.get(k), list):
                n = len(survivors[k])
                break
    status = schemas.OK if n > 0 else schemas.MISSING
    return _stage(
        "promote", "Promote", status,
        candidates=n,
        note="research-only; promotion gate is DSR/PBO/Holm/fee-stress gated",
    )


def build() -> dict:
    latency_evidence = _latency_evidence()
    stages = [
        _capture_stage(),
        _feature_stage(),
        _stage_a_stage(),
        _q001_stage(),
        _universe_stage("gauntlet_b", "Gauntlet B", _preferred_universe_path(paths.STAGE_B_RESULT)),
        _gate_stage(latency_evidence),
        _promote_stage(),
    ]
    non_ok = [s for s in stages if s.get("status") != schemas.OK]
    if any(s["status"] == schemas.FAIL for s in stages):
        health = schemas.RED
    elif (
        _active_cme_m6_sweep()
        and non_ok
        and all(s.get("id") in _ACTIVE_SWEEP_PLACEHOLDER_IDS for s in non_ok)
    ):
        health = schemas.GREEN
    elif any(s["status"] in (schemas.STALE, schemas.MISSING, schemas.UNKNOWN) for s in stages):
        health = schemas.AMBER
    else:
        health = schemas.GREEN
    return {
        "zone": "pipeline",
        "generated_utc": paths.now_iso(),
        "health": health,
        "latency_evidence": latency_evidence,
        "stages": stages,
    }
