"""Weekly Rithmic coverage manifest builder (Phase 1)."""

from __future__ import annotations

import json
import os
import re
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Iterable

from research_pipeline.continuous_universe import (
    apply_profile_to_contract_row,
    filter_contracts_for_profile,
    stub_liquidity_score,
)

MANIFEST_SCHEMA_VERSION = "1"
_DEFAULT_DATA_TYPES = ("mbo", "quotes", "trades")
_WEEK_LABEL_RE = re.compile(r"^(\d{4})-W(\d{2})$")
_EVENTS_FILENAME = "events.ndjson"
_DATE_DIR_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def coverage_manifest_path(repo_root: Path, rithmic_week: str) -> Path:
    safe_week = rithmic_week.replace("-", "_")
    return repo_root / "runtime" / "continuous_cme" / f"coverage_manifest_{safe_week}.json"


def empty_contract_row(*, contract: str = "") -> dict[str, Any]:
    """Per-contract row shell (Phase 1 acceptance shape)."""
    return {
        "contract": contract,
        "row_count": 0,
        "missing_ratio": None,
        "liquidity_score": None,
        "eligible": None,
    }


def _week_label_variants(rithmic_week: str) -> tuple[str, ...]:
    return (rithmic_week, rithmic_week.replace("-", "_"))


def _parse_rithmic_week(rithmic_week: str) -> tuple[int, int]:
    """Validate ISO week label; fail closed on malformed input."""
    match = _WEEK_LABEL_RE.match(rithmic_week.strip())
    if not match:
        raise ValueError(f"invalid rithmic_week label: {rithmic_week!r}")
    year, week = int(match.group(1)), int(match.group(2))
    if week < 1 or week > 53:
        raise ValueError(f"invalid ISO week number in rithmic_week: {rithmic_week!r}")
    try:
        date.fromisocalendar(year, week, 1)
    except ValueError as exc:
        raise ValueError(f"invalid rithmic_week label: {rithmic_week!r}") from exc
    return year, week


def _iso_week_trading_days(rithmic_week: str) -> int:
    year, week = _parse_rithmic_week(rithmic_week)
    monday = date.fromisocalendar(year, week, 1)
    return sum(
        1
        for offset in range(7)
        if (monday + timedelta(days=offset)).weekday() < 5
    )


def _count_ndjson_rows(path: Path) -> int:
    count = 0
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                count += 1
    return count


def _contract_row_count_single(contract_dir: Path) -> int:
    events_path = contract_dir / _EVENTS_FILENAME
    if events_path.is_file():
        return _count_ndjson_rows(events_path)
    total = 0
    for path in contract_dir.rglob("*.ndjson"):
        if path.is_file():
            total += _count_ndjson_rows(path)
    return total


def _contract_row_count(contract_dirs: list[Path]) -> int:
    return sum(_contract_row_count_single(contract_dir) for contract_dir in contract_dirs)


def _is_date_dir_name(name: str) -> bool:
    if not _DATE_DIR_RE.match(name):
        return False
    try:
        datetime.strptime(name, "%Y-%m-%d")
    except ValueError:
        return False
    return True


def _has_flat_typed_capture(contract_dir: Path) -> bool:
    """True when typed ndjson exists at contract root without date partitions."""
    for data_type in _DEFAULT_DATA_TYPES:
        typed_path = contract_dir / f"{data_type}.ndjson"
        if not typed_path.is_file() or _count_ndjson_rows(typed_path) <= 0:
            continue
        if not any(_is_date_dir_name(parent.name) for parent in typed_path.parents):
            return True
    return False


def _contract_days_with_data(contract_dirs: list[Path]) -> int | None:
    days: set[str] = set()
    has_flat_data = False
    for contract_dir in contract_dirs:
        found_date_partition = False
        for path in contract_dir.rglob(_EVENTS_FILENAME):
            row_count = _count_ndjson_rows(path) if path.is_file() else 0
            for parent in path.parents:
                name = parent.name
                if _is_date_dir_name(name):
                    if row_count > 0:
                        days.add(name)
                    found_date_partition = True
                    break
            if not found_date_partition and path.is_file() and row_count > 0:
                has_flat_data = True
        if not has_flat_data and _has_flat_typed_capture(contract_dir):
            has_flat_data = True
    if days:
        return len(days)
    if has_flat_data:
        return None
    return 0


def _discover_contract_dirs(week_root: Path) -> dict[str, list[Path]]:
    contracts: dict[str, list[Path]] = {}
    if not week_root.is_dir():
        return contracts
    for child in sorted(week_root.iterdir()):
        if not child.is_dir() or child.name.startswith("."):
            continue
        name = child.name
        if _is_date_dir_name(name):
            for sym_dir in sorted(child.iterdir()):
                if sym_dir.is_dir() and not sym_dir.name.startswith("."):
                    contracts.setdefault(sym_dir.name, []).append(sym_dir)
            continue
        contracts.setdefault(name, []).append(child)
    return contracts


def _discover_data_types(contract_dirs: dict[str, list[Path]]) -> list[str]:
    """Derive present capture types from filesystem (mbo/quotes/trades)."""
    found: set[str] = set()
    for dirs in contract_dirs.values():
        for contract_dir in dirs:
            for data_type in _DEFAULT_DATA_TYPES:
                typed_file = contract_dir / f"{data_type}.ndjson"
                if typed_file.is_file() and _count_ndjson_rows(typed_file) > 0:
                    found.add(data_type)
                    continue
                typed_dir = contract_dir / data_type
                if typed_dir.is_dir() and any(typed_dir.rglob("*.ndjson")):
                    found.add(data_type)
            for path in contract_dir.rglob("*.ndjson"):
                stem = path.stem.lower()
                for data_type in _DEFAULT_DATA_TYPES:
                    if stem == data_type or stem.endswith(f"_{data_type}"):
                        found.add(data_type)
    if found:
        return [data_type for data_type in _DEFAULT_DATA_TYPES if data_type in found]
    return list(_DEFAULT_DATA_TYPES)


def discover_rithmic_weekly_roots(
    repo_root: Path,
    rithmic_week: str,
    *,
    extra_roots: Iterable[Path] | None = None,
) -> list[dict[str, Any]]:
    """Locate weekly Rithmic filesystem roots for *rithmic_week*."""
    _parse_rithmic_week(rithmic_week)
    candidates: list[Path] = []
    env_root = os.environ.get("RITHMIC_CONTINUOUS_WEEK_ROOT")
    if env_root:
        candidates.append(Path(env_root))
    for variant in _week_label_variants(rithmic_week):
        candidates.extend(
            (
                repo_root / "data" / "raw" / "rithmic_continuous" / variant,
                repo_root / "data" / "raw" / "rithmic_weekly" / variant,
            )
        )
    if extra_roots:
        candidates.extend(extra_roots)

    roots: list[dict[str, Any]] = []
    seen: set[str] = set()
    for candidate in candidates:
        resolved = candidate.resolve()
        key = str(resolved)
        if key in seen:
            continue
        seen.add(key)
        roots.append(
            {
                "path": str(resolved),
                "exists": resolved.is_dir(),
                "rithmic_week": rithmic_week,
            }
        )
    return roots


def _build_contract_row(
    contract: str,
    contract_dirs: list[Path],
    *,
    expected_trading_days: int,
    universe_profile: str,
) -> dict[str, Any]:
    row_count = _contract_row_count(contract_dirs)
    days_with_data = _contract_days_with_data(contract_dirs)
    if expected_trading_days <= 0:
        missing_ratio = None
    elif row_count <= 0:
        missing_ratio = 1.0
    elif days_with_data is None:
        missing_ratio = None
    else:
        missing_ratio = max(
            0.0,
            min(1.0, 1.0 - (days_with_data / expected_trading_days)),
        )
    liquidity_score = stub_liquidity_score(row_count)
    row = {
        "contract": contract,
        "row_count": row_count,
        "missing_ratio": missing_ratio,
        "liquidity_score": liquidity_score,
        "eligible": None,
    }
    return apply_profile_to_contract_row(row, universe_profile)


def build_coverage_manifest(
    *,
    repo_root: Path,
    rithmic_week: str,
    universe_profile: str,
    extra_roots: Iterable[Path] | None = None,
) -> dict[str, Any]:
    """Build weekly coverage manifest from discovered Rithmic roots."""
    roots = discover_rithmic_weekly_roots(
        repo_root, rithmic_week, extra_roots=extra_roots
    )
    expected_trading_days = _iso_week_trading_days(rithmic_week)
    contract_dirs: dict[str, list[Path]] = {}
    for root in roots:
        if not root["exists"]:
            continue
        discovered = _discover_contract_dirs(Path(root["path"]))
        for contract, dirs in discovered.items():
            contract_dirs.setdefault(contract, []).extend(dirs)

    contracts = filter_contracts_for_profile(sorted(contract_dirs), universe_profile)
    contract_rows = [
        _build_contract_row(
            contract,
            contract_dirs[contract],
            expected_trading_days=expected_trading_days,
            universe_profile=universe_profile,
        )
        for contract in contracts
    ]
    missing_values = [
        row["missing_ratio"]
        for row in contract_rows
        if row["missing_ratio"] is not None
    ]
    mean_missing = (
        sum(missing_values) / len(missing_values) if missing_values else None
    )
    eligible_count = sum(1 for row in contract_rows if row.get("eligible") is True)
    total_rows = sum(int(row.get("row_count") or 0) for row in contract_rows)

    return {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "lane": "continuous",
        "rithmic_week": rithmic_week,
        "universe_profile": universe_profile,
        "roots": roots,
        "contracts": contracts,
        "data_types": _discover_data_types(contract_dirs),
        "contract_rows": contract_rows,
        "summary": {
            "total_contracts": len(contracts),
            "eligible_contracts": eligible_count,
            "total_rows": total_rows,
            "mean_missing_ratio": mean_missing,
            "expected_trading_days": expected_trading_days,
        },
    }


def build_coverage_manifest_stub(
    *,
    repo_root: Path,
    rithmic_week: str,
    universe_profile: str,
) -> dict[str, Any]:
    """Backward-compatible alias: discovery-backed manifest builder."""
    return build_coverage_manifest(
        repo_root=repo_root,
        rithmic_week=rithmic_week,
        universe_profile=universe_profile,
    )


def write_coverage_manifest(repo_root: Path, manifest: dict[str, Any]) -> Path:
    week = str(manifest["rithmic_week"])
    path = coverage_manifest_path(repo_root, week)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path
