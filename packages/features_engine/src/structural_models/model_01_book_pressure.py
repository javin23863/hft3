"""PDF_MODEL_1 — OFI, MLOFI, PCA, spoof defense."""

from __future__ import annotations

from collections import deque
from typing import Any, Deque, Dict, List, Optional, Tuple

import heapq
import numpy as np

from features_engine.src.features.mbo_features import OrderBook
from .base import BaseStructuralModel, ModelOutput
from .types import BookPressureOutput


def _level_ofi_delta(
    prev_price: float,
    prev_qty: int,
    curr_price: float,
    curr_qty: int,
    is_bid: bool,
) -> float:
    """Single-level OFI contribution (Cont et al. style)."""
    if prev_price <= 0 and curr_price <= 0:
        return 0.0
    if is_bid:
        if curr_price > prev_price:
            return float(curr_qty)
        if curr_price < prev_price:
            return float(-prev_qty)
        return float(curr_qty - prev_qty)
    # ask side enters OFI with opposite sign
    if curr_price < prev_price or (prev_price == float("inf") and curr_price < float("inf")):
        return -float(curr_qty)
    if curr_price > prev_price:
        return float(prev_qty)
    return float(prev_qty - curr_qty)


def compute_level1_ofi_event(
    prev_bid_p: float,
    prev_bid_q: int,
    prev_ask_p: float,
    prev_ask_q: int,
    bid_p: float,
    bid_q: int,
    ask_p: float,
    ask_q: int,
) -> float:
    bid_contrib = _level_ofi_delta(prev_bid_p, prev_bid_q, bid_p, bid_q, is_bid=True)
    ask_contrib = _level_ofi_delta(prev_ask_p, prev_ask_q, ask_p, ask_q, is_bid=False)
    return bid_contrib + ask_contrib


def top_m_levels(book: OrderBook, m: int) -> Tuple[List[Tuple[float, int]], List[Tuple[float, int]]]:
    bid_prices = heapq.nlargest(m, book.bids.keys()) if book.bids else []
    ask_prices = heapq.nsmallest(m, book.asks.keys()) if book.asks else []
    bids = [(p, book.bids[p].total_qty) for p in bid_prices]
    asks = [(p, book.asks[p].total_qty) for p in ask_prices]
    return bids, asks


def compute_mlofi_vector(
    prev_bids: List[Tuple[float, int]],
    prev_asks: List[Tuple[float, int]],
    curr_bids: List[Tuple[float, int]],
    curr_asks: List[Tuple[float, int]],
    m: int,
) -> List[float]:
    """MLOFI^m = OF_{m,b} - OF_{m,a} per level index."""
    mlofi: List[float] = []
    for i in range(m):
        pb, qb_prev = prev_bids[i] if i < len(prev_bids) else (0.0, 0)
        cb, qb_curr = curr_bids[i] if i < len(curr_bids) else (0.0, 0)
        pa, qa_prev = prev_asks[i] if i < len(prev_asks) else (float("inf"), 0)
        ca, qa_curr = curr_asks[i] if i < len(curr_asks) else (float("inf"), 0)
        of_b = _level_ofi_delta(pb, qb_prev, cb, qb_curr, is_bid=True)
        of_a = _level_ofi_delta(pa, qa_prev, ca, qa_curr, is_bid=False)
        mlofi.append(of_b - of_a)
    return mlofi


def pca_first_component(history: List[List[float]]) -> Tuple[float, np.ndarray]:
    """Return PC1 score for latest MLOFI vector and the loading vector."""
    if len(history) < 2:
        vec = np.array(history[-1] if history else [0.0], dtype=float)
        return 0.0, vec
    width = max((len(row) for row in history), default=1)
    mat = np.zeros((len(history), max(width, 1)), dtype=float)
    for index, row in enumerate(history):
        values = np.array(row, dtype=float)
        mat[index, : len(values)] = values
    mat = mat - mat.mean(axis=0, keepdims=True)
    if mat.shape[0] < 2:
        return 0.0, mat.flatten()
    _, _, vt = np.linalg.svd(mat, full_matrices=False)
    pc1 = vt[0]
    score = float(np.dot(mat[-1], pc1))
    return score, pc1


class BookPressureModel(BaseStructuralModel[BookPressureOutput]):
    model_id = "BOOK_PRESSURE"

    def __init__(self, params: Optional[dict] = None):
        cfg = (params or {}).get("ofi", {})
        self.pca_levels = int(cfg.get("pca_levels", 5))
        self.spoof_threshold = float(cfg.get("spoof_threshold", 0.5))
        self.smooth_window = int(cfg.get("smooth_window", 10))
        self.zscore_window = int(cfg.get("zscore_window", 50))
        self._ofi_cum = 0.0
        self._ofi_history: Deque[float] = deque(maxlen=self.zscore_window)
        self._ofi_smooth_buf: Deque[float] = deque(maxlen=self.smooth_window)
        self._mlofi_history: Deque[List[float]] = deque(maxlen=100)
        self._prev_bid_p = 0.0
        self._prev_bid_q = 0
        self._prev_ask_p = float("inf")
        self._prev_ask_q = 0
        self._prev_bids: List[Tuple[float, int]] = []
        self._prev_asks: List[Tuple[float, int]] = []

    def update_bbo(
        self,
        bid_p: float,
        bid_q: int,
        ask_p: float,
        ask_q: int,
        timestamp_ns: int = 0,
    ) -> ModelOutput[BookPressureOutput]:
        e = compute_level1_ofi_event(
            self._prev_bid_p,
            self._prev_bid_q,
            self._prev_ask_p,
            self._prev_ask_q,
            bid_p,
            bid_q,
            ask_p,
            ask_q,
        )
        self._ofi_cum += e
        self._ofi_history.append(e)
        self._ofi_smooth_buf.append(e)

        curr_bids = [(bid_p, bid_q)] if bid_p > 0 else []
        curr_asks = [(ask_p, ask_q)] if ask_p < float("inf") else []
        mlofi = compute_mlofi_vector(
            self._prev_bids or curr_bids,
            self._prev_asks or curr_asks,
            curr_bids,
            curr_asks,
            m=1,
        )
        self._mlofi_history.append(mlofi)
        pc1, _ = pca_first_component(list(self._mlofi_history))

        z = 0.0
        if len(self._ofi_history) >= 2:
            arr = np.array(self._ofi_history, dtype=float)
            std = float(arr.std())
            if std > 1e-12:
                z = float((arr[-1] - arr.mean()) / std)

        smooth = float(np.mean(self._ofi_smooth_buf)) if self._ofi_smooth_buf else 0.0
        direction = 1 if self._ofi_cum > 0 else (-1 if self._ofi_cum < 0 else 0)
        l1_sign = 1 if e > 0 else (-1 if e < 0 else 0)
        pc1_sign = 1 if pc1 > 0 else (-1 if pc1 < 0 else 0)
        spoof = (
            l1_sign != 0
            and pc1_sign != 0
            and l1_sign != pc1_sign
            and abs(z) >= self.spoof_threshold
        )

        self._prev_bid_p, self._prev_bid_q = bid_p, bid_q
        self._prev_ask_p, self._prev_ask_q = ask_p, ask_q
        self._prev_bids, self._prev_asks = curr_bids, curr_asks

        payload = BookPressureOutput(
            OFI_value=self._ofi_cum,
            OFI_zscore=z,
            OFI_smooth=smooth,
            MLOFI_vector=mlofi,
            MLOFI_PC1=pc1,
            book_pressure_direction=direction,
            spoofing_risk_flag=spoof,
        )
        return ModelOutput(model_id=self.model_id, timestamp_ns=timestamp_ns, payload=payload)

    def update_book(self, book: OrderBook, timestamp_ns: int = 0) -> ModelOutput[BookPressureOutput]:
        bid_p = book.get_best_bid()
        ask_p = book.get_best_ask()
        bid_q = book.bids[bid_p].total_qty if bid_p in book.bids else 0
        ask_q = book.asks[ask_p].total_qty if ask_p in book.asks else 0

        curr_bids, curr_asks = top_m_levels(book, self.pca_levels)
        mlofi = compute_mlofi_vector(
            self._prev_bids or curr_bids,
            self._prev_asks or curr_asks,
            curr_bids,
            curr_asks,
            self.pca_levels,
        )
        out = self.update_bbo(bid_p, bid_q, ask_p, ask_q, timestamp_ns)
        self._mlofi_history[-1] = mlofi
        pc1, _ = pca_first_component(list(self._mlofi_history))
        out.payload.MLOFI_vector = mlofi
        out.payload.MLOFI_PC1 = pc1
        self._prev_bids, self._prev_asks = curr_bids, curr_asks
        return out

    def evaluate(self, **kwargs: Any) -> ModelOutput[BookPressureOutput]:
        book: Optional[OrderBook] = kwargs.get("book")
        if book is not None:
            return self.update_book(book, kwargs.get("timestamp_ns", 0))
        return self.update_bbo(
            kwargs["bid_p"],
            kwargs["bid_q"],
            kwargs["ask_p"],
            kwargs["ask_q"],
            kwargs.get("timestamp_ns", 0),
        )
