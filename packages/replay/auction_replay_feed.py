"""Event-time auction imbalance feed for MBO replay (filtration-safe)."""

from __future__ import annotations

from dataclasses import asdict, replace
from typing import Callable, List, Optional

import numpy as np

from features_engine.src.hypotheses.modules import MarketState
from features_engine.src.imbalance.auction import AuctionImbalanceEvent
from features_engine.src.imbalance.auction_events import window_phase_for_event
from features_engine.src.imbalance.engine import ImbalanceEngine, ImbalanceSnapshot
from features_engine.src.pipeline.market_state_pipeline import MarketStatePipeline


def auction_record(ev: AuctionImbalanceEvent) -> dict:
    rec = asdict(ev)
    rec["ts_ns"] = ev.imbalance_update_timestamp_ns
    return rec


class AuctionReplayFeed:
    """Apply auction events with ts_ns <= replay clock (no lookahead)."""

    def __init__(
        self,
        events: List[AuctionImbalanceEvent],
        *,
        event_window_id: str = "",
        window_phase_fn: Callable[[str, str], str] | None = None,
    ) -> None:
        self._events = sorted(events, key=lambda e: e.imbalance_update_timestamp_ns)
        self._idx = 0
        self._event_window_id = event_window_id
        self._window_phase_fn = window_phase_fn or window_phase_for_event

    def apply_through(
        self,
        timestamp_ns: int,
        pipeline: MarketStatePipeline,
        last_state: Optional[MarketState],
    ) -> Optional[MarketState]:
        if not self._events:
            return last_state
        engine = _ensure_imbalance_engine(pipeline)
        state = last_state
        while self._idx < len(self._events):
            ev = self._events[self._idx]
            if ev.imbalance_update_timestamp_ns > timestamp_ns:
                break
            phase = self._window_phase_fn(self._event_window_id, ev.auction_type)
            snap = engine.on_auction_event(
                auction_record(ev),
                window_phase=phase,
                event_window_id=self._event_window_id,
            )
            state = _merge_auction_snapshot(state, snap, pipeline)
            self._idx += 1
        return state


def _ensure_imbalance_engine(pipeline: MarketStatePipeline) -> ImbalanceEngine:
    if pipeline.imbalance_engine is None:
        from features_engine.src.imbalance.classification import DataClass

        pipeline.imbalance_engine = ImbalanceEngine(
            DataClass.MBO,
            ablation_mode=pipeline.imbalance_ablation_mode,
            shared_book=pipeline.extractor.book,
            snapshot_collector=pipeline.snapshot_collector,
        )
    return pipeline.imbalance_engine


def _merge_auction_snapshot(
    state: Optional[MarketState],
    snap: ImbalanceSnapshot,
    pipeline: MarketStatePipeline,
) -> MarketState:
    snap_dict = snap.to_dict()
    if state is None:
        vec = np.zeros(64)
        from features_engine.src.imbalance.apply import mask_imbalance_catalog_slots

        mask_imbalance_catalog_slots(vec, pipeline.imbalance_ablation_mode)
        return MarketState(
            primary_features={},
            cross_asset_features={},
            regime_state="NORMAL",
            event_context="NORMAL",
            volatility_state="NORMAL",
            liquidity_state="NORMAL",
            latency_ms=pipeline.latency_ms,
            current_inventory=pipeline.current_inventory,
            feature_vector=vec,
            imbalance_snapshot=snap_dict,
        )
    prev = state.imbalance_snapshot or {}
    merged_snap = {**prev, "auction": snap_dict.get("auction")}
    if snap_dict.get("classification"):
        merged_snap["classification"] = snap_dict["classification"]
    return replace(state, imbalance_snapshot=merged_snap)
