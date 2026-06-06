"""Inventory and non-destructive sync for paid lane data.

The source repo may contain paid/downloaded files that are intentionally not
tracked by git. This tool copies those files into the current code authority's
ignored data lake and writes local audit reports under runtime/data_audits.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


DEFAULT_SOURCE_ROOT = Path(r"C:\Users\MSI\Documents\New project\data")
DATE_RE = re.compile(r"(20\d{2})[-_](\d{2})[-_](\d{2})")
SYMBOL_RE = re.compile(r"^(?P<symbol>[A-Z0-9]+(?:\.v\.0)?)_", re.IGNORECASE)


@dataclass(frozen=True)
class CopySpec:
    name: str
    source_rel: Path
    dest_rel: Path
    pattern: str


COPY_SPECS = (
    CopySpec("cme_runnable_npz", Path("npz"), Path("npz"), "**/*_mbo.npz"),
    CopySpec("cme_replay_mbp10", Path("replay") / "mbp10", Path("replay") / "mbp10", "**/*.dbn.zst"),
    CopySpec("cme_raw_root_dbn", Path("."), Path("raw") / "databento_mbo", "*.dbn.zst"),
    CopySpec(
        "cme_pilot_raw_dbn",
        Path("raw") / "databento_mbo" / "mbo_pilot_basket_20260605",
        Path("raw") / "databento_mbo" / "mbo_pilot_basket_20260605",
        "**/*.dbn.zst",
    ),
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
) -> dict[str, Any]:
    data_root = repo_root / "data"
    before = inventory_data_root(data_root)
    source = inventory_data_root(source_root)
    synced_files = sync_paid_data(source_root, data_root, dry_run=dry_run) if sync else []
    after = inventory_data_root(data_root)
    return {
        "schema_version": 1,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "repo_root": str(repo_root),
        "data_root_used": str(data_root),
        "source_root": str(source_root),
        "sync_requested": sync,
        "dry_run": dry_run,
        "source_inventory": source,
        "destination_inventory_before": before,
        "destination_inventory_after": after,
        "synced_files": synced_files,
        "runnable_npz_days": after["runnable_npz_days"],
        "raw_download_days": after["raw_download_days"],
        "missing_conversion_days": after["missing_conversion_days"],
        "official_coverage_status": after["official_coverage_status"],
    }


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


def _resolve_source_root(repo_root: Path, explicit: Path | None, from_manifest: bool) -> Path:
    if explicit is not None:
        return explicit.resolve()
    if from_manifest:
        from data_system.src.data_roots import paid_data_root

        return paid_data_root(repo_root)
    env = __import__("os").environ.get("HFT3_PAID_DATA_ROOT", "").strip()
    if env:
        return Path(env).resolve()
    try:
        from data_system.src.data_roots import paid_data_root

        return paid_data_root(repo_root)
    except Exception:
        return DEFAULT_SOURCE_ROOT.resolve()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument(
        "--source-root",
        type=Path,
        default=None,
        help="Paid data root (default: HFT3_PAID_DATA_ROOT or pilot manifest)",
    )
    parser.add_argument(
        "--from-manifest",
        action="store_true",
        help="Resolve --source-root from mbo_pilot_basket_20260605_manifest.json",
    )
    parser.add_argument("--output-dir", type=Path, default=Path("runtime") / "data_audits")
    parser.add_argument("--sync", action="store_true", help="Copy paid files into the repo data root without deleting sources.")
    parser.add_argument("--dry-run", action="store_true", help="Report intended copy actions without copying.")
    return parser.parse_args()


def main() -> int:
    import sys

    _repo = Path(__file__).resolve().parents[1]
    if str(_repo) not in sys.path:
        sys.path.insert(0, str(_repo))
    for sub in ("packages", "apps"):
        p = str(_repo / sub)
        if p not in sys.path:
            sys.path.insert(0, p)
    try:
        from dotenv import load_dotenv

        load_dotenv(_repo / ".env")
    except ImportError:
        pass

    args = parse_args()
    repo_root = args.repo_root.resolve()
    source_root = _resolve_source_root(repo_root, args.source_root, args.from_manifest)
    output_dir = args.output_dir if args.output_dir.is_absolute() else repo_root / args.output_dir
    if not source_root.is_dir():
        raise SystemExit(f"source root not found: {source_root}")
    report = build_report(repo_root=repo_root, source_root=source_root, sync=args.sync, dry_run=args.dry_run)
    json_path, md_path = write_reports(report, output_dir)
    print(f"wrote {json_path}")
    print(f"wrote {md_path}")
    print(f"official_coverage_status={report['official_coverage_status']}")
    print(f"synced_files={len(report['synced_files'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
