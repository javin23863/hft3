"""esq zone — ES futures strategy shadow-trading status (separate repo, read-only).

esq (``C:\\Users\\MSI\\repos\\esq``) is a standalone repo running an ES futures
strategy through its shadow-trading phase. This zone reads esq's shadow trade
ledger, heartbeat log, and newest validation artifacts for read-only display —
the cockpit never writes into esq. All reads are defensive: esq not checked
out, or any individual artifact missing/corrupt, must render as a graceful
"no data" state (health GRAY / null fields), never raise.
"""
from __future__ import annotations

import json
from typing import Any, Optional

from .. import paths, schemas

# esq predates the shared GREEN/AMBER/RED vocabulary in schemas.py; GRAY is
# esq-local ("no data yet") and only meaningful for this zone.
GRAY = "gray"

_HEARTBEAT_STALE_S = 2 * 3600.0  # 2h
_RECENT_TRADES_LIMIT = 20


def _read_jsonl(path: Any) -> list[dict]:
    """All well-formed dict lines from a jsonl file, in file order; [] on any miss."""
    if not path.is_file():
        return []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    out: list[dict] = []
    for line in lines:
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(rec, dict):
            out.append(rec)
    return out


def _latest_file(files: list) -> Optional[Any]:
    existing = [p for p in files if p.is_file()]
    if not existing:
        return None
    try:
        return max(existing, key=lambda p: p.stat().st_mtime)
    except OSError:
        return None


def _newest_validation(suffix: str) -> Optional[dict]:
    """Newest ``*<suffix>`` under esq's validation dir, or None if absent."""
    root = paths.ESQ_VALIDATION_DIR
    if not root.is_dir():
        return None
    try:
        candidates = list(root.glob(f"*{suffix}"))
    except OSError:
        return None
    latest = _latest_file(candidates)
    if latest is None:
        return None
    data = paths.read_json(latest)
    return data if isinstance(data, dict) else None


def _stats_and_curve(trades: list[dict]) -> tuple[dict, list[dict]]:
    """(stats, equity_curve) computed from trades.jsonl's pnl_usd stream.

    trades is in file order (oldest first); equity_curve mirrors that order.
    """
    curve: list[dict] = []
    cum = 0.0
    peak = 0.0
    max_dd = 0.0
    n = 0
    wins = 0
    gross_win = 0.0
    gross_loss = 0.0
    for t in trades:
        v = t.get("pnl_usd")
        if not isinstance(v, (int, float)) or isinstance(v, bool):
            continue
        n += 1
        cum += float(v)
        curve.append({"ts": t.get("exit_ts"), "equity": round(cum, 2)})
        if v > 0:
            wins += 1
            gross_win += v
        elif v < 0:
            gross_loss += -v
        if cum > peak:
            peak = cum
        dd = peak - cum
        if dd > max_dd:
            max_dd = dd

    stats = {
        "n_trades": n,
        "net_usd": round(cum, 2) if n else None,
        "win_rate": round(wins / n, 4) if n else None,
        "profit_factor": round(gross_win / gross_loss, 4) if gross_loss > 0 else None,
        "max_dd_usd": round(max_dd, 2) if n else None,
    }
    return stats, curve


def _heartbeat_age_min(heartbeat: Optional[dict]) -> Optional[float]:
    if not heartbeat:
        return None
    age_s = schemas.age_seconds(heartbeat.get("run_utc"))
    return round(age_s / 60.0, 2) if age_s is not None else None


def build() -> dict:
    trades = _read_jsonl(paths.ESQ_SHADOW_TRADES)
    log_lines = _read_jsonl(paths.ESQ_SHADOW_LOG)
    heartbeat = log_lines[-1] if log_lines else None
    heartbeat_age_min = _heartbeat_age_min(heartbeat)

    stats, curve = _stats_and_curve(trades)
    recent_trades = list(reversed(trades[-_RECENT_TRADES_LIMIT:]))

    audit = _newest_validation("-shadow-audit.json")
    sizing_memo = _newest_validation("-sizing-memo.json")
    xmkt = _newest_validation("-xmkt.json")
    sizing = {"trades_per_year": sizing_memo.get("trades_per_year") if sizing_memo else None}

    no_data = heartbeat is None and not trades and audit is None
    verdict = str(audit.get("verdict")) if audit else None
    dormant = bool(audit.get("dormant")) if audit else False
    heartbeat_stale = heartbeat_age_min is not None and heartbeat_age_min * 60.0 > _HEARTBEAT_STALE_S

    if no_data:
        health = GRAY
    elif heartbeat_stale or verdict == "FAIL":
        health = schemas.RED
    elif verdict == "WARN" or dormant:
        health = schemas.AMBER
    else:
        health = schemas.GREEN

    return {
        "zone": "esq",
        "generated_utc": paths.now_iso(),
        "health": health,
        "heartbeat": heartbeat,
        "heartbeat_age_min": heartbeat_age_min,
        "stats": stats,
        "equity_curve": curve,
        "recent_trades": recent_trades,
        "audit": audit,
        "sizing": sizing,
        "xmkt": xmkt,
    }
