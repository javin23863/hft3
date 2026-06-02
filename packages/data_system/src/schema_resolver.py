"""Resolve requested vs available Databento schemas with explicit downgrade records."""

from __future__ import annotations

from typing import List, Optional

from features_engine.src.imbalance.classification import DataClassResolution, resolve_data_class


def resolve_schema(
    requested_schema: str,
    *,
    available_schema: Optional[str] = None,
    asset_class: str = "",
    symbols: Optional[List[str]] = None,
    dates: Optional[List[str]] = None,
) -> DataClassResolution:
    return resolve_data_class(
        requested_schema,
        available_schema=available_schema or requested_schema,
        asset_class=asset_class,
        symbols=symbols,
        dates=dates,
    )
