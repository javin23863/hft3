"""Point-in-time Databento event-window download (never multi-year range)."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Iterable, List, Optional, Tuple

from data_system.src.npz_resolver import candidate_npz_symbols
from workbench.src.data.event_catalog import EventSpec

logger = logging.getLogger(__name__)

FULL_UNIVERSE_SCOPE = "full_universe"
SOURCED_RUNNABLE_SCOPE = "sourced_runnable"
PROMOTION_CAMPAIGN_SCOPE = "promotion_campaign"

_SCOPE_ALIASES = {
    "full": FULL_UNIVERSE_SCOPE,
    "universe": FULL_UNIVERSE_SCOPE,
    "full_universe": FULL_UNIVERSE_SCOPE,
    "sourced": SOURCED_RUNNABLE_SCOPE,
    "runnable": SOURCED_RUNNABLE_SCOPE,
    "sourced_runnable": SOURCED_RUNNABLE_SCOPE,
    "campaign": PROMOTION_CAMPAIGN_SCOPE,
    "promotion": PROMOTION_CAMPAIGN_SCOPE,
    "promotion_campaign": PROMOTION_CAMPAIGN_SCOPE,
}


def normalize_universe_scope(scope: str | None) -> str:
    normalized = str(scope or FULL_UNIVERSE_SCOPE).strip().lower().replace("-", "_")
    if normalized not in _SCOPE_ALIASES:
        allowed = ", ".join(sorted({FULL_UNIVERSE_SCOPE, SOURCED_RUNNABLE_SCOPE, PROMOTION_CAMPAIGN_SCOPE}))
        raise ValueError(f"unknown universe scope {scope!r}; expected one of {allowed}")
    return _SCOPE_ALIASES[normalized]


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


def events_for_scope(
    repo_root: Path,
    model_id: str,
    symbol: str,
    *,
    universe_scope: str = FULL_UNIVERSE_SCOPE,
) -> list[tuple[str, EventSpec]]:
    from workbench.src.data.event_catalog import list_campaign_events, list_universe_events, load_periods

    scope = normalize_universe_scope(universe_scope)
    rows: list[tuple[str, EventSpec]] = []
    for period in load_periods(repo_root):
        if scope == PROMOTION_CAMPAIGN_SCOPE:
            events = list_campaign_events(model_id, period, symbol, repo_root)
        elif scope == SOURCED_RUNNABLE_SCOPE:
            events = list_universe_events(
                model_id,
                period,
                symbol,
                repo_root,
                include_seed=False,
                statuses={"SOURCED"},
            )
        elif scope == FULL_UNIVERSE_SCOPE:
            events = list_universe_events(model_id, period, symbol, repo_root, include_seed=True)
        else:
            raise AssertionError(f"unhandled universe scope {scope!r}")
        for ev in events:
            rows.append((period.name, ev))
    return rows


def missing_for_campaign(
    repo_root: Path,
    model_id: str,
    symbol: str,
    *,
    universe_scope: str = FULL_UNIVERSE_SCOPE,
) -> List[EventSpec]:
    return [
        ev
        for _, ev in events_for_scope(
            repo_root,
            model_id,
            symbol,
            universe_scope=universe_scope,
        )
        if not ev.npz_present
    ]


def summarize_event_specs(period_events: Iterable[tuple[str, EventSpec]]) -> dict:
    rows = list(period_events)
    by_status: dict[str, int] = {}
    by_type: dict[str, int] = {}
    missing_by_status: dict[str, int] = {}
    missing_by_type: dict[str, int] = {}
    model_ineligible = 0
    runnable_eligible = 0
    for _, ev in rows:
        status = ev.row_status or "UNKNOWN"
        by_status[status] = by_status.get(status, 0) + 1
        by_type[ev.event_type] = by_type.get(ev.event_type, 0) + 1
        if not ev.model_eligible:
            model_ineligible += 1
        if ev.runnable_eligible:
            runnable_eligible += 1
        if not ev.npz_present:
            missing_by_status[status] = missing_by_status.get(status, 0) + 1
            missing_by_type[ev.event_type] = missing_by_type.get(ev.event_type, 0) + 1
    return {
        "visible_events": len(rows),
        "visible_event_types": len(by_type),
        "npz_present": sum(1 for _, ev in rows if ev.npz_present),
        "missing_count": sum(1 for _, ev in rows if not ev.npz_present),
        "runnable_eligible_count": runnable_eligible,
        "model_ineligible_count": model_ineligible,
        "row_status_counts": dict(sorted(by_status.items())),
        "event_type_counts": dict(sorted(by_type.items())),
        "missing_by_row_status": dict(sorted(missing_by_status.items())),
        "missing_by_event_type": dict(sorted(missing_by_type.items())),
    }
