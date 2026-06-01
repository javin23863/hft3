"""Budget-gated Databento download for options parity legs."""
from __future__ import annotations

import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from options_lane.src.config_loader import load_universe
from options_lane.src.models import LegSpec, ParityGroup


def collect_download_specs(config_path: str | Path) -> list[dict[str, Any]]:
    """Return unique download jobs from parity universe (no hardcoded symbols)."""
    _, groups, paths = load_universe(config_path)
    seen: set[tuple[str, str, str]] = set()
    jobs: list[dict[str, Any]] = []
    for group in groups:
        for leg in group.legs:
            if not leg.dataset:
                continue
            key = (leg.dataset, leg.symbol, leg.role)
            if key in seen:
                continue
            seen.add(key)
            jobs.append(
                {
                    "group_id": group.id,
                    "role": leg.role,
                    "symbol": leg.symbol,
                    "dataset": leg.dataset,
                    "output_dir": str(paths["raw_root"]),
                }
            )
    return jobs


def download_leg_window(
    leg: LegSpec,
    start_utc: datetime,
    end_utc: datetime,
    output_dir: Path,
    schema: str = "mbp-1",
    stype_in: str = "continuous",
) -> Path:
    """
    Download one leg via DatabentoResearchClient budget gate.
    Requires DATABENTO_API_KEY; writes under data/options/raw only.
    """
    from data_system.src.databento_client import DatabentoResearchClient

    client = DatabentoResearchClient()
    event_id = f"parity_{leg.role}_{leg.symbol.replace('.', '_')}"
    result_path = client.download_event_window(
        event_id=event_id,
        symbols=[leg.symbol],
        start_utc=start_utc,
        end_utc=end_utc,
        dataset=leg.dataset or "GLBX.MDP3",
        schema=schema,
        stype_in=stype_in,
    )
    src = Path(result_path)
    output_dir.mkdir(parents=True, exist_ok=True)
    dest = output_dir / src.name
    if src.resolve() != dest.resolve():
        if dest.exists():
            dest.unlink()
        dest.write_bytes(src.read_bytes())
    return dest


def discover_groups(config_path: str | Path) -> list[ParityGroup]:
    _, groups, _ = load_universe(config_path)
    return groups
