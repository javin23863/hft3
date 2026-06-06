"""Single source of truth: 44 macro event types vs runnable events.csv / MBO coverage."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from economic_event_universe.calendar_io import sourced_event_types_in_dir
from economic_event_universe.registry import (
    catalog_event_type_count,
    catalog_event_types,
    default_cme_symbols,
    event_definitions,
)

MACRO_EVENT_TYPE_COUNT = catalog_event_type_count()


@dataclass(frozen=True)
class MacroCatalogSummary:
    catalog_event_types: int
    sourced_calendar_types: int
    events_csv_rows: int
    events_csv_types: int
    catalog_window_count: int
    npz_slots_present: int
    npz_slots_missing: int
    types_with_zero_windows: list[str]
    types_missing_from_events_csv: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "macro_event_type_count": self.catalog_event_types,
            "authority": "packages/economic_event_universe/config/event_universe.yaml",
            "sourced_calendar_type_count": self.sourced_calendar_types,
            "events_csv_row_count": self.events_csv_rows,
            "events_csv_type_count": self.events_csv_types,
            "catalog_window_count": self.catalog_window_count,
            "npz_slots_present": self.npz_slots_present,
            "npz_slots_missing": self.npz_slots_missing,
            "types_with_zero_windows": self.types_with_zero_windows,
            "types_missing_from_events_csv": self.types_missing_from_events_csv,
            "note": (
                f"There are {self.catalog_event_types} macro event types in the catalog. "
                f"events.csv ({self.events_csv_rows} rows, {self.events_csv_types} types) holds "
                "SOURCED release-calendar rows in walk-forward years — not the full catalog."
            ),
        }


def events_csv_summary(repo_root: Path) -> tuple[int, int, set[str]]:
    csv = repo_root / "packages" / "data_system" / "config" / "events.csv"
    if not csv.is_file():
        return 0, 0, set()
    from data_system.src.events_parser import load_and_parse_events

    df = load_and_parse_events(str(csv))
    types = {str(t) for t in df["event_type"].unique()}
    return len(df), len(types), types


def build_macro_catalog_summary(
    repo_root: Path,
    *,
    include_seed_calendars: bool = True,
    include_rule_based: bool = True,
    windows: list | None = None,
) -> MacroCatalogSummary:
    from economic_event_universe.window_catalog import (
        count_windows_by_type,
        iter_catalog_windows,
        npz_slot_coverage,
    )

    cal_dir = repo_root / "packages" / "data_system" / "config" / "release_calendars"
    sourced_types = sourced_event_types_in_dir(cal_dir)
    csv_rows, csv_types_count, csv_types = events_csv_summary(repo_root)
    if windows is None:
        windows = iter_catalog_windows(
            repo_root,
            include_seed=include_seed_calendars,
            include_rule_based=include_rule_based,
        )
    by_type = count_windows_by_type(windows)
    all_types = set(catalog_event_types())
    zero_windows = sorted(et for et in all_types if by_type.get(et, 0) == 0)
    missing_from_csv = sorted(all_types - csv_types)
    present, missing = npz_slot_coverage(repo_root, windows)

    return MacroCatalogSummary(
        catalog_event_types=MACRO_EVENT_TYPE_COUNT,
        sourced_calendar_types=len(sourced_types),
        events_csv_rows=csv_rows,
        events_csv_types=csv_types_count,
        catalog_window_count=len(windows),
        npz_slots_present=present,
        npz_slots_missing=missing,
        types_with_zero_windows=zero_windows,
        types_missing_from_events_csv=missing_from_csv,
    )


def format_catalog_banner(repo_root: Path | None = None) -> str:
    """One-screen truth for humans and agents."""
    from hft3_bootstrap import repo_root as default_root

    root = repo_root or default_root()
    cal_dir = root / "packages" / "data_system" / "config" / "release_calendars"
    sourced = sorted(sourced_event_types_in_dir(cal_dir))
    lines = [
        f"Macro event catalog: {MACRO_EVENT_TYPE_COUNT} event types "
        f"(authority: packages/economic_event_universe/config/event_universe.yaml)",
        f"  SOURCED release calendars ({len(sourced)} types): {', '.join(sourced) or '(none)'}",
        f"  events.csv mirrors SOURCED + rule-based types in walk-forward years",
        f"  CME default symbols ({len(default_cme_symbols())}): {', '.join(default_cme_symbols())}",
        "",
        "events.csv mirrors SOURCED calendars + rule-based session types in walk-forward years.",
        "Sync all calendars: python tools/economic_event_universe/sync_all_calendars.py --rebuild-events",
        "All-scopes MBO cost: python scripts/estimate_full_macro_mbo_cost.py --all-scopes --estimate",
        "Coverage audit: python scripts/audit_all_research_data.py",
    ]
    return "\n".join(lines)
