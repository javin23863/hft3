"""Databento connector — consolidated imports from data_system.

Usage:
    from hft3.connectors.databento import resolve_npz_for_event, DatabentoResearchClient
"""

from data_system.src.npz_resolver import (
    resolve_npz_for_event,
    npz_path_for,
    candidate_npz_symbols,
)
from data_system.src.event_data_resolver import (
    resolve_event_assets,
    resolve_mbo_npz_for_event,
    resolve_mbo_raw_for_event,
)
from data_system.src.data_roots import paid_data_root, npz_search_dirs
from data_system.src.databento_client import DatabentoResearchClient, BudgetManager
from data_system.src.events_parser import load_and_parse_events
from data_system.src.schema_resolver import resolve_schema, DataClassResolution

__all__ = [
    "resolve_npz_for_event",
    "npz_path_for",
    "candidate_npz_symbols",
    "resolve_event_assets",
    "resolve_mbo_npz_for_event",
    "resolve_mbo_raw_for_event",
    "paid_data_root",
    "npz_search_dirs",
    "DatabentoResearchClient",
    "BudgetManager",
    "load_and_parse_events",
    "resolve_schema",
    "DataClassResolution",
]
