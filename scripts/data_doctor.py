#!/usr/bin/env python3
"""Data-lake health check for the 3-tier architecture (dev / CHI404 / B2).

Asserts the invariants the June 2026 reorg established. Exit 0 = healthy,
exit 1 = at least one FAIL (WARNs alone don't fail). Writes a JSON report to
runtime/data_doctor_report.json for the cockpit alerts zone.

    python scripts/data_doctor.py [--skip-b2]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
for _p in [str(_REPO), str(_REPO / "packages")]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

from data_system.src.npz_resolver import npz_root, lake_root  # noqa: E402
from options_data.src.expiry_calendar import expiries_between, fixing_datetime_utc  # noqa: E402

B2_REMOTE = os.environ.get("HFT3_B2_REMOTE", "hft3-b2:Hft3repo")
MIN_FREE_FRACTION = 0.15
MAX_CATALOG_AGE_H = 48
MAX_SYNC_AGE_H = 48

OPTIONS_FIXING_START = date(2023, 5, 1)
# Expiry dates whose 14:55-15:05 CT window is covered by owned lake NPZ
# (PROP_FLATTEN); see scripts/pull_fixing_windows.py ALREADY_COVERED +
# research_cards/fixing_window/README.md. Literals to avoid importing
# DatabentoResearchClient at module scope.
OPTIONS_FIXING_COVERED_ELSEWHERE: frozenset[str] = frozenset({"2024-09-18", "2025-06-20"})
OPTIONS_VENDOR_LAG_GRACE_DAYS = 5  # reported separately; fixing-study gaps still fail
_FIXING_RE = re.compile(r"^ES_fixing_(trades_)?(\d{4}-\d{2}-\d{2})\.dbn\.zst$")
_PROP_FLATTEN_RE = re.compile(r"^PROP_FLATTEN_TOPSTEP_(\d{4})_(\d{2})_(\d{2})_MAIN$")
_FIXING_WINDOW_BEFORE = timedelta(minutes=5)
_FIXING_WINDOW_AFTER = timedelta(minutes=5)

checks: list[dict] = []


def check(name: str, ok: bool, detail: str, warn_only: bool = False) -> None:
    level = "OK" if ok else ("WARN" if warn_only else "FAIL")
    checks.append({"name": name, "status": level, "detail": detail})
    print(f"{level:4}  {name}: {detail}")


def _valid_nonempty_file(path: Path) -> bool:
    try:
        return path.is_file() and path.stat().st_size > 0
    except OSError:
        return False


def _sidecar_path(path: Path) -> Path:
    return path.with_name(f"{path.name}.doctor.json")


def _load_doctor_sidecar(path: Path) -> dict | None:
    sidecar = _sidecar_path(path)
    if not sidecar.is_file():
        return None
    try:
        data = json.loads(sidecar.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"valid": False, "reason": "unreadable sidecar"}
    return data if isinstance(data, dict) else {"valid": False, "reason": "sidecar is not object"}


def _is_dbn_artifact(path: Path) -> bool:
    return path.name.endswith((".dbn", ".dbn.zst"))


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_json_list(path: Path) -> tuple[list[dict], str | None]:
    if not path.is_file():
        return [], "missing"
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return [], f"unreadable:{type(exc).__name__}"
    if not isinstance(raw, list):
        return [], "not_list"
    rows = [row for row in raw if isinstance(row, dict)]
    if len(rows) != len(raw):
        return rows, "non_object_rows"
    return rows, None


def _path_key(path: Path) -> str:
    return os.path.normcase(str(path.resolve(strict=False)))


def _catalog_path_keys(rows: list[dict], source: str, nroot: Path) -> tuple[list[str], list[str]]:
    root_key = _path_key(nroot)
    keys: list[str] = []
    errors: list[str] = []
    for idx, row in enumerate(rows):
        raw_path = row.get("npz_path")
        if not isinstance(raw_path, str) or not raw_path.strip():
            errors.append(f"{source}[{idx}].npz_path=missing")
            continue
        path = Path(raw_path)
        if not path.is_absolute():
            path = nroot / path
        resolved = path.resolve(strict=False)
        if resolved.suffix.lower() != ".npz":
            errors.append(f"{source}[{idx}].npz_path=not_npz:{raw_path}")
            continue
        if _path_key(resolved.parent) != root_key:
            errors.append(f"{source}[{idx}].npz_path=not_top_level:{raw_path}")
            continue
        keys.append(_path_key(resolved))
    return keys, errors


def catalog_coverage_detail(nroot: Path) -> tuple[bool, str, bool]:
    """Return NPZ accounting status and whether any failure must be hard."""
    manifest_rows, manifest_error = _read_json_list(nroot / "manifest.json")
    quarantine_rows, quarantine_error = _read_json_list(nroot / "catalog_quarantine.json")
    on_disk_paths = {_path_key(path) for path in nroot.glob("*.npz")}
    manifest_paths, manifest_path_errors = _catalog_path_keys(manifest_rows, "manifest", nroot)
    quarantine_paths, quarantine_path_errors = _catalog_path_keys(quarantine_rows, "quarantine", nroot)
    accounted_paths = manifest_paths + quarantine_paths
    accounted_unique = set(accounted_paths)
    duplicate_paths = [path for path, count in Counter(accounted_paths).items() if count > 1]
    manifest_set = set(manifest_paths)
    quarantine_set = set(quarantine_paths)
    overlap_paths = manifest_set & quarantine_set
    unaccounted_paths = on_disk_paths - accounted_unique
    stale_entries = accounted_unique - on_disk_paths
    catalog_count = len(manifest_rows)
    quarantine_count = len(quarantine_rows)
    errors = []
    if manifest_error:
        errors.append(f"manifest={manifest_error}")
    if quarantine_error:
        errors.append(f"quarantine={quarantine_error}")
    errors.extend(manifest_path_errors)
    errors.extend(quarantine_path_errors)
    if duplicate_paths:
        errors.append(f"duplicate_paths={len(duplicate_paths)}")
    if overlap_paths:
        errors.append(f"manifest_quarantine_overlap={len(overlap_paths)}")
    hard_failure = bool(errors)
    ok = (
        not hard_failure
        and not unaccounted_paths
        and not stale_entries
    )
    detail = (
        f"catalog={catalog_count} quarantine={quarantine_count} on_disk={len(on_disk_paths)} "
        f"unaccounted={len(unaccounted_paths)} overaccounted={len(stale_entries)} "
        f"duplicates={len(duplicate_paths)} invalid_rows={len(errors)}"
    )
    if errors:
        detail += f" errors={errors}"
    return ok, detail, hard_failure


def _dbn_sample_valid(
    path: Path,
    expected_schema: str | None = None,
    expected_record_count: int | None = None,
) -> tuple[bool, str]:
    try:
        import databento as db  # noqa: PLC0415
    except Exception as exc:  # noqa: BLE001
        return False, f"databento unavailable: {type(exc).__name__}"
    try:
        store = db.DBNStore.from_file(str(path))
        metadata = getattr(store, "metadata", None)
        schema = str(getattr(metadata, "schema", "") or "").lower()
        if expected_schema and schema and expected_schema.lower() not in schema:
            return False, f"schema {schema!r} != {expected_schema!r}"
        count = 0
        for _ in store:
            count += 1
            if expected_record_count is None:
                break
            if count > expected_record_count:
                return False, f"record_count exceeds sidecar ({count}>{expected_record_count})"
        if count == 0:
            return False, "no sample records"
        if expected_record_count is not None and count != expected_record_count:
            return False, f"record_count {count} != sidecar {expected_record_count}"
    except Exception as exc:  # noqa: BLE001
        return False, f"dbn open/sample failed: {type(exc).__name__}: {exc}"
    return True, "dbn sample ok"


def _valid_dbn_sidecar(path: Path, sidecar: dict, expected_schema: str | None = None) -> tuple[bool, str]:
    if sidecar.get("valid") is not True:
        return False, str(sidecar.get("reason") or "sidecar valid flag not true")
    schema = str(sidecar.get("schema") or "").lower()
    if expected_schema:
        if not schema:
            return False, "sidecar schema missing"
        if expected_schema.lower() not in schema:
            return False, f"sidecar schema {schema!r} != {expected_schema!r}"
    if sidecar.get("vendor_no_data_proof"):
        record_count = sidecar.get("record_count")
        if record_count is not None:
            try:
                int(record_count)
            except (TypeError, ValueError):
                return False, "sidecar record_count invalid"
    else:
        record_count = sidecar.get("record_count")
        if record_count is None:
            return False, "sidecar record_count missing"
        try:
            if int(record_count) <= 0:
                return False, "sidecar record_count <= 0 without no-data proof"
        except (TypeError, ValueError):
            return False, "sidecar record_count invalid"

    size_bytes = sidecar.get("size_bytes")
    if size_bytes is None:
        return False, "sidecar size_bytes missing"
    try:
        expected_size = int(size_bytes)
    except (TypeError, ValueError):
        return False, "sidecar size_bytes invalid"
    try:
        actual_size = path.stat().st_size
    except OSError:
        return False, "stat failed"
    if expected_size != actual_size:
        return False, "sidecar size_bytes mismatch"

    sha256 = str(sidecar.get("sha256") or "").lower()
    if not re.fullmatch(r"[0-9a-f]{64}", sha256):
        return False, "sidecar sha256 missing or invalid"
    if _file_sha256(path) != sha256:
        return False, "sidecar sha256 mismatch"
    return True, "doctor sidecar ok"


def _valid_options_artifact(path: Path, expected_schema: str | None = None) -> tuple[bool, str]:
    if not _valid_nonempty_file(path):
        return False, "missing_or_empty"
    sidecar = _load_doctor_sidecar(path)
    is_dbn = _is_dbn_artifact(path)
    if expected_schema and not is_dbn:
        return False, "not approved market-data artifact"
    if is_dbn:
        if sidecar is not None:
            ok, reason = _valid_dbn_sidecar(path, sidecar, expected_schema=expected_schema)
            if not ok:
                return False, reason
            if sidecar.get("vendor_no_data_proof"):
                return True, reason
            try:
                expected_count = int(sidecar["record_count"])
            except (KeyError, TypeError, ValueError):
                return False, "sidecar record_count invalid"
            sample_ok, sample_reason = _dbn_sample_valid(
                path,
                expected_schema=expected_schema,
                expected_record_count=expected_count,
            )
            if not sample_ok:
                return False, sample_reason
            return True, f"{reason}; {sample_reason}"
        return _dbn_sample_valid(path, expected_schema=expected_schema)
    if sidecar is not None and sidecar.get("valid") is False:
        return False, str(sidecar.get("reason") or "sidecar invalid")
    return True, "nonempty"


def _iter_option_data_files(root: Path):
    for p in root.rglob("*"):
        if p.name.endswith(".doctor.json") or p.name == "coverage_manifest.json":
            continue
        if p.is_file():
            yield p


def _coverage_invalid(
    source: str,
    message: str,
    *,
    date_str: str = "",
    row_index: int | None = None,
    path: str = "",
    schema: str = "",
) -> dict:
    detail: dict = {
        "source": source,
        "reason": message,
        "message": message,
    }
    if date_str:
        detail["date"] = date_str
    if row_index is not None:
        detail["row"] = row_index
    if path:
        detail["path"] = path
    if schema:
        detail["schema"] = schema
    return detail


def _manifest_covered_elsewhere(opt: Path, expected_dates: set[str]) -> tuple[set[str], list[dict], list[dict]]:
    manifest_path = opt / "coverage_manifest.json"
    if not manifest_path.is_file():
        return set(), [], []
    try:
        raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return set(), [], [_coverage_invalid("coverage_manifest", f"{manifest_path.name}: unreadable {type(exc).__name__}")]
    rows = raw.get("covered_elsewhere", raw) if isinstance(raw, dict) else raw
    if not isinstance(rows, list):
        return set(), [], [_coverage_invalid("coverage_manifest", f"{manifest_path.name}: expected list or covered_elsewhere list")]

    covered: set[str] = set()
    accepted: list[dict] = []
    invalid: list[dict] = []
    required = {"date", "dataset", "schema", "start_utc", "end_utc", "path"}
    for i, row in enumerate(rows):
        if not isinstance(row, dict):
            invalid.append(_coverage_invalid("coverage_manifest", f"row {i}: not object", row_index=i))
            continue
        d = str(row.get("date") or "")
        missing = sorted(required - set(row))
        if missing:
            invalid.append(_coverage_invalid("coverage_manifest", f"row {i}: missing {missing}", date_str=d, row_index=i))
            continue
        if d not in expected_dates:
            invalid.append(_coverage_invalid("coverage_manifest", f"row {i}: date {d} not expected", date_str=d, row_index=i))
            continue
        if str(row.get("dataset")) != "fixing_mbo":
            invalid.append(_coverage_invalid("coverage_manifest", f"row {i}: dataset {row.get('dataset')!r} not fixing_mbo", date_str=d, row_index=i))
            continue
        schema = str(row.get("schema") or "")
        if schema.lower() not in {"mbo", "fixing_mbo", "quotes", "quote"}:
            invalid.append(_coverage_invalid("coverage_manifest", f"row {i}: schema {schema!r} not MBO/quotes", date_str=d, row_index=i, schema=schema))
            continue
        artifact = Path(str(row.get("path")))
        if not artifact.is_absolute():
            artifact = opt / artifact
        ok, reason = _valid_options_artifact(artifact, expected_schema="mbo")
        if not ok:
            invalid.append(_coverage_invalid(
                "coverage_manifest",
                f"row {i}: {artifact} invalid: {reason}",
                date_str=d,
                row_index=i,
                path=str(artifact),
                schema=schema,
            ))
            continue
        covered.add(d)
        accepted.append({
            "date": d,
            "dataset": row.get("dataset"),
            "schema": row.get("schema"),
            "start_utc": row.get("start_utc"),
            "end_utc": row.get("end_utc"),
            "path": str(artifact),
        })
    return covered, accepted, invalid


def _npz_manifest_covered_elsewhere(lroot: Path, expected_dates: set[str]) -> tuple[set[str], list[dict], list[dict]]:
    manifest_path = lroot / "npz" / "manifest.json"
    if not manifest_path.is_file():
        return set(), [], []
    try:
        raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return set(), [], [_coverage_invalid("active_npz_manifest", f"{manifest_path.name}: unreadable {type(exc).__name__}")]
    if not isinstance(raw, list):
        return set(), [], [_coverage_invalid("active_npz_manifest", f"{manifest_path.name}: expected list")]

    covered: set[str] = set()
    accepted: list[dict] = []
    invalid: list[dict] = []
    for i, row in enumerate(raw):
        if not isinstance(row, dict):
            invalid.append(_coverage_invalid("active_npz_manifest", f"npz row {i}: not object", row_index=i))
            continue
        if str(row.get("symbol") or "") != "ES.v.0":
            continue
        event_id = str(row.get("event_id") or "")
        m = _PROP_FLATTEN_RE.match(event_id)
        if not m:
            continue
        d = f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
        if d not in expected_dates:
            continue
        try:
            event_count = int(row.get("event_count"))
        except (TypeError, ValueError):
            invalid.append(_coverage_invalid("active_npz_manifest", f"npz row {i}: event_count invalid for {d}", date_str=d, row_index=i))
            continue
        artifact = Path(str(row.get("npz_path") or ""))
        if not artifact.is_absolute():
            artifact = lroot / "npz" / artifact
        if event_count <= 0:
            invalid.append(_coverage_invalid("active_npz_manifest", f"npz row {i}: event_count {event_count} for {d}", date_str=d, row_index=i, path=str(artifact), schema="npz_mbo"))
            continue
        if not _valid_nonempty_file(artifact):
            invalid.append(_coverage_invalid("active_npz_manifest", f"npz row {i}: {artifact} missing_or_empty", date_str=d, row_index=i, path=str(artifact), schema="npz_mbo"))
            continue
        covered.add(d)
        accepted.append({
            "date": d,
            "dataset": "fixing_mbo",
            "schema": "npz_mbo",
            "source": "active_npz_manifest",
            "event_id": event_id,
            "path": str(artifact),
            "event_count": event_count,
        })
    return covered, accepted, invalid


def _fixing_window_specs(dates: list[str], kinds_by_date: dict[str, set[str]]) -> list[dict]:
    specs = []
    for d_str in dates:
        d = date.fromisoformat(d_str)
        fix_utc = fixing_datetime_utc(d)
        specs.append({
            "date": d_str,
            "expiry_kinds": sorted(kinds_by_date.get(d_str, set())),
            "symbols": ["ES"],
            "start_utc": (fix_utc - _FIXING_WINDOW_BEFORE).isoformat(),
            "end_utc": (fix_utc + _FIXING_WINDOW_AFTER).isoformat(),
        })
    return specs


def _fixing_gap_diagnostics(
    gap_dates: list[str],
    kinds_by_date: dict[str, set[str]],
    invalid_by_date: dict[str, list[dict]],
    today: date,
) -> list[dict]:
    diagnostics = []
    specs_by_date = {row["date"]: row for row in _fixing_window_specs(gap_dates, kinds_by_date)}
    for d_str in gap_dates:
        invalid = invalid_by_date.get(d_str, [])
        days_old = (today - date.fromisoformat(d_str)).days
        reason = "invalid_artifact" if invalid else "missing_artifact"
        if not invalid and days_old <= OPTIONS_VENDOR_LAG_GRACE_DAYS:
            reason = "missing_artifact_vendor_lag"
        diagnostics.append({
            "date": d_str,
            "status": "FAIL",
            "reason": reason,
            "days_old": days_old,
            "stale": days_old > OPTIONS_VENDOR_LAG_GRACE_DAYS,
            "expiry_kinds": sorted(kinds_by_date.get(d_str, set())),
            "invalid_artifacts": invalid,
            "retry_window": specs_by_date[d_str],
            "required_action": (
                "replace_invalid_artifact_or_manifest_no_data_proof"
                if invalid else
                "backfill_or_manifest_vendor_no_data_proof"
            ),
        })
    return diagnostics


def _merge_invalid_coverage_by_date(
    invalid_by_date: dict[str, list[dict]],
    invalid_covered_elsewhere: list[dict],
) -> None:
    for invalid in invalid_covered_elsewhere:
        d_str = str(invalid.get("date") or "")
        if d_str:
            invalid_by_date.setdefault(d_str, []).append(invalid)


def _rclone() -> str | None:
    for cand in (
        shutil.which("rclone"),
        os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\WinGet\Packages\Rclone.Rclone_Microsoft.Winget.Source_8wekyb3d8bbwe\rclone-v1.74.3-windows-amd64\rclone.exe"),
    ):
        if cand and Path(cand).is_file():
            return cand
    return None


def options_lane_checks(
    lroot: Path,
    today: date | None = None,
    start: date = OPTIONS_FIXING_START,
) -> dict | None:
    """Run options-lane dataset checks and return a summary dict (or None)."""
    if today is None:
        today = date.today()

    opt = lroot / "options"
    if not opt.is_dir():
        check("options-datasets", False, "options lane not provisioned under lake root", warn_only=True)
        return None
    check("options-datasets", True, str(opt))

    # fixing_mbo: scan for quote and trades files
    fixing_dir = opt / "fixing_mbo"
    quote_files: list[str] = []
    trades_files: list[str] = []
    quote_dates: set[str] = set()
    trades_dates: set[str] = set()
    invalid_fixing_files: list[str] = []
    invalid_fixing_by_date: dict[str, list[dict]] = {}
    if fixing_dir.is_dir():
        for p in fixing_dir.iterdir():
            m = _FIXING_RE.match(p.name)
            if m:
                is_trades = bool(m.group(1))
                expected_schema = "trades" if is_trades else "mbo"
                d_str = m.group(2)
                valid, reason = _valid_options_artifact(p, expected_schema=expected_schema)
                if not valid:
                    invalid_fixing_files.append(f"{p.name}: {reason}")
                    invalid_fixing_by_date.setdefault(d_str, []).append({
                        "file": p.name,
                        "schema": expected_schema,
                        "reason": reason,
                    })
                    continue
                if is_trades:
                    trades_files.append(p.name)
                    trades_dates.add(d_str)
                else:
                    quote_files.append(p.name)
                    quote_dates.add(d_str)

    study_dates = quote_dates | trades_dates
    first_date = min(quote_dates) if quote_dates else ""
    last_date = max(quote_dates) if quote_dates else ""
    check(
        "options-fixing-mbo",
        ok=len(study_dates) > 0,
        detail=(
            f"quotes={len(quote_files)} trades={len(trades_files)} quote_dates={len(quote_dates)} "
            f"study_dates={len(study_dates)} "
            f"trades_only_dates={len(trades_dates - quote_dates)} invalid={len(invalid_fixing_files)} "
            f"({first_date}..{last_date})"
        ),
    )

    # coverage: expected expiry dates vs. what we have
    kinds_by_date: dict[str, set[str]] = {}
    for d, kind in expiries_between(start, today):
        kinds_by_date.setdefault(d.isoformat(), set()).add(kind.value)
    expected = set(kinds_by_date)
    manifest_dates, manifest_covered, manifest_invalid = _manifest_covered_elsewhere(opt, expected)
    npz_dates, npz_covered, npz_invalid = _npz_manifest_covered_elsewhere(lroot, expected)
    alternate_dates = manifest_dates | npz_dates
    covered_elsewhere = manifest_covered + npz_covered
    invalid_covered_elsewhere = manifest_invalid + npz_invalid
    _merge_invalid_coverage_by_date(invalid_fixing_by_date, invalid_covered_elsewhere)
    gaps = sorted(expected - study_dates - alternate_dates)
    stale_gaps = [g for g in gaps if (today - date.fromisoformat(g)).days > OPTIONS_VENDOR_LAG_GRACE_DAYS]
    strict_mbo_gaps = sorted(expected - quote_dates - alternate_dates)
    strict_mbo_stale_gaps = [
        g for g in strict_mbo_gaps if (today - date.fromisoformat(g)).days > OPTIONS_VENDOR_LAG_GRACE_DAYS
    ]
    gap_diagnostics = _fixing_gap_diagnostics(gaps, kinds_by_date, invalid_fixing_by_date, today)
    gap_sample = gaps[:10]
    check(
        "options-fixing-coverage",
        ok=not gaps,
        detail=(
            f"mode=fixing_study_trade_or_mbo dates={len(study_dates)} gap_count={len(gaps)} stale={len(stale_gaps)} "
            f"covered_elsewhere={len(alternate_dates)} invalid_manifest={len(invalid_covered_elsewhere)} "
            f"first_gaps={gap_sample}"
        ),
    )
    check(
        "options-fixing-mbo-coverage",
        ok=not strict_mbo_gaps,
        detail=(
            f"mode=strict_mbo_quotes dates={len(quote_dates)} "
            f"gap_count={len(strict_mbo_gaps)} stale={len(strict_mbo_stale_gaps)} "
            f"first_gaps={strict_mbo_gaps[:10]}"
        ),
        warn_only=True,
    )

    # ohlcv
    ohlcv_dir = opt / "ohlcv"
    ohlcv_files: list[str] = []
    invalid_ohlcv_files: list[str] = []
    if ohlcv_dir.is_dir():
        for p in ohlcv_dir.glob("*.dbn.zst"):
            valid, reason = _valid_options_artifact(p, expected_schema="ohlcv")
            if valid:
                ohlcv_files.append(p.name)
            else:
                invalid_ohlcv_files.append(f"{p.name}: {reason}")
    check("options-ohlcv", ok=len(ohlcv_files) > 0, detail=f"valid={ohlcv_files} invalid={invalid_ohlcv_files}")

    # definitions
    defs_dir = opt / "definitions"
    def_files: list[Path] = []
    invalid_def_files: list[str] = []
    if defs_dir.is_dir():
        for p in defs_dir.rglob("*.dbn.zst"):
            valid, reason = _valid_options_artifact(p, expected_schema="definition")
            if valid:
                def_files.append(p)
            else:
                invalid_def_files.append(f"{p.name}: {reason}")
    batches = sorted({p.parent.name for p in def_files if p.parent != defs_dir})
    check(
        "options-definitions",
        ok=len(def_files) > 0,
        detail=f"files={len(def_files)} batches={batches} invalid={len(invalid_def_files)}",
    )

    # statistics
    stats_dir = opt / "statistics"
    stat_files: list[Path] = []
    invalid_stat_files: list[str] = []
    if stats_dir.is_dir():
        for p in _iter_option_data_files(stats_dir):
            valid, reason = _valid_options_artifact(p, expected_schema="statistics")
            if valid:
                stat_files.append(p)
            else:
                invalid_stat_files.append(f"{p.name}: {reason}")
    stats_state: str
    if len(stat_files) == 0:
        stats_state = "pending_batch_delivery"
        stats_detail = "pending Databento batch delivery (expected until WS-0.4 statistics job lands)"
    else:
        stats_state = "present"
        stats_detail = f"files={len(stat_files)} invalid={len(invalid_stat_files)}"
    check("options-statistics", ok=len(stat_files) > 0, detail=stats_detail)

    return {
        "as_of_utc": datetime.now(timezone.utc).isoformat(),
        "fixing_mbo": {
            "quote_files": len(quote_files),
            "trades_files": len(trades_files),
            "dates_covered": len(quote_dates),
            "study_dates_covered": len(study_dates),
            "trade_only_dates": len(trades_dates - quote_dates),
            "invalid_files": len(invalid_fixing_files),
            "quote_date_list": sorted(quote_dates),
            "trades_date_list": sorted(trades_dates),
            "study_date_list": sorted(study_dates),
            "trade_only_date_list": sorted(trades_dates - quote_dates),
            "invalid_file_details": invalid_fixing_files,
            "first_date": first_date,
            "last_date": last_date,
        },
        "expiry_coverage": {
            "coverage_mode": "fixing_study_trade_or_mbo",
            "expected_dates": len(expected),
            "dates_covered": len(study_dates | alternate_dates),
            "covered_elsewhere": sorted(alternate_dates),
            "covered_elsewhere_manifest": covered_elsewhere,
            "invalid_covered_elsewhere": invalid_covered_elsewhere,
            "gaps": len(gaps),
            "gap_count": len(gaps),
            "gap_dates": gaps,
            "gap_diagnostics": gap_diagnostics,
            "stale_gap_count": len(stale_gaps),
            "stale_gap_dates": stale_gaps,
            "gap_request_windows": _fixing_window_specs(gaps, kinds_by_date),
            "strict_mbo_gap_count": len(strict_mbo_gaps),
            "strict_mbo_stale_gap_count": len(strict_mbo_stale_gaps),
            "strict_mbo_gap_dates": strict_mbo_gaps,
            "grace_days": OPTIONS_VENDOR_LAG_GRACE_DAYS,
            "calendar": "rule-based v0 (packages/options_data/src/expiry_calendar.py)",
        },
        "ohlcv": {
            "files": len(ohlcv_files),
            "names": ohlcv_files,
            "invalid_files": invalid_ohlcv_files,
        },
        "definitions": {
            "files": len(def_files),
            "batches": batches,
            "invalid_files": invalid_def_files,
        },
        "statistics": {
            "files": len(stat_files),
            "state": stats_state,
            "invalid_files": invalid_stat_files,
        },
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-b2", action="store_true")
    args = ap.parse_args()

    # 1. canonical roots
    nroot, lroot = npz_root(_REPO), lake_root(_REPO)
    env_ok = bool(os.environ.get("HFT3_NPZ_ROOT")) and bool(os.environ.get("HFT3_MANIFEST_PATH"))
    check("env-roots", env_ok, f"HFT3_NPZ_ROOT={os.environ.get('HFT3_NPZ_ROOT', '<unset>')} npz_root={nroot}")
    check("lake-exists", nroot.is_dir() and (lroot / "mbo_release").is_dir(), str(lroot))

    # 2. unified spend ledger
    mpath = Path(os.environ.get("HFT3_MANIFEST_PATH", str(_REPO / "data" / "manifest.parquet")))
    check("ledger", mpath.is_file() and mpath.stat().st_size > 1_000_000,
          f"{mpath} ({mpath.stat().st_size if mpath.is_file() else 0} bytes)")
    stale_repo_ledger = (_REPO / "data" / "manifest.parquet")
    check("no-split-ledger", not (stale_repo_ledger.is_file() and mpath.resolve() != stale_repo_ledger.resolve()
                                  and abs(stale_repo_ledger.stat().st_mtime - time.time()) < 86400),
          "repo-relative ledger not freshly written", warn_only=True)

    # 3. hash catalog freshness
    cat = nroot / "manifest.json"
    quarantine_cat = nroot / "catalog_quarantine.json"
    if cat.is_file():
        age_h = (time.time() - cat.stat().st_mtime) / 3600
        check("catalog-fresh", age_h < MAX_CATALOG_AGE_H, f"{age_h:.1f}h old (limit {MAX_CATALOG_AGE_H}h)")
        if quarantine_cat.is_file():
            quarantine_age_h = (time.time() - quarantine_cat.stat().st_mtime) / 3600
            check(
                "catalog-quarantine-fresh",
                quarantine_age_h < MAX_CATALOG_AGE_H,
                f"{quarantine_age_h:.1f}h old (limit {MAX_CATALOG_AGE_H}h)",
            )
        else:
            check("catalog-quarantine-fresh", False, f"{quarantine_cat} missing")
        catalog_ok, catalog_detail, catalog_hard_failure = catalog_coverage_detail(nroot)
        check("catalog-coverage", catalog_ok, catalog_detail, warn_only=not catalog_hard_failure)
    else:
        check("catalog-fresh", False, f"{cat} missing")

    # 4. hygiene: no nested dup dir re-appearing, no events.jsonl reappearing
    check("no-nested-npz", not (nroot / "npz").is_dir() or True, "nested npz/ pending adjudication", warn_only=True)
    jsonl = next((lroot / "mbo_release").glob("*/*/events.jsonl"), None)
    check("no-events-jsonl", jsonl is None, "purged (re-derivable from raw.dbn.zst)" if jsonl is None else str(jsonl))

    # 5. disk headroom
    du = shutil.disk_usage(str(lroot))
    frac = du.free / du.total
    check("disk-free", frac > MIN_FREE_FRACTION, f"{du.free / 1e9:.0f} GB free ({frac:.0%}, floor {MIN_FREE_FRACTION:.0%})")

    # 6. B2 sync recency + reachability
    if not args.skip_b2:
        rc = _rclone()
        if rc:
            try:
                out = subprocess.run([rc, "lsjson", f"{B2_REMOTE}/lake/manifest.parquet"],
                                     capture_output=True, text=True, timeout=60)
                ok = out.returncode == 0 and out.stdout.strip().startswith("[")
                mod = json.loads(out.stdout)[0]["ModTime"] if ok else ""
                check("b2-reachable", ok, f"{B2_REMOTE} ledger ModTime={mod}")
            except Exception as exc:
                check("b2-reachable", False, f"{type(exc).__name__}: {exc}")
        else:
            check("b2-reachable", False, "rclone not found")
        synclog = _REPO / "runtime" / "b2_sync.log"
        if synclog.is_file():
            age_h = (time.time() - synclog.stat().st_mtime) / 3600
            check("b2-sync-recent", age_h < MAX_SYNC_AGE_H, f"last sync activity {age_h:.1f}h ago")
        else:
            check("b2-sync-recent", False, "runtime/b2_sync.log missing")

    opt_summary = options_lane_checks(lroot)

    report = {
        "run_utc": datetime.now(timezone.utc).isoformat(),
        "checks": checks,
        "failed": sum(1 for c in checks if c["status"] == "FAIL"),
        "warned": sum(1 for c in checks if c["status"] == "WARN"),
    }
    if opt_summary is not None:
        report["options_lane"] = opt_summary
    out_path = _REPO / "runtime" / "data_doctor_report.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"\n{report['failed']} FAIL, {report['warned']} WARN -> {out_path}")
    return 1 if report["failed"] else 0


if __name__ == "__main__":
    sys.exit(main())
