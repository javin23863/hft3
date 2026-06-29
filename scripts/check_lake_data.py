#!/usr/bin/env python3
"""Scan lake units or manifest NPZ paths for OHLCV viability (Phase 0)."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from hft3_bootstrap import setup_repo_paths

setup_repo_paths()

from data_system.src.lake_manifest import load_manifest, resolve_npz_path
from data_system.src.npz_resolver import resolve_npz_for_event
from research_pipeline.data_quality import check_npz_ohlcv


def _load_units_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            if isinstance(row, dict):
                rows.append(row)
    return rows


def _iter_units_jsonl(
    path: Path,
    *,
    offset: int = 0,
    max_units: int | None = None,
) -> Iterator[tuple[int, dict[str, Any]]]:
    """Yield (unit_index, row) for dict JSONL rows; offset skips prior units."""
    unit_index = 0
    yielded = 0
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            if not isinstance(row, dict):
                continue
            if unit_index < offset:
                unit_index += 1
                continue
            yield unit_index, row
            unit_index += 1
            yielded += 1
            if max_units is not None and yielded >= max_units:
                break


def _count_jsonl_unit_rows(path: Path) -> int:
    count = 0
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            if isinstance(row, dict):
                count += 1
    return count


def _check_unit_row(row: dict[str, Any], repo_root: Path, symbols: tuple[str, ...]) -> tuple[str, str, str | None]:
    unit_id = str(row.get("unit_id") or "")
    symbol = str(row.get("symbol") or "")
    event_id = str(row.get("event_id") or "")
    if not unit_id:
        unit_id = f"{symbol}_{event_id}" if symbol and event_id else "unknown"
    path, present, _ = resolve_npz_for_event(repo_root, event_id, symbol, symbols)
    if not present or path is None:
        return unit_id, "missing_npz", None
    result = check_npz_ohlcv(path)
    if result.valid:
        return unit_id, "ok", str(path)
    return unit_id, result.reason or "invalid_npz", str(path)


def _check_manifest_row(row: dict[str, Any], repo_root: Path) -> tuple[str, str, str | None]:
    symbol = str(row.get("symbol") or "")
    event_id = str(row.get("event_id") or "")
    npz_path_raw = str(row.get("npz_path") or "")
    unit_id = f"{symbol}_{event_id}" if symbol and event_id else (npz_path_raw or "unknown")
    if not npz_path_raw:
        return unit_id, "missing_npz_path", None
    npz_path = resolve_npz_path(repo_root, npz_path_raw)
    result = check_npz_ohlcv(npz_path)
    if result.valid:
        return unit_id, "ok", str(npz_path)
    return unit_id, result.reason or "invalid_npz", str(npz_path)


def _empty_report(*, source: str, omit_valid_ids: bool = False) -> dict[str, Any]:
    report: dict[str, Any] = {
        "invalid_unit_ids": {},
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "source": source,
        "valid_count": 0,
        "invalid_count": 0,
        "invalid_reasons": {},
        "scan_progress": {
            "offset": 0,
            "units_scanned_this_run": 0,
            "next_offset": 0,
            "total_source_rows": None,
            "complete": False,
        },
    }
    if omit_valid_ids:
        report["valid_unit_ids_omitted"] = True
        report["checked_paths_omitted"] = True
    else:
        report["valid_unit_ids"] = []
        report["checked_paths"] = {}
    return report


def _merge_prior_report(
    base: dict[str, Any],
    prior: dict[str, Any],
    *,
    source: str,
    omit_valid_ids: bool,
) -> None:
    if str(prior.get("source")) != source:
        raise ValueError(
            f"resume source mismatch: {prior.get('source')!r} != {source!r}"
        )
    prior_omit = bool(prior.get("valid_unit_ids_omitted"))
    if prior_omit != omit_valid_ids:
        raise ValueError(
            "resume omit_valid_ids mismatch: "
            f"prior={prior_omit} current={omit_valid_ids}"
        )

    if prior_omit:
        base["valid_unit_ids_omitted"] = True
        base["checked_paths_omitted"] = True
        base.pop("valid_unit_ids", None)
        base.pop("checked_paths", None)
        base["valid_count"] = int(prior.get("valid_count") or 0)
    else:
        base["valid_unit_ids"] = list(prior.get("valid_unit_ids") or [])
        base["checked_paths"] = dict(prior.get("checked_paths") or {})
        base["valid_count"] = len(base["valid_unit_ids"])

    base["invalid_unit_ids"] = dict(prior.get("invalid_unit_ids") or {})
    prior_progress = prior.get("scan_progress") or {}
    if isinstance(prior_progress, dict):
        base["scan_progress"]["offset"] = int(prior_progress.get("offset") or 0)
        base["scan_progress"]["next_offset"] = int(
            prior_progress.get("next_offset") or prior_progress.get("offset") or 0
        )
        if prior_progress.get("total_source_rows") is not None:
            base["scan_progress"]["total_source_rows"] = int(prior_progress["total_source_rows"])
    base["invalid_count"] = len(base["invalid_unit_ids"])
    base["invalid_reasons"] = dict(Counter(base["invalid_unit_ids"].values()))


def _apply_scan_result(
    report: dict[str, Any],
    *,
    unit_id: str,
    reason: str,
    npz_path: str | None,
) -> None:
    if npz_path and not report.get("checked_paths_omitted"):
        report["checked_paths"][unit_id] = npz_path
    if reason == "ok":
        if not report.get("valid_unit_ids_omitted"):
            report.setdefault("valid_unit_ids", []).append(unit_id)
        report["valid_count"] = int(report.get("valid_count") or 0) + 1
    else:
        report["invalid_unit_ids"][unit_id] = reason


def _finalize_report(report: dict[str, Any]) -> dict[str, Any]:
    if report.get("valid_unit_ids_omitted"):
        report["valid_count"] = int(report.get("valid_count") or 0)
    else:
        report["valid_count"] = len(report.get("valid_unit_ids") or [])
    report["invalid_count"] = len(report["invalid_unit_ids"])
    report["invalid_reasons"] = dict(Counter(report["invalid_unit_ids"].values()))
    report["checked_at"] = datetime.now(timezone.utc).isoformat()
    return report


def build_summary_report(report: dict[str, Any]) -> dict[str, Any]:
    """Compact aggregate suitable for commit (counts + reason breakdown only)."""
    progress = report.get("scan_progress") or {}
    return {
        "valid_count": int(report.get("valid_count") or 0),
        "invalid_count": int(report.get("invalid_count") or 0),
        "invalid_reasons": dict(report.get("invalid_reasons") or {}),
        "source": report.get("source"),
        "checked_at": report.get("checked_at"),
        "scan_progress": progress,
    }


def scan_lake_data(
    *,
    repo_root: Path,
    units_jsonl: Path | None = None,
    manifest_only: bool = False,
    symbols: tuple[str, ...],
    offset: int = 0,
    max_units: int | None = None,
    progress_every: int = 0,
    prior_report: dict[str, Any] | None = None,
    total_source_rows: int | None = None,
    omit_valid_ids: bool = False,
) -> dict[str, Any]:
    if units_jsonl is not None:
        source = str(units_jsonl)
    elif manifest_only:
        source = "lake_manifest"
    else:
        raise ValueError("Provide --units-jsonl or --manifest-only")

    report = _empty_report(source=source, omit_valid_ids=omit_valid_ids)
    if prior_report is not None:
        _merge_prior_report(
            report,
            prior_report,
            source=source,
            omit_valid_ids=omit_valid_ids,
        )
        if offset == 0:
            offset = int(report["scan_progress"].get("next_offset") or 0)
        if total_source_rows is None:
            cached = report["scan_progress"].get("total_source_rows")
            if cached is not None:
                total_source_rows = int(cached)

    start_offset = offset
    scanned_this_run = 0
    hit_eof = False

    if units_jsonl is not None:
        for _unit_index, row in _iter_units_jsonl(
            units_jsonl,
            offset=offset,
            max_units=max_units,
        ):
            unit_id, reason, npz_path = _check_unit_row(row, repo_root, symbols)
            _apply_scan_result(report, unit_id=unit_id, reason=reason, npz_path=npz_path)
            scanned_this_run += 1
            if progress_every > 0 and scanned_this_run % progress_every == 0:
                print(
                    f"progress offset={start_offset} scanned={scanned_this_run} "
                    f"valid={report['valid_count']} "
                    f"invalid={report['invalid_count']}",
                    file=sys.stderr,
                )
        if max_units is None:
            hit_eof = True
        else:
            hit_eof = scanned_this_run < max_units
    elif manifest_only:
        rows = list(load_manifest(repo_root))
        if total_source_rows is None:
            total_source_rows = len(rows)
        slice_rows = rows[offset:]
        if max_units is not None:
            slice_rows = slice_rows[:max_units]
            hit_eof = len(slice_rows) < max_units
        else:
            hit_eof = True
        for row in slice_rows:
            unit_id, reason, npz_path = _check_manifest_row(row, repo_root)
            _apply_scan_result(report, unit_id=unit_id, reason=reason, npz_path=npz_path)
            scanned_this_run += 1
            if progress_every > 0 and scanned_this_run % progress_every == 0:
                print(
                    f"progress offset={start_offset} scanned={scanned_this_run} "
                    f"valid={report['valid_count']} "
                    f"invalid={report['invalid_count']}",
                    file=sys.stderr,
                )
    else:
        raise ValueError("Provide --units-jsonl or --manifest-only")

    next_offset = start_offset + scanned_this_run
    if hit_eof:
        total_source_rows = next_offset
        complete = True
    elif total_source_rows is None:
        total_source_rows = _count_jsonl_unit_rows(units_jsonl) if units_jsonl is not None else next_offset
        complete = next_offset >= total_source_rows
    else:
        complete = next_offset >= total_source_rows

    report["scan_progress"].update(
        {
            "offset": start_offset,
            "units_scanned_this_run": scanned_this_run,
            "next_offset": next_offset,
            "total_source_rows": total_source_rows,
            "complete": complete,
        }
    )
    return _finalize_report(report)


def load_report_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid JSON in lake report {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"report must be a JSON object: {path}")
    return payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check lake NPZ OHLCV viability for paid-screen units")
    parser.add_argument("--repo-root", type=Path, default=_REPO)
    parser.add_argument("--units-jsonl", type=Path, default=None, help="Paid-screen units JSONL to validate")
    parser.add_argument(
        "--manifest-only",
        action="store_true",
        help="Validate every row in lake manifest.json instead of units JSONL",
    )
    parser.add_argument(
        "--symbols",
        default="MES.v.0,MNQ.v.0,ES.v.0,NQ.v.0,ZN.v.0,ZB.v.0,RTY.v.0",
        help="Comma-separated symbols for NPZ resolution when using --units-jsonl",
    )
    parser.add_argument("--out", type=Path, required=True, help="Write JSON report (valid/invalid unit ids)")
    parser.add_argument(
        "--summary-out",
        type=Path,
        default=None,
        help="Write compact summary JSON (counts + invalid_reasons only)",
    )
    parser.add_argument("--offset", type=int, default=0, help="Skip first N unit rows before scanning")
    parser.add_argument("--max-units", type=int, default=None, help="Maximum units to scan this invocation")
    parser.add_argument(
        "--progress-every",
        type=int,
        default=1000,
        help="Log progress to stderr every N units (0 disables)",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Merge prior partial report from --out and continue at next_offset",
    )
    parser.add_argument(
        "--omit-valid-ids",
        action="store_true",
        help="Store valid_count only (omit valid_unit_ids list) for large scans",
    )
    args = parser.parse_args(argv)

    if bool(args.units_jsonl) == bool(args.manifest_only):
        print("ERROR: specify exactly one of --units-jsonl or --manifest-only", file=sys.stderr)
        return 2

    repo_root = args.repo_root.resolve()
    symbols = tuple(s.strip() for s in args.symbols.split(",") if s.strip())
    units_path = None
    if args.units_jsonl:
        units_path = args.units_jsonl if args.units_jsonl.is_absolute() else repo_root / args.units_jsonl
        if not units_path.is_file():
            print(f"ERROR: units jsonl not found: {units_path}", file=sys.stderr)
            return 1

    out_path = args.out if args.out.is_absolute() else repo_root / args.out
    prior_report: dict[str, Any] | None = None
    if args.resume and out_path.is_file():
        try:
            prior_report = load_report_json(out_path)
        except ValueError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 1

    try:
        report = scan_lake_data(
            repo_root=repo_root,
            units_jsonl=units_path,
            manifest_only=bool(args.manifest_only),
            symbols=symbols,
            offset=max(0, int(args.offset)),
            max_units=args.max_units,
            progress_every=max(0, int(args.progress_every)),
            prior_report=prior_report,
            omit_valid_ids=bool(args.omit_valid_ids),
        )
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")

    summary_path = args.summary_out
    if summary_path is not None:
        summary_path = summary_path if summary_path.is_absolute() else repo_root / summary_path
        summary_path.parent.mkdir(parents=True, exist_ok=True)
        summary_path.write_text(
            json.dumps(build_summary_report(report), indent=2) + "\n",
            encoding="utf-8",
        )

    progress = report.get("scan_progress") or {}
    print(
        f"checked={report['valid_count'] + report['invalid_count']} "
        f"valid={report['valid_count']} invalid={report['invalid_count']} "
        f"next_offset={progress.get('next_offset')} complete={progress.get('complete')} "
        f"out={out_path}"
    )
    if summary_path is not None:
        print(f"summary={summary_path}")
    # Signal invalid units to CI only once the scan is complete — a partial
    # (resumable) batch that found invalid units must exit 0 so &&-chained
    # incremental workflows can continue to the next batch.
    scan_complete = bool(progress.get("complete"))
    if scan_complete and report.get("invalid_count", 0) > 0:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
