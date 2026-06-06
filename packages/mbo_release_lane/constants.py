"""MBO release lane constants."""

from __future__ import annotations

PARSER_VERSION = "1.0.0"
MBO_SCHEMA = "mbo"
SOURCE_VENDOR = "databento"
DEFAULT_DATASET_ID = "GLBX.MDP3"

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
