"""ET session windows and per-session Databento download."""
from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from equities_lane.src.types import DecadalSession

ET = ZoneInfo("America/New_York")


def collect_download_specs(config_path: str | Path) -> list[dict[str, Any]]:
    from equities_lane.src.config_loader import load_universe

    _, universe, paths = load_universe(config_path)
    jobs: list[dict[str, Any]] = []
    for session in universe.sessions:
        jobs.append(
            {
                "session_id": session.id,
                "symbol": session.symbol,
                "date": session.date,
                "dataset": universe.databento.dataset,
                "schema_primary": universe.databento.schema_primary,
                "schema_degraded": universe.databento.schema_degraded,
                "output_dir": str(paths["raw_root"]),
            }
        )
    return jobs


def download_session(
    config_path: str | Path,
    symbol: str,
    session_date: str,
    *,
    use_mbo: bool = False,
) -> Path:
    return download_session_legacy(config_path, symbol, session_date, use_mbo=use_mbo)


def session_window_utc(session: DecadalSession) -> tuple[datetime, datetime]:
    """Premarket start through session end in US/Eastern, returned as UTC."""
    d = session.date
    y, m, day = (int(x) for x in d.split("-"))
    ph, pm = (int(x) for x in session.premarket_start.split(":"))
    eh, em = (int(x) for x in session.session_end.split(":"))
    start_et = datetime(y, m, day, ph, pm, tzinfo=ET)
    end_et = datetime(y, m, day, eh, em, tzinfo=ET)
    return start_et.astimezone(ZoneInfo("UTC")), end_et.astimezone(ZoneInfo("UTC"))


def daily_window_utc(session: DecadalSession, lookback_days: int) -> tuple[datetime, datetime]:
    from datetime import timedelta

    end_start, _ = session_window_utc(session)
    start = end_start - timedelta(days=lookback_days)
    return start, end_start


def download_decadal_session(
    session: DecadalSession,
    raw_root: Path,
    *,
    override_hard_limit: bool = False,
    override_operating_cap: bool = False,
) -> Path:
    if not os.getenv("DATABENTO_API_KEY"):
        raise RuntimeError("DATABENTO_API_KEY must be set for download")

    from data_system.src.databento_client import DatabentoResearchClient

    start_utc, end_utc = session_window_utc(session)
    sym = session.resolved_symbol or session.symbol
    raw_root.mkdir(parents=True, exist_ok=True)
    dest = raw_root / session.raw_filename()

    if dest.exists() and dest.stat().st_size > 0:
        return dest

    client = DatabentoResearchClient()
    event_id = f"decadal_{session.id}_{sym}_{session.date}".replace(".", "_")
    client.download_event_window(
        event_id=event_id,
        symbols=[sym],
        start_utc=start_utc,
        end_utc=end_utc,
        dataset=session.dataset,
        schema=session.schema,
        stype_in=session.stype_in,
        requested_symbol=session.symbol,
        output_path=str(dest),
        override_hard_limit=override_hard_limit,
        override_operating_cap=override_operating_cap,
    )
    return dest


def download_session_legacy(
    config_path: str | Path,
    symbol: str,
    session_date: str,
    *,
    use_mbo: bool = False,
    dataset: str | None = None,
    stype_in: str | None = None,
) -> Path:
    """Legacy single-session download via universe.yaml config."""
    from equities_lane.src.config_loader import load_universe

    _, universe, paths = load_universe(config_path)
    db = universe.databento
    use_mbo = use_mbo or universe.l3_only
    if universe.l3_only and not use_mbo:
        raise ValueError("L3-only lane: download requires MBO schema (--mbo)")
    session = DecadalSession(
        id=f"legacy_{symbol}_{session_date}",
        symbol=symbol,
        date=session_date,
        dataset=dataset or db.dataset,
        schema=db.schema_primary if use_mbo else db.schema_degraded,
        stype_in=stype_in or db.stype_in,
        premarket_start=db.premarket_start,
        session_end=db.session_end,
    )
    return download_decadal_session(session, paths["raw_root"])
