"""Point-in-time Databento event-window download (never multi-year range)."""

from __future__ import annotations

from pathlib import Path
from typing import Iterable, List, Optional

from workbench.src.data.event_catalog import EventSpec


def estimate_download_cost_usd(events: Iterable[EventSpec]) -> float:
    """Sum Databento get_cost for missing event windows (0 if no API key or estimate fails)."""
    import os

    if not os.getenv("DATABENTO_API_KEY"):
        return 0.0
    try:
        from data_system.src.databento_client import DatabentoResearchClient

        client = DatabentoResearchClient()
    except ValueError:
        return 0.0

    total = 0.0
    for ev in events:
        if ev.npz_present:
            continue
        start = ev.start_utc
        end = ev.end_utc
        if hasattr(start, "to_pydatetime"):
            start = start.to_pydatetime()
        if hasattr(end, "to_pydatetime"):
            end = end.to_pydatetime()
        try:
            total += float(
                client.client.metadata.get_cost(
                    dataset="GLBX.MDP3",
                    schema="mbo",
                    symbols=[ev.symbol],
                    stype_in="continuous",
                    start=start,
                    end=end,
                )
            )
        except Exception:
            continue
    return total


def download_events(
    repo_root: Path,
    events: Iterable[EventSpec],
    *,
    max_cost_usd: Optional[float] = None,
) -> List[str]:
    """Download missing event windows via Databento; convert to data/npz/."""
    from backtest_pipeline.src.converter import DatabentoConverter
    from data_system.src.databento_client import DatabentoResearchClient

    pending = [ev for ev in events if not ev.npz_present]
    if max_cost_usd is not None and pending:
        est = estimate_download_cost_usd(pending)
        if est > max_cost_usd:
            raise RuntimeError(f"Estimated cost ${est:.2f} exceeds max ${max_cost_usd:.2f}")

    client = DatabentoResearchClient()
    converter = DatabentoConverter(str(repo_root / "data" / "npz"))
    done: List[str] = []
    for ev in pending:
        start = ev.start_utc
        end = ev.end_utc
        if hasattr(start, "to_pydatetime"):
            start = start.to_pydatetime()
        if hasattr(end, "to_pydatetime"):
            end = end.to_pydatetime()
        dbn_path = client.download_event_window(
            event_id=ev.event_id,
            symbols=[ev.symbol],
            start_utc=start,
            end_utc=end,
        )
        converter.convert_file(dbn_path, ev.symbol)
        done.append(ev.event_id)
    return done


def missing_for_campaign(
    repo_root: Path,
    model_id: str,
    symbol: str,
) -> List[EventSpec]:
    from decision_engine.python.src.walk_forward import WalkForwardValidator
    from workbench.src.data.event_catalog import list_campaign_events, load_periods

    missing: List[EventSpec] = []
    for period in load_periods(repo_root):
        for ev in list_campaign_events(model_id, period, symbol, repo_root):
            if not ev.npz_present:
                missing.append(ev)
    return missing
