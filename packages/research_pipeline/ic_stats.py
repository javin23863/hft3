"""Pure statistics for the IC diagnostic (EVENT_ALPHA_REBUILD_PLAN PR-1).

Implements the estimator side of the repo's hypothesis test
(HYPOTHESIS_SPEC_TEMPLATE section 3):

    E[ mid(t + H) - mid(t) | signal(t) > s ] > hurdle

with the statistical discipline the plan pre-registers:

- effective N = EVENTS, never rows: all inference runs on per-event means;
- errors two-way clustered (event x calendar month) via Cameron-Gelbach-Miller;
- multiple testing corrected with the existing Benjamini-Hochberg machinery
  (``research_pipeline.statistics.p_value_correction``);
- spread-adjusted edge reported alongside raw edge (mid-space IC does not pay
  the taker's spread — Pass A died in that gap);
- per-horizon censoring rates so tape-end truncation is visible, not silent.

No pipeline imports: numpy/pandas only, plus the sibling ``statistics`` module.
Feature extraction and manifest handling live in scripts/build_ic_diagnostic.py.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Mapping, Sequence

import numpy as np
import pandas as pd

from research_pipeline.statistics import p_value_correction

__all__ = [
    "EventStats",
    "per_event_conditional_stats",
    "spearman_ic_per_event",
    "clustered_t_two_way",
    "bh_reject",
    "hurdle_ticks",
    "censoring_rate_per_event",
]


# ---------------------------------------------------------------------------
# Per-event aggregation (the only unit at which inference is allowed)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class EventStats:
    """Container for one model's per-event table plus fired-row coverage."""

    table: pd.DataFrame  # one row per event
    n_events_total: int
    n_events_fired: int  # events meeting the min fired-row floor


def _month_of(event_id: str) -> str:
    """Calendar month key from an event id like ``CORE_CPI_2019_05_10_TIGHT``.

    Fails closed: an unparseable event id raises rather than silently
    collapsing all events into one cluster.
    """
    parts = str(event_id).split("_")
    for i in range(len(parts) - 1):
        if len(parts[i]) == 4 and parts[i].isdigit() and parts[i + 1].isdigit():
            return f"{parts[i]}-{parts[i + 1]}"
    raise ValueError(f"unparseable_event_id_for_month:{event_id!r}")


def per_event_conditional_stats(
    frame: pd.DataFrame,
    *,
    signal_col: str,
    ret_cols: Sequence[str],
    threshold: float,
    event_col: str = "event_id",
    spread_col: str | None = None,
    min_fired_rows: int = 5,
) -> EventStats:
    """Per-event conditional means of SIGNED forward returns.

    A row "fires" when ``abs(signal) > threshold``. The signed return for a
    fired row is ``sign(signal) * ret`` — the mid move in the trade direction.
    Events with fewer than ``min_fired_rows`` fired rows are kept in the table
    with ``fired_floor_met = False`` and excluded by callers from inference
    (coverage must be visible: "no verdict" is not "pass").

    When ``spread_col`` is given, ``mean_spread_ticks`` is the average spread
    (in ticks) over fired rows — the taker's crossing cost at signal time.
    """
    if signal_col not in frame.columns:
        raise KeyError(f"missing_signal_col:{signal_col}")
    missing = [c for c in ret_cols if c not in frame.columns]
    if missing:
        raise KeyError(f"missing_ret_cols:{missing}")

    sig = frame[signal_col].to_numpy(dtype=np.float64)
    fired_mask = np.abs(sig) > float(threshold)
    sign = np.sign(sig)

    work = frame[[event_col]].copy()
    work["_fired"] = fired_mask
    for col in ret_cols:
        signed = sign * frame[col].to_numpy(dtype=np.float64)
        work[f"_signed__{col}"] = np.where(fired_mask, signed, np.nan)
    if spread_col is not None:
        if spread_col not in frame.columns:
            raise KeyError(f"missing_spread_col:{spread_col}")
        work["_spread"] = np.where(
            fired_mask, frame[spread_col].to_numpy(dtype=np.float64), np.nan
        )

    grouped = work.groupby(event_col, sort=True)
    out = pd.DataFrame(index=grouped.size().index)
    out.index.name = event_col
    out["n_rows"] = grouped.size()
    out["n_fired"] = grouped["_fired"].sum().astype(int)
    for col in ret_cols:
        out[f"mean_signed__{col}"] = grouped[f"_signed__{col}"].mean()
    if spread_col is not None:
        out["mean_spread_ticks"] = grouped["_spread"].mean()
    out["fired_floor_met"] = out["n_fired"] >= int(min_fired_rows)
    out = out.reset_index()
    out["month"] = out[event_col].map(_month_of)

    return EventStats(
        table=out,
        n_events_total=int(len(out)),
        n_events_fired=int(out["fired_floor_met"].sum()),
    )


def spearman_ic_per_event(
    frame: pd.DataFrame,
    *,
    signal_col: str,
    ret_col: str,
    event_col: str = "event_id",
    min_rows: int = 10,
) -> pd.DataFrame:
    """Per-event Spearman rank IC between signal and forward return.

    Rank correlation computed within each event (never pooled across events —
    pooling manufactures IC from cross-event level differences). Events with
    fewer than ``min_rows`` usable rows yield NaN.
    """
    rows = []
    for event_id, g in frame.groupby(event_col, sort=True):
        s = g[signal_col]
        r = g[ret_col]
        ok = s.notna() & r.notna()
        n = int(ok.sum())
        if n >= int(min_rows) and s[ok].nunique() > 1 and r[ok].nunique() > 1:
            ic = float(s[ok].rank().corr(r[ok].rank()))
        else:
            ic = float("nan")
        rows.append({event_col: event_id, "n": n, "ic": ic, "month": _month_of(event_id)})
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Two-way clustered inference (Cameron-Gelbach-Miller for a mean)
# ---------------------------------------------------------------------------

def _cluster_variance_of_mean(x: np.ndarray, clusters: np.ndarray) -> float:
    """Cluster-robust variance of the sample mean under one-way clustering."""
    n = len(x)
    centered = x - x.mean()
    total = 0.0
    for key in pd.unique(clusters):
        s = float(centered[clusters == key].sum())
        total += s * s
    return total / (n * n)


def clustered_t_two_way(
    values: Sequence[float],
    cluster_a: Sequence[object],
    cluster_b: Sequence[object],
) -> tuple[float, float, int]:
    """Two-way cluster-robust t-test of H0: mean == 0.

    Cameron-Gelbach-Miller: V = V_A + V_B - V_(A x B). If the CGM variance is
    non-positive (possible in finite samples), fail conservative to
    max(V_A, V_B). Degrees of freedom = min(#clusters_A, #clusters_B) - 1
    (standard CGM practice). Returns (t_stat, p_value_two_sided, dof).
    """
    x = np.asarray(list(values), dtype=np.float64)
    a = np.asarray(list(cluster_a), dtype=object)
    b = np.asarray(list(cluster_b), dtype=object)
    if not (len(x) == len(a) == len(b)):
        raise ValueError("values/cluster_a/cluster_b length mismatch")
    keep = np.isfinite(x)
    x, a, b = x[keep], a[keep], b[keep]
    n = len(x)
    if n < 3:
        return float("nan"), float("nan"), 0

    inter = np.array([f"{ai}\x1f{bi}" for ai, bi in zip(a, b)], dtype=object)
    v_a = _cluster_variance_of_mean(x, a)
    v_b = _cluster_variance_of_mean(x, b)
    v_ab = _cluster_variance_of_mean(x, inter)
    v = v_a + v_b - v_ab
    if v <= 0.0:
        v = max(v_a, v_b)
    if v <= 0.0:
        return float("nan"), float("nan"), 0

    g_a = len(pd.unique(a))
    g_b = len(pd.unique(b))
    dof = max(1, min(g_a, g_b) - 1)
    t_stat = float(x.mean() / math.sqrt(v))
    p = 2.0 * _student_t_sf(abs(t_stat), dof)
    return t_stat, float(min(1.0, p)), dof


def _student_t_sf(t: float, dof: int) -> float:
    """Student-t survival function P(T > t) via scipy's regularized beta.

    scipy is already a hard transitive dependency (scikit-learn); using its
    ``betainc`` avoids a hand-rolled continued fraction with its own failure
    modes in the tails — exactly where p-values matter.
    """
    from scipy.special import betainc

    if not math.isfinite(t):
        return float("nan")
    x = dof / (dof + t * t)
    return float(0.5 * betainc(dof / 2.0, 0.5, x))


# ---------------------------------------------------------------------------
# Multiple testing + hurdle
# ---------------------------------------------------------------------------

def bh_reject(p_values: Sequence[float], q: float) -> list[bool]:
    """Benjamini-Hochberg rejection mask at FDR level ``q``.

    Delegates the adjustment to the repo's existing
    ``statistics.p_value_correction(..., "bh")`` — one implementation only.
    NaN p-values are never rejected.
    """
    ps = [float(p) for p in p_values]
    finite = [p for p in ps if math.isfinite(p)]
    adjusted_finite = p_value_correction(finite, "bh")
    it = iter(adjusted_finite)
    out: list[bool] = []
    for p in ps:
        if math.isfinite(p):
            out.append(next(it) <= float(q))
        else:
            out.append(False)
    return out


def hurdle_ticks(
    *,
    fee_per_side: float,
    contract_multiplier: float,
    tick_size: float,
    slippage_ticks: float = 1.0,
) -> Mapping[str, float]:
    """Cost hurdle per HYPOTHESIS_SPEC_TEMPLATE section 4, in points and ticks.

    ``hurdle = 2 x per-side all-in fee / contract_multiplier + slippage``.
    Pure arithmetic — the caller resolves authoritative numbers from
    ``instrument_specs.py`` / ``fee_model.py``.
    """
    if fee_per_side < 0 or contract_multiplier <= 0 or tick_size <= 0:
        raise ValueError("fee_per_side>=0, contract_multiplier>0, tick_size>0 required")
    fee_points = 2.0 * float(fee_per_side) / float(contract_multiplier)
    fee_ticks = fee_points / float(tick_size)
    total_ticks = fee_ticks + float(slippage_ticks)
    return {
        "fee_points": fee_points,
        "fee_ticks": fee_ticks,
        "slippage_ticks": float(slippage_ticks),
        "hurdle_ticks": total_ticks,
        "hurdle_points": total_ticks * float(tick_size),
    }


def censoring_rate_per_event(
    frame: pd.DataFrame,
    *,
    ret_col: str,
    signal_col: str,
    threshold: float,
    event_col: str = "event_id",
) -> pd.DataFrame:
    """Share of FIRED rows whose forward return is censored (NaN) per event.

    Horizons that run past the tape end are NaN in ``build_labels_frame``;
    near-window-close censoring systematically drops the largest post-event
    moves (biases momentum down, reversion up). Callers exclude events whose
    fired-row censoring exceeds their cutoff (plan: 20%).
    """
    sig = frame[signal_col].to_numpy(dtype=np.float64)
    fired = np.abs(sig) > float(threshold)
    work = frame[[event_col]].copy()
    work["_fired"] = fired
    work["_censored_fired"] = fired & frame[ret_col].isna().to_numpy()
    g = work.groupby(event_col, sort=True)
    out = pd.DataFrame(index=g.size().index)
    out.index.name = event_col
    n_fired = g["_fired"].sum()
    out["n_fired"] = n_fired.astype(int)
    out["n_censored"] = g["_censored_fired"].sum().astype(int)
    with np.errstate(invalid="ignore", divide="ignore"):
        out["censoring_rate"] = np.where(
            n_fired > 0, out["n_censored"] / n_fired, np.nan
        )
    return out.reset_index()
