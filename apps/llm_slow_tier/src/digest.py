"""Deterministic tape-stat digest from per-symbol capture manifests.

All functions are pure; no I/O side-effects beyond reading the manifest files.
The z-score baseline is built from previously-dated manifest files found in the
same manifest tree.

Manifest JSON schema (produced on CHI404):
{
  "symbol":                 str,
  "exchange":               str,
  "trade_date":             "YYYY-MM-DD",
  "records":                int,
  "trades":                 int,
  "quotes":                 int,
  "first_ts_exch_ns":       int,
  "last_ts_exch_ns":        int,
  "max_queue_gap_flag_count": int,
  "md_drops_total":         int,
  "reconnects":             int,
  "file_bytes":             int,
  "updated_wall_ns":        int,
  "unknown_symbol_drops":   int,
}
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# Per-symbol digest entry
# ---------------------------------------------------------------------------

@dataclass
class SymbolDigest:
    symbol: str
    records: int
    trades: int
    quotes: int
    gap_flags: int      # max_queue_gap_flag_count
    drops: int          # md_drops_total
    reconnects: int
    z_score: float      # trade-count z-score vs trailing baseline; 0.0 if insufficient
    insufficient_baseline: bool  # True when fewer than 5 prior sessions available

    def to_dict(self) -> Dict[str, Any]:
        return {
            "symbol": self.symbol,
            "records": self.records,
            "trades": self.trades,
            "quotes": self.quotes,
            "gap_flags": self.gap_flags,
            "drops": self.drops,
            "reconnects": self.reconnects,
            "z_score": self.z_score,
            "insufficient_baseline": self.insufficient_baseline,
        }


# ---------------------------------------------------------------------------
# Top-level digest
# ---------------------------------------------------------------------------

@dataclass
class Digest:
    trade_date: str
    symbols: List[str]
    total_records: int
    total_trades: int
    total_quotes: int
    total_gap_flags: int
    total_drops: int
    total_reconnects: int
    per_symbol: Dict[str, SymbolDigest]
    trade_count_zscores: Dict[str, float]
    insufficient_baseline: List[str]
    n_symbols: int

    def to_dict(self) -> Dict[str, Any]:
        return {
            "trade_date": self.trade_date,
            "symbols": self.symbols,
            "total_records": self.total_records,
            "total_trades": self.total_trades,
            "total_quotes": self.total_quotes,
            "total_gap_flags": self.total_gap_flags,
            "total_drops": self.total_drops,
            "total_reconnects": self.total_reconnects,
            "per_symbol": {k: v.to_dict() for k, v in self.per_symbol.items()},
            "trade_count_zscores": self.trade_count_zscores,
            "insufficient_baseline": self.insufficient_baseline,
            "n_symbols": self.n_symbols,
        }


# ---------------------------------------------------------------------------
# Manifest loading helpers
# ---------------------------------------------------------------------------

def _load_manifest(path: Path) -> Optional[Dict[str, Any]]:
    """Load a single manifest JSON file.  Returns None on any parse error."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(raw, dict):
        return None
    return raw


def load_manifests_for_date(trade_date: str, manifest_dir: Path) -> List[Dict[str, Any]]:
    """Return all valid manifest dicts found in manifest_dir.

    manifest_dir is expected to contain ``{symbol}.manifest.json`` files
    (or any ``*.json`` or ``*.manifest.json`` files) for the given date.
    Files that fail to parse are silently skipped.
    """
    if not manifest_dir.is_dir():
        return []

    manifests = []
    for path in sorted(manifest_dir.glob("*.json")):
        raw = _load_manifest(path)
        if raw is None:
            continue
        # Accept manifests that either match the date or have no trade_date field.
        file_date = raw.get("trade_date")
        if file_date is not None and file_date != trade_date:
            continue
        manifests.append(raw)
    return manifests


# ---------------------------------------------------------------------------
# Baseline / z-score computation
# ---------------------------------------------------------------------------

_MIN_BASELINE_SESSIONS = 5
_MAX_BASELINE_SESSIONS = 20


def _collect_prior_trade_counts(
    symbol: str,
    trade_date: str,
    manifest_tree_root: Path,
) -> List[int]:
    """Collect up to 20 prior trade counts for a symbol from dated subdirectories.

    The manifest tree is expected to look like::

        manifest_tree_root/
            2026-06-10/
                MES.manifest.json
            2026-06-11/
                MES.manifest.json
            {trade_date}/          <-- the current date being labeled
                ...

    Only directories whose name is a date string strictly less than trade_date
    are scanned (prior sessions only).
    """
    if not manifest_tree_root.is_dir():
        return []

    prior_dates: List[str] = []
    for entry in manifest_tree_root.iterdir():
        if not entry.is_dir():
            continue
        name = entry.name
        # Accept YYYY-MM-DD directory names that predate the current session.
        if len(name) == 10 and name < trade_date:
            prior_dates.append(name)

    prior_dates.sort(reverse=True)
    prior_dates = prior_dates[:_MAX_BASELINE_SESSIONS]

    counts: List[int] = []
    for date_dir in prior_dates:
        session_dir = manifest_tree_root / date_dir
        for path in session_dir.glob("*.json"):
            raw = _load_manifest(path)
            if raw is None:
                continue
            sym = raw.get("symbol", "")
            if sym != symbol:
                continue
            trades = raw.get("trades")
            if isinstance(trades, int):
                counts.append(trades)
                break  # one manifest per symbol per session

    return counts


def _compute_zscore(value: int, prior_counts: List[int]) -> tuple[float, bool]:
    """Return (z_score, insufficient_baseline).

    z_score is 0.0 when fewer than _MIN_BASELINE_SESSIONS prior counts exist,
    or when the standard deviation of the prior is zero.
    """
    if len(prior_counts) < _MIN_BASELINE_SESSIONS:
        return 0.0, True

    n = len(prior_counts)
    mean = sum(prior_counts) / n
    variance = sum((x - mean) ** 2 for x in prior_counts) / n
    std = math.sqrt(variance)

    if std == 0.0:
        return 0.0, False

    return (value - mean) / std, False


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def build_digest(trade_date: str, manifest_dir: Path) -> Digest:
    """Build a Digest from all manifests found in manifest_dir.

    manifest_dir should be the per-date directory:
    ``{artifact_root}/manifests/{trade_date}/``

    The parent of manifest_dir (``{artifact_root}/manifests/``) is used as the
    manifest tree root for baseline lookups.
    """
    manifests = load_manifests_for_date(trade_date, manifest_dir)

    # The tree root is the parent of the per-date directory (for baseline lookup).
    manifest_tree_root = manifest_dir.parent

    symbols: List[str] = []
    per_symbol: Dict[str, SymbolDigest] = {}
    total_records = 0
    total_trades = 0
    total_quotes = 0
    total_gap_flags = 0
    total_drops = 0
    total_reconnects = 0
    trade_count_zscores: Dict[str, float] = {}
    insufficient_baseline: List[str] = []

    for m in manifests:
        sym = str(m.get("symbol") or "")
        if not sym:
            continue

        records = int(m.get("records") or 0)
        trades = int(m.get("trades") or 0)
        quotes = int(m.get("quotes") or 0)
        gap_flags = int(m.get("max_queue_gap_flag_count") or 0)
        drops = int(m.get("md_drops_total") or 0)
        reconnects = int(m.get("reconnects") or 0)

        # Compute z-score from prior sessions in the manifest tree
        prior_counts = _collect_prior_trade_counts(sym, trade_date, manifest_tree_root)
        z, insuff = _compute_zscore(trades, prior_counts)

        sym_digest = SymbolDigest(
            symbol=sym,
            records=records,
            trades=trades,
            quotes=quotes,
            gap_flags=gap_flags,
            drops=drops,
            reconnects=reconnects,
            z_score=z,
            insufficient_baseline=insuff,
        )

        symbols.append(sym)
        per_symbol[sym] = sym_digest
        total_records += records
        total_trades += trades
        total_quotes += quotes
        total_gap_flags += gap_flags
        total_drops += drops
        total_reconnects += reconnects
        trade_count_zscores[sym] = z
        if insuff:
            insufficient_baseline.append(sym)

    return Digest(
        trade_date=trade_date,
        symbols=sorted(symbols),
        total_records=total_records,
        total_trades=total_trades,
        total_quotes=total_quotes,
        total_gap_flags=total_gap_flags,
        total_drops=total_drops,
        total_reconnects=total_reconnects,
        per_symbol=per_symbol,
        trade_count_zscores=trade_count_zscores,
        insufficient_baseline=sorted(insufficient_baseline),
        n_symbols=len(symbols),
    )
