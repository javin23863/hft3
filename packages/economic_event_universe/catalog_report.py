"""Single source of truth: 44 macro event types vs runnable events.csv / MBO coverage."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from economic_event_universe.registry import (
    catalog_event_type_count,
    catalog_event_types,
    default_cme_symbols,
    event_definitions,
    research_ready_types,
)

# Plain label for docs, audits, and agent rules — always use this count.
MACRO_EVENT_TYPE_COUNT = catalog_event_type_count()


@dataclass(frozen=True)
class MacroCatalogSummary:
    catalog_event_types: int
    research_ready_types: int
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
            "research_ready_type_count": self.research_ready_types,
            "research_ready_types": research_ready_types(),
            "events_csv_row_count": self.events_csv_rows,
            "events_csv_type_count": self.events_csv_types,
            "catalog_window_count": self.catalog_window_count,
            "npz_slots_present": self.npz_slots_present,
            "npz_slots_missing": self.npz_slots_missing,
            "types_with_zero_windows": self.types_with_zero_windows,
            "types_missing_from_events_csv": self.types_missing_from_events_csv,
            "note": (
                f"There are {self.catalog_event_types} macro event types in the catalog. "
                f"events.csv ({self.events_csv_rows} rows, {self.events_csv_types} types) is only the "
                "research-ready runnable subset — not the full universe."
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
) -> MacroCatalogSummary:
    from economic_event_universe.window_catalog import (
        count_windows_by_type,
        iter_catalog_windows,
        npz_slot_coverage,
    )

    csv_rows, csv_types_count, csv_types = events_csv_summary(repo_root)
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
        research_ready_types=len(research_ready_types()),
        events_csv_rows=csv_rows,
        events_csv_types=csv_types_count,
        catalog_window_count=len(windows),
        npz_slots_present=present,
        npz_slots_missing=missing,
        types_with_zero_windows=zero_windows,
        types_missing_from_events_csv=missing_from_csv,
    )


def format_catalog_banner() -> str:
    """One-screen truth for humans and agents."""
    ready = research_ready_types()
    lines = [
        f"Macro event catalog: {MACRO_EVENT_TYPE_COUNT} event types "
        f"(authority: packages/economic_event_universe/config/event_universe.yaml)",
        f"  Research-ready (may appear in events.csv): {len(ready)} — {', '.join(ready)}",
        f"  CME default symbols ({len(default_cme_symbols())}): {', '.join(default_cme_symbols())}",
        "",
        "events.csv is NOT the full catalog — it holds sourced, runnable rows only.",
        "Cost / MBO coverage: python scripts/estimate_full_macro_mbo_cost.py --estimate",
        "Coverage audit: python scripts/audit_all_research_data.py",
    ]
    return "\n".join(lines)
