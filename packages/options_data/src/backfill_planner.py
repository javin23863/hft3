"""WS-0.4 Databento backfill purchase planner — no network calls, no purchases.

This module emits purchase plan specs for human review only.  Nothing is
submitted to the Databento API from here.

Manifest output is guarded to research_cards/backfill_plans/ exclusively —
any other path is rejected at runtime (mirrors fixing_window_study.py guard).
"""
from __future__ import annotations

import json
import os
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Internal imports — options_data calendar and budget cap constants
# ---------------------------------------------------------------------------
from options_data.src.expiry_calendar import (
    ExpiryKind,
    expiries_between,
    fixing_datetime_utc,
)

# Locate repo root for budget_manager import.
# packages/ lives two levels above this file (packages/options_data/src/).
_PKG_ROOT = Path(__file__).resolve().parents[2]  # packages/
_REPO_ROOT = _PKG_ROOT.parent

if str(_PKG_ROOT) not in sys.path:
    sys.path.insert(0, str(_PKG_ROOT))
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from data_system.src.budget_manager import BudgetManager

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Default GB/window is UNKNOWN — to be calibrated from one probe window.
# Set to None here; estimate_cost accepts it as a parameter defaulting to None.
_DEFAULT_GB_PER_WINDOW: float | None = None  # UNKNOWN — calibrate before use

# Fixing window: 14:55–15:05 CT (10 minutes)
_FIX_WINDOW_MINUTES_BEFORE = timedelta(minutes=5)
_FIX_WINDOW_MINUTES_AFTER = timedelta(minutes=5)

# Output guard
_MANIFEST_SUBDIR = "research_cards/backfill_plans"


# ---------------------------------------------------------------------------
# plan_fixing_windows
# ---------------------------------------------------------------------------

def plan_fixing_windows(
    start: date,
    end: date,
    symbols: tuple[str, ...] = ("ES",),
) -> list[dict[str, Any]]:
    """Emit window specs for 14:55–15:05 CT on every expiry day.

    Parameters
    ----------
    start   : inclusive start date
    end     : inclusive end date
    symbols : futures symbols (currently informational — spec is per-symbol
              but Databento pricing is dataset-level; kept for plan clarity)

    Returns
    -------
    List of window spec dicts, one per unique expiry date, sorted ascending:
        date          : str ISO date of the expiry
        expiry_kinds  : list[str] — ExpiryKind names on that date
        symbols       : list[str]
        start_utc     : str ISO datetime for 14:55 CT in UTC
        end_utc       : str ISO datetime for 15:05 CT in UTC

    Multiple expiry kinds on the same calendar date are merged into one window
    (union); Databento charges per time range, not per kind.
    """
    all_expiries = expiries_between(start, end)  # all kinds

    # Group by date
    by_date: dict[date, list[ExpiryKind]] = {}
    for d, kind in all_expiries:
        by_date.setdefault(d, []).append(kind)

    windows: list[dict[str, Any]] = []
    for d in sorted(by_date):
        kinds = by_date[d]
        # fixing_datetime_utc gives 15:00 CT as UTC datetime
        fix_utc = fixing_datetime_utc(d)
        start_utc = fix_utc - _FIX_WINDOW_MINUTES_BEFORE
        end_utc = fix_utc + _FIX_WINDOW_MINUTES_AFTER
        windows.append(
            {
                "date": d.isoformat(),
                "expiry_kinds": sorted(k.value for k in kinds),
                "symbols": list(symbols),
                "start_utc": start_utc.isoformat(),
                "end_utc": end_utc.isoformat(),
            }
        )
    return windows


# ---------------------------------------------------------------------------
# plan_baltussen_bars
# ---------------------------------------------------------------------------

def plan_baltussen_bars(
    start: date,
    end: date,
) -> dict[str, Any]:
    """Return a single spec for GLBX ohlcv-1m ES.v.0 continuous (WS-1.3 input).

    This is the near-zero cost line item for the Baltussen et al. replication.
    No expiry grouping needed — it is one continuous dataset span.

    Returns
    -------
    Single dict:
        dataset       : "GLBX.MDP3"
        schema        : "ohlcv-1m"
        symbols       : ["ES.v.0"]
        start_date    : str ISO
        end_date      : str ISO
        note          : description
    """
    return {
        "dataset": "GLBX.MDP3",
        "schema": "ohlcv-1m",
        "symbols": ["ES.v.0"],
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "note": (
            "WS-1.3 Baltussen continuous-futures input. "
            "Near-zero cost; ohlcv-1m is highly compressed."
        ),
    }


# ---------------------------------------------------------------------------
# estimate_cost
# ---------------------------------------------------------------------------

def estimate_cost(
    windows: list[dict[str, Any]],
    *,
    schema: str = "mbo",
    gb_per_window: float | None = None,
    usd_per_gb: float,
) -> dict[str, Any]:
    """Estimate cost and compare against BudgetManager caps.

    Parameters
    ----------
    windows      : list of window specs from plan_fixing_windows
    schema       : Databento schema string (informational; affects real cost)
    gb_per_window: assumed GB per 10-minute MBO window.  If None, the result
                   documents gb_per_window as UNKNOWN and marks usd_total as None.
                   Calibrate from one probe window before committing budget.
    usd_per_gb   : Databento price per GB for the given schema/dataset.

    Returns
    -------
    Dict with:
        windows_count          : int
        schema                 : str
        gb_per_window          : float | None  (None → UNKNOWN)
        gb_per_window_note     : str — calibration status
        usd_per_gb             : float
        usd_total              : float | None  (None when gb_per_window unknown)
        operating_cap_usd      : float
        hard_limit_usd         : float
        requires_budget_uplift : bool  (True when usd_total > operating_cap or unknown)
    """
    bm = BudgetManager.__new__(BudgetManager)  # avoid manifest I/O
    bm.manifest_path = ""
    bm.total_used = 0.0

    n = len(windows)
    gb_note = (
        "UNKNOWN — calibrate from one probe window before committing budget."
        if gb_per_window is None
        else f"Assumed {gb_per_window:.4f} GB/window (parameter-supplied, unverified)."
    )

    usd_total: float | None
    if gb_per_window is None:
        usd_total = None
        requires_uplift = True  # unknown cost → cannot confirm it fits
    else:
        usd_total = n * gb_per_window * usd_per_gb
        requires_uplift = usd_total > BudgetManager.OPERATING_CAP

    return {
        "windows_count": n,
        "schema": schema,
        "gb_per_window": gb_per_window,
        "gb_per_window_note": gb_note,
        "usd_per_gb": usd_per_gb,
        "usd_total": usd_total,
        "operating_cap_usd": BudgetManager.OPERATING_CAP,
        "hard_limit_usd": BudgetManager.HARD_LIMIT,
        "requires_budget_uplift": requires_uplift,
    }


# ---------------------------------------------------------------------------
# to_purchase_manifest
# ---------------------------------------------------------------------------

def to_purchase_manifest(
    plan: dict[str, Any],
    path: str | os.PathLike[str],
) -> Path:
    """Write a JSON purchase manifest to *path* under research_cards/backfill_plans/.

    Parameters
    ----------
    plan : any JSON-serialisable plan dict (e.g. output of estimate_cost or
           a combined dict).
    path : destination path.  Must resolve to a location inside
           research_cards/backfill_plans/ — any other location is rejected.

    Returns
    -------
    Resolved Path of the written file.

    Raises
    ------
    ValueError : if *path* is not inside research_cards/backfill_plans/.
    """
    dest = Path(path).resolve()
    allowed = (_REPO_ROOT / _MANIFEST_SUBDIR).resolve()

    # Ensure the destination is inside the allowed subtree
    try:
        dest.relative_to(allowed)
    except ValueError:
        raise ValueError(
            f"Manifest path must be inside research_cards/backfill_plans/; "
            f"got: {dest}"
        )

    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(plan, indent=2, default=str), encoding="utf-8")
    return dest
