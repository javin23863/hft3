"""Pipeline zone — start-to-end stage state machine.

Capture -> Feature Build -> Stage A -> Gauntlet B -> M6 Gate -> Promote.
Each stage is derived purely from the artifact it writes; a stage whose
artifact is absent renders as MISSING ("not yet run"), never an error.
"""
from __future__ import annotations

import re
from typing import Optional

from .. import loaders, paths, schemas


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


def _universe_stage(id_: str, label: str, path) -> dict:
    data = paths.read_json(path)
    if not isinstance(data, dict):
        return _stage(id_, label, schemas.MISSING)
    errored = data.get("units_errored", 0) or 0
    run = data.get("units_run", 0) or 0
    status = schemas.OK if run > 0 and errored == 0 else (
        schemas.FAIL if errored > 0 else schemas.MISSING)
    return _stage(
        id_, label, status,
        run_end=data.get("run_end_utc"),
        units_run=run,
        units_skipped=data.get("units_skipped", 0),
        units_errored=errored,
        latency_bands_ms=data.get("latency_bands_ms"),
        artifact=str(path.relative_to(paths.REPO)),
    )


_M6_LINE = re.compile(r"^.*\bM6\b.*$", re.MULTILINE)


def _gate_stage() -> dict:
    base = _universe_stage("m6_gate", "M6 Gate", paths.M6_RESULT)
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
    stages = [
        _capture_stage(),
        _feature_stage(),
        _stage_a_stage(),
        _universe_stage("gauntlet_b", "Gauntlet B", paths.STAGE_B_RESULT),
        _gate_stage(),
        _promote_stage(),
    ]
    if any(s["status"] == schemas.FAIL for s in stages):
        health = schemas.RED
    elif any(s["status"] in (schemas.STALE, schemas.MISSING) for s in stages):
        health = schemas.AMBER
    else:
        health = schemas.GREEN
    return {
        "zone": "pipeline",
        "generated_utc": paths.now_iso(),
        "health": health,
        "stages": stages,
    }
