"""Zone aggregators — each builds one dashboard zone from existing artifacts.

ZONES maps a zone name to its builder. The file-watcher and REST routes both
go through this table so a new zone is wired in one place.
"""
from __future__ import annotations

from . import alerts, autonomy, esq, lifecycle, models, options, pipeline, portfolio, system

ZONES = {
    "pipeline": pipeline.build,
    "portfolio": portfolio.build,
    "models": models.build,
    "options": options.build,
    "lifecycle": lifecycle.build,
    "autonomy": autonomy.build,
    "system": system.build,
    "alerts": alerts.build,
    "esq": esq.build,
}

__all__ = ["ZONES", "pipeline", "portfolio", "models", "options", "lifecycle", "autonomy", "system", "alerts", "esq"]
