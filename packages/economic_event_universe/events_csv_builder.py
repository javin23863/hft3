"""Build events.csv from catalog windows — no event-type or filename allowlists."""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any

from economic_event_universe.registry import get_event_def
from economic_event_universe.window_catalog import CatalogWindow, iter_catalog_windows
from economic_event_universe.windows import download_window


def is_manual_events_csv_row(row: dict[str, Any]) -> bool:
    """Rows explicitly marked manual — survives calendar rebuilds."""
    if str(row.get("source", "")).strip().lower() == "manual":
        return True
    notes = str(row.get("notes", "")).strip().lower()
    return notes.startswith("manual session") or notes == "manual row"


def _resolve_year_range(
    repo_root: Path,
    start_year: int | None,
    end_year: int | None,
) -> tuple[int, int]:
    if start_year is not None and end_year is not None:
        return start_year, end_year
    from economic_event_universe.walk_forward_years import backtest_year_range

    wf_start, wf_end = backtest_year_range(repo_root)
    return start_year if start_year is not None else wf_start, end_year if end_year is not None else wf_end


def catalog_window_to_events_csv_row(window: CatalogWindow) -> dict[str, Any]:
    cfg = get_event_def(window.event_type)
    start_off, end_off = download_window(window.event_type)
    rt = window.release_time or str(cfg.get("anchor_time", "08:30:00"))
    tz = window.timezone or str(cfg.get("timezone", "America/New_York"))
    source = window.source or str(cfg.get("agency", ""))
    source_url = window.source_url or str(cfg.get("official_source_url", ""))
    notes = (
        "SEED_PLACEHOLDER: replace with sourced agency date before research use"
        if window.row_status == "SEED"
        else (
            f"{window.event_type} rule-based session window"
            if window.row_status == "RULE_BASED"
            else f"{window.event_type} from release calendar"
        )
    )
    return {
        "event_id": window.event_id,
        "event_type": window.event_type,
        "release_date": window.release_date,
        "release_time": rt,
        "timezone": tz,
        "window_name": window.window_name,
        "start_offset_seconds": start_off,
        "end_offset_seconds": end_off,
        "symbols": str(cfg.get("symbol_universe", "")),
        "priority": int(cfg.get("context_priority", 50)),
        "source": source,
        "source_url": source_url,
        "effective_date": "2018-01-01",
        "notes": notes,
    }


def iter_events_csv_rows(
    repo_root: Path,
    *,
    include_seed: bool = False,
    include_rule_based: bool = True,
    start_year: int | None = None,
    end_year: int | None = None,
) -> list[dict[str, Any]]:
    """Catalog windows → events.csv rows for any type with a calendar (SOURCED ± SEED ± rule-based)."""
    start_year, end_year = _resolve_year_range(repo_root, start_year, end_year)
    allowed_status = {"SOURCED", "SEED", "RULE_BASED"} if include_rule_based else {"SOURCED", "SEED"}
    if not include_seed:
        allowed_status.discard("SEED")
    windows = iter_catalog_windows(
        repo_root,
        include_seed=include_seed,
        include_rule_based=include_rule_based,
        start_year=start_year,
        end_year=end_year,
    )
    return [
        catalog_window_to_events_csv_row(w)
        for w in windows
        if w.row_status in allowed_status
    ]


def iter_sourced_events_csv_rows(
    repo_root: Path,
    *,
    start_year: int | None = None,
    end_year: int | None = None,
) -> list[dict[str, Any]]:
    return iter_events_csv_rows(
        repo_root,
        include_seed=False,
        include_rule_based=False,
        start_year=start_year,
        end_year=end_year,
    )


def events_csv_path(repo_root: Path) -> Path:
    return repo_root / "packages" / "data_system" / "config" / "events.csv"


def load_events_csv_event_ids(repo_root: Path) -> set[str]:
    path = events_csv_path(repo_root)
    if not path.is_file():
        return set()
    with path.open(newline="", encoding="utf-8") as f:
        return {str(row["event_id"]) for row in csv.DictReader(f)}


def iter_backtest_scope_windows(
    repo_root: Path,
    *,
    start_year: int | None = None,
    end_year: int | None = None,
) -> list[CatalogWindow]:
    """All SOURCED release_calendars windows in walk-forward years (events.csv authority)."""
    start_year, end_year = _resolve_year_range(repo_root, start_year, end_year)
    windows = iter_catalog_windows(
        repo_root,
        include_seed=False,
        include_rule_based=False,
        start_year=start_year,
        end_year=end_year,
    )
    return [w for w in windows if w.row_status == "SOURCED"]


def iter_campaign_scope_windows(
    repo_root: Path,
    *,
    start_year: int | None = None,
    end_year: int | None = None,
) -> list[CatalogWindow]:
    """Workbench campaign download scope — same as macro_releases (all sourced macro, no CPI/NFP filter)."""
    return iter_macro_releases_scope_windows(
        repo_root, start_year=start_year, end_year=end_year
    )


def iter_macro_releases_scope_windows(
    repo_root: Path,
    *,
    start_year: int | None = None,
    end_year: int | None = None,
) -> list[CatalogWindow]:
    """SOURCED macro release calendars excluding optional FED_SPEAKER noise."""
    return [
        w
        for w in iter_backtest_scope_windows(
            repo_root, start_year=start_year, end_year=end_year
        )
        if w.event_type != "FED_SPEAKER"
    ]


def resolve_download_scope_windows(
    repo_root: Path,
    scope: str,
    *,
    start_year: int | None = None,
    end_year: int | None = None,
    include_seed: bool = False,
    include_rule_based: bool = False,
) -> list[CatalogWindow]:
    """Resolve catalog windows for MBO download / cost estimate scopes."""
    if scope == "campaign":
        return iter_campaign_scope_windows(
            repo_root, start_year=start_year, end_year=end_year
        )
    if scope == "macro_releases":
        return iter_macro_releases_scope_windows(
            repo_root, start_year=start_year, end_year=end_year
        )
    if scope == "backtest":
        return iter_backtest_scope_windows(
            repo_root, start_year=start_year, end_year=end_year
        )
    if scope == "full_catalog":
        start_year, end_year = _resolve_year_range(repo_root, start_year, end_year)
        windows = iter_catalog_windows(
            repo_root,
            include_seed=include_seed,
            include_rule_based=include_rule_based,
            start_year=start_year,
            end_year=end_year,
        )
        return list(windows)
    raise ValueError(f"unknown download scope: {scope}")
