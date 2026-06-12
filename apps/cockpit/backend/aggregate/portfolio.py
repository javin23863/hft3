"""Portfolio zone — positions / PnL / fills.

Ships ready but shows ``live_session: false`` until EXECUTION_MODE=LIVE. Until
then it surfaces the most recent backtest/replay fills (research_cards/fills.csv)
as the PnL/markout preview so the panel is never blank. Live positions plumbing
(trade_manager.monitor + Rithmic sPnlCnnctPt) lights up the same shape when live.
"""
from __future__ import annotations

import csv
from typing import Optional

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


def build() -> dict:
    mode = paths.execution_mode()
    live = mode == "LIVE"
    recent, total, net_pnl, es = _load_fills()
    markout = _markout()

    return {
        "zone": "portfolio",
        "generated_utc": paths.now_iso(),
        "health": schemas.GREEN,
        "live_session": live,
        "execution_mode": mode,
        "banner": None if live else f"No live session — EXECUTION_MODE={mode}. Showing latest replay fills.",
        "positions": [],  # populated from trade_manager.monitor PositionSnapshot when live
        "pnl": {
            "net_pnl": net_pnl,
            "realized": net_pnl if not live else None,
            "unrealized": None,
            "expected_shortfall_5pct": es,
        },
        "adverse_selection_ticks": markout,
        "fills": {"total": total, "recent": recent},
        "source": "research_cards/fills.csv" if not live else "rithmic_pnl",
    }
