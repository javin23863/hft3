"""Pull and normalize OPRA options chains time-aligned to equity sessions."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pandas as pd

from equities_lane.src.ingest.databento_equities import session_window_utc
from equities_lane.src.ingest.session_io import load_session
from equities_lane.src.options.chain_resolver import reference_price_from_meta, resolve_pull_symbols
from equities_lane.src.types import DecadalCatalog, DecadalSession


def options_paths(catalog: DecadalCatalog) -> dict[str, Path]:
    root = catalog.repo_root / "data" / "options" / "equity_chains"
    return {
        "raw_root": root / "raw",
        "normalized_root": root / "normalized",
    }


def estimate_options_cost(session: DecadalSession) -> float | None:
    symbols, stype_in, schema = resolve_pull_symbols(session)
    if not symbols or not session.options:
        return None
    if not os.getenv("DATABENTO_API_KEY"):
        raise RuntimeError("DATABENTO_API_KEY must be set for estimate")
    from data_system.src.databento_client import DatabentoResearchClient

    start_utc, end_utc = session_window_utc(session)
    client = DatabentoResearchClient()
    return client.estimate_cost(
        symbols,
        start_utc,
        end_utc,
        dataset=session.options.dataset,
        schema=schema,
        stype_in=stype_in,
    )


def _read_equity_meta(catalog: DecadalCatalog, session: DecadalSession) -> tuple[float | None, float | None]:
    norm = catalog.paths["normalized_root"] / session.normalized_filename()
    if not norm.exists():
        return None, None
    meta, _ = load_session(norm)
    return meta.prior_close, meta.premarket_open


def pull_options_chain(
    catalog: DecadalCatalog,
    session: DecadalSession,
    *,
    override_hard_limit: bool = False,
    override_operating_cap: bool = False,
    refresh: bool = False,
) -> dict[str, Any]:
    """Pull OPRA chain for session window; return factual options block for manifest v2."""
    opts = session.options
    paths = options_paths(catalog)
    paths["raw_root"].mkdir(parents=True, exist_ok=True)
    paths["normalized_root"].mkdir(parents=True, exist_ok=True)

    start_utc, end_utc = session_window_utc(session)
    result: dict[str, Any] = {
        "dataset": opts.dataset if opts else None,
        "schema": opts.schema if opts else None,
        "window_start_utc": start_utc.isoformat(),
        "window_end_utc": end_utc.isoformat(),
        "pull_error": None,
    }

    if opts is None or not opts.enabled:
        result["pull_error"] = "options disabled in catalog"
        return result

    symbols, stype_in, schema = resolve_pull_symbols(session)
    if not symbols:
        result["pull_error"] = "no symbols resolved"
        return result

    prior, pm = _read_equity_meta(catalog, session)
    result["reference_price"] = reference_price_from_meta(
        session, prior_close=prior, premarket_open=pm
    )

    raw_dir = paths["raw_root"] / session.id
    norm_path = paths["normalized_root"] / f"{session.id}.ndjson"
    raw_file = raw_dir / f"{session.id}_{schema}.dbn.zst"

    if norm_path.exists() and norm_path.stat().st_size > 0 and not refresh:
        result["raw_dir"] = str(raw_dir)
        result["normalized_path"] = str(norm_path)
        result["resolved_symbol_count"] = _count_ndjson_lines(norm_path)
        return result

    if raw_file.exists() and not refresh:
        try:
            count = normalize_options_dbn(
                raw_file,
                norm_path,
                session_id=session.id,
                underlying=session.symbol,
            )
            result["raw_dir"] = str(raw_dir)
            result["normalized_path"] = str(norm_path)
            result["resolved_symbol_count"] = count
        except Exception as exc:
            result["pull_error"] = str(exc)
        return result

    if not os.getenv("DATABENTO_API_KEY"):
        raise RuntimeError("DATABENTO_API_KEY must be set")

    from data_system.src.databento_client import DatabentoResearchClient

    raw_dir.mkdir(parents=True, exist_ok=True)
    client = DatabentoResearchClient()
    event_id = f"decadal_options_{session.id}".replace(".", "_")
    try:
        cost = estimate_options_cost(session)
        if refresh and raw_file.exists():
            raw_file.unlink()
        client.download_event_window(
            event_id=event_id,
            symbols=symbols,
            start_utc=start_utc,
            end_utc=end_utc,
            dataset=opts.dataset,
            schema=schema,
            stype_in=stype_in,
            requested_symbol=session.symbol,
            output_path=str(raw_file),
            override_hard_limit=override_hard_limit,
            override_operating_cap=override_operating_cap,
        )
        count = normalize_options_dbn(
            raw_file,
            norm_path,
            session_id=session.id,
            underlying=session.symbol,
        )
        result["raw_dir"] = str(raw_dir)
        result["normalized_path"] = str(norm_path)
        result["resolved_symbol_count"] = count
        result["options_cost_usd"] = cost
    except Exception as exc:
        result["pull_error"] = str(exc)
    return result


def normalize_options_dbn(
    raw_path: Path,
    output_path: Path,
    *,
    session_id: str,
    underlying: str,
) -> int:
    """Normalize OPRA cbbo-1m DBN to NDJSON (bar receive time as quote_ts_ns)."""
    import databento as db
    from datetime import date, datetime, timezone

    store = db.DBNStore.from_file(str(raw_path))
    imap = store._instrument_map
    imap.insert_metadata(store.metadata)

    lines: list[str] = []
    for rec in store:
        ts_ns = _cbbo_quote_ts_ns(rec)
        if ts_ns <= 0:
            continue
        quote_date = datetime.fromtimestamp(ts_ns / 1e9, tz=timezone.utc).date()
        symbol = imap.resolve(rec.instrument_id, quote_date)
        if not symbol:
            continue
        if not rec.levels:
            continue
        lvl = rec.levels[0]
        bid = _fixed_price(lvl.bid_px)
        ask = _fixed_price(lvl.ask_px)
        strike, right, expiry = parse_opra_symbol(symbol)
        row = {
            "session_id": session_id,
            "underlying": underlying,
            "quote_ts_ns": ts_ns,
            "symbol": symbol.strip(),
            "strike": strike,
            "right": right,
            "expiry": expiry,
            "bid": bid,
            "ask": ask,
        }
        lines.append(json.dumps(row))

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
    return len(lines)


def parse_opra_symbol(symbol: str) -> tuple[float | None, str | None, str | None]:
    """Parse OPRA option symbol into strike, right, expiry (YYYY-MM-DD)."""
    import re

    m = re.match(r"^([A-Z0-9 ]+)\s+(\d{6})([CP])(\d{8})$", symbol.strip())
    if not m:
        return None, None, None
    yymmdd, right, strike_raw = m.group(2), m.group(3), m.group(4)
    yy, mm, dd = int(yymmdd[:2]), int(yymmdd[2:4]), int(yymmdd[4:6])
    expiry = f"20{yy:02d}-{mm:02d}-{dd:02d}"
    strike = int(strike_raw) / 1000.0
    return strike, right, expiry


def _cbbo_quote_ts_ns(rec) -> int:
    """cbbo-1m bars use ts_recv when exchange ts_event is unset (UINT64_MAX)."""
    ts_event = int(getattr(rec, "ts_event", 0))
    if ts_event > 0 and ts_event < (1 << 63):
        return ts_event
    return int(getattr(rec, "ts_recv", 0))


def _fixed_price(raw: int) -> float:
    if raw is None or raw <= 0:
        return 0.0
    return float(raw) / 1e9


def _count_ndjson_lines(path: Path) -> int:
    if not path.exists():
        return 0
    return sum(1 for line in path.read_text(encoding="utf-8").splitlines() if line.strip())
