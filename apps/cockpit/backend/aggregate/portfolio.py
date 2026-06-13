"""Portfolio zone — positions / PnL / fills.

Ships ready but shows ``live_session: false`` until EXECUTION_MODE=LIVE. Until
then it surfaces the most recent backtest/replay fills (research_cards/fills.csv)
as the PnL/markout preview so the panel is never blank. Live positions plumbing
(trade_manager.monitor + Rithmic sPnlCnnctPt) lights up the same shape when live.
"""
from __future__ import annotations

import csv
import time
from typing import Any, Optional

from .. import paths, schemas


def _load_fills(limit: int = 50) -> tuple[list[dict], int, Optional[float], Optional[float]]:
    """Return (recent_fills, total_count, net_pnl, expected_shortfall)."""
    path = paths.FILLS_CSV
    if not path.exists():
        return [], 0, None, None
    rows: list[dict] = []
    pnls: list[float] = []
    try:
        with open(path, "r", encoding="utf-8", newline="") as fh:
            reader = csv.DictReader(fh)
            for r in reader:
                rows.append(r)
                v = r.get("pnl")
                if v not in (None, ""):
                    try:
                        pnls.append(float(v))
                    except (TypeError, ValueError):
                        pass
    except OSError:
        return [], 0, None, None

    net_pnl = sum(pnls) if pnls else None
    es: Optional[float] = None
    if pnls:
        try:
            from telemetry.src.metrics import TelemetryMetrics  # type: ignore

            es = float(TelemetryMetrics.expected_shortfall(pnls))
        except Exception:
            es = None
    recent = rows[-limit:]
    return recent, len(rows), net_pnl, es


def _markout() -> Optional[dict]:
    """Adverse-selection markout across horizons, if telemetry + pandas available."""
    try:
        import pandas as pd  # type: ignore
        from telemetry.src.metrics import TelemetryMetrics  # type: ignore

        if not paths.FILLS_CSV.exists():
            return None
        fills = pd.read_csv(paths.FILLS_CSV)
        return {k: round(float(v), 4) for k, v in TelemetryMetrics.adverse_selection(fills, tick_size=0.25).items()}
    except Exception:
        return None


# A live session that hasn't written in this long is treated as stale.
_LIVE_STALE_S = 600.0


def _live_session() -> tuple[Any, Optional[str], Optional[float], Optional[str]]:
    """Resolve the latest trade_manager session report. Returns
    (view, error, age_s, session_id):

    - (None, None, None, None): no session dir yet (no live/paper run).
    - (None, "<reason>", age, sid): a session dir EXISTS but its artifacts are
      unreadable/malformed -> surfaced as a problem, never silently swallowed.
    - (ObserverView, None, age, sid): healthy read.

    age_s = seconds since the session dir last changed (recency/staleness signal).
    Detect-only: the cockpit READS the artifact, never creates a session.
    """
    root = paths.SESSIONS_ROOT
    if not root.is_dir():
        return None, None, None, None
    try:
        sessions = [d for d in root.iterdir() if d.is_dir()]
    except OSError as exc:
        return None, f"sessions root unreadable: {exc}", None, None
    if not sessions:
        return None, None, None, None
    latest = max(sessions, key=lambda d: d.stat().st_mtime)
    try:
        age_s = round(time.time() - latest.stat().st_mtime, 1)
    except OSError:
        age_s = None
    try:
        from observer.read_model import load_observer_view  # apps/observer

        return load_observer_view(root, latest.name), None, age_s, latest.name
    except ImportError:
        # observer package not importable (bare dev env): no live wiring, not a fault.
        return None, None, age_s, latest.name
    except Exception as exc:  # malformed/unsafe artifacts -> SURFACE, do not hide
        return None, f"session '{latest.name}' artifacts unreadable: {exc}", age_s, latest.name


def _kill_engaged(kill: Any) -> bool:
    if not isinstance(kill, dict):
        return False
    if kill.get("active") is True:
        return True
    return str(kill.get("status", "")).upper() in {"FIRED", "ENGAGED", "ACTIVE", "TRIGGERED"}


def _ts(rec: dict) -> float:
    v = rec.get("timestamp_ns", -1)
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else -1.0


def _current_positions(records: Any) -> list[dict]:
    """Flatten heterogeneous positions.jsonl records to a consistent
    [{symbol, quantity}] of CURRENT holdings. positions.jsonl is a timeseries, so
    we take the latest state. Handles the PositionSnapshot form
    ({timestamp_ns, positions: {sym: qty}}) and a flat per-symbol form
    ({symbol, quantity, timestamp_ns}); unknown shapes are skipped, never emitted."""
    recs = [r for r in (records or ()) if isinstance(r, dict)]
    if not recs:
        return []
    snapshots = [r for r in recs if isinstance(r.get("positions"), dict)]
    if snapshots:
        latest = max(snapshots, key=_ts)
        return [{"symbol": str(s), "quantity": q} for s, q in latest["positions"].items()]
    # flat per-symbol form: keep the latest record per symbol
    by_sym: dict[str, tuple[float, dict]] = {}
    for r in recs:
        sym = r.get("symbol")
        if sym is None:
            continue
        t = _ts(r)
        if str(sym) not in by_sym or t >= by_sym[str(sym)][0]:
            by_sym[str(sym)] = (t, r)
    return [{"symbol": s, "quantity": r.get("quantity")} for s, (_, r) in sorted(by_sym.items())]


def build() -> dict:
    mode = paths.execution_mode()
    live = mode == "LIVE"
    recent, total, net_pnl, es = _load_fills()
    markout = _markout()
    view, view_err, age_s, sid = _live_session()

    positions = _current_positions(view.positions) if view else []
    kill = view.kill_switch_status if view else None
    realized = unrealized = total_pnl = None
    if view and isinstance(view.pnl, dict):
        realized = view.pnl.get("realized_pnl")
        unrealized = view.pnl.get("unrealized_pnl")
        total_pnl = view.pnl.get("total_pnl")

    # Honest health: GREEN only when nothing is wrong. Surface unreadable artifacts,
    # an engaged kill switch, and a stale live session instead of a deceptive green.
    health = schemas.GREEN
    notes: list[str] = []
    if view_err:
        health = schemas.AMBER
        notes.append(view_err)
    if _kill_engaged(kill):
        health = schemas.RED
        notes.append("kill switch engaged")
    if live and age_s is not None and age_s > _LIVE_STALE_S:
        if health == schemas.GREEN:
            health = schemas.AMBER
        notes.append(f"live session stale: {age_s:.0f}s since last write")

    if view:
        source = f"trade_manager session {sid} (positions.jsonl)"
        banner = None if live else f"EXECUTION_MODE={mode}; latest session positions shown alongside replay fills."
    elif view_err:
        source = f"session {sid} (unreadable)"
        banner = view_err
    else:
        source = "rithmic_pnl" if live else "research_cards/fills.csv"
        banner = None if live else f"No live session — EXECUTION_MODE={mode}. Showing latest replay fills."

    return {
        "zone": "portfolio",
        "generated_utc": paths.now_iso(),
        "health": health,
        "live_session": live,
        "execution_mode": mode,
        "banner": banner,
        "session_id": sid,
        "session_age_s": age_s,
        "kill_switch": kill,
        "notes": notes,
        "positions": positions,  # real positions.jsonl when a session exists, else []
        "pnl": {
            "net_pnl": total_pnl if total_pnl is not None else net_pnl,
            "realized": realized if realized is not None else (net_pnl if not live else None),
            "unrealized": unrealized,
            "expected_shortfall_5pct": es,
        },
        "adverse_selection_ticks": markout,
        "fills": {"total": total, "recent": recent},
        "source": source,
    }
