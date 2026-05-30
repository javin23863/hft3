"""
PDF_MODEL_1 → PDF_MODEL_3 → PDF_MODEL_4 strategy for ReplayRunner (real MBO NPZ).
"""

from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from hftbacktest.order import GTC, LIMIT

from features_engine.src.features.mbo_features import MBOEvent, OrderBook
from features_engine.src.features.npz_feed import iter_mbo_events, load_npz_events
from features_engine.src.structural_models.model_01_book_pressure import BookPressureModel
from features_engine.src.structural_models.model_03_vpin_toxicity import VPINToxicityModel
from features_engine.src.structural_models.model_04_hybrid_execution import HybridExecutionModel
from features_engine.src.structural_models.registry import get_structural_model_by_id
from features_engine.src.structural_models.types import VPINToxicityOutput

from backtest_pipeline.src.pdf_defensive_config import DefensiveConfig

VPIN_ONLY_OFI_PROBE = 1.0
_UNSET = object()


def _parse_utc_iso(value: str) -> datetime:
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def event_window_start_ns(event_meta: Dict[str, Any]) -> int:
    """Start of macro event window in nanoseconds (UTC)."""
    if "start_utc" in event_meta:
        start = event_meta["start_utc"]
        if isinstance(start, datetime):
            return int(start.timestamp() * 1_000_000_000)
        return int(_parse_utc_iso(str(start)).timestamp() * 1_000_000_000)
    if "window_utc" in event_meta:
        part = str(event_meta["window_utc"]).split(" to ")[0].strip()
        return int(_parse_utc_iso(part).timestamp() * 1_000_000_000)
    raise ValueError("event_meta missing start_utc or window_utc")


def event_window_end_ns(event_meta: Dict[str, Any]) -> int:
    """End of macro event window in nanoseconds (UTC)."""
    if "end_utc" in event_meta:
        end = event_meta["end_utc"]
        if isinstance(end, datetime):
            return int(end.timestamp() * 1_000_000_000)
        return int(_parse_utc_iso(str(end)).timestamp() * 1_000_000_000)
    if "window_utc" in event_meta:
        part = str(event_meta["window_utc"]).split(" to ")[-1].strip()
        return int(_parse_utc_iso(part).timestamp() * 1_000_000_000)
    raise ValueError("event_meta missing end_utc or window_utc")


def _round_to_tick(price: float, tick_size: float) -> float:
    if not math.isfinite(price) or tick_size <= 0:
        return price
    ticks = round(price / tick_size)
    return ticks * tick_size


class HybridExecutionStrategy:
    """
    MBO-synced structural stack for hftbacktest replay.
    VPIN ingests TRADE volume only (not depth qty).
    """

    def __init__(
        self,
        npz_path: str,
        *,
        tick_size: float = 0.25,
        order_qty: float = 1.0,
        latency_ms: float = 1.0,
        event_meta: Optional[Dict[str, Any]] = None,
        mbo_events: Optional[List[MBOEvent]] = None,
        use_ofi: Any = _UNSET,
        use_vpin: Any = _UNSET,
        defensive: Optional[DefensiveConfig] = None,
        quote_refresh_ticks: int = 10,
    ):
        if defensive is not None:
            if use_ofi is not _UNSET and use_ofi != defensive.use_ofi:
                raise ValueError(
                    "defensive= conflicts with use_ofi/use_vpin kwargs; "
                    "pass flags only via DefensiveConfig"
                )
            if use_vpin is not _UNSET and use_vpin != defensive.use_vpin:
                raise ValueError(
                    "defensive= conflicts with use_ofi/use_vpin kwargs; "
                    "pass flags only via DefensiveConfig"
                )
            cfg = defensive
        else:
            cfg = DefensiveConfig(
                use_ofi=True if use_ofi is _UNSET else bool(use_ofi),
                use_vpin=True if use_vpin is _UNSET else bool(use_vpin),
            )
        self.use_ofi = cfg.use_ofi
        self.use_vpin = cfg.use_vpin
        self.defensive_mode = cfg.mode_id
        self.tick_size = tick_size
        self.order_qty = order_qty
        self.latency_ms = latency_ms
        self.quote_refresh_ticks = max(1, int(quote_refresh_ticks))
        self.event_meta = event_meta or {}
        self._window_start_ns: Optional[int] = None
        self._window_end_ns: Optional[int] = None
        if self.event_meta:
            self._window_start_ns = event_window_start_ns(self.event_meta)
            self._window_end_ns = event_window_end_ns(self.event_meta)

        raw = load_npz_events(npz_path) if mbo_events is None else None
        self._mbo_events = mbo_events if mbo_events is not None else list(iter_mbo_events(raw))
        self._mbo_idx = 0
        self._order_id = 2000
        self._bid_oid: Optional[int] = None
        self._ask_oid: Optional[int] = None
        self._last_bid_px: Optional[float] = None
        self._last_ask_px: Optional[float] = None
        self._steps_since_quote_refresh = 0

        self._diag_cancel_count = 0
        self._diag_quote_refresh_count = 0
        self._diag_sum_vpin = 0.0
        self._diag_sum_ofi_smooth = 0.0
        self._diag_eval_steps = 0

        self._book = OrderBook()
        book_model = get_structural_model_by_id("PDF_MODEL_1")
        vpin_model = get_structural_model_by_id("PDF_MODEL_3")
        hybrid_model = get_structural_model_by_id("PDF_MODEL_4")
        assert isinstance(book_model, BookPressureModel)
        assert isinstance(vpin_model, VPINToxicityModel)
        assert isinstance(hybrid_model, HybridExecutionModel)
        self.book_model = book_model
        self.vpin_model = vpin_model
        self.hybrid_model = hybrid_model

        self._last_book_out = None
        self._last_vpin_out: Optional[VPINToxicityOutput] = VPINToxicityOutput()
        self.last_hybrid_out = None

    @property
    def diagnostics(self) -> Dict[str, float]:
        n = max(1, self._diag_eval_steps)
        return {
            "cancel_count": float(self._diag_cancel_count),
            "quote_refresh_count": float(self._diag_quote_refresh_count),
            "mean_vpin": self._diag_sum_vpin / n,
            "mean_ofi_smooth": self._diag_sum_ofi_smooth / n,
            "eval_steps": float(self._diag_eval_steps),
            "ofi_unit_probe": float(self.use_vpin and not self.use_ofi),
        }

    def _depth_qty(self, depth, side: str) -> int:
        if side == "bid":
            return int(getattr(depth, "best_bid_qty", getattr(depth, "best_bid_tick", 0)) or 0)
        return int(getattr(depth, "best_ask_qty", getattr(depth, "best_ask_tick", 0)) or 0)

    def _depth_mid(self, hbt) -> Optional[float]:
        depth = hbt.depth(0)
        if depth.best_bid <= 0 or depth.best_ask <= 0:
            return None
        return (float(depth.best_bid) + float(depth.best_ask)) / 2.0

    def _sync_mbo(self, hbt) -> None:
        ts = int(hbt.current_timestamp)
        depth_mid_fallback = self._depth_mid(hbt)
        while self._mbo_idx < len(self._mbo_events):
            mbo = self._mbo_events[self._mbo_idx]
            if mbo.timestamp_ns > ts:
                break
            self._book.apply_event(mbo)
            bid_p = self._book.get_best_bid()
            ask_p = self._book.get_best_ask()
            if bid_p > 0 and ask_p < float("inf"):
                book_mid = (bid_p + ask_p) / 2.0
            else:
                book_mid = 0.0
            if mbo.action == "TRADE" and mbo.size > 0 and self.use_vpin:
                if book_mid > 0:
                    mid = book_mid
                elif depth_mid_fallback is not None and depth_mid_fallback > 0:
                    mid = depth_mid_fallback
                else:
                    mid = 0.0
                if mid > 0:
                    out = self.vpin_model.ingest_bar(mid, float(mbo.size), mbo.timestamp_ns)
                    if out is not None and out.payload is not None:
                        self._last_vpin_out = out.payload
            self._mbo_idx += 1

    def _time_remaining_sec(self, hbt) -> float:
        if self._window_end_ns is None:
            return float(self.hybrid_model.time_horizon_sec)
        now = int(hbt.current_timestamp)
        if self._window_start_ns is not None and now < self._window_start_ns:
            return max(0.0, (self._window_end_ns - self._window_start_ns) / 1e9)
        remaining_ns = self._window_end_ns - now
        return max(0.0, remaining_ns / 1e9)

    def _sigma(self) -> float:
        """AS volatility input — fixed default so ablation modes differ only via OFI/VPIN inputs."""
        return float(self.hybrid_model.default_sigma)

    def _cancel_resting(self, hbt) -> None:
        cancelled = 0
        if self._bid_oid is not None:
            hbt.cancel(0, self._bid_oid, False)
            self._bid_oid = None
            cancelled += 1
        if self._ask_oid is not None:
            hbt.cancel(0, self._ask_oid, False)
            self._ask_oid = None
            cancelled += 1
        self._diag_cancel_count += cancelled

    def _prices_changed_materially(self, bid_px: float, ask_px: float) -> bool:
        if self._last_bid_px is None or self._last_ask_px is None:
            return True
        return (
            abs(bid_px - self._last_bid_px) >= self.tick_size
            or abs(ask_px - self._last_ask_px) >= self.tick_size
        )

    def _should_refresh_quotes(self, bid_px: float, ask_px: float) -> bool:
        self._steps_since_quote_refresh += 1
        if self._prices_changed_materially(bid_px, ask_px):
            return True
        return self._steps_since_quote_refresh >= self.quote_refresh_ticks

    def _record_eval_diagnostics(self, ofi_smooth: float, vpin_value: float) -> None:
        self._diag_eval_steps += 1
        self._diag_sum_ofi_smooth += ofi_smooth
        self._diag_sum_vpin += vpin_value

    def on_step(self, hbt) -> None:
        depth = hbt.depth(0)
        if depth.best_bid <= 0 or depth.best_ask <= 0:
            return

        self._sync_mbo(hbt)

        bid_p = float(depth.best_bid)
        ask_p = float(depth.best_ask)
        bid_q = self._depth_qty(depth, "bid")
        ask_q = self._depth_qty(depth, "ask")
        ts = int(hbt.current_timestamp)

        book_payload = None
        if self.use_ofi:
            book_out = self.book_model.update_bbo(bid_p, bid_q, ask_p, ask_q, ts)
            self._last_book_out = book_out
            book_payload = book_out.payload

        vpin_payload = self._last_vpin_out if self.use_vpin else None
        vpin_value = float(vpin_payload.VPIN_value) if vpin_payload is not None else 0.0

        eval_kw: Dict[str, Any] = {}
        if self.use_vpin and not self.use_ofi:
            eval_kw["ofi_smooth"] = VPIN_ONLY_OFI_PROBE

        hybrid_out = self.hybrid_model.evaluate(
            mid=(bid_p + ask_p) / 2.0,
            inventory=float(hbt.position(0)),
            sigma=self._sigma(),
            time_remaining=self._time_remaining_sec(hbt),
            book_pressure=book_payload,
            vpin=vpin_payload,
            timestamp_ns=ts,
            **eval_kw,
        )
        self.last_hybrid_out = hybrid_out
        payload = hybrid_out.payload
        if payload is None:
            return

        ofi_smooth = float(eval_kw.get("ofi_smooth", book_payload.OFI_smooth if book_payload else 0.0))
        self._record_eval_diagnostics(ofi_smooth, vpin_value)

        bid_p = float(depth.best_bid)
        ask_p = float(depth.best_ask)
        inventory = float(hbt.position(0))

        bid_px = _round_to_tick(payload.optimal_bid, self.tick_size)
        ask_px = _round_to_tick(payload.optimal_ask, self.tick_size)

        if payload.cancel_quote_flag:
            self._cancel_resting(hbt)
            if abs(inventory) > 1.0:
                if inventory > 0:
                    self._submit(hbt, buy=False, price=bid_p, replace=False)
                elif inventory < 0:
                    self._submit(hbt, buy=True, price=ask_p, replace=False)
            return

        if payload.passive_to_aggressive_flag:
            self._cancel_resting(hbt)
            if inventory > 0:
                self._submit(hbt, buy=False, price=bid_p, replace=False)
            elif inventory < 0:
                self._submit(hbt, buy=True, price=ask_p, replace=False)
            return

        if not self._should_refresh_quotes(bid_px, ask_px):
            return

        self._cancel_resting(hbt)
        if math.isfinite(bid_px) and bid_px > 0 and bid_px < ask_p:
            self._submit(hbt, buy=True, price=bid_px, replace=False)
        if math.isfinite(ask_px) and ask_px > bid_px:
            self._submit(hbt, buy=False, price=ask_px, replace=False)

        self._last_bid_px = bid_px
        self._last_ask_px = ask_px
        self._steps_since_quote_refresh = 0
        self._diag_quote_refresh_count += 1

    def _submit(self, hbt, buy: bool, price: float, *, replace: bool) -> None:
        if not math.isfinite(price) or price <= 0:
            return
        if replace:
            if buy and self._bid_oid is not None:
                hbt.cancel(0, self._bid_oid, False)
            if not buy and self._ask_oid is not None:
                hbt.cancel(0, self._ask_oid, False)
        oid = self._order_id
        self._order_id += 1
        if buy:
            hbt.submit_buy_order(0, oid, price, self.order_qty, GTC, LIMIT, False)
            self._bid_oid = oid
        else:
            hbt.submit_sell_order(0, oid, price, self.order_qty, GTC, LIMIT, False)
            self._ask_oid = oid
