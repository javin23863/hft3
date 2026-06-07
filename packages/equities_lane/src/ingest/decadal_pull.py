"""Batch decadal catalog pull: estimate, MBO, daily OHLCV, normalize, manifest."""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from equities_lane.src.decadal_config import get_decadal_session, load_decadal_catalog
from equities_lane.src.ingest.daily_bars_io import daily_coverage_calendar_days, load_daily_parquet
from equities_lane.src.ingest.databento_equities import (
    daily_window_utc,
    download_decadal_session,
    session_window_utc,
)
from equities_lane.src.ingest.normalize import normalize_dbn
from equities_lane.src.ingest.manifest_v2 import (
    load_manifest_v2,
    manifest_v2_path,
    migrate_v1_file,
    upsert_session,
    window_fields,
    write_manifest_v2,
)
from equities_lane.src.ingest.options_chain_pull import estimate_options_cost, pull_options_chain
from equities_lane.src.types import DecadalSession


def resolve_symbology(session: DecadalSession) -> str | None:
    """Resolve historical symbol via Databento symbology API."""
    if not os.getenv("DATABENTO_API_KEY"):
        return session.symbol
    try:
        import databento as db
    except ImportError:
        return session.symbol

    client = db.Historical(os.environ["DATABENTO_API_KEY"])
    start_utc, end_utc = session_window_utc(session)
    try:
        result = client.symbology.resolve(
            dataset=session.dataset,
            symbols=[session.symbol],
            stype_in=session.stype_in,
            stype_out="raw_symbol",
            start_date=start_utc.date(),
            end_date=end_utc.date(),
        )
        if result and session.symbol in result:
            mapped = result[session.symbol]
            if isinstance(mapped, list) and mapped:
                return str(mapped[0])
            if isinstance(mapped, str):
                return mapped
    except Exception:
        pass
    return session.symbol


def estimate_session_cost(session: DecadalSession) -> float:
    if not os.getenv("DATABENTO_API_KEY"):
        raise RuntimeError("DATABENTO_API_KEY must be set for estimate")
    from data_system.src.databento_client import DatabentoResearchClient

    sym = session.resolved_symbol or session.symbol
    start_utc, end_utc = session_window_utc(session)
    client = DatabentoResearchClient()
    return client.estimate_cost(
        [sym],
        start_utc,
        end_utc,
        dataset=session.dataset,
        schema=session.schema,
        stype_in=session.stype_in,
    )


def estimate_daily_cost(session: DecadalSession, lookback_days: int) -> float:
    if not os.getenv("DATABENTO_API_KEY"):
        raise RuntimeError("DATABENTO_API_KEY must be set for estimate")
    from data_system.src.databento_client import DatabentoResearchClient

    sym = session.resolved_symbol or session.symbol
    start_utc, end_utc = daily_window_utc(session, lookback_days)
    client = DatabentoResearchClient()
    return client.estimate_cost(
        [sym],
        start_utc,
        end_utc,
        dataset=session.dataset,
        schema="ohlcv-1d",
        stype_in=session.stype_in,
    )


def estimate_catalog_cost(
    catalog_path: str | Path,
    *,
    session_id: str | None = None,
) -> list[dict[str, Any]]:
    catalog = load_decadal_catalog(catalog_path)
    sessions = catalog.sessions
    if session_id:
        sessions = [get_decadal_session(catalog, session_id)]
    rows: list[dict[str, Any]] = []
    for s in sessions:
        if s.skip_pull:
            rows.append(
                {
                    "session_id": s.id,
                    "symbol": s.symbol,
                    "date": s.date,
                    "skip_pull": True,
                    "skip_reason": s.skip_reason,
                }
            )
            continue
        resolved = resolve_symbology(s)
        s.resolved_symbol = resolved
        row: dict[str, Any] = {
            "session_id": s.id,
            "symbol": s.symbol,
            "resolved_symbol": resolved,
            "date": s.date,
            "dataset": s.dataset,
            "schema": s.schema,
        }
        try:
            mbo_cost = estimate_session_cost(s)
            daily_cost = estimate_daily_cost(s, catalog.daily_lookback_days)
            row["mbo_cost_usd"] = mbo_cost
            row["daily_cost_usd"] = daily_cost
            row["total_cost_usd"] = mbo_cost + daily_cost
            if s.options and s.options.enabled:
                opt_cost = estimate_options_cost(s)
                row["options_cost_usd"] = opt_cost
                row["total_cost_usd"] = row["total_cost_usd"] + (opt_cost or 0)
        except Exception as exc:
            row["estimate_error"] = str(exc)
            row["total_cost_usd"] = None
        rows.append(row)
    return rows


def pull_daily_history(
    session: DecadalSession,
    daily_root: Path,
    lookback_days: int,
    *,
    override_hard_limit: bool = False,
    override_operating_cap: bool = False,
    refresh: bool = False,
) -> Path:
    if not os.getenv("DATABENTO_API_KEY"):
        raise RuntimeError("DATABENTO_API_KEY must be set")
    from data_system.src.databento_client import DatabentoResearchClient

    sym = session.resolved_symbol or session.symbol
    daily_root.mkdir(parents=True, exist_ok=True)
    dest = daily_root / f"{sym}.parquet"
    if dest.exists() and dest.stat().st_size > 0 and not refresh:
        coverage = daily_coverage_calendar_days(dest, session.date)
        if coverage >= lookback_days:
            return dest

    if dest.exists():
        dest.unlink()
    start_utc, end_utc = daily_window_utc(session, lookback_days)
    tmp = daily_root / f".tmp_{sym}_{session.date}_ohlcv.dbn.zst"
    client = DatabentoResearchClient()
    event_id = f"decadal_daily_{session.id}_{sym}".replace(".", "_")
    client.download_event_window(
        event_id=event_id,
        symbols=[sym],
        start_utc=start_utc,
        end_utc=end_utc,
        dataset=session.dataset,
        schema="ohlcv-1d",
        stype_in=session.stype_in,
        requested_symbol=session.symbol,
        output_path=str(tmp),
        override_hard_limit=override_hard_limit,
        override_operating_cap=override_operating_cap,
    )
    df = _dbn_to_daily_df(tmp)
    if df.empty:
        raise ValueError(f"No daily OHLCV decoded for {sym}")
    df.to_parquet(dest, index=False)
    if tmp.exists():
        tmp.unlink()
    return dest


def _dbn_to_daily_df(path: Path) -> pd.DataFrame:
    import databento as db

    store = db.DBNStore.from_file(str(path))
    df = store.to_df()
    if df.empty:
        return df
    df = df.reset_index()
    rename = {}
    for col in df.columns:
        lc = str(col).lower()
        if lc in ("ts_event", "datetime"):
            rename[col] = "date"
        if lc == "close":
            rename[col] = "close"
        if lc == "open":
            rename[col] = "open"
        if lc == "high":
            rename[col] = "high"
        if lc == "low":
            rename[col] = "low"
        if lc in ("volume", "vol"):
            rename[col] = "volume"
    df = df.rename(columns=rename)
    if "date" in df.columns:
        df["date"] = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d")
    sym_col = session_symbol_col(df)
    if sym_col:
        df = df.rename(columns={sym_col: "symbol"})
    for req in ("open", "high", "low", "close", "volume"):
        if req not in df.columns:
            df[req] = 0.0
    return df[["symbol", "date", "open", "high", "low", "close", "volume"]].copy()


def session_symbol_col(df: pd.DataFrame) -> str | None:
    for c in df.columns:
        if str(c).lower() in ("symbol", "raw_symbol"):
            return str(c)
    return None

def _upsert_manifest_session(manifest: dict[str, Any], entry: dict[str, Any]) -> None:
    sessions = manifest.setdefault("sessions", [])
    sid = entry.get("session_id")
    for i, row in enumerate(sessions):
        if row.get("session_id") == sid:
            sessions[i] = entry
            return
    sessions.append(entry)


def pull_catalog(
    catalog_path: str | Path,
    *,
    session_id: str | None = None,
    dry_run: bool = False,
    override_hard_limit: bool = False,
    override_operating_cap: bool = False,
    resume: bool = False,
    refresh_daily: bool = False,
    daily_only: bool = False,
    pull_options: bool = False,
    options_only: bool = False,
    refresh_options: bool = False,
) -> dict[str, Any]:
    catalog = load_decadal_catalog(catalog_path)
    manifest_path = catalog.paths["manifest_root"] / "decadal_pull.json"
    v2_path = manifest_v2_path(catalog.paths["manifest_root"])
    if not v2_path.exists():
        migrate_v1_file(manifest_path, v2_path)
    manifest_v2 = load_manifest_v2(v2_path)

    manifest: dict[str, Any] = {"sessions": [], "started_at": datetime.now(timezone.utc).isoformat()}
    if resume and manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        done_ids = {
            s["session_id"]
            for s in manifest.get("sessions", [])
            if s.get("status") == "pulled"
        }
    else:
        done_ids = set()

    sessions = catalog.sessions
    if session_id:
        sessions = [get_decadal_session(catalog, session_id)]

    if dry_run:
        estimates = estimate_catalog_cost(catalog_path, session_id=session_id)
        total = sum(r.get("total_cost_usd") or 0 for r in estimates)
        return {"dry_run": True, "estimates": estimates, "total_cost_usd": total}

    for session in sessions:
        start_utc, end_utc = session_window_utc(session)
        v2_row: dict[str, Any] = {
            "session_id": session.id,
            "underlying": session.symbol,
            "date": session.date,
            "skip_pull": session.skip_pull,
            **window_fields(start_utc, end_utc),
        }
        if session.skip_reason:
            v2_row["skip_reason"] = session.skip_reason

        if session.skip_pull:
            _upsert_manifest_session(manifest, {**v2_row, "status": "skip_pull"})
            upsert_session(manifest_v2, v2_row)
            write_manifest_v2(v2_path, manifest_v2)
            _write_manifest(manifest_path, manifest)
            continue

        if options_only:
            if not session.resolved_symbol:
                session.resolved_symbol = resolve_symbology(session)
            opt_block = pull_options_chain(
                catalog,
                session,
                override_hard_limit=override_hard_limit,
                override_operating_cap=override_operating_cap,
                refresh=refresh_options,
            )
            v2_row["options"] = opt_block
            upsert_session(manifest_v2, v2_row)
            write_manifest_v2(v2_path, manifest_v2)
            _upsert_manifest_session(
                manifest,
                {**v2_row, "status": "options_pulled", "options": opt_block},
            )
            _write_manifest(manifest_path, manifest)
            continue

        if session.id in done_ids and not refresh_daily:
            if not pull_options:
                continue

        entry: dict[str, Any] = {
            "session_id": session.id,
            "symbol": session.symbol,
            "date": session.date,
        }
        try:
            resolved = resolve_symbology(session)
            if not resolved:
                entry["error"] = "symbology resolve returned empty"
                entry["status"] = "failed"
                _upsert_manifest_session(manifest, entry)
                upsert_session(manifest_v2, {**v2_row, "equity": {"pull_error": entry["error"]}})
                write_manifest_v2(v2_path, manifest_v2)
                _write_manifest(manifest_path, manifest)
                continue
            session.resolved_symbol = resolved
            entry["resolved_symbol"] = resolved

            daily_path = pull_daily_history(
                session,
                catalog.paths["daily_root"],
                catalog.daily_lookback_days,
                override_hard_limit=override_hard_limit,
                override_operating_cap=override_operating_cap,
                refresh=refresh_daily,
            )
            entry["daily_path"] = str(daily_path)
            entry["daily_lookback_days"] = catalog.daily_lookback_days
            entry["daily_coverage_days"] = daily_coverage_calendar_days(
                daily_path, session.date
            )

            if daily_only:
                entry["status"] = "daily_refreshed"
                _upsert_manifest_session(manifest, entry)
                upsert_session(
                    manifest_v2,
                    {**v2_row, "equity": {k: entry[k] for k in entry if k not in ("session_id", "symbol", "date", "status")}},
                )
                write_manifest_v2(v2_path, manifest_v2)
                _write_manifest(manifest_path, manifest)
                continue

            if session.id in done_ids:
                entry["status"] = "daily_refreshed"
                _upsert_manifest_session(manifest, entry)
                _write_manifest(manifest_path, manifest)
                if pull_options:
                    opt_block = pull_options_chain(
                        catalog,
                        session,
                        override_hard_limit=override_hard_limit,
                        override_operating_cap=override_operating_cap,
                        refresh=refresh_options,
                    )
                    v2_row["options"] = opt_block
                    upsert_session(manifest_v2, {**v2_row, "equity": entry})
                    write_manifest_v2(v2_path, manifest_v2)
                continue

            raw_path = download_decadal_session(
                session,
                catalog.paths["raw_root"],
                override_hard_limit=override_hard_limit,
                override_operating_cap=override_operating_cap,
            )
            entry["raw_path"] = str(raw_path)
            entry["mbo_cost_usd"] = estimate_session_cost(session)

            norm_path = catalog.paths["normalized_root"] / session.normalized_filename()
            bars = load_daily_parquet(daily_path, session.symbol)
            normalize_dbn(
                raw_path,
                norm_path,
                session.symbol,
                session.date,
                schema=session.schema,
                daily_bars=bars,
            )
            entry["normalized_path"] = str(norm_path)
            entry["status"] = "pulled"

            equity_block = {k: v for k, v in entry.items() if k not in ("session_id", "symbol", "date", "status")}
            v2_row["equity"] = equity_block
            if pull_options:
                v2_row["options"] = pull_options_chain(
                    catalog,
                    session,
                    override_hard_limit=override_hard_limit,
                    override_operating_cap=override_operating_cap,
                    refresh=refresh_options,
                )
            upsert_session(manifest_v2, v2_row)
            write_manifest_v2(v2_path, manifest_v2)
        except Exception as exc:
            entry["status"] = "failed"
            entry["error"] = str(exc)
            upsert_session(manifest_v2, {**v2_row, "equity": {"pull_error": str(exc)}})
            write_manifest_v2(v2_path, manifest_v2)
        _upsert_manifest_session(manifest, entry)
        _write_manifest(manifest_path, manifest)

    manifest["finished_at"] = datetime.now(timezone.utc).isoformat()
    manifest_v2["finished_at"] = manifest["finished_at"]
    write_manifest_v2(v2_path, manifest_v2)
    _write_manifest(manifest_path, manifest)
    return manifest


def _write_manifest(path: Path, manifest: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
