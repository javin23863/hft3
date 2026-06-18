"""Shared HftBacktest asset builder (latency bands + queue models).

Level-2 vs Level-3 auto-detection
-----------------------------------
Real lake NPZs from the CME MBO feed contain Level-3 events:
  ADD_ORDER_EVENT (10), CANCEL_ORDER_EVENT (11), MODIFY_ORDER_EVENT (12),
  FILL_EVENT (13), TRADE_EVENT (2) — no DEPTH_EVENT (1).

Synthetic DEPTH-event fixtures (unit tests, crypto converters) contain Level-2
events: DEPTH_EVENT (1) and TRADE_EVENT (2).

This module detects which tier is present by examining the low-8-bits of the ev
field.  Detection rule:
  * If ADD_ORDER_EVENT (10) events are present AND DEPTH_EVENT (1) events are
    absent → L3 path.
  * Otherwise → L2 path.

An explicit ``force_l3`` override is also supported.

Level-3 queue model
--------------------
The L3 path uses ``asset.l3_fifo_queue_model()`` (L3FIFOQueueModel).  This model
tracks exact queue position per order_id and does NOT use the LogProbQueueModel2
probability calculation — that L2-only model would produce nonsensical results on
L3 data.  The ``queue_model_type`` parameter is only applied on the L2 path.

Level-3 orphan-event filtering
---------------------------------
Real lake captures start mid-session: orders placed before the recording window
appear as CANCEL/MODIFY/FILL events whose ADD_ORDER_EVENT was never recorded.
hftbacktest's L3 engine errors (returns 12) when it encounters an action on an
unknown order_id.  We pre-scan the event array and drop any CANCEL/MODIFY/FILL
whose order_id was never seen in a prior ADD_ORDER_EVENT within the same array.
This is the correct treatment: the engine has no prior book state for those
orders and cannot simulate their queue position.
"""
from __future__ import annotations

import atexit
import os
import tempfile
from typing import Any, Mapping, Optional

import numpy as np

from hftbacktest import BacktestAsset, HashMapMarketDepthBacktest
from hftbacktest.types import (
    ADD_ORDER_EVENT,
    CANCEL_ORDER_EVENT,
    DEPTH_EVENT,
    FILL_EVENT,
    MODIFY_ORDER_EVENT,
    event_dtype,
)

from backtest_pipeline.src.fee_model import FeeModel

# ---------------------------------------------------------------------------
# Temp-file registry
#
# hftbacktest reads NPZ files LAZILY (on demand at first step, not at
# construction time).  When we write orphan-filtered L3 events to a temp file
# and pass its path to BacktestAsset.data(), the file must remain on disk for
# the entire lifetime of the resulting HashMapMarketDepthBacktest instance.
#
# Since the HashMapMarketDepthBacktest jitclass cannot hold Python-level
# attributes (numba restriction), we instead register each temp file with
# atexit so it is cleaned up when the Python process exits — well after any
# backtest has completed.  For long-running servers the list is bounded by the
# number of distinct build_hftbacktest() calls made during the process lifetime.
# ---------------------------------------------------------------------------
_l3_tmp_files: list[str] = []


def _cleanup_l3_tmp_files() -> None:
    for p in _l3_tmp_files:
        try:
            os.unlink(p)
        except OSError:
            pass
    _l3_tmp_files.clear()


atexit.register(_cleanup_l3_tmp_files)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _apply_constant_latency(asset: BacktestAsset, entry_ns: int, resp_ns: int) -> None:
    """Set constant order latency using whichever method the installed hftbacktest exposes.

    hftbacktest 2.4+ renamed ``constant_latency`` to ``constant_order_latency`` (with a
    deprecation warning on the old name). hftbacktest 2.3 only ships the old name.
    Pick whichever the running build provides.
    """
    if hasattr(asset, "constant_order_latency"):
        asset.constant_order_latency(entry_ns, resp_ns)
    else:
        asset.constant_latency(entry_ns, resp_ns)


def _load_events(data_path: str) -> np.ndarray:
    """Load the event array from an NPZ file (key='data')."""
    d = np.load(data_path, allow_pickle=False)
    return d["data"]


from backtest_pipeline.src.l3_orphan_filter import filter_l3_orphans as _filter_l3_orphans
from backtest_pipeline.src.l3_orphan_filter import is_l3_data as _is_l3_data
# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

LATENCY_BANDS_MS = [0.5, 1.0, 2.0, 5.0, 10.0]

# Queue model builders — L2 path only.
# The L3 path always uses l3_fifo_queue_model(); queue_model_type is ignored for L3.
QUEUE_MODEL_BUILDERS = {
    "LogProbQueueModel2": lambda a: a.log_prob_queue_model2(),
    "SquareProbQueueModel": lambda a: a.power_prob_queue_model2(2),
}
QUEUE_MODELS = list(QUEUE_MODEL_BUILDERS.keys())


# ---------------------------------------------------------------------------
# Public builder
# ---------------------------------------------------------------------------

def _apply_latency_model(
    asset: BacktestAsset,
    *,
    latency_ms: float,
    latency_model: Mapping[str, Any] | None = None,
) -> None:
    """Apply ConstantLatency or IntpOrderLatency from an optional latency_model dict."""
    if latency_model and str(latency_model.get("latency_model_family")) == "IntpOrderLatency":
        sample_path = latency_model.get("latency_sample_artifact")
        if sample_path and hasattr(asset, "intp_order_latency"):
            import numpy as np

            samples = np.load(str(sample_path), allow_pickle=False)
            asset.intp_order_latency(samples)
            return

    entry_ms = latency_ms
    resp_ms = latency_ms
    if latency_model:
        entry_val = latency_model.get("order_entry_latency_ms")
        resp_val = latency_model.get("order_response_latency_ms")
        if isinstance(entry_val, (int, float)):
            entry_ms = float(entry_val)
        if isinstance(resp_val, (int, float)):
            resp_ms = float(resp_val)
    entry_ns = int(entry_ms * 1_000_000)
    resp_ns = int(resp_ms * 1_000_000)
    _apply_constant_latency(asset, entry_ns, resp_ns)


def build_hftbacktest(
    data_path: str,
    *,
    latency_ms: float = 1.0,
    latency_model: Mapping[str, Any] | None = None,
    queue_model_type: str = "LogProbQueueModel2",
    tick_size: float = 0.25,
    lot_size: float = 1.0,
    product: str = "MES",
    force_l3: Optional[bool] = None,
    prepared_data: bool = False,
) -> HashMapMarketDepthBacktest:
    """Build a HashMapMarketDepthBacktest for the given NPZ data file.

    Automatically detects whether the data is Level-2 (DEPTH_EVENT) or Level-3
    (ADD/CANCEL/MODIFY/FILL events) and configures the appropriate queue model:

    * **L2 path**: ``queue_model_type`` is applied (LogProbQueueModel2 or
      SquareProbQueueModel); exchange model is ``no_partial_fill_exchange``.
    * **L3 path**: ``l3_fifo_queue_model()`` is always used (``queue_model_type``
      is ignored); exchange model is ``no_partial_fill_exchange``; orphan
      CANCEL/MODIFY/FILL events are filtered before ingestion.

    Args:
        data_path: Path to the NPZ file (key ``data``, dtype ``event_dtype``).
        latency_ms: Fallback scalar when latency_model absent. Also used as combined
            latency when latency_model has no separate entry/response fields.
        latency_model: Optional HftBacktest latency_model.json dict with separate
            order_entry_latency_ms / order_response_latency_ms or IntpOrderLatency samples.
        queue_model_type: Queue model for the **L2 path only**.  Ignored on L3.
            One of ``"LogProbQueueModel2"`` (default) or ``"SquareProbQueueModel"``.
        tick_size: Instrument tick size.
        lot_size: Instrument lot size.
        product: Instrument name for fee-model lookup.
        force_l3: Override auto-detection.  ``True`` forces L3; ``False`` forces
            L2; ``None`` (default) auto-detects from the event array.
        prepared_data: When True, skip L3 orphan filtering — data_path is
            already content-addressed prepared replay output.

    Returns:
        A ready-to-use ``HashMapMarketDepthBacktest`` instance.
    """
    if queue_model_type not in QUEUE_MODEL_BUILDERS:
        raise ValueError(f"Unsupported queue model: {queue_model_type}")

    fee_model = FeeModel(product=product)
    latency_ns = int(latency_ms * 1_000_000)

    # Load events for detection (and potentially for L3 inline filtering).
    events = _load_events(data_path)

    if force_l3 is None:
        use_l3 = _is_l3_data(events)
    else:
        use_l3 = force_l3

    asset = BacktestAsset()

    if use_l3 and not prepared_data:
        # L3 path: filter orphan events, write to a temp NPZ, and pass the file path.
        #
        # IMPORTANT: hftbacktest reads NPZ files LAZILY — the file must stay on disk
        # for the entire lifetime of the backtest, not just during construction.
        # We write to a temp file registered with atexit for cleanup at process exit.
        # Passing a numpy array via asset.data(array) triggers an immediate copy, but
        # causes Windows access violations in numba jitclass boxing on subsequent
        # depth(0).best_bid calls.  The file-path approach is robust.
        #
        # The L3FIFOQueueModel tracks exact queue position per order_id; no
        # probability calculation is applied — LogProbQueueModel2 is a L2-only concept.
        filtered = _filter_l3_orphans(events)
        fd, tmp_path = tempfile.mkstemp(suffix=".npz")
        os.close(fd)
        np.savez_compressed(tmp_path, data=filtered)
        _l3_tmp_files.append(tmp_path)
        asset.data(tmp_path)
    elif use_l3 and prepared_data:
        asset.data(data_path)
    else:
        # L2 path: pass file path directly (no filtering needed).
        asset.data(data_path)

    asset.tick_size(tick_size)
    asset.lot_size(lot_size)
    _apply_latency_model(asset, latency_ms=latency_ms, latency_model=latency_model)
    asset.no_partial_fill_exchange()

    fee = fee_model.get_fee_per_contract()
    asset.trading_qty_fee_model(fee, fee)

    if use_l3:
        # L3FIFOQueueModel: exact FIFO queue position tracked by order_id.
        # No probability model — fills happen when the engine's order reaches
        # the front of the queue as ADD/CANCEL/FILL events advance it.
        asset.l3_fifo_queue_model()
    else:
        # L2 path: apply the requested probability-based queue model.
        QUEUE_MODEL_BUILDERS[queue_model_type](asset)

    return HashMapMarketDepthBacktest([asset])
