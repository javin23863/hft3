"""Point-in-time Databento event-window download (never multi-year range)."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Iterable, List, Optional, Tuple

from data_system.src.npz_resolver import candidate_npz_symbols
from workbench.src.data.event_catalog import EventSpec
logger = logging.getLogger(__name__)


def _candidate_download_symbols(requested_symbol: str, parsed_symbols: tuple[str, ...]) -> List[str]:
    return candidate_npz_symbols(requested_symbol, parsed_symbols)


def _is_symbology_error(exc: Exception) -> bool:
    msg = str(exc).lower()
    if "symbology" in msg:
        return True
    if "422" in msg and ("symbol" in msg or "continuous" in msg):
        return True
    name = type(exc).__name__
    return "Bento" in name and ("422" in msg or "symbology" in msg)


def _as_datetime(value):
    if hasattr(value, "to_pydatetime"):
        return value.to_pydatetime()
    return value


def resolve_download_symbol(client, ev: EventSpec) -> Tuple[str, float]:
    """Pick Databento symbol for download; ES fallback when MES symbology fails (handoff PDF §8)."""
    start = _as_datetime(ev.start_utc)
    end = _as_datetime(ev.end_utc)
    parsed = ev.parsed_symbols or (ev.symbol,)
    last_err: Optional[Exception] = None
    for sym in _candidate_download_symbols(ev.symbol, parsed):
        try:
            cost = float(
                client.client.metadata.get_cost(
                    dataset="GLBX.MDP3",
                    schema="mbo",
                    symbols=[sym],
                    stype_in="continuous",
                    start=start,
                    end=end,
                )
            )
            if sym != ev.symbol:
                logger.info(
                    "symbol fallback %s -> %s for %s",
                    ev.symbol,
                    sym,
                    ev.event_id,
                )
            return sym, cost
        except Exception as exc:
            if not _is_symbology_error(exc):
                raise
            last_err = exc
            continue
    raise RuntimeError(f"symbology failed for {ev.event_id}: {last_err}")


def estimate_download_cost_usd(events: Iterable[EventSpec]) -> float:
    """Sum Databento get_cost for missing event windows (0 if no API key)."""
    import os

    if not os.getenv("DATABENTO_API_KEY"):
        return 0.0
    try:
        from data_system.src.databento_client import DatabentoResearchClient

        client = DatabentoResearchClient()
    except ValueError:
        return 0.0

    total = 0.0
    unpriced = 0
    for ev in events:
        if ev.npz_present:
            continue
        try:
            _, cost = resolve_download_symbol(client, ev)
            total += cost
        except RuntimeError as exc:
            unpriced += 1
            logger.warning("%s", exc)
        except Exception as exc:
            unpriced += 1
            logger.warning("cost estimate failed for %s: %s", ev.event_id, exc)
    if unpriced:
        logger.warning("%d events could not be priced", unpriced)
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
        start = _as_datetime(ev.start_utc)
        end = _as_datetime(ev.end_utc)
        try:
            sym_used, _ = resolve_download_symbol(client, ev)
            dbn_path = client.download_event_window(
                event_id=ev.event_id,
                symbols=[sym_used],
                start_utc=start,
                end_utc=end,
                requested_symbol=ev.symbol,
            )
            converter.convert_file(dbn_path, sym_used)
            done.append(ev.event_id)
        except Exception as exc:
            logger.error("download failed for %s: %s", ev.event_id, exc)
            continue
    return done


def missing_for_campaign(
    repo_root: Path,
    model_id: str,
    symbol: str,
) -> List[EventSpec]:
    from workbench.src.data.event_catalog import list_campaign_events, load_periods

    missing: List[EventSpec] = []
    for period in load_periods(repo_root):
        for ev in list_campaign_events(model_id, period, symbol, repo_root):
            if not ev.npz_present:
                missing.append(ev)
    return missing
