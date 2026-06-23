"""Inventory and non-destructive sync for paid lane data.

The source repo may contain paid/downloaded files that are intentionally not
tracked by git. This tool copies those files into the current code authority's
ignored data lake and writes local audit reports under runtime/data_audits.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import shutil
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


# Default source root: prefer the HFT3_PAID_DATA_SOURCE env var; fall back to
# a sibling data/ directory next to the repo root, which matches the original
# layout on developer machines.  Override with --source-root at the CLI.
_REPO_ROOT = Path(__file__).resolve().parents[1]
for _p in [str(_REPO_ROOT), str(_REPO_ROOT / "packages")]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

from data_system.src.lake_manifest import manifest_path, resolve_npz_path  # noqa: E402

DEFAULT_SOURCE_ROOT = Path(
    os.environ.get("HFT3_PAID_DATA_SOURCE") or (_REPO_ROOT.parent / "hft3" / "data")
)
DATE_RE = re.compile(r"(20\d{2})[-_](\d{2})[-_](\d{2})")
SYMBOL_RE = re.compile(r"^(?P<symbol>[A-Z0-9]+(?:\.v\.0)?)_", re.IGNORECASE)
DEFAULT_DATA_DOCTOR_REPORT = _REPO_ROOT / "runtime" / "data_doctor_report.json"
DEFAULT_BACKFILL_SIDECAR = _REPO_ROOT / "docs" / "project" / "missing_data_backfill_sidecar.json"
DEFAULT_MBO_PILOT_MANIFEST = (
    _REPO_ROOT / "packages" / "data_system" / "config" / "mbo_pilot_basket_20260605_manifest.json"
)
DEFAULT_EVENTS_CSV = _REPO_ROOT / "packages" / "data_system" / "config" / "events.csv"
DEFAULT_CME_SYMBOLS = ("MES.v.0", "MNQ.v.0", "ES.v.0", "NQ.v.0", "ZN.v.0", "ZB.v.0", "RTY.v.0")
BACKFILL_SIDECAR_STATUS = "PLANNED_NON_BLOCKING_INTAKE"
BACKFILL_QUEUE_STATUSES = {"BACKLOG_AS_NEEDED", "BACKLOG_NON_BLOCKING"}
BACKFILL_QUEUE_LANES = {"CME_FUTURES", "CME_FUTURES_FEATURES", "CME_OPTIONS"}
BACKFILL_QUEUE_REQUIRED_STRINGS = ("id", "status", "lane", "missing_evidence", "model_treatment")
BACKFILL_QUEUE_REQUIRED_LISTS = (
    "source_layouts",
    "landing_zones",
    "tracking_surfaces",
    "preflight_commands",
    "staging_commands",
    "manual_steps_remaining",
)
BACKFILL_INTAKE_FLAGS = {
    "preflight_required": True,
    "destructive_actions_allowed": False,
    "raw_downloads_are_runnable_coverage": False,
    "completion_requires_catalog_or_data_doctor_evidence": True,
}
DATA_SCOPE_SECTIONS = (
    "cme_mbo",
    "vix_vvix",
    "vix_options",
    "cme_options",
    "macro",
    "cross_asset",
    "continuous_session",
    "latency_authority",
)


@dataclass(frozen=True)
class CopySpec:
    name: str
    source_rel: Path
    dest_rel: Path
    pattern: str


@dataclass(frozen=True)
class MalformedJson:
    error: str


COPY_SPECS = (
    CopySpec("cme_runnable_npz", Path("npz"), Path("npz"), "**/*_mbo.npz"),
    CopySpec("cme_replay_mbp10", Path("replay") / "mbp10", Path("replay") / "mbp10", "**/*.dbn.zst"),
    CopySpec("cme_raw_root_dbn", Path("."), Path("raw") / "databento_mbo", "*.dbn.zst"),
    CopySpec("equities_quarantine", Path("equities"), Path("equities"), "**/*"),
    CopySpec("options_quarantine", Path("options"), Path("options"), "**/*"),
    CopySpec("crypto_quarantine", Path("crypto"), Path("crypto"), "**/*"),
)
EVENT_PREFIXES = {"CPI", "NFP", "PPI", "FOMC", "PROP"}


def _date_from_name(path: Path) -> str | None:
    match = DATE_RE.search(path.name)
    if not match:
        return None
    return f"{match.group(1)}-{match.group(2)}-{match.group(3)}"


def _symbol_from_name(path: Path) -> str:
    match = SYMBOL_RE.match(path.name)
    if not match:
        return "unspecified"
    symbol = match.group("symbol").split(".")[0].upper()
    return "unspecified" if symbol in EVENT_PREFIXES else symbol


def _files(root: Path, pattern: str) -> list[Path]:
    if not root.is_dir():
        return []
    return sorted(path for path in root.glob(pattern) if path.is_file())


def _summarize_files(root: Path, files: Iterable[Path]) -> dict[str, Any]:
    file_list = list(files)
    dates = sorted({date for path in file_list for date in [_date_from_name(path)] if date})
    size_bytes = sum(path.stat().st_size for path in file_list)
    by_symbol: dict[str, set[str]] = {}
    for path in file_list:
        date = _date_from_name(path)
        if date is None:
            continue
        by_symbol.setdefault(_symbol_from_name(path), set()).add(date)
    return {
        "count": len(file_list),
        "size_bytes": size_bytes,
        "size_mb": round(size_bytes / 1024 / 1024, 3),
        "date_min": dates[0] if dates else "",
        "date_max": dates[-1] if dates else "",
        "date_count": len(dates),
        "dates_by_symbol": {symbol: sorted(values) for symbol, values in sorted(by_symbol.items())},
        "sample_paths": [str(path.relative_to(root)) for path in file_list[:10]],
    }


def inventory_data_root(data_root: Path) -> dict[str, Any]:
    categories: dict[str, Any] = {}
    for spec in COPY_SPECS:
        source_dir = data_root / spec.source_rel
        categories[spec.name] = _summarize_files(data_root, _files(source_dir, spec.pattern))

    npz_files = _files(data_root / "npz", "**/*_mbo.npz")
    raw_files = (
        _files(data_root / "replay" / "mbp10", "**/*.dbn.zst")
        + _files(data_root / "raw" / "databento_mbo", "**/*.dbn.zst")
        + _files(data_root, "*.dbn.zst")
    )
    runnable = _dates_by_symbol(npz_files)
    raw = _dates_by_symbol(raw_files)
    missing_conversion: dict[str, list[str]] = {}
    for symbol, raw_dates in raw.items():
        missing = set(raw_dates) - set(runnable.get(symbol, []))
        if missing:
            missing_conversion[symbol] = sorted(missing)

    return {
        "path": str(data_root),
        "exists": data_root.is_dir(),
        "categories": categories,
        "runnable_npz_days": {symbol: len(days) for symbol, days in runnable.items()},
        "raw_download_days": {symbol: len(days) for symbol, days in raw.items()},
        "missing_conversion_days": {symbol: len(days) for symbol, days in missing_conversion.items()},
        "missing_conversion_dates": missing_conversion,
        "official_coverage_status": "RUNNABLE_CME_NPZ_PRESENT" if npz_files else "NO_RUNNABLE_CME_NPZ",
    }


def _dates_by_symbol(files: Iterable[Path]) -> dict[str, list[str]]:
    out: dict[str, set[str]] = {}
    for path in files:
        date = _date_from_name(path)
        if date is None:
            continue
        out.setdefault(_symbol_from_name(path), set()).add(date)
    return {symbol: sorted(values) for symbol, values in sorted(out.items())}


def _load_json_file(path: Path) -> Any | None:
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return MalformedJson(str(exc))


def _event_type_from_id(event_id: str) -> str:
    parts = str(event_id).split("_")
    for idx, part in enumerate(parts):
        if len(part) == 4 and part.isdigit():
            return "_".join(parts[:idx]) or "UNKNOWN"
    return parts[0] if parts else "UNKNOWN"


def _date_from_event_id(event_id: str) -> str | None:
    parts = str(event_id).split("_")
    for idx in range(len(parts) - 2):
        y, m, d = parts[idx : idx + 3]
        if len(y) == 4 and y.isdigit() and len(m) == 2 and m.isdigit() and len(d) == 2 and d.isdigit():
            return f"{y}-{m}-{d}"
    return None


def _short_examples(values: list[str], limit: int = 5) -> list[str]:
    return values[:limit]


def _coerce_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _summarize_events_csv(events_csv: Path) -> dict[str, Any]:
    if not events_csv.is_file():
        return {
            "path": str(events_csv),
            "exists": False,
            "status": "MISSING",
            "symbols": [],
            "expected_canonical_symbols": list(DEFAULT_CME_SYMBOLS),
        }
    symbols: set[str] = set()
    try:
        with events_csv.open(newline="", encoding="utf-8") as fh:
            reader = csv.DictReader(fh)
            if "symbols" not in (reader.fieldnames or []):
                return {
                    "path": str(events_csv),
                    "exists": True,
                    "status": "MALFORMED",
                    "symbols": [],
                    "expected_canonical_symbols": list(DEFAULT_CME_SYMBOLS),
                    "detail": "missing symbols column",
                }
            for row in reader:
                raw = row.get("symbols") or ""
                for sym in raw.split(","):
                    sym = sym.strip()
                    if sym:
                        symbols.add(sym)
    except csv.Error as exc:
        return {
            "path": str(events_csv),
            "exists": True,
            "status": "MALFORMED",
            "symbols": [],
            "expected_canonical_symbols": list(DEFAULT_CME_SYMBOLS),
            "detail": str(exc),
        }
    status = "OK" if symbols else "EMPTY"
    return {
        "path": str(events_csv),
        "exists": True,
        "status": status,
        "symbols": sorted(symbols),
        "expected_canonical_symbols": list(DEFAULT_CME_SYMBOLS),
    }


def _summarize_active_npz_manifest(repo_root: Path, *, verify_hashes: bool = False) -> dict[str, Any]:
    path = manifest_path(repo_root)
    raw = _load_json_file(path)
    if raw is None:
        return {"path": str(path), "exists": False, "record_count": 0, "status": "MISSING"}
    if isinstance(raw, MalformedJson):
        return {
            "path": str(path),
            "exists": True,
            "record_count": 0,
            "status": "MALFORMED_JSON",
            "error": raw.error,
        }
    if not isinstance(raw, list):
        return {"path": str(path), "exists": True, "record_count": 0, "status": "MALFORMED"}

    symbols: dict[str, int] = {}
    event_types: dict[str, int] = {}
    dates: set[str] = set()
    malformed = 0
    missing_required_fields = 0
    missing_npz_files = 0
    invalid_event_count = 0
    invalid_sha256 = 0
    invalid_created_utc = 0
    examples: list[str] = []
    required_fields = ("event_id", "symbol", "npz_path", "event_count", "sha256", "created_utc")
    for row in raw:
        if not isinstance(row, dict):
            malformed += 1
            examples.append("non-object manifest row")
            continue
        event_id = str(row.get("event_id") or "")
        symbol = str(row.get("symbol") or "")
        if not event_id or not symbol:
            malformed += 1
            examples.append(f"missing event_id/symbol: {row!r}")
            continue
        symbols[symbol] = symbols.get(symbol, 0) + 1
        et = _event_type_from_id(event_id)
        event_types[et] = event_types.get(et, 0) + 1
        d = _date_from_event_id(event_id)
        if d:
            dates.add(d)
        missing = [field for field in required_fields if row.get(field) in (None, "")]
        if missing:
            missing_required_fields += 1
            examples.append(f"{symbol}_{event_id}: missing {','.join(missing)}")
        else:
            npz_path = resolve_npz_path(repo_root, str(row["npz_path"]))
            npz_exists = npz_path.is_file()
            if not npz_exists:
                missing_npz_files += 1
                examples.append(f"{symbol}_{event_id}: npz_path missing")
            try:
                count = int(row["event_count"])
            except (TypeError, ValueError):
                invalid_event_count += 1
                examples.append(f"{symbol}_{event_id}: invalid event_count")
            else:
                if count <= 0:
                    invalid_event_count += 1
                    examples.append(f"{symbol}_{event_id}: nonpositive event_count")
            sha = str(row["sha256"])
            if not re.fullmatch(r"[0-9a-fA-F]{64}", sha):
                invalid_sha256 += 1
                examples.append(f"{symbol}_{event_id}: invalid sha256")
            elif verify_hashes and npz_exists and _sha256_file(npz_path).lower() != sha.lower():
                invalid_sha256 += 1
                examples.append(f"{symbol}_{event_id}: sha256 mismatch")
            try:
                datetime.fromisoformat(str(row["created_utc"]).replace("Z", "+00:00"))
            except ValueError:
                invalid_created_utc += 1
                examples.append(f"{symbol}_{event_id}: invalid created_utc")
    sorted_dates = sorted(dates)
    validation_failures = (
        malformed
        + missing_required_fields
        + missing_npz_files
        + invalid_event_count
        + invalid_sha256
        + invalid_created_utc
    )
    return {
        "path": str(path),
        "exists": True,
        "status": "OK" if validation_failures == 0 else "FAIL_SCHEMA_OR_PATH_VALIDATION",
        "record_count": len(raw),
        "malformed_rows": malformed,
        "missing_required_field_rows": missing_required_fields,
        "missing_npz_files": missing_npz_files,
        "invalid_event_count_rows": invalid_event_count,
        "invalid_sha256_rows": invalid_sha256,
        "sha256_content_verified": verify_hashes,
        "sha256_validation_mode": "content_verified" if verify_hashes else "format_only",
        "invalid_created_utc_rows": invalid_created_utc,
        "validation_error_examples": _short_examples(examples),
        "symbols": dict(sorted(symbols.items())),
        "event_types": dict(sorted(event_types.items())),
        "date_min": sorted_dates[0] if sorted_dates else "",
        "date_max": sorted_dates[-1] if sorted_dates else "",
        "date_count": len(sorted_dates),
    }


def _summarize_mbo_pilot_manifest(path: Path) -> dict[str, Any]:
    raw = _load_json_file(path)
    if raw is None:
        return {"path": str(path), "exists": False, "status": "MISSING"}
    if isinstance(raw, MalformedJson):
        return {"path": str(path), "exists": True, "status": "MALFORMED_JSON", "error": raw.error}
    if not isinstance(raw, dict):
        return {"path": str(path), "exists": True, "status": "MALFORMED"}
    req = raw.get("databento_request") if isinstance(raw.get("databento_request"), dict) else {}
    cov = raw.get("coverage") if isinstance(raw.get("coverage"), dict) else {}
    expected = _coerce_int(cov.get("expected_event_symbol_slots"))
    present = _coerce_int(cov.get("present_runnable_npz_slots"))
    missing = _coerce_int(cov.get("missing_or_unavailable_slots"))
    coverage_pct = round(100.0 * present / expected, 4) if expected else None
    validation_errors: list[str] = []
    for field in ("dataset", "schema", "stype_in", "range_start_utc", "range_end_utc"):
        if not req.get(field):
            validation_errors.append(f"databento_request.{field}")
    if expected <= 0:
        validation_errors.append("coverage.expected_event_symbol_slots")
    if present < 0:
        validation_errors.append("coverage.present_runnable_npz_slots")
    if missing < 0:
        validation_errors.append("coverage.missing_or_unavailable_slots")
    if expected > 0 and present > expected:
        validation_errors.append("coverage.present_runnable_npz_slots_gt_expected")
    if expected > 0 and present + missing > expected:
        validation_errors.append("coverage.present_plus_missing_gt_expected")
    status = str(raw.get("status") or "UNKNOWN")
    if validation_errors:
        status = "MALFORMED_SCHEMA"
    return {
        "path": str(path),
        "exists": True,
        "status": status,
        "run_id": raw.get("run_id"),
        "validation_errors": validation_errors,
        "dataset": req.get("dataset"),
        "schema": req.get("schema"),
        "stype_in": req.get("stype_in"),
        "range_start_utc": req.get("range_start_utc"),
        "range_end_utc": req.get("range_end_utc"),
        "expected_event_symbol_slots": expected,
        "present_runnable_npz_slots": present,
        "missing_or_unavailable_slots": missing,
        "coverage_pct": coverage_pct,
        "windows_by_event_type": raw.get("windows_by_event_type") or {},
        "present_npz_slots_by_event_type": raw.get("present_npz_slots_by_event_type") or {},
        "missing_npz_slots_by_event_type": raw.get("missing_npz_slots_by_event_type") or {},
        "partial_window_count": len(raw.get("partial_windows") or []),
        "no_market_window_count": len(raw.get("no_market_windows") or []),
        "partial_windows": raw.get("partial_windows") or [],
    }


def _summarize_data_doctor(path: Path) -> dict[str, Any]:
    raw = _load_json_file(path)
    if raw is None:
        return {"path": str(path), "exists": False, "status": "MISSING", "checks": []}
    if isinstance(raw, MalformedJson):
        return {"path": str(path), "exists": True, "status": "MALFORMED_JSON", "checks": [], "error": raw.error}
    if not isinstance(raw, dict):
        return {"path": str(path), "exists": True, "status": "MALFORMED", "checks": []}
    checks = raw.get("checks") if isinstance(raw.get("checks"), list) else []
    statuses = {str(row.get("status")) for row in checks if isinstance(row, dict)}
    failed = _coerce_int(raw.get("failed"))
    warned = _coerce_int(raw.get("warned"))
    status = "FAIL" if failed > 0 or "FAIL" in statuses else ("WARN" if warned > 0 or "WARN" in statuses else "OK")
    return {
        "path": str(path),
        "exists": True,
        "status": status,
        "run_utc": raw.get("run_utc"),
        "failed": failed,
        "warned": warned,
        "warn_checks": [row for row in checks if isinstance(row, dict) and row.get("status") == "WARN"],
        "fail_checks": [row for row in checks if isinstance(row, dict) and row.get("status") == "FAIL"],
        "options_lane": raw.get("options_lane") if isinstance(raw.get("options_lane"), dict) else {},
    }


def _sidecar_error(
    path: Path,
    status: str,
    *,
    errors: list[str] | None = None,
    error: str | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "path": str(path),
        "exists": True,
        "status": status,
        "non_blocking": False,
        "queues": [],
        "queue_count": 0,
    }
    if errors:
        payload["validation_errors"] = errors
    if error:
        payload["error"] = error
    return payload


def _string_list(value: Any) -> list[str] | None:
    if not isinstance(value, list) or not value:
        return None
    out = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            return None
        out.append(item)
    return out


def _allowed_backfill_landing_zone(zone: str) -> bool:
    normalized = zone.replace("\\", "/")
    if re.search(r"\s", zone):
        return False
    if any(part == ".." for part in normalized.split("/")):
        return False
    if zone in {"HFT3_FEATURE_ROOT", "HFT3_NPZ_ROOT"}:
        return True
    if normalized.startswith(("<lake_root>/", "data/")):
        return True
    return bool(re.match(r"^C:\\hft3-lake\\", zone, flags=re.IGNORECASE))


def _summarize_backfill_sidecar(path: Path) -> dict[str, Any]:
    raw = _load_json_file(path)
    if raw is None:
        return {
            "path": str(path),
            "exists": False,
            "status": "MISSING",
            "non_blocking": False,
            "queues": [],
            "queue_count": 0,
        }
    if isinstance(raw, MalformedJson):
        return _sidecar_error(path, "MALFORMED_JSON", error=raw.error)
    if not isinstance(raw, dict):
        return _sidecar_error(path, "MALFORMED")
    validation_errors: list[str] = []
    if isinstance(raw.get("schema_version"), bool) or raw.get("schema_version") != 1:
        validation_errors.append("schema_version must equal 1")
    if raw.get("status") != BACKFILL_SIDECAR_STATUS:
        validation_errors.append(f"status must equal {BACKFILL_SIDECAR_STATUS}")
    if raw.get("non_blocking") is not True:
        validation_errors.append("non_blocking must be literal true")
    authority_sources = _string_list(raw.get("authority_sources"))
    if authority_sources is None:
        validation_errors.append("authority_sources must be a non-empty string list")
    intake = raw.get("intake_contract")
    if not isinstance(intake, dict):
        validation_errors.append("intake_contract must be an object")
    else:
        for key, expected in BACKFILL_INTAKE_FLAGS.items():
            if intake.get(key) is not expected:
                validation_errors.append(f"intake_contract.{key} must be {expected!r}")
        if (
            not isinstance(intake.get("refresh_command"), str)
            or not intake.get("refresh_command", "").strip()
        ):
            validation_errors.append("intake_contract.refresh_command must be a non-empty string")
    queues = raw.get("queues")
    if not isinstance(queues, list) or not queues:
        validation_errors.append("queues must be a non-empty list")
        queues = []
    normalized_queues: list[dict[str, Any]] = []
    for idx, queue in enumerate(queues):
        if not isinstance(queue, dict):
            validation_errors.append(f"queues[{idx}] must be an object")
            continue
        normalized: dict[str, Any] = {}
        for key in BACKFILL_QUEUE_REQUIRED_STRINGS:
            value = queue.get(key)
            if not isinstance(value, str) or not value.strip():
                validation_errors.append(f"queues[{idx}].{key} must be a non-empty string")
                value = ""
            normalized[key] = value
        if normalized["status"] and normalized["status"] not in BACKFILL_QUEUE_STATUSES:
            validation_errors.append(f"queues[{idx}].status is not allowed")
        if normalized["lane"] and normalized["lane"] not in BACKFILL_QUEUE_LANES:
            validation_errors.append(f"queues[{idx}].lane is not allowed")
        for key in BACKFILL_QUEUE_REQUIRED_LISTS:
            values = _string_list(queue.get(key))
            if values is None:
                validation_errors.append(f"queues[{idx}].{key} must be a non-empty string list")
                values = []
            normalized[key] = values
        for zone in normalized["landing_zones"]:
            if not _allowed_backfill_landing_zone(zone):
                validation_errors.append(f"queues[{idx}].landing_zones contains unsupported zone: {zone}")
        normalized_queues.append(
            {
                "id": normalized["id"],
                "status": normalized["status"] or "UNKNOWN",
                "lane": normalized["lane"],
                "landing_zones": normalized["landing_zones"],
                "tracking_surfaces": normalized["tracking_surfaces"],
                "model_treatment": normalized["model_treatment"],
                "manual_steps_remaining": normalized["manual_steps_remaining"],
            }
        )
    if validation_errors:
        payload = _sidecar_error(path, "MALFORMED_SCHEMA", errors=validation_errors)
        payload["schema_version"] = raw.get("schema_version")
        payload["updated_utc"] = raw.get("updated_utc")
        payload["queues"] = normalized_queues
        payload["queue_count"] = len(normalized_queues)
        return payload
    return {
        "path": str(path),
        "exists": True,
        "status": BACKFILL_SIDECAR_STATUS,
        "schema_version": raw.get("schema_version"),
        "updated_utc": raw.get("updated_utc"),
        "non_blocking": True,
        "queue_count": len(normalized_queues),
        "queues": normalized_queues,
    }


def _q001_status(gaps: list[dict[str, Any]]) -> str:
    if any(gap.get("severity") == "FAIL" for gap in gaps):
        return "BLOCKED"
    if any(gap.get("severity") == "WARN" for gap in gaps):
        return "INVENTORIED_WITH_WARNINGS"
    return "INVENTORIED"


def build_q001_cme_data_inventory(
    *,
    repo_root: Path,
    data_doctor_report: Path | None = None,
    mbo_pilot_manifest: Path | None = None,
    events_csv: Path | None = None,
    verify_hashes: bool = False,
) -> dict[str, Any]:
    data_doctor_path = data_doctor_report or repo_root / DEFAULT_DATA_DOCTOR_REPORT.relative_to(_REPO_ROOT)
    mbo_manifest_path = mbo_pilot_manifest or repo_root / DEFAULT_MBO_PILOT_MANIFEST.relative_to(_REPO_ROOT)
    events_path = events_csv or repo_root / DEFAULT_EVENTS_CSV.relative_to(_REPO_ROOT)
    active_npz = _summarize_active_npz_manifest(repo_root, verify_hashes=verify_hashes)
    mbo_pilot = _summarize_mbo_pilot_manifest(mbo_manifest_path)
    data_doctor = _summarize_data_doctor(data_doctor_path)
    events = _summarize_events_csv(events_path)

    gaps: list[dict[str, Any]] = []
    if active_npz["status"] != "OK":
        detail_parts = [str(active_npz["status"])]
        for key in (
            "malformed_rows",
            "missing_required_field_rows",
            "missing_npz_files",
            "invalid_event_count_rows",
            "invalid_sha256_rows",
            "invalid_created_utc_rows",
        ):
            if active_npz.get(key):
                detail_parts.append(f"{key}={active_npz[key]}")
        gaps.append({"source": "active_npz_manifest", "severity": "FAIL", "detail": "; ".join(detail_parts)})
    if not active_npz.get("sha256_content_verified") and active_npz.get("record_count", 0):
        gaps.append({
            "source": "active_npz_manifest",
            "severity": "FAIL",
            "detail": "sha256_content_not_verified; rerun with --verify-q001-hashes for full digest validation",
        })
    if not mbo_pilot.get("exists"):
        gaps.append({"source": "mbo_pilot_manifest", "severity": "WARN", "detail": "tracked MBO pilot manifest missing"})
    elif str(mbo_pilot.get("status", "")).startswith("MALFORMED"):
        errors = ",".join(mbo_pilot.get("validation_errors") or [])
        detail = str(mbo_pilot.get("status")) if not errors else f"{mbo_pilot.get('status')}: {errors}"
        gaps.append({"source": "mbo_pilot_manifest", "severity": "FAIL", "detail": detail})
    elif mbo_pilot.get("missing_or_unavailable_slots", 0):
        gaps.append({
            "source": "mbo_pilot_manifest",
            "severity": "WARN",
            "detail": f"missing_or_unavailable_slots={mbo_pilot.get('missing_or_unavailable_slots')}",
        })
    if data_doctor["status"] == "MISSING":
        gaps.append({"source": "data_doctor", "severity": "WARN", "detail": "runtime data doctor report missing"})
    elif data_doctor["status"] in {"MALFORMED", "MALFORMED_JSON"}:
        gaps.append({"source": "data_doctor", "severity": "FAIL", "detail": str(data_doctor["status"])})
    elif data_doctor["status"] == "FAIL":
        gaps.append({"source": "data_doctor", "severity": "FAIL", "detail": f"failed={data_doctor.get('failed', 0)}"})
    elif data_doctor["status"] == "WARN":
        gaps.append({"source": "data_doctor", "severity": "WARN", "detail": f"warned={data_doctor.get('warned', 0)}"})
    if events["status"] in {"MISSING", "EMPTY"}:
        gaps.append({"source": "events_csv", "severity": "WARN", "detail": str(events["status"])})
    elif events["status"] == "MALFORMED":
        gaps.append({"source": "events_csv", "severity": "FAIL", "detail": str(events.get("detail") or "MALFORMED")})

    return {
        "question_id": "Q001",
        "question": "What exact CME futures/options historical datasets are available for full universe research after the lane split?",
        "status": _q001_status(gaps),
        "scope": "read-only local inventory; not model execution and not promotion evidence",
        "cme_symbol_universe": events["symbols"],
        "expected_canonical_cme_symbols": events["expected_canonical_symbols"],
        "sources": {
            "active_npz_manifest": active_npz["path"],
            "mbo_pilot_manifest": mbo_pilot["path"],
            "data_doctor_report": data_doctor["path"],
            "events_csv": str(events_path),
        },
        "event_catalog": events,
        "futures": {
            "active_npz_manifest": active_npz,
            "mbo_pilot_basket": mbo_pilot,
        },
        "options": {
            "data_doctor_status": data_doctor["status"],
            "run_utc": data_doctor.get("run_utc"),
            "warn_checks": data_doctor.get("warn_checks", []),
            "fail_checks": data_doctor.get("fail_checks", []),
            "options_lane": data_doctor.get("options_lane", {}),
        },
        "gaps": gaps,
    }


def _copy_destination(source_data_root: Path, dest_data_root: Path, spec: CopySpec, source_file: Path) -> Path:
    if spec.source_rel == Path("."):
        return dest_data_root / spec.dest_rel / source_file.name
    return dest_data_root / spec.dest_rel / source_file.relative_to(source_data_root / spec.source_rel)


def sync_paid_data(source_data_root: Path, dest_data_root: Path, *, dry_run: bool = False) -> list[dict[str, Any]]:
    actions: list[dict[str, Any]] = []
    for spec in COPY_SPECS:
        source_dir = source_data_root / spec.source_rel
        for source_file in _files(source_dir, spec.pattern):
            dest_file = _copy_destination(source_data_root, dest_data_root, spec, source_file)
            action = "copied"
            reason = ""
            if dest_file.exists():
                if dest_file.stat().st_size == source_file.stat().st_size:
                    action = "skipped_existing_same_size"
                else:
                    action = "conflict_not_overwritten"
                    reason = "destination exists with different size"
            elif dry_run:
                action = "planned_copy"
            elif not dry_run:
                dest_file.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source_file, dest_file)
            actions.append(
                {
                    "category": spec.name,
                    "action": action,
                    "reason": reason,
                    "source": str(source_file),
                    "destination": str(dest_file),
                    "size_bytes": source_file.stat().st_size,
                }
            )
    return actions


def build_report(
    *,
    repo_root: Path,
    source_root: Path,
    sync: bool,
    dry_run: bool,
    verify_q001_hashes: bool = False,
) -> dict[str, Any]:
    data_root = repo_root / "data"
    before = inventory_data_root(data_root)
    source = inventory_data_root(source_root)
    synced_files = sync_paid_data(source_root, data_root, dry_run=dry_run) if sync else []
    after = inventory_data_root(data_root)
    q001 = build_q001_cme_data_inventory(repo_root=repo_root, verify_hashes=verify_q001_hashes)
    backfill_sidecar = _summarize_backfill_sidecar(repo_root / DEFAULT_BACKFILL_SIDECAR.relative_to(_REPO_ROOT))
    return {
        "schema_version": 1,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "repo_root": str(repo_root),
        "data_root_used": str(data_root),
        "source_root": str(source_root),
        "sync_requested": sync,
        "dry_run": dry_run,
        "verify_q001_hashes": verify_q001_hashes,
        "source_inventory": source,
        "destination_inventory_before": before,
        "destination_inventory_after": after,
        "synced_files": synced_files,
        "runnable_npz_days": after["runnable_npz_days"],
        "raw_download_days": after["raw_download_days"],
        "missing_conversion_days": after["missing_conversion_days"],
        "official_coverage_status": after["official_coverage_status"],
        "q001_cme_data_inventory": q001,
        "missing_data_backfill_sidecar": backfill_sidecar,
    }


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _source_file(path: Path | str | None) -> dict[str, Any]:
    value = Path(path) if path else Path("")
    return {"path": str(value), "exists": value.is_file()}


def _bounded_file_summary(root: Path, patterns: tuple[str, ...], *, limit: int = 1000) -> dict[str, Any]:
    if not root.is_dir():
        return {"path": str(root), "exists": False, "file_count": 0, "truncated": False, "sample_paths": []}
    seen: set[Path] = set()
    samples: list[str] = []
    for pattern in patterns:
        for path in root.glob(pattern):
            if not path.is_file() or path in seen:
                continue
            seen.add(path)
            if len(samples) < 10:
                samples.append(str(path.relative_to(root)))
            if len(seen) >= limit:
                return {
                    "path": str(root),
                    "exists": True,
                    "file_count": len(seen),
                    "truncated": True,
                    "sample_paths": samples,
                }
    return {
        "path": str(root),
        "exists": True,
        "file_count": len(seen),
        "truncated": False,
        "sample_paths": samples,
    }


def _lake_root_candidates(repo_root: Path, data_root: Path) -> list[Path]:
    candidates = [data_root, Path("C:/hft3-lake")]
    npz_root = os.environ.get("HFT3_NPZ_ROOT")
    if npz_root:
        candidates.insert(1, Path(npz_root).expanduser().resolve().parent)
    source_root = repo_root.parent / "hft3" / "data"
    if source_root != data_root:
        candidates.append(source_root)
    out: list[Path] = []
    seen: set[str] = set()
    for path in candidates:
        key = str(path).lower()
        if key not in seen:
            seen.add(key)
            out.append(path)
    return out


def _feature_root_candidates(lake_roots: list[Path]) -> list[Path]:
    candidates = [root / "features" for root in lake_roots]
    feature_root = os.environ.get("HFT3_FEATURE_ROOT")
    if feature_root:
        candidates.insert(0, Path(feature_root).expanduser().resolve())
    out: list[Path] = []
    seen: set[str] = set()
    for path in candidates:
        key = str(path).lower()
        if key not in seen:
            seen.add(key)
            out.append(path)
    return out


def _total_file_count(summaries: list[dict[str, Any]]) -> int:
    return sum(_coerce_int(summary.get("file_count")) for summary in summaries)


def _missing_line(label: str, value: Any) -> str | None:
    if value in (None, "", 0, "0", "unknown"):
        return None
    return f"{label}={value}"


def build_data_scope_manifest(report: dict[str, Any]) -> dict[str, Any]:
    repo_root = Path(str(report.get("repo_root") or _REPO_ROOT))
    data_root = Path(str(report.get("data_root_used") or repo_root / "data"))
    lake_roots = _lake_root_candidates(repo_root, data_root)
    feature_roots = _feature_root_candidates(lake_roots)

    q001 = _as_dict(report.get("q001_cme_data_inventory"))
    futures = _as_dict(q001.get("futures"))
    active = _as_dict(futures.get("active_npz_manifest"))
    pilot = _as_dict(futures.get("mbo_pilot_basket"))
    options = _as_dict(q001.get("options"))
    options_lane = _as_dict(options.get("options_lane"))
    fixing = _as_dict(options_lane.get("fixing_mbo"))
    expiry = _as_dict(options_lane.get("expiry_coverage"))
    events = _as_dict(q001.get("event_catalog"))
    sources = _as_dict(q001.get("sources"))
    sidecar = _as_dict(report.get("missing_data_backfill_sidecar"))

    sensor_sources = [_bounded_file_summary(root / "sensors", ("**/*.parquet", "**/*.npz", "**/*.json")) for root in lake_roots]
    vix_option_sources = [
        *[_bounded_file_summary(root / "VIX.OPT", ("**/*",)) for root in feature_roots],
        *[_bounded_file_summary(root / "vix_options", ("**/*",)) for root in lake_roots],
    ]
    latency_paths = [
        repo_root / "runtime" / "latency_reports" / "latency_summary.json",
        repo_root / "runtime" / "latency_reports" / "live_placement_capability.json",
        repo_root / "runtime" / "latency_reports" / "order_ack_distribution.json",
        repo_root / "reports" / "latency_baselines" / "current_baseline.json",
        repo_root / "reports" / "latency_baselines" / "live_r01_chicago_baseline.json",
    ]
    latency_sources = [_source_file(path) for path in latency_paths]
    latency_existing = [source for source in latency_sources if source["exists"]]

    cme_mbo_missing = []
    if active.get("status") != "OK":
        cme_mbo_missing.append(f"active_npz_manifest_status={active.get('status', 'UNKNOWN')}")
    for item in (
        _missing_line("missing_npz_files", active.get("missing_npz_files")),
        _missing_line("mbo_missing_or_unavailable_slots", pilot.get("missing_or_unavailable_slots")),
    ):
        if item:
            cme_mbo_missing.append(item)
    if pilot.get("status") and pilot.get("status") not in {"completed", "completed_with_gaps"}:
        cme_mbo_missing.append(f"mbo_pilot_status={pilot.get('status')}")
    if active.get("status") == "OK" and not pilot.get("missing_or_unavailable_slots"):
        cme_mbo_missing = ["none_observed_by_inventory"]
    cme_mbo_status = "blocked"
    if q001.get("status") in {"INVENTORIED", "INVENTORIED_WITH_WARNINGS"} and active.get("status") == "OK":
        cme_mbo_status = (
            "available_with_dependency_scoped_gaps"
            if pilot.get("missing_or_unavailable_slots")
            else "available"
        )
    elif active.get("status") in {"MISSING", "MALFORMED", "MALFORMED_JSON", "FAIL_SCHEMA_OR_PATH_VALIDATION"}:
        cme_mbo_status = "unavailable_or_invalid"

    sensor_count = _total_file_count(sensor_sources)
    vix_options_count = _total_file_count(vix_option_sources)
    strict_gaps = expiry.get("strict_mbo_gap_count")
    study_gaps = expiry.get("gap_count")
    options_status = str(options.get("data_doctor_status") or "UNKNOWN")
    cme_options_status = "unavailable_not_consumed"
    if _coerce_int(strict_gaps) or options_status in {"WARN", "FAIL"}:
        cme_options_status = "sidelined_missing_data"
    elif options_status == "OK":
        cme_options_status = "partial_not_consumed"
    macro_status = "available_not_consumed_as_context" if events.get("status") == "OK" else "unavailable_or_uncertain"
    cross_asset_symbol_count = len(active.get("symbols") or {})
    cross_asset_status = "partial_not_consumed" if cross_asset_symbol_count >= 2 else "unavailable_not_consumed"

    sections = {
        "cme_mbo": {
            "status": cme_mbo_status,
            "sources": {
                "active_npz_manifest": active.get("path") or sources.get("active_npz_manifest"),
                "mbo_pilot_manifest": pilot.get("path") or sources.get("mbo_pilot_manifest"),
                "events_csv": sources.get("events_csv") or events.get("path"),
            },
            "availability_summary": {
                "official_coverage_status": report.get("official_coverage_status"),
                "active_manifest_status": active.get("status", "UNKNOWN"),
                "active_manifest_records": active.get("record_count", 0),
                "date_min": active.get("date_min", ""),
                "date_max": active.get("date_max", ""),
                "mbo_pilot_present_slots": pilot.get("present_runnable_npz_slots", 0),
                "mbo_pilot_expected_slots": pilot.get("expected_event_symbol_slots", 0),
                "mbo_pilot_coverage_pct": pilot.get("coverage_pct"),
            },
            "missing_or_sidelined": cme_mbo_missing,
            "model_treatment": "Runnable rows may proceed as available-data research; missing MBO slots stay explicit skips or rejections and are not promotion evidence.",
            "dependency_scope": "Blocks only affected event-symbol cells and strict models requiring those exact MBO windows.",
        },
        "vix_vvix": {
            "status": "partial_not_consumed" if sensor_count else "unavailable_not_consumed",
            "sources": {
                "sensor_roots": sensor_sources,
                "feature_code": "packages/features_engine/src/features/vix_features.py",
                "feature_status_manifest": "docs/project/FEATURE_FAMILY_STATUS_MANIFEST.yaml",
            },
            "availability_summary": {
                "sensor_file_count_observed": sensor_count,
                "pit_feature_lake_certified_by_q001": False,
                "vectorbt_consumed": False,
            },
            "missing_or_sidelined": [
                "not_consumed_by_phase1_data_scope_manifest",
                "PIT VIX/VVIX feature rows are not certified by Q001 inventory",
                "missing VIX/VVIX inputs remain unknown and must never be zero-filled",
            ],
            "model_treatment": "Not consumed in Phase 1; target_plus_vix_vvix cells require PIT sensor provenance before use.",
            "dependency_scope": "Blocks only VIX/VVIX context claims; target-only and non-volatility context rows continue.",
        },
        "vix_options": {
            "status": "partial_not_consumed" if vix_options_count else "unavailable_not_consumed",
            "sources": {
                "vix_option_roots": vix_option_sources,
                "feature_status_manifest": "docs/project/FEATURE_FAMILY_STATUS_MANIFEST.yaml",
                "backfill_sidecar": sidecar.get("path"),
            },
            "availability_summary": {
                "local_file_count_observed": vix_options_count,
                "strike_expiry_depth": "partial_or_unmeasured",
                "vectorbt_consumed": False,
            },
            "missing_or_sidelined": [
                "not_consumed_by_phase1_data_scope_manifest",
                "limited strike/expiry depth unless a PIT option-chain feature manifest proves otherwise",
            ],
            "model_treatment": "Do not claim target_plus_vix_options coverage until PIT option-chain provenance and model consumption are proven.",
            "dependency_scope": "Blocks VIX-options context claims only; target-only, CME MBO, and unrelated context rows continue.",
        },
        "cme_options": {
            "status": cme_options_status,
            "sources": {
                "data_doctor_report": sources.get("data_doctor_report"),
                "options_lane": "packages/options_lane/",
                "backfill_sidecar": sidecar.get("path"),
            },
            "availability_summary": {
                "data_doctor_status": options.get("data_doctor_status", "UNKNOWN"),
                "strict_quote_dates": fixing.get("dates_covered", 0),
                "study_file_dates": fixing.get("study_dates_covered", 0),
                "trade_only_dates": fixing.get("trade_only_dates", 0),
                "study_gap_count": study_gaps,
                "strict_quote_gap_count": strict_gaps,
                "strict_quote_stale_gap_count": expiry.get("strict_mbo_stale_gap_count"),
            },
            "missing_or_sidelined": [
                item
                for item in (
                    _missing_line("study_gap_count", study_gaps),
                    _missing_line("strict_quote_gap_count", strict_gaps),
                    _missing_line("strict_quote_stale_gap_count", expiry.get("strict_mbo_stale_gap_count")),
                    "strict quote reconstruction and strict options context remain sidelined until quote proof exists",
                )
                if item
            ],
            "model_treatment": "Study rows can remain descriptive where explicitly labeled; strict CME options context is not consumed for futures promotion.",
            "dependency_scope": "Blocks strict options quote reconstruction, strict options features, options replay, and options promotion only.",
        },
        "macro": {
            "status": macro_status,
            "sources": {
                "events_csv": sources.get("events_csv") or events.get("path"),
                "macro_context_code": "packages/replay/context_assembly.py",
            },
            "availability_summary": {
                "event_catalog_status": events.get("status", "UNKNOWN"),
                "observed_symbol_count": len(events.get("symbols") or []),
                "expected_canonical_symbols": events.get("expected_canonical_symbols", []),
                "context_uplift_consumed": False,
            },
            "missing_or_sidelined": [
                "macro_context_uplift_not_consumed_by_phase1_data_scope_manifest",
                "ambiguous release/tradable timestamps must be sidelined before VBT",
            ],
            "model_treatment": "Scheduled-event target rows may use the event catalog; macro-context uplift needs separate PIT feature records and ablation.",
            "dependency_scope": "Blocks only macro-context rows for affected release/event combinations, not target-only scheduled-event rows.",
        },
        "cross_asset": {
            "status": cross_asset_status,
            "sources": {
                "active_npz_manifest": active.get("path") or sources.get("active_npz_manifest"),
                "cross_asset_code": [
                    "packages/replay/market_data_adapter.py",
                    "packages/replay/replay_session.py",
                ],
            },
            "availability_summary": {
                "active_manifest_status": active.get("status", "UNKNOWN"),
                "symbol_count": cross_asset_symbol_count,
                "symbols": sorted((active.get("symbols") or {}).keys()),
                "date_min": active.get("date_min", ""),
                "date_max": active.get("date_max", ""),
                "vectorbt_consumed": False,
            },
            "missing_or_sidelined": [
                "not_consumed_by_phase1_data_scope_manifest",
                "multi-symbol PIT alignment proof is required before target_plus_cross_asset claims",
            ],
            "model_treatment": "Available multi-symbol MBO is candidate context only; no cross-asset uplift is counted until PIT alignment and consumption are proven.",
            "dependency_scope": "Blocks cross-asset context claims for affected symbols/windows only; single-symbol target-only rows continue.",
        },
        "continuous_session": {
            "status": "sidelined_scope_not_consumed",
            "sources": {
                "research_clock": "continuous_intraday",
                "context_code": "packages/replay/context_assembly.py",
                "feature_status_manifest": "docs/project/FEATURE_FAMILY_STATUS_MANIFEST.yaml",
            },
            "availability_summary": {
                "scheduled_event_scope_default": True,
                "continuous_clock_consumed": False,
                "continuous_intraday_pit_proof": "not_measured",
            },
            "missing_or_sidelined": [
                "continuous_intraday_clock_out_of_scope_for_scheduled_screen",
                "full-session aggregates before session end are forbidden",
            ],
            "model_treatment": "Sidelined for scheduled-event Phase 1; enable only under a continuous_intraday clock with trailing/PIT session state.",
            "dependency_scope": "Blocks continuous-intraday and continuous/session context claims only.",
        },
        "latency_authority": {
            "status": "measured_not_consumed" if latency_existing else "unavailable_not_consumed",
            "sources": {
                "latency_files": latency_sources,
                "latency_architecture": "docs/workbench/LATENCY_ARCHITECTURE.md",
                "latency_ontology": "docs/vault/HFTBACKTEST_LATENCY_ONTOLOGY.md",
            },
            "availability_summary": {
                "measured_authority_files_observed": len(latency_existing),
                "existing_files": [source["path"] for source in latency_existing],
                "latency_as_vectorbt_feature_consumed": False,
            },
            "missing_or_sidelined": [
                "latency_state_not_consumed_by_phase1_data_scope_manifest",
                "production-realistic execution claims require measured CHI404/Rithmic authority",
            ],
            "model_treatment": "Latency may later parameterize HBT/replay with measured or explicitly synthetic authority; it is not a consumed VBT context feature here.",
            "dependency_scope": "Blocks latency-as-feature uplift and production-realism claims, not read-only target-only research.",
        },
    }

    return {
        "schema_version": 1,
        "manifest_type": "data_scope_manifest",
        "phase": "phase_1_data_scope_inventory",
        "generated_at_utc": report.get("generated_at_utc"),
        "read_only": True,
        "network_access": "not_used",
        "paid_download_commands": [],
        "source_report": {
            "repo_root": report.get("repo_root"),
            "data_root_used": report.get("data_root_used"),
            "source_root": report.get("source_root"),
            "sync_requested": report.get("sync_requested"),
            "dry_run": report.get("dry_run"),
            "verify_q001_hashes": report.get("verify_q001_hashes"),
            "q001_status": q001.get("status"),
        },
        "section_order": list(DATA_SCOPE_SECTIONS),
        "sections": sections,
    }


def write_data_scope_manifest(report: dict[str, Any], output_dir: Path) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest = build_data_scope_manifest(report)
    json_path = output_dir / "data_scope_manifest.json"
    md_path = output_dir / "data_scope_manifest.md"
    json_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    md_path.write_text(_markdown_data_scope_manifest(manifest), encoding="utf-8")
    return json_path, md_path


def write_reports(report: dict[str, Any], output_dir: Path) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "paid_data_inventory.json"
    md_path = output_dir / "paid_data_inventory.md"
    json_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    md_path.write_text(_markdown_report(report), encoding="utf-8")
    return json_path, md_path


def _markdown_report(report: dict[str, Any]) -> str:
    lines = [
        "# Paid Data Inventory",
        "",
        f"Generated UTC: {report['generated_at_utc']}",
        f"Data root used: `{report['data_root_used']}`",
        f"Source root: `{report['source_root']}`",
        f"Sync requested: `{report['sync_requested']}`",
        f"Dry run: `{report['dry_run']}`",
        f"Official coverage status: `{report['official_coverage_status']}`",
        "",
        "## Destination Runnable Coverage",
        "",
        "| Symbol | Runnable MBO NPZ days | Raw downloaded days | Missing conversion days |",
        "|---|---:|---:|---:|",
    ]
    runnable = report.get("runnable_npz_days") or {}
    raw = report.get("raw_download_days") or {}
    missing = report.get("missing_conversion_days") or {}
    for symbol in sorted(set(runnable) | set(raw) | set(missing)):
        lines.append(f"| {symbol} | {runnable.get(symbol, 0)} | {raw.get(symbol, 0)} | {missing.get(symbol, 0)} |")

    q001 = report.get("q001_cme_data_inventory")
    if isinstance(q001, dict):
        futures = q001.get("futures") if isinstance(q001.get("futures"), dict) else {}
        active = futures.get("active_npz_manifest") if isinstance(futures.get("active_npz_manifest"), dict) else {}
        pilot = futures.get("mbo_pilot_basket") if isinstance(futures.get("mbo_pilot_basket"), dict) else {}
        options = q001.get("options") if isinstance(q001.get("options"), dict) else {}
        events = q001.get("event_catalog") if isinstance(q001.get("event_catalog"), dict) else {}
        options_lane = options.get("options_lane") if isinstance(options.get("options_lane"), dict) else {}
        fixing = options_lane.get("fixing_mbo") if isinstance(options_lane.get("fixing_mbo"), dict) else {}
        expiry = options_lane.get("expiry_coverage") if isinstance(options_lane.get("expiry_coverage"), dict) else {}
        covered_elsewhere = expiry.get("covered_elsewhere")
        if not isinstance(covered_elsewhere, list):
            covered_elsewhere = []
        study_date_list = fixing.get("study_date_list")
        if isinstance(study_date_list, list):
            study_dates = {str(d) for d in study_date_list}
            covered_elsewhere_dates = {str(d) for d in covered_elsewhere}
            covered_elsewhere_net_new = len(covered_elsewhere_dates - study_dates)
            covered_elsewhere_overlap = len(covered_elsewhere_dates & study_dates)
        else:
            covered_elsewhere_net_new = "unknown"
            covered_elsewhere_overlap = "unknown"
        lines.extend(
            [
                "",
                "## Q001 CME Data Inventory",
                "",
                f"Status: `{q001.get('status', 'UNKNOWN')}`",
                f"Scope: {q001.get('scope', '')}",
                f"Event catalog: `{events.get('status', 'UNKNOWN')}`; observed_symbols={len(events.get('symbols', []))}",
                f"Active manifest SHA256 validation: `{active.get('sha256_validation_mode', 'UNKNOWN')}`",
                "",
                "| Surface | Status | Count / Range | Gaps |",
                "|---|---|---|---|",
                (
                    f"| Active NPZ manifest | {active.get('status', 'UNKNOWN')} | "
                    f"{active.get('record_count', 0)} records; {active.get('date_min', '')}..{active.get('date_max', '')} | "
                    f"malformed_rows={active.get('malformed_rows', 0)}; "
                    f"missing_required={active.get('missing_required_field_rows', 0)}; "
                    f"missing_files={active.get('missing_npz_files', 0)}; "
                    f"bad_event_count={active.get('invalid_event_count_rows', 0)}; "
                    f"bad_sha256={active.get('invalid_sha256_rows', 0)}; "
                    f"bad_created_utc={active.get('invalid_created_utc_rows', 0)} |"
                ),
                (
                    f"| MBO pilot basket | {pilot.get('status', 'UNKNOWN')} | "
                    f"{pilot.get('present_runnable_npz_slots', 0)}/{pilot.get('expected_event_symbol_slots', 0)} slots "
                    f"({pilot.get('coverage_pct', '')}%) | "
                    f"missing_or_unavailable={pilot.get('missing_or_unavailable_slots', 0)} |"
                ),
                (
                    f"| CME options fixing MBO | {options.get('data_doctor_status', 'UNKNOWN')} | "
                    f"strict_quote_dates={fixing.get('dates_covered', 0)}; "
                    f"study_file_dates={fixing.get('study_dates_covered', 0)}; "
                    f"coverage_dates={expiry.get('dates_covered', 'unknown')}/{expiry.get('expected_dates', 'unknown')}; "
                    f"trade_only_dates={fixing.get('trade_only_dates', 0)}; "
                    f"covered_elsewhere={len(covered_elsewhere)} "
                    f"(net_new={covered_elsewhere_net_new}; overlap={covered_elsewhere_overlap}); "
                    f"strict_quote_range={fixing.get('first_date', '')}..{fixing.get('last_date', '')} | "
                    f"study_gap_count={expiry.get('gap_count', 'unknown')}; "
                    f"strict_quote_gap_count={expiry.get('strict_mbo_gap_count', 'unknown')}; "
                    f"strict_quote_stale={expiry.get('strict_mbo_stale_gap_count', 'unknown')} |"
                ),
            ]
        )
        gaps = q001.get("gaps") if isinstance(q001.get("gaps"), list) else []
        if gaps:
            lines.extend(["", "### Q001 Gaps", ""])
            for gap in gaps:
                if isinstance(gap, dict):
                    lines.append(f"- `{gap.get('severity', 'INFO')}` {gap.get('source', '')}: {gap.get('detail', '')}")

    sidecar = report.get("missing_data_backfill_sidecar")
    if isinstance(sidecar, dict) and sidecar.get("exists"):
        lines.extend(
            [
                "",
                "## Missing Data Backfill Sidecar",
                "",
                f"Status: `{sidecar.get('status', 'UNKNOWN')}`",
                f"Non-blocking: `{sidecar.get('non_blocking', False)}`",
                f"Queue count: `{sidecar.get('queue_count', 0)}`",
                "",
                "| Queue | Status | Lane | Landing zones | Treatment |",
                "|---|---|---|---|---|",
            ]
        )
        errors = sidecar.get("validation_errors") if isinstance(sidecar.get("validation_errors"), list) else []
        if errors:
            lines.extend(["", "Validation errors:"])
            for error in errors:
                lines.append(f"- {error}")
            lines.append("")
        for queue in sidecar.get("queues") or []:
            if not isinstance(queue, dict):
                continue
            zones = ", ".join(str(zone) for zone in queue.get("landing_zones", []))
            lines.append(
                f"| {queue.get('id', '')} | {queue.get('status', '')} | {queue.get('lane', '')} | "
                f"{zones} | {queue.get('model_treatment', '')} |"
            )

    lines.extend(["", "## Source Categories", "", "| Category | Files | Size MB | Dates | Date range |", "|---|---:|---:|---:|---|"])
    for name, stats in (report.get("source_inventory", {}).get("categories") or {}).items():
        date_range = f"{stats.get('date_min', '')}..{stats.get('date_max', '')}".strip(".")
        lines.append(
            f"| {name} | {stats.get('count', 0)} | {stats.get('size_mb', 0)} | "
            f"{stats.get('date_count', 0)} | {date_range} |"
        )

    copied = sum(1 for row in report.get("synced_files", []) if row.get("action") == "copied")
    conflicts = sum(1 for row in report.get("synced_files", []) if row.get("action") == "conflict_not_overwritten")
    skipped = sum(1 for row in report.get("synced_files", []) if row.get("action") == "skipped_existing_same_size")
    lines.extend(
        [
            "",
            "## Sync Summary",
            "",
            f"- Copied: {copied}",
            f"- Skipped existing same size: {skipped}",
            f"- Conflicts not overwritten: {conflicts}",
            "",
            "Raw DBN/MBP10 files are downloaded backlog. They are not official runnable CME robustness coverage until converted to `data/npz/*_mbo.npz`.",
        ]
    )
    return "\n".join(lines) + "\n"


def _markdown_data_scope_manifest(manifest: dict[str, Any]) -> str:
    lines = [
        "# Data Scope Manifest",
        "",
        f"Generated UTC: {manifest.get('generated_at_utc')}",
        f"Phase: `{manifest.get('phase')}`",
        f"Read only: `{manifest.get('read_only')}`",
        f"Network access: `{manifest.get('network_access')}`",
        "",
        "| Section | Status | Dependency scope |",
        "|---|---|---|",
    ]
    sections = _as_dict(manifest.get("sections"))
    for name in manifest.get("section_order") or DATA_SCOPE_SECTIONS:
        section = _as_dict(sections.get(name))
        lines.append(f"| {name} | {section.get('status', 'UNKNOWN')} | {section.get('dependency_scope', '')} |")
    for name in manifest.get("section_order") or DATA_SCOPE_SECTIONS:
        section = _as_dict(sections.get(name))
        lines.extend(["", f"## {name}", "", f"Status: `{section.get('status', 'UNKNOWN')}`", ""])
        lines.append(f"Model treatment: {section.get('model_treatment', '')}")
        lines.append("")
        lines.append("Missing or sidelined:")
        for item in section.get("missing_or_sidelined") or []:
            lines.append(f"- {item}")
    return "\n".join(lines) + "\n"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument("--source-root", type=Path, default=DEFAULT_SOURCE_ROOT)
    parser.add_argument("--output-dir", type=Path, default=Path("runtime") / "data_audits")
    parser.add_argument("--sync", action="store_true", help="Copy paid files into the repo data root without deleting sources.")
    parser.add_argument("--dry-run", action="store_true", help="Report intended copy actions without copying.")
    parser.add_argument(
        "--data-scope-manifest",
        action="store_true",
        help="Also write Phase 1 data_scope_manifest.json/md from the inventory report.",
    )
    parser.add_argument(
        "--data-scope-manifest-only",
        action="store_true",
        help="Write only the read-only Phase 1 data_scope_manifest.json/md; forces no sync.",
    )
    parser.add_argument(
        "--verify-q001-hashes",
        action="store_true",
        help="Compute SHA256 for every active NPZ manifest row. Slow on the full external lake; default is fail-closed format-only reporting.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    repo_root = args.repo_root.resolve()
    source_root = args.source_root.resolve()
    output_dir = args.output_dir if args.output_dir.is_absolute() else repo_root / args.output_dir
    if args.data_scope_manifest_only and args.sync:
        raise SystemExit("--data-scope-manifest-only cannot be combined with --sync")
    if args.data_scope_manifest and args.sync:
        raise SystemExit("--data-scope-manifest cannot be combined with --sync")
    if not source_root.is_dir() and not args.data_scope_manifest_only:
        raise SystemExit(f"source root not found: {source_root}")
    report = build_report(
        repo_root=repo_root,
        source_root=source_root,
        sync=False if args.data_scope_manifest_only else args.sync,
        dry_run=True if args.data_scope_manifest_only else args.dry_run,
        verify_q001_hashes=args.verify_q001_hashes,
    )
    if not args.data_scope_manifest_only:
        json_path, md_path = write_reports(report, output_dir)
        print(f"wrote {json_path}")
        print(f"wrote {md_path}")
        print(f"official_coverage_status={report['official_coverage_status']}")
        print(f"synced_files={len(report['synced_files'])}")
    if args.data_scope_manifest or args.data_scope_manifest_only:
        manifest_json_path, manifest_md_path = write_data_scope_manifest(report, output_dir)
        print(f"wrote {manifest_json_path}")
        print(f"wrote {manifest_md_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
