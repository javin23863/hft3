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


def _iso_week_trading_days(rithmic_week: str) -> int:
    match = _WEEK_LABEL_RE.match(rithmic_week.strip())
    if not match:
        return 5
    year, week = int(match.group(1)), int(match.group(2))
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


def _contract_row_count(contract_dir: Path) -> int:
    events_path = contract_dir / _EVENTS_FILENAME
    if events_path.is_file():
        return _count_ndjson_rows(events_path)
    total = 0
    for path in contract_dir.rglob("*.ndjson"):
        if path.is_file():
            total += _count_ndjson_rows(path)
    return total


def _contract_days_with_data(contract_dir: Path) -> int:
    days: set[str] = set()
    for path in contract_dir.rglob(_EVENTS_FILENAME):
        for parent in path.parents:
            name = parent.name
            if len(name) == 10 and name[4] == "-" and name[7] == "-":
                try:
                    datetime.strptime(name, "%Y-%m-%d")
                except ValueError:
                    continue
                else:
                    days.add(name)
                    break
    if days:
        return len(days)
    return 1 if _contract_row_count(contract_dir) > 0 else 0


def _discover_contract_dirs(week_root: Path) -> dict[str, Path]:
    contracts: dict[str, Path] = {}
    if not week_root.is_dir():
        return contracts
    for child in sorted(week_root.iterdir()):
        if not child.is_dir():
            continue
        name = child.name
        if name.startswith("."):
            continue
        if len(name) == 10 and name[4] == "-" and name[7] == "-":
            for sym_dir in sorted(child.iterdir()):
                if sym_dir.is_dir() and not sym_dir.name.startswith("."):
                    contracts.setdefault(sym_dir.name, sym_dir)
            continue
        contracts.setdefault(name, child)
    return contracts


def discover_rithmic_weekly_roots(
    repo_root: Path,
    rithmic_week: str,
    *,
    extra_roots: Iterable[Path] | None = None,
) -> list[dict[str, Any]]:
    """Locate weekly Rithmic filesystem roots for *rithmic_week*."""
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
    contract_dir: Path,
    *,
    expected_trading_days: int,
    universe_profile: str,
) -> dict[str, Any]:
    row_count = _contract_row_count(contract_dir)
    days_with_data = _contract_days_with_data(contract_dir)
    if expected_trading_days <= 0:
        missing_ratio = None
    elif row_count <= 0:
        missing_ratio = 1.0
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
    contract_dirs: dict[str, Path] = {}
    for root in roots:
        if not root["exists"]:
            continue
        contract_dirs.update(_discover_contract_dirs(Path(root["path"])))

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
        "data_types": list(_DEFAULT_DATA_TYPES),
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
