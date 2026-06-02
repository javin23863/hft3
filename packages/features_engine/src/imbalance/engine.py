"""Unified imbalance snapshot per market event."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from features_engine.src.features.mbo_features import MBOEvent, OrderBook
from features_engine.src.imbalance.ablation import ImbalanceAblationMode, ImbalanceFamily, family_enabled
from features_engine.src.imbalance.auction import (
    AuctionImbalanceEvent,
    AuctionImbalanceTracker,
)
from features_engine.src.imbalance.book import compute_book_imbalance
from features_engine.src.imbalance.classification import DataClass, resolve_data_class
from features_engine.src.imbalance.order_flow import OrderFlowImbalanceEngine
from features_engine.src.imbalance.normalize import build_envelope
from features_engine.src.imbalance.snapshot_collect import SnapshotCollector


@dataclass
class ImbalanceSnapshot:
    book: Optional[Dict[str, Any]] = None
    order_flow: Optional[Dict[str, Any]] = None
    auction: Optional[Dict[str, Any]] = None
    lineage: Dict[str, Any] = field(default_factory=dict)
    classification: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "book": self.book,
            "order_flow": self.order_flow,
            "auction": self.auction,
            "lineage": self.lineage,
            "classification": self.classification,
        }


class ImbalanceEngine:
    def __init__(
        self,
        data_class: DataClass,
        *,
        ablation_mode: Optional[ImbalanceAblationMode] = None,
        window_ms: Optional[list[int]] = None,
        asset_class: str = "futures",
        instrument_id: str = "",
        venue: str = "GLBX.MDP3",
        shared_book: Optional[OrderBook] = None,
        snapshot_collector: Optional[SnapshotCollector] = None,
    ) -> None:
        self.data_class = data_class
        self.ablation_mode = ablation_mode
        self.book = shared_book if shared_book is not None else OrderBook()
        self._owns_book = shared_book is None
        self.order_flow = OrderFlowImbalanceEngine(data_class, window_ms=window_ms)
        self.auction = AuctionImbalanceTracker()
        self._collector = snapshot_collector
        self._asset_class = asset_class
        self._instrument_id = instrument_id
        self._venue = venue
        self._resolution = resolve_data_class(
            data_class.value.lower().replace("_", "-"),
            available_schema=data_class.value.lower().replace("_", "-"),
            asset_class=asset_class,
        )

    def _enabled(self, family: ImbalanceFamily) -> bool:
        if self.ablation_mode is None:
            return True
        return family_enabled(self.ablation_mode, family)

    def _update_order_flow_event(self, event: MBOEvent) -> None:
        if event.action == "TRADE":
            self.order_flow.on_mbo_trade(event.side, event.size)
        elif event.action == "ADD":
            self.order_flow.on_mbo_add(event.side, event.size)
        elif event.action == "CANCEL":
            self.order_flow.on_mbo_cancel(event.side, event.size)

    def on_mbo_after_book(self, event: MBOEvent, *, window_phase: str = "continuous") -> ImbalanceSnapshot:
        """Use after shared OrderBook already applied event (no second book rebuild)."""
        self._update_order_flow_event(event)
        return self._snapshot_from_book(event.timestamp_ns, window_phase=window_phase)

    def on_mbo_event(self, event: MBOEvent, *, window_phase: str = "continuous") -> ImbalanceSnapshot:
        self._update_order_flow_event(event)
        if self._owns_book:
            self.book.apply_event(event)
        return self._snapshot_from_book(event.timestamp_ns, window_phase=window_phase)

    def _snapshot_from_book(self, ts_ns: int, *, window_phase: str) -> ImbalanceSnapshot:
        snap = ImbalanceSnapshot(classification=self._resolution.to_dict())
        bb = self.book.get_best_bid()
        ba = self.book.get_best_ask()
        b1, a1 = self.book.top_k_depth(1)

        if self._enabled(ImbalanceFamily.BOOK):
            snap.book = compute_book_imbalance(self.book).to_dict()

        if self._enabled(ImbalanceFamily.ORDER_FLOW):
            snap.order_flow = self.order_flow.on_bbo(
                ts_ns, bb, int(b1), ba, int(a1)
            ).to_dict()

        snap.lineage = build_envelope(
            asset_class=self._asset_class,
            source="databento",
            venue=self._venue,
            instrument_id=self._instrument_id,
            data_schema=self.data_class.value,
            data_class=self.data_class.value,
            feature_family="imbalance_bundle",
            feature_source="imbalance.engine",
            timestamp_event_ns=ts_ns,
            event_window_id="",
            data_granularity="event",
        ).to_dict()

        if self._collector is not None:
            self._collector.maybe_record(snap.to_dict())
        return snap

    def on_auction_event(
        self,
        record: dict,
        *,
        window_phase: str,
        event_window_id: str = "",
    ) -> ImbalanceSnapshot:
        if self.ablation_mode is not None and not self._enabled(ImbalanceFamily.AUCTION):
            return ImbalanceSnapshot(classification=self._resolution.to_dict())
        ev = AuctionImbalanceEvent.from_record(record)
        auc = self.auction.update(
            ev, window_phase=window_phase, event_window_id=event_window_id
        )
        snap = ImbalanceSnapshot(
            auction=auc.to_dict(),
            classification=self._resolution.to_dict(),
        )
        if self._collector is not None:
            self._collector.maybe_record(snap.to_dict())
        return snap
