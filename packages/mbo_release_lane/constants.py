"""MBO release lane constants."""

from __future__ import annotations

PARSER_VERSION = "1.0.0"
MBO_SCHEMA = "mbo"
SOURCE_VENDOR = "databento"
DEFAULT_DATASET_ID = "GLBX.MDP3"

# Skipped by download_mbo_release_data.py unless --include-event-type overrides.
DEFAULT_DOWNLOAD_EXCLUDE_EVENT_TYPES: tuple[str, ...] = ("FACTORY_ORDERS",)

# Tier 1–3 priority macro + UNEMPLOYMENT_CLAIMS (see --priority-events).
PRIORITY_DOWNLOAD_EVENT_TYPES: tuple[str, ...] = (
    # Tier 1
    "CPI",
    "NFP",
    "FOMC_PRESS",
    "FOMC_STATEMENT",
    "PROP_FLATTEN_TOPSTEP",
    # Tier 2
    "FOMC_MINUTES",
    "CORE_CPI",
    "CORE_PCE",
    "PCE",
    "GDP_ADVANCE",
    "GDP_SECOND",
    "GDP_FINAL",
    "RETAIL_SALES",
    "ISM_MANUFACTURING",
    "ISM_SERVICES",
    "JOLTS",
    # Tier 3
    "FED_H41",
    "FED_BEIGE_BOOK",
    "TREASURY_AUCTION",
    "TREASURY_REFUNDING",
    "IMPORT_PRICES",
    "EXPORT_PRICES",
    # Weekly labor
    "UNEMPLOYMENT_CLAIMS",
)

LIFECYCLE_ACTIONS = frozenset(
    {
        "add",
        "cancel",
        "modify",
        "trade",
        "fill",
        "partial_fill",
        "full_fill",
        "delete",
        "replace",
    }
)
