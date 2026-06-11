"""
VIX options activity features for CME event windows.

Data limitations
----------------
The VIX options quote lake (OPRA feed via Databento) carries no strike, right
(call/put), or expiry per row — only instrument_id.  Consequently:

  * No Black-Scholes IV surface can be computed without a separate reference
    mapping (not available at inference time).
  * No put-call decomposition is possible.  ``vix_opt_depth_imbalance`` is
    therefore a *labeled proxy* for the options-order-imbalance decomposition
    of [11 Options Microstructure] 2023 (Muravyev & Ni); it captures aggregate
    signed depth pressure across all VIX option instruments without separating
    the directional put/call gamma flows described in that paper.

Silent-zero prohibition (defect ke — specs/CRYPTO_LIVE.md §9)
--------------------------------------------------------------
Absent data MUST NOT be zero-filled and returned as if valid.  When the
necessary source data is unavailable the function raises ``VixDataUnavailable``
and writes no output file.  Optional derived columns whose sensor feed is
absent are *omitted* from the file entirely and recorded in ``attrs
['columns_omitted']``.  Consumers must treat a missing column as unknown, not
as zero.

Timestamp policy
----------------
Primary clock: ``ts_recv`` (availability time; absorbs OPRA-vs-CME exchange
clock skew).  Output ``ts`` = ts_recv + vix_extra_latency_ms * 1e6 ns (a
configurable downstream latency shift).  Raw originals ``ts_event_raw`` and
``ts_recv_raw`` are preserved for audit.  Grid rows are causal: each 100 ms
grid point uses only quotes whose shifted ts <= grid ts.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


# ---------------------------------------------------------------------------
# Public exception
# ---------------------------------------------------------------------------

class VixDataUnavailable(RuntimeError):
    """Raised when source data is missing or entirely placeholder."""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_VIX_OPT_PREFIX = re.compile(r"^VIX\.OPT_", re.IGNORECASE)
_QUOTES_SUFFIX = re.compile(r"_quotes\.npz$", re.IGNORECASE)


def derive_event_id(quotes_npz_name: str) -> str:
    """Strip ``VIX.OPT_`` prefix and ``_quotes.npz`` suffix from filename.

    Examples
    --------
    >>> derive_event_id("VIX.OPT_FED_BEIGE_BOOK_2023_04_19_TIGHT_quotes.npz")
    'FED_BEIGE_BOOK_2023_04_19_TIGHT'
    """
    name = Path(quotes_npz_name).name
    name = _VIX_OPT_PREFIX.sub("", name)
    name = _QUOTES_SUFFIX.sub("", name)
    return name


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

_NS_PER_MS = 1_000_000
_NS_PER_S = 1_000_000_000
_GRID_NS = 100 * _NS_PER_MS  # 100 ms grid


def _load_quotes(quotes_npz: Path) -> np.ndarray:
    """Load structured quotes array; raise VixDataUnavailable if empty/missing."""
    if not quotes_npz.exists():
        raise VixDataUnavailable(f"Quotes npz not found: {quotes_npz}")
    data = np.load(quotes_npz, allow_pickle=False)
    if "quotes" not in data:
        raise VixDataUnavailable(f"Key 'quotes' missing in {quotes_npz}")
    q = data["quotes"]
    if q.size == 0:
        raise VixDataUnavailable(f"Quotes array empty in {quotes_npz}")
    return q


def _load_sensor_atm(sensors_parquet: Path | None) -> pd.DataFrame | None:
    """Return VIX_ATM_STRIKE rows, or None when absent/placeholder-only.

    Placeholder definition: rows where sensor == 'VIX' and level is NaN.
    Such rows carry no real data and must be treated as NO DATA per spec.
    """
    if sensors_parquet is None or not sensors_parquet.exists():
        return None
    df = pd.read_parquet(sensors_parquet)
    real = df[df["sensor"] == "VIX_ATM_STRIKE"].copy()
    if real.empty:
        return None
    real = real.dropna(subset=["level"])
    if real.empty:
        return None
    real = real.sort_values("ts_ns").reset_index(drop=True)
    return real


def _build_grid(ts_min: int, ts_max: int) -> np.ndarray:
    """Build 100 ms grid from ts_min to ts_max (inclusive), int64 ns."""
    start = (ts_min // _GRID_NS) * _GRID_NS
    stop = ((ts_max // _GRID_NS) + 1) * _GRID_NS
    return np.arange(start, stop + 1, _GRID_NS, dtype=np.int64)


def _ffill_sensor_onto_grid(
    sensor_ts: np.ndarray,  # int64 ns, sorted
    sensor_val: np.ndarray,  # float64
    grid: np.ndarray,        # int64 ns
) -> np.ndarray:
    """Step-forward-fill sensor values onto grid; NaN before first sample."""
    out = np.full(len(grid), np.nan, dtype=np.float64)
    idx = np.searchsorted(sensor_ts, grid, side="right") - 1
    valid = idx >= 0
    out[valid] = sensor_val[idx[valid]]
    return out


def _slope_trailing_n(vals: np.ndarray, n: int = 3) -> np.ndarray:
    """Slope of last n available (non-NaN) samples; NaN until 2 samples."""
    out = np.full(len(vals), np.nan, dtype=np.float64)
    for i in range(len(vals)):
        segment = vals[: i + 1]
        mask = ~np.isnan(segment)
        xs = np.where(mask)[0]
        if len(xs) < 2:
            continue
        xs = xs[-n:]
        ys = segment[xs]
        if len(xs) < 2:
            continue
        # simple linear slope via first differences normalised by index gap
        out[i] = float(np.polyfit(xs, ys, 1)[0])
    return out


def _quote_intensity_1s(shifted_ts: np.ndarray, grid: np.ndarray) -> np.ndarray:
    """Count quotes in trailing 1 s window at each grid point (causal)."""
    out = np.empty(len(grid), dtype=np.float64)
    for i, g in enumerate(grid):
        lo = g - _NS_PER_S
        count = int(np.sum((shifted_ts > lo) & (shifted_ts <= g)))
        out[i] = float(count)
    return out


def _arrival_accel(intensity: np.ndarray, grid: np.ndarray) -> np.ndarray:
    """intensity(1s) − mean of prior 5 per-second samples; NaN until 5 prior."""
    out = np.full(len(grid), np.nan, dtype=np.float64)
    # Build 1 s intensity at each point first (already done), then look back 5
    # grid slots corresponding to 5 s worth of data at 100 ms grid.
    steps_per_second = int(_NS_PER_S / _GRID_NS)  # 10
    prior_steps = 5 * steps_per_second  # 50 grid points = 5 s
    for i in range(prior_steps, len(grid)):
        prior_mean = float(np.mean(intensity[i - prior_steps: i]))
        out[i] = intensity[i] - prior_mean
    return out


def _spread_stress(
    shifted_ts: np.ndarray,
    bid_px: np.ndarray,
    ask_px: np.ndarray,
    instr_id: np.ndarray,
    grid: np.ndarray,
) -> np.ndarray:
    """Median (ask-bid)/mid over instruments active in trailing 5 s."""
    out = np.full(len(grid), np.nan, dtype=np.float64)
    window = 5 * _NS_PER_S
    for i, g in enumerate(grid):
        lo = g - window
        mask = (shifted_ts > lo) & (shifted_ts <= g)
        if not np.any(mask):
            continue
        ids = instr_id[mask]
        bids = bid_px[mask]
        asks = ask_px[mask]
        # latest quote per instrument
        ratios = []
        for uid in np.unique(ids):
            m2 = ids == uid
            last = int(np.where(m2)[0][-1])
            b, a = bids[last], asks[last]
            mid = (b + a) / 2.0
            if mid > 0 and b > 0 and np.isfinite(a):
                ratios.append((a - b) / mid)
        if ratios:
            out[i] = float(np.median(ratios))
    return out


def _depth_imbalance(
    shifted_ts: np.ndarray,
    bid_px: np.ndarray,
    ask_px: np.ndarray,
    bid_sz: np.ndarray,
    ask_sz: np.ndarray,
    instr_id: np.ndarray,
    grid: np.ndarray,
) -> np.ndarray:
    """(Σbid_sz - Σask_sz)/(Σbid_sz + Σask_sz) latest quote per instrument, trailing 5 s."""
    out = np.full(len(grid), np.nan, dtype=np.float64)
    window = 5 * _NS_PER_S
    for i, g in enumerate(grid):
        lo = g - window
        mask = (shifted_ts > lo) & (shifted_ts <= g)
        if not np.any(mask):
            continue
        ids = instr_id[mask]
        bids_m = bid_sz[mask]
        asks_m = ask_sz[mask]
        total_bid = 0.0
        total_ask = 0.0
        for uid in np.unique(ids):
            m2 = ids == uid
            last = int(np.where(m2)[0][-1])
            total_bid += float(bids_m[last])
            total_ask += float(asks_m[last])
        denom = total_bid + total_ask
        if denom > 0:
            out[i] = (total_bid - total_ask) / denom
    return out


def _most_quoted_instrument(
    shifted_ts: np.ndarray,
    instr_id: np.ndarray,
    grid_end: int,
    window_ns: int,
) -> int | None:
    """Return instrument_id with most quotes in window ending at grid_end."""
    lo = grid_end - window_ns
    mask = (shifted_ts > lo) & (shifted_ts <= grid_end)
    if not np.any(mask):
        return None
    ids, counts = np.unique(instr_id[mask], return_counts=True)
    return int(ids[np.argmax(counts)])


def _log_mid_returns_trailing(
    shifted_ts: np.ndarray,
    bid_px: np.ndarray,
    ask_px: np.ndarray,
    instr_id: np.ndarray,
    target_id: int,
    grid_end: int,
    window_ns: int,
) -> np.ndarray:
    """Log-mid returns of target instrument in trailing window, causal."""
    lo = grid_end - window_ns
    mask = (shifted_ts > lo) & (shifted_ts <= grid_end) & (instr_id == target_id)
    if np.sum(mask) < 2:
        return np.array([], dtype=np.float64)
    b = bid_px[mask]
    a = ask_px[mask]
    mid = (b + a) / 2.0
    pos = mid > 0
    if np.sum(pos) < 2:
        return np.array([], dtype=np.float64)
    log_mid = np.log(mid[pos])
    return np.diff(log_mid)


def _bipower_var(returns: np.ndarray) -> float:
    """BNS bipower variation Σ|r_i||r_{i-1}| * π/2; NaN if < 2 returns."""
    if len(returns) < 2:
        return np.nan
    return float(np.sum(np.abs(returns[1:]) * np.abs(returns[:-1])) * (np.pi / 2.0))


def _tsrv(returns: np.ndarray, slow_scale: int = 5) -> float:
    """Two-scale realised variance (Zhang-Mykland-Ait-Sahalia 2005).

    Slow scale K ticks; fast scale 1 tick.  Small-n caveat applies when
    fewer than 2*slow_scale+1 returns are available (attr 'tsrv_small_n').
    Returns NaN when insufficient data.
    """
    n = len(returns)
    if n < slow_scale + 1:
        return np.nan
    # fast-scale RV
    rv_fast = float(np.sum(returns ** 2))
    # slow-scale RV: average over K sub-sampled series
    rv_slow_parts = []
    for offset in range(slow_scale):
        sub = returns[offset::slow_scale]
        if len(sub) > 0:
            rv_slow_parts.append(float(np.sum(sub ** 2)))
    if not rv_slow_parts:
        return np.nan
    rv_slow = float(np.mean(rv_slow_parts))
    # bias correction
    correction = float(n) / float(slow_scale)
    tsrv_val = rv_slow - rv_fast / correction
    return tsrv_val


def _bipower_series(
    shifted_ts: np.ndarray,
    bid_px: np.ndarray,
    ask_px: np.ndarray,
    instr_id: np.ndarray,
    grid: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    """Compute bipower var and tsrv at each grid point, trailing 10 s."""
    window = 10 * _NS_PER_S
    bpv = np.full(len(grid), np.nan, dtype=np.float64)
    tsrv_arr = np.full(len(grid), np.nan, dtype=np.float64)
    for i, g in enumerate(grid):
        uid = _most_quoted_instrument(shifted_ts, instr_id, g, window)
        if uid is None:
            continue
        rets = _log_mid_returns_trailing(
            shifted_ts, bid_px, ask_px, instr_id, uid, g, window
        )
        if len(rets) < 2:
            continue
        bpv[i] = _bipower_var(rets)
        tsrv_arr[i] = _tsrv(rets)
    return bpv, tsrv_arr


# ---------------------------------------------------------------------------
# Main public function
# ---------------------------------------------------------------------------

def build_vix_feature_file(
    quotes_npz: Path,
    sensors_parquet: Path | None,
    out_path: Path,
    *,
    vix_extra_latency_ms: float = 0.0,
) -> dict[str, Any]:
    """Build and write VIX options feature npz for one event window.

    Parameters
    ----------
    quotes_npz:
        Path to ``VIX.OPT_<event_id>_quotes.npz`` containing key ``quotes``
        with structured dtype (ts_event i8, ts_recv i8, bid_px f8, ask_px f8,
        bid_sz f8, ask_sz f8, instrument_id u8, publisher_id u4).
    sensors_parquet:
        Path to ``<event_id>_sensors.parquet`` or None.  Rows where
        sensor=='VIX' with level NaN are placeholder-only and treated as NO
        DATA (defect ke, specs/CRYPTO_LIVE.md §9).
    out_path:
        Destination npz path.  Parent dirs created if needed.
    vix_extra_latency_ms:
        Downstream latency shift in milliseconds added to ts_recv to produce
        the output ``ts`` column.  Default 0.

    Returns
    -------
    dict with keys: n_rows, columns, event_id.

    Raises
    ------
    VixDataUnavailable
        When quotes_npz is missing/empty AND sensors_parquet is absent or
        placeholder-only.  Also when quotes_npz alone is missing/empty.
    """
    # --- derive event_id ---
    event_id = derive_event_id(quotes_npz.name)

    # --- load quotes (required) ---
    q = _load_quotes(quotes_npz)

    ts_event_raw: np.ndarray = q["ts_event"].astype(np.int64)
    ts_recv_raw: np.ndarray = q["ts_recv"].astype(np.int64)
    bid_px: np.ndarray = q["bid_px"].astype(np.float64)
    ask_px: np.ndarray = q["ask_px"].astype(np.float64)
    bid_sz: np.ndarray = q["bid_sz"].astype(np.float64)
    ask_sz: np.ndarray = q["ask_sz"].astype(np.float64)
    instr_id: np.ndarray = q["instrument_id"].astype(np.uint64)

    latency_shift_ns = int(round(vix_extra_latency_ms * _NS_PER_MS))
    shifted_ts = ts_recv_raw + latency_shift_ns

    # --- build 100 ms grid from unshifted clock, then shift for output ---
    grid = _build_grid(int(ts_recv_raw.min()), int(ts_recv_raw.max()))
    n_grid = len(grid)

    columns_omitted: list[str] = []

    # --- sensor: vix_atm_strike ---
    atm_df = _load_sensor_atm(sensors_parquet)
    if atm_df is not None:
        sensor_ts_ns = atm_df["ts_ns"].to_numpy(dtype=np.int64) + latency_shift_ns
        sensor_vals = atm_df["level"].to_numpy(dtype=np.float64)
        vix_atm_strike = _ffill_sensor_onto_grid(sensor_ts_ns, sensor_vals, grid)
        vix_atm_ramp = _slope_trailing_n(vix_atm_strike, n=3)
    else:
        columns_omitted.extend(["vix_atm_strike", "vix_atm_ramp"])
        vix_atm_strike = None
        vix_atm_ramp = None

    # --- intensity ---
    vix_opt_quote_intensity = _quote_intensity_1s(ts_recv_raw, grid)
    vix_quote_arrival_accel = _arrival_accel(vix_opt_quote_intensity, grid)

    # --- spread stress ---
    vix_opt_spread_stress = _spread_stress(
        ts_recv_raw, bid_px, ask_px, instr_id, grid
    )

    # --- depth imbalance ---
    vix_opt_depth_imbalance = _depth_imbalance(
        ts_recv_raw, bid_px, ask_px, bid_sz, ask_sz, instr_id, grid
    )

    # --- bipower var + tsrv ---
    vix_opt_bipower_var, vix_opt_tsrv = _bipower_series(
        ts_recv_raw, bid_px, ask_px, instr_id, grid
    )

    # --- assemble output columns ---
    # ts_out = unshifted grid + latency shift, so ts reflects availability clock
    ts_out = grid + latency_shift_ns

    # audit columns: ffill last-known raw timestamp at each grid point
    _idx = np.searchsorted(ts_recv_raw, grid, side="right") - 1
    _idx_clipped = np.clip(_idx, 0, len(shifted_ts) - 1)
    ts_event_raw_grid = ts_event_raw[_idx_clipped].astype(np.int64)
    ts_recv_raw_grid = ts_recv_raw[_idx_clipped].astype(np.int64)

    arrays: dict[str, np.ndarray] = {
        "ts": ts_out,
        "ts_event_raw": ts_event_raw_grid,
        "ts_recv_raw": ts_recv_raw_grid,
        "vix_opt_quote_intensity": vix_opt_quote_intensity,
        "vix_quote_arrival_accel": vix_quote_arrival_accel,
        "vix_opt_spread_stress": vix_opt_spread_stress,
        "vix_opt_depth_imbalance": vix_opt_depth_imbalance,
        "vix_opt_bipower_var": vix_opt_bipower_var,
        "vix_opt_tsrv": vix_opt_tsrv,
    }
    if vix_atm_strike is not None:
        arrays["vix_atm_strike"] = vix_atm_strike
        arrays["vix_atm_ramp"] = vix_atm_ramp

    present_columns = [k for k in arrays if k not in ("ts", "ts_event_raw", "ts_recv_raw")]

    # --- write ---
    out_path.parent.mkdir(parents=True, exist_ok=True)
    np.savez(
        out_path,
        **arrays,
    )

    # Reload and attach attrs via allow_pickle trick — npz doesn't natively
    # support attrs; store as a separate metadata array encoded as object array.
    attrs: dict[str, Any] = {
        "feature_version": "vixf_v1",
        "ts_policy": "ts_recv",
        "vix_extra_latency_ms": vix_extra_latency_ms,
        "event_id": event_id,
        "columns": present_columns,
        "columns_omitted": columns_omitted,
        "vix_quality": 1.0,
        "tsrv_small_n_caveat": (
            "tsrv estimated on short windows (<2*slow_scale+1 returns); "
            "treat with caution for n_returns < 11"
        ),
    }
    # Re-save with attrs encoded as structured string array
    np.savez(
        out_path,
        **arrays,
        _attrs_json=np.array([str(attrs)], dtype=object),
    )

    return {
        "n_rows": n_grid,
        "columns": present_columns,
        "event_id": event_id,
    }
