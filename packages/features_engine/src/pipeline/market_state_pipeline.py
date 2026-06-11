"""
Builds full MarketState X_t from MBO events: features, regime posterior, event context.

Backend selection via HFT3_FEATURE_BACKEND env var (default: "auto"):
  - "auto"   : use C++ pybind module when available, else python (one-time warning)
  - "cpp"    : C++ only — hard-fails at init time if module not loadable
  - "python" : pure-Python path (ablation / debug)
"""
from __future__ import annotations

import logging
import os
import warnings
from dataclasses import dataclass, field
from typing import Dict, Optional, Tuple

import numpy as np

from features_engine.src.features.feature_index import (
    FEATURE_DIM,
    FeatureIndex,
    REGIME_INDEX_MAP,
    vector_to_feature_dict,
)
from features_engine.src.features.mbo_features import MBOEvent, MBOFeatureExtractor
from features_engine.src.hypotheses.modules import MarketState
from features_engine.src.regime.event_context import EventContextEngine
from features_engine.src.regime.regime_filter import RegimeFilter

_log = logging.getLogger(__name__)

# Feature keys consumed by RegimeFilter.update() — only these are needed for the
# per-event regime posterior update; the rest of the dict can be deferred.
_REGIME_FILTER_KEYS = (
    "spread_stress",
    "cancel_to_add_ratio",
    "aggressor_volume_imbalance",
    "book_slope_change",
    "liquidity_vacuum_score",
    "near_touch_cancel_pressure",
)

# Corresponding FeatureIndex values for the regime-filter keys (same order)
_REGIME_FILTER_INDICES = (
    FeatureIndex.SPREAD_STRESS,
    FeatureIndex.CANCEL_TO_ADD_RATIO,
    FeatureIndex.AGGRESSOR_VOLUME_IMBALANCE,
    FeatureIndex.BOOK_SLOPE_CHANGE,
    FeatureIndex.LIQUIDITY_VACUUM_SCORE,
    FeatureIndex.NEAR_TOUCH_CANCEL_PRESSURE,
)


def _regime_mini_dict(vec: np.ndarray) -> Dict[str, float]:
    """Build the minimal feature dict needed by RegimeFilter.update()."""
    return {k: float(vec[idx]) for k, idx in zip(_REGIME_FILTER_KEYS, _REGIME_FILTER_INDICES)}


def _resolve_backend() -> str:
    """Return the canonical backend string from env, default 'auto'."""
    raw = os.environ.get("HFT3_FEATURE_BACKEND", "auto").strip().lower()
    if raw not in ("auto", "cpp", "python"):
        warnings.warn(
            f"Unknown HFT3_FEATURE_BACKEND={raw!r}; falling back to 'auto'",
            stacklevel=3,
        )
        return "auto"
    return raw


@dataclass
class MarketStatePipeline:
    tick_size: float = 0.25
    extractor: MBOFeatureExtractor = field(default_factory=MBOFeatureExtractor)
    regime_filter: RegimeFilter = field(default_factory=RegimeFilter)
    event_engine: Optional[EventContextEngine] = None
    latency_ms: float = 1.0
    current_inventory: int = 0
    cross_asset_features: Dict[str, Dict[str, float]] = field(default_factory=dict)
    round_number_increment: Optional[float] = 10.0

    # --- running session state (not dataclass-serialised) ---
    _vwap_sum_px_qty: float = field(default=0.0, init=False, repr=False)
    _vwap_sum_qty: float = field(default=0.0, init=False, repr=False)

    _session_high: float = field(default=0.0, init=False, repr=False)
    _session_low: float = field(default=float("inf"), init=False, repr=False)

    # --- fast-path deferred state ---
    _fast_vec: Optional[np.ndarray] = field(default=None, init=False, repr=False)
    _fast_posterior: Optional[Dict[str, float]] = field(default=None, init=False, repr=False)
    _fast_event_ctx: Optional[str] = field(default=None, init=False, repr=False)
    # Pre-computed session feature values from the fast path
    _fast_dist_vwap: float = field(default=0.0, init=False, repr=False)
    _fast_spread_stress_elevated: float = field(default=0.0, init=False, repr=False)
    _fast_session_break: float = field(default=0.0, init=False, repr=False)
    _fast_dist_round: float = field(default=0.0, init=False, repr=False)
    _fast_dirty: bool = field(default=False, init=False, repr=False)
    _fast_cached_state: Optional[MarketState] = field(default=None, init=False, repr=False)

    # --- C++ backend state ---
    _cpp_extractor: Optional[object] = field(default=None, init=False, repr=False)
    _cpp_backend_active: bool = field(default=False, init=False, repr=False)
    _cpp_last_event_ctx: Optional[str] = field(default=None, init=False, repr=False)

    # --- backend provenance (read by callers / run metadata) ---
    feature_backend: str = field(default="python", init=False, repr=True)

    def __post_init__(self) -> None:
        self.extractor.tick_size = self.tick_size
        if self.event_engine is None:
            self.event_engine = EventContextEngine()

        # Backend selection
        backend = _resolve_backend()
        if backend in ("cpp", "auto"):
            from features_engine.src.features._cpp_loader import load_cpp_features
            cpp_mod = load_cpp_features()
            if cpp_mod is not None:
                self._cpp_extractor = cpp_mod.FeatureExtractor(
                    self.tick_size,
                    self.extractor.rolling_window_ns,
                )
                self._cpp_backend_active = True
                self.feature_backend = "cpp"
                _log.debug("MarketStatePipeline: using C++ backend")
            elif backend == "cpp":
                raise RuntimeError(
                    "HFT3_FEATURE_BACKEND=cpp but hft3_features_cpp module not loadable. "
                    "Build it with cmake --build build first."
                )
            else:
                warnings.warn(
                    "hft3_features_cpp not found — python extractor fallback; "
                    "research numbers will be python-path",
                    stacklevel=2,
                )
                self.feature_backend = "python"
        # backend == "python": stay with pure-Python extractor (default)

    def reset_session(self) -> None:
        """Reset intra-session accumulators (call at session boundary if known)."""
        self._vwap_sum_px_qty = 0.0
        self._vwap_sum_qty = 0.0
        self._session_high = 0.0
        self._session_low = float("inf")

    def _update_vwap(self, event: MBOEvent) -> None:
        if event.action == "TRADE" and event.size > 0:
            self._vwap_sum_px_qty += event.price * event.size
            self._vwap_sum_qty += event.size

    def _current_vwap(self) -> Optional[float]:
        if self._vwap_sum_qty > 0.0:
            return self._vwap_sum_px_qty / self._vwap_sum_qty
        return None

    def _compute_session_features(
        self, vec: np.ndarray, mid: float, update_state: bool
    ) -> Tuple[float, float, float, float]:
        """Compute session-level derived feature values given mid price.

        If update_state=True, mutates _session_high/_session_low (for per-event paths).
        Returns (dist_vwap, spread_stress_elevated, session_break, dist_round).
        """
        vwap = self._current_vwap()
        dist_vwap = (mid - vwap) / self.tick_size if vwap is not None else 0.0

        spread_stress = float(vec[FeatureIndex.SPREAD_STRESS])
        ss_elevated = 1.0 if spread_stress > 2.0 else 0.0

        prev_high = self._session_high
        prev_low = self._session_low
        if self._session_high == 0.0 and self._session_low == float("inf"):
            if update_state:
                self._session_high = mid
                self._session_low = mid
            session_break = 0.0
        elif mid > prev_high:
            if update_state:
                self._session_high = mid
            session_break = 1.0
        elif mid < prev_low:
            if update_state:
                self._session_low = mid
            session_break = -1.0
        else:
            session_break = 0.0

        if self.round_number_increment is not None and self.round_number_increment > 0:
            inc = self.round_number_increment
            remainder = mid % inc
            dist_round = min(remainder, inc - remainder) / self.tick_size
        else:
            dist_round = 0.0

        return dist_vwap, ss_elevated, session_break, dist_round

    def _cpp_process_event_get_vec(self, event: MBOEvent, event_ctx: Optional[str]) -> np.ndarray:
        """Feed event into C++ extractor, push event_ctx when it changes, return vec copy.

        event_ctx may be None when resolve_ns() returns outside a known window (treated
        as "NORMAL" — matching what EventContextEngineCpp expects for no active window).
        We never pass None to the C++ binding.
        """
        assert self._cpp_extractor is not None
        # Normalise None → "NORMAL" so the C++ binding never receives a null string.
        safe_ctx: str = event_ctx if event_ctx is not None else "NORMAL"
        # Forward event context only on change (cheap guard; also fires on first call
        # because _cpp_last_event_ctx starts as None and safe_ctx is always a str).
        if safe_ctx != self._cpp_last_event_ctx:
            self._cpp_extractor.set_event_context(safe_ctx)
            self._cpp_last_event_ctx = safe_ctx
        self._cpp_extractor.process_event(
            event.timestamp_ns,
            event.order_id,
            event.action,
            event.side,
            event.price,
            event.size,
        )
        # extract() allocates a fresh (64,) float64 ndarray per call (no persistent buffer).
        return self._cpp_extractor.extract()

    def process_event(self, event: MBOEvent) -> MarketState:
        """Fully eager path — returns a complete MarketState. Used by direct callers / tests."""
        self._update_vwap(event)

        assert self.event_engine is not None
        event_ctx = self.event_engine.resolve_ns(event.timestamp_ns)

        if self._cpp_backend_active:
            vec = self._cpp_process_event_get_vec(event, event_ctx)
            # C++ already filled regime slots 41-49; do NOT run Python RegimeFilter on vec.
            # We still need a Python posterior for MarketState fields (regime_argmax, vol, liq).
            # Re-derive it from the C++ regime slots in the vector.
            posterior = {
                regime: float(vec[idx])
                for regime, idx in REGIME_INDEX_MAP.items()
            }
        else:
            vec = self.extractor.process_event(event)
            feat_dict = vector_to_feature_dict(vec)
            posterior = self.regime_filter.update(feat_dict, event_ctx)
            for regime, prob in posterior.items():
                idx = REGIME_INDEX_MAP.get(regime)
                if idx is not None:
                    vec[idx] = prob

        regime_argmax = RegimeFilter.argmax(posterior)
        feat_dict = vector_to_feature_dict(vec)
        vol_state = self.regime_filter.volatility_state(feat_dict)
        liq_state = self.regime_filter.liquidity_state(feat_dict)

        mid = feat_dict.get("mid_price", 0.0)
        if mid > 0:
            dist_vwap, ss_elevated, session_break, dist_round = self._compute_session_features(
                vec, mid, update_state=True
            )
            vec[FeatureIndex.DISTANCE_TO_VWAP] = dist_vwap
            vec[FeatureIndex.SPREAD_STRESS_ELEVATED] = ss_elevated
            vec[FeatureIndex.IS_BREAKING_SESSION_LEVEL] = session_break
            vec[FeatureIndex.DISTANCE_TO_ROUND_NUMBER] = dist_round

        # Invalidate fast cache
        self._fast_dirty = False
        self._fast_cached_state = None

        return MarketState(
            feature_vector=vec,
            primary_features=feat_dict,
            cross_asset_features=self.cross_asset_features,
            regime_posterior=posterior,
            regime_state=regime_argmax,
            event_context=event_ctx,
            volatility_state=vol_state,
            liquidity_state=liq_state,
            latency_ms=self.latency_ms,
            current_inventory=self.current_inventory,
        )

    def process_event_fast(self, event: MBOEvent) -> None:
        """Fast per-event path used by the adapter's hot loop.

        Per-event work only:
          - VWAP accumulation (stateful, affects _current_vwap for DISTANCE_TO_VWAP)
          - Feature extraction (extractor.process_event → vec, or C++ extractor)
          - Session high/low state update (stateful; subsequent events need correct prev)
          - regime_filter.update (stateful posterior; 6-key mini dict, no full dict alloc)
            [cpp path: posterior read back from C++ slots in vec]
          - event_context.resolve_ns

        Deferred to finalize(): full feat_dict, MarketState construction, argmax, vol/liq state.
        """
        self._update_vwap(event)

        assert self.event_engine is not None
        event_ctx = self.event_engine.resolve_ns(event.timestamp_ns)

        if self._cpp_backend_active:
            vec = self._cpp_process_event_get_vec(event, event_ctx)
            # C++ slots 41-49 are the canonical regime posterior
            posterior = {
                regime: float(vec[idx])
                for regime, idx in REGIME_INDEX_MAP.items()
            }
            # Sync Python RegimeFilter's internal state so vol/liq queries remain valid.
            # We use a mini dict from the C++ vector so the Python filter mirrors C++.
            mini_dict = _regime_mini_dict(vec)
            self.regime_filter.update(mini_dict, event_ctx)
        else:
            vec = self.extractor.process_event(event)
            # Compute and apply session features (must update _session_high/_session_low per event)
            mid = float(vec[FeatureIndex.MID_PRICE])
            # Regime filter: only build the 6-key mini dict
            mini_dict = _regime_mini_dict(vec)
            posterior = self.regime_filter.update(mini_dict, event_ctx)

        # Compute and apply session features (must update _session_high/_session_low per event)
        mid = float(vec[FeatureIndex.MID_PRICE])
        if mid > 0:
            dist_vwap, ss_elevated, session_break, dist_round = self._compute_session_features(
                vec, mid, update_state=True
            )
            self._fast_dist_vwap = dist_vwap
            self._fast_spread_stress_elevated = ss_elevated
            self._fast_session_break = session_break
            self._fast_dist_round = dist_round
        else:
            self._fast_dist_vwap = 0.0
            self._fast_spread_stress_elevated = 0.0
            self._fast_session_break = 0.0
            self._fast_dist_round = 0.0

        # Copy vec (extractor may reuse the same buffer)
        self._fast_vec = vec.copy() if not self._cpp_backend_active else vec
        self._fast_posterior = posterior
        self._fast_event_ctx = event_ctx
        self._fast_dirty = True
        self._fast_cached_state = None

    def finalize(self) -> Optional[MarketState]:
        """Build and return the full MarketState from the last fast-path event.

        Returns None if process_event_fast has never been called.
        Caches the result until the next process_event_fast call.
        """
        if not self._fast_dirty:
            return self._fast_cached_state

        if self._fast_vec is None or self._fast_posterior is None or self._fast_event_ctx is None:
            return None

        vec = self._fast_vec
        posterior = self._fast_posterior
        event_ctx = self._fast_event_ctx

        if not self._cpp_backend_active:
            # Write regime probabilities into vector (python path only; C++ already filled them)
            for regime, prob in posterior.items():
                idx = REGIME_INDEX_MAP.get(regime)
                if idx is not None:
                    vec[idx] = prob

        # Write session-level features (already computed per-event, stored as scalars)
        vec[FeatureIndex.DISTANCE_TO_VWAP] = self._fast_dist_vwap
        vec[FeatureIndex.SPREAD_STRESS_ELEVATED] = self._fast_spread_stress_elevated
        vec[FeatureIndex.IS_BREAKING_SESSION_LEVEL] = self._fast_session_break
        vec[FeatureIndex.DISTANCE_TO_ROUND_NUMBER] = self._fast_dist_round

        feat_dict = vector_to_feature_dict(vec)

        regime_argmax = RegimeFilter.argmax(posterior)
        vol_state = self.regime_filter.volatility_state(feat_dict)
        liq_state = self.regime_filter.liquidity_state(feat_dict)

        state = MarketState(
            feature_vector=vec,
            primary_features=feat_dict,
            cross_asset_features=self.cross_asset_features,
            regime_posterior=posterior,
            regime_state=regime_argmax,
            event_context=event_ctx,
            volatility_state=vol_state,
            liquidity_state=liq_state,
            latency_ms=self.latency_ms,
            current_inventory=self.current_inventory,
        )
        self._fast_dirty = False
        self._fast_cached_state = state
        return state
