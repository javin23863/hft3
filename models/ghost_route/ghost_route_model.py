"""Ghost Route MBO queue-decay signal model.

This module is intentionally research/backtest only. It emits structured
order-intent objects for simulation and does not route live orders.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from typing import Any, Iterable, Literal


CONTRACT_PAIRS: dict[str, str] = {
    "ES": "MES",
    "NQ": "MNQ",
    "YM": "MYM",
}

Side = Literal["bid", "ask"]
Direction = Literal["BUY", "SELL"]
FillStatus = Literal[
    "FULL_FILL",
    "PARTIAL_FILL",
    "MISS_STALE_QUOTE_GONE",
    "MISS_REPRICED_BEFORE_ARRIVAL",
    "MISS_INSUFFICIENT_DEPTH",
    "REJECT_DATA_QUALITY",
    "REJECT_RISK_BLOCK",
]


@dataclass(frozen=True)
class PairCalibration:
    alpha: float
    beta: float
    mu_spread: float
    sigma_spread: float
    tau_hat_us: int


@dataclass(frozen=True)
class GhostRouteConfig:
    latency_wire_to_wire_us: int = 23
    compute_latency_us: int = 0
    delta_t_us: int = 250
    min_depth_contracts: int = 1
    max_spread_ticks: float = 1.0
    tau_decay_norm: float = 0.40
    tau_remaining: float = 0.70
    epsilon_trade: float = 1.0
    tau_cancel_trade_ratio: float = 3.0
    tau_ofi_norm: float = 0.15
    tau_z: float = 1.0
    min_expected_edge_ticks: float = 0.25
    toxicity_enter_threshold: float = 2.0
    toxicity_exit_threshold: float = 1.0
    toxicity_min_hold_us: int = 250
    order_qty: int = 1
    fees_ticks: float = 0.05
    estimated_slippage_ticks: float = 0.05
    adverse_selection_penalty_ticks: float = 0.05
    miss_penalty_ticks: float = 0.05
    crossing_cost_ticks: float = 0.5
    epsilon_q: float = 1.0
    epsilon_t: float = 1.0
    epsilon_sigma: float = 1e-9
    tick_sizes: dict[str, float] = field(
        default_factory=lambda: {
            "ES": 0.25,
            "MES": 0.25,
            "NQ": 0.25,
            "MNQ": 0.25,
            "YM": 1.0,
            "MYM": 1.0,
        }
    )
    pair_calibration: dict[str, PairCalibration] = field(
        default_factory=lambda: {
            "ES_MES": PairCalibration(alpha=0.0, beta=1.0, mu_spread=0.0, sigma_spread=1.0, tau_hat_us=50),
            "NQ_MNQ": PairCalibration(alpha=0.0, beta=1.0, mu_spread=0.0, sigma_spread=1.0, tau_hat_us=50),
            "YM_MYM": PairCalibration(alpha=0.0, beta=1.0, mu_spread=0.0, sigma_spread=1.0, tau_hat_us=50),
        }
    )

    @property
    def total_latency_us(self) -> int:
        return int(self.compute_latency_us) + int(self.latency_wire_to_wire_us)


@dataclass(frozen=True)
class MBOEvent:
    exchange_timestamp: int
    local_receive_timestamp: int
    sequence_number: int
    instrument: str
    order_id: str = ""
    event_type: str = ""
    side: str = ""
    price: float = 0.0
    size: float = 0.0
    remaining_size: float = 0.0
    best_bid: float = 0.0
    best_ask: float = 0.0
    best_bid_size: float = 0.0
    best_ask_size: float = 0.0
    trade_price: float = 0.0
    trade_size: float = 0.0
    aggressor_side: str = ""

    @classmethod
    def from_mapping(cls, row: dict[str, Any]) -> "MBOEvent":
        return cls(
            exchange_timestamp=int(row.get("exchange_timestamp", row.get("timestamp", 0)) or 0),
            local_receive_timestamp=int(row.get("local_receive_timestamp", 0) or 0),
            sequence_number=int(row.get("sequence_number", 0) or 0),
            instrument=str(row.get("instrument", "")),
            order_id=str(row.get("order_id", "")),
            event_type=str(row.get("event_type", "")).lower(),
            side=str(row.get("side", "")).lower(),
            price=float(row.get("price", 0.0) or 0.0),
            size=float(row.get("size", 0.0) or 0.0),
            remaining_size=float(row.get("remaining_size", 0.0) or 0.0),
            best_bid=float(row.get("best_bid", 0.0) or 0.0),
            best_ask=float(row.get("best_ask", 0.0) or 0.0),
            best_bid_size=float(row.get("best_bid_size", 0.0) or 0.0),
            best_ask_size=float(row.get("best_ask_size", 0.0) or 0.0),
            trade_price=float(row.get("trade_price", 0.0) or 0.0),
            trade_size=float(row.get("trade_size", 0.0) or 0.0),
            aggressor_side=str(row.get("aggressor_side", "")).lower(),
        )

    def mid_ticks(self, tick_size: float) -> float:
        return ((self.best_bid + self.best_ask) / 2.0) / max(tick_size, 1e-12)

    def spread_ticks(self, tick_size: float) -> float:
        return (self.best_ask - self.best_bid) / max(tick_size, 1e-12)


@dataclass(frozen=True)
class DataQualityResult:
    ok: bool
    reject_reason: str = ""
    issue_count: int = 0


@dataclass(frozen=True)
class QueueDecayMetrics:
    side: Side
    cancel_volume: float
    modify_down_volume: float
    add_volume: float
    trade_volume: float
    initial_quantity: float
    current_quantity: float
    raw_queue_decay: float
    shadow_decay: float
    normalized_shadow_decay: float
    remaining_queue_ratio: float
    cancel_trade_ratio: float
    shadow_decay_event: bool


@dataclass(frozen=True)
class StaleQuoteMetrics:
    pair: str
    direction: Direction
    spread_zscore: float
    available_depth: float
    target_price: float
    micro_spread_ticks: float
    stale_quote: bool


@dataclass(frozen=True)
class ToxicityState:
    timestamp: int
    contract: str
    toxicity_score: float
    toxicity_state: Literal["NORMAL", "ELEVATED", "TOXIC"]
    VI: float
    trade_intensity_z: float
    cancel_intensity_z: float
    book_thinning_z: float


@dataclass(frozen=True)
class OrderIntent:
    model: str
    timestamp_signal: int
    macro_contract: str
    micro_contract: str
    direction: Direction
    target_price: float
    target_quantity: int
    order_type: str
    reason: dict[str, Any]

    @property
    def timestamp_order_arrival(self) -> int:
        latency = int(self.reason.get("total_latency_us", 0))
        return int(self.timestamp_signal) + latency

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["timestamp_order_arrival"] = self.timestamp_order_arrival
        return payload


@dataclass(frozen=True)
class FillResult:
    fill_status: FillStatus
    filled_quantity: int
    fill_price: float
    available_depth_at_arrival: float
    reject_reason: str = ""


def sort_events(events: Iterable[MBOEvent | dict[str, Any]]) -> list[MBOEvent]:
    rows = [event if isinstance(event, MBOEvent) else MBOEvent.from_mapping(event) for event in events]
    return sorted(rows, key=lambda e: (e.sequence_number, e.exchange_timestamp, e.local_receive_timestamp))


def validate_event_window(events: Iterable[MBOEvent | dict[str, Any]], *, allow_locked_books: bool = False) -> DataQualityResult:
    rows = sort_events(events)
    if not rows:
        return DataQualityResult(False, "empty_window", 1)
    issues: list[str] = []
    last_seq = rows[0].sequence_number - 1
    last_exchange_ts = rows[0].exchange_timestamp
    for event in rows:
        if event.sequence_number <= last_seq:
            issues.append("non_monotonic_sequence")
        elif last_seq and event.sequence_number > last_seq + 1:
            issues.append("missing_sequence_numbers")
        if event.exchange_timestamp < last_exchange_ts:
            issues.append("non_monotonic_timestamps")
        if event.best_bid > event.best_ask:
            issues.append("crossed_book")
        if event.best_bid == event.best_ask and not allow_locked_books:
            issues.append("locked_book")
        last_seq = event.sequence_number
        last_exchange_ts = event.exchange_timestamp
    if issues:
        return DataQualityResult(False, ",".join(sorted(set(issues))), len(issues))
    return DataQualityResult(True)


def _at_touch(event: MBOEvent, side: Side) -> bool:
    if event.side != side:
        return False
    touch = event.best_bid if side == "bid" else event.best_ask
    return math.isclose(event.price, touch, rel_tol=0.0, abs_tol=1e-12) or (
        event.event_type == "trade" and math.isclose(event.trade_price, touch, rel_tol=0.0, abs_tol=1e-12)
    )


def _event_volume(event: MBOEvent) -> float:
    if event.event_type == "trade":
        return max(event.trade_size or event.size, 0.0)
    if event.event_type == "modify":
        if event.remaining_size and event.size and event.remaining_size < event.size:
            return max(event.size - event.remaining_size, 0.0)
        return max(event.size, 0.0)
    return max(event.size or event.remaining_size, 0.0)


def compute_queue_decay(
    events: Iterable[MBOEvent | dict[str, Any]],
    *,
    side: Side,
    initial_quantity: float,
    current_quantity: float,
    config: GhostRouteConfig | None = None,
) -> QueueDecayMetrics:
    cfg = config or GhostRouteConfig()
    cancel_volume = 0.0
    modify_down_volume = 0.0
    add_volume = 0.0
    trade_volume = 0.0
    for event in sort_events(events):
        if not _at_touch(event, side):
            continue
        volume = _event_volume(event)
        if event.event_type == "cancel":
            cancel_volume += volume
        elif event.event_type == "modify":
            modify_down_volume += volume
        elif event.event_type == "add":
            add_volume += volume
        elif event.event_type == "trade":
            trade_volume += volume
    raw = cancel_volume + modify_down_volume - add_volume
    shadow = raw - trade_volume
    denominator = max(initial_quantity, cfg.epsilon_q)
    nsd = shadow / denominator
    remaining = current_quantity / denominator
    ctr = (cancel_volume + modify_down_volume) / max(trade_volume, cfg.epsilon_t)
    valid = (
        nsd >= cfg.tau_decay_norm
        and trade_volume <= cfg.epsilon_trade
        and remaining <= cfg.tau_remaining
        and ctr >= cfg.tau_cancel_trade_ratio
    )
    return QueueDecayMetrics(
        side=side,
        cancel_volume=cancel_volume,
        modify_down_volume=modify_down_volume,
        add_volume=add_volume,
        trade_volume=trade_volume,
        initial_quantity=initial_quantity,
        current_quantity=current_quantity,
        raw_queue_decay=raw,
        shadow_decay=shadow,
        normalized_shadow_decay=nsd,
        remaining_queue_ratio=remaining,
        cancel_trade_ratio=ctr,
        shadow_decay_event=valid,
    )


def compute_ofi(previous: MBOEvent | dict[str, Any], current: MBOEvent | dict[str, Any], config: GhostRouteConfig | None = None) -> float:
    cfg = config or GhostRouteConfig()
    prev = previous if isinstance(previous, MBOEvent) else MBOEvent.from_mapping(previous)
    cur = current if isinstance(current, MBOEvent) else MBOEvent.from_mapping(current)
    ofi = 0.0
    ofi += (1.0 if cur.best_bid >= prev.best_bid else 0.0) * cur.best_bid_size
    ofi -= (1.0 if cur.best_bid <= prev.best_bid else 0.0) * prev.best_bid_size
    ofi -= (1.0 if cur.best_ask <= prev.best_ask else 0.0) * cur.best_ask_size
    ofi += (1.0 if cur.best_ask >= prev.best_ask else 0.0) * prev.best_ask_size
    return ofi / max(prev.best_bid_size + prev.best_ask_size, cfg.epsilon_q)


def stale_quote_metrics(
    *,
    macro_event: MBOEvent | dict[str, Any],
    micro_event: MBOEvent | dict[str, Any],
    macro_contract: str,
    direction: Direction,
    config: GhostRouteConfig | None = None,
) -> StaleQuoteMetrics:
    cfg = config or GhostRouteConfig()
    macro = macro_event if isinstance(macro_event, MBOEvent) else MBOEvent.from_mapping(macro_event)
    micro = micro_event if isinstance(micro_event, MBOEvent) else MBOEvent.from_mapping(micro_event)
    micro_contract = CONTRACT_PAIRS[macro_contract]
    pair = f"{macro_contract}_{micro_contract}"
    calibration = cfg.pair_calibration[pair]
    macro_mid = macro.mid_ticks(cfg.tick_sizes[macro_contract])
    micro_mid = micro.mid_ticks(cfg.tick_sizes[micro_contract])
    spread = micro_mid - calibration.alpha - calibration.beta * macro_mid
    zscore = (spread - calibration.mu_spread) / max(calibration.sigma_spread, cfg.epsilon_sigma)
    if direction == "SELL":
        available_depth = micro.best_bid_size
        target_price = micro.best_bid
        directional_ok = zscore >= cfg.tau_z
    else:
        available_depth = micro.best_ask_size
        target_price = micro.best_ask
        directional_ok = zscore <= -cfg.tau_z
    micro_spread = micro.spread_ticks(cfg.tick_sizes[micro_contract])
    valid = directional_ok and available_depth >= cfg.min_depth_contracts and micro_spread <= cfg.max_spread_ticks
    return StaleQuoteMetrics(
        pair=pair,
        direction=direction,
        spread_zscore=zscore,
        available_depth=available_depth,
        target_price=target_price,
        micro_spread_ticks=micro_spread,
        stale_quote=valid,
    )


def expected_edge_ticks(
    *,
    direction: Direction,
    spread_zscore: float,
    config: GhostRouteConfig | None = None,
) -> float:
    cfg = config or GhostRouteConfig()
    gross = max(abs(spread_zscore) - cfg.tau_z, 0.0)
    cost = (
        cfg.crossing_cost_ticks
        + cfg.fees_ticks
        + cfg.estimated_slippage_ticks
        + cfg.adverse_selection_penalty_ticks
        + cfg.miss_penalty_ticks
    )
    return gross - cost


def toxicity_state(
    *,
    timestamp: int,
    contract: str,
    buy_volume: float,
    sell_volume: float,
    trade_intensity_z: float,
    cancel_intensity_z: float,
    book_thinning_z: float,
    config: GhostRouteConfig | None = None,
    weights: tuple[float, float, float, float] = (1.0, 1.0, 1.0, 1.0),
) -> ToxicityState:
    cfg = config or GhostRouteConfig()
    vi = abs(buy_volume - sell_volume) / max(buy_volume + sell_volume, cfg.epsilon_t)
    score = (
        weights[0] * vi
        + weights[1] * trade_intensity_z
        + weights[2] * cancel_intensity_z
        + weights[3] * book_thinning_z
    )
    if score >= cfg.toxicity_enter_threshold:
        state: Literal["NORMAL", "ELEVATED", "TOXIC"] = "TOXIC"
    elif score >= cfg.toxicity_exit_threshold:
        state = "ELEVATED"
    else:
        state = "NORMAL"
    return ToxicityState(
        timestamp=timestamp,
        contract=contract,
        toxicity_score=score,
        toxicity_state=state,
        VI=vi,
        trade_intensity_z=trade_intensity_z,
        cancel_intensity_z=cancel_intensity_z,
        book_thinning_z=book_thinning_z,
    )


def simulate_fak_order(
    intent: OrderIntent,
    micro_book_at_arrival: MBOEvent | dict[str, Any],
    *,
    data_quality_ok: bool = True,
    global_risk_block: bool = False,
) -> FillResult:
    book = micro_book_at_arrival if isinstance(micro_book_at_arrival, MBOEvent) else MBOEvent.from_mapping(micro_book_at_arrival)
    if not data_quality_ok:
        return FillResult("REJECT_DATA_QUALITY", 0, 0.0, 0.0, "data_quality")
    if global_risk_block:
        return FillResult("REJECT_RISK_BLOCK", 0, 0.0, 0.0, "risk_block")
    if intent.direction == "BUY":
        available = book.best_ask_size if book.best_ask <= intent.target_price else 0.0
        price = book.best_ask
        quote_gone = book.best_ask > intent.target_price
    else:
        available = book.best_bid_size if book.best_bid >= intent.target_price else 0.0
        price = book.best_bid
        quote_gone = book.best_bid < intent.target_price
    if available <= 0:
        return FillResult(
            "MISS_REPRICED_BEFORE_ARRIVAL" if quote_gone else "MISS_INSUFFICIENT_DEPTH",
            0,
            0.0,
            available,
            "stale_quote_not_executable",
        )
    filled = min(int(intent.target_quantity), int(available))
    status: FillStatus = "FULL_FILL" if filled >= intent.target_quantity else "PARTIAL_FILL"
    return FillResult(status, filled, price, available)


class GhostRouteModel:
    model_id = "GHOST_ROUTE"

    def __init__(self, config: GhostRouteConfig | None = None) -> None:
        self.config = config or GhostRouteConfig()

    def evaluate(
        self,
        *,
        macro_contract: str,
        macro_window_events: Iterable[MBOEvent | dict[str, Any]],
        previous_macro: MBOEvent | dict[str, Any],
        current_macro: MBOEvent | dict[str, Any],
        current_micro: MBOEvent | dict[str, Any],
        data_quality_ok: bool = True,
        global_risk_block: bool = False,
    ) -> OrderIntent | None:
        if macro_contract not in CONTRACT_PAIRS or not data_quality_ok or global_risk_block:
            return None
        cfg = self.config
        prev = previous_macro if isinstance(previous_macro, MBOEvent) else MBOEvent.from_mapping(previous_macro)
        macro = current_macro if isinstance(current_macro, MBOEvent) else MBOEvent.from_mapping(current_macro)
        micro = current_micro if isinstance(current_micro, MBOEvent) else MBOEvent.from_mapping(current_micro)
        bid_decay = compute_queue_decay(
            macro_window_events,
            side="bid",
            initial_quantity=max(prev.best_bid_size, cfg.epsilon_q),
            current_quantity=macro.best_bid_size,
            config=cfg,
        )
        ask_decay = compute_queue_decay(
            macro_window_events,
            side="ask",
            initial_quantity=max(prev.best_ask_size, cfg.epsilon_q),
            current_quantity=macro.best_ask_size,
            config=cfg,
        )
        nofi = compute_ofi(previous_macro, current_macro, cfg)
        if bid_decay.shadow_decay_event and nofi <= -cfg.tau_ofi_norm:
            direction: Direction = "SELL"
            decay = bid_decay
        elif ask_decay.shadow_decay_event and nofi >= cfg.tau_ofi_norm:
            direction = "BUY"
            decay = ask_decay
        else:
            return None
        stale = stale_quote_metrics(
            macro_event=macro,
            micro_event=micro,
            macro_contract=macro_contract,
            direction=direction,
            config=cfg,
        )
        edge = expected_edge_ticks(direction=direction, spread_zscore=stale.spread_zscore, config=cfg)
        if not stale.stale_quote or edge < cfg.min_expected_edge_ticks:
            return None
        tox = toxicity_state(
            timestamp=macro.exchange_timestamp,
            contract=CONTRACT_PAIRS[macro_contract],
            buy_volume=0.0,
            sell_volume=0.0,
            trade_intensity_z=0.0,
            cancel_intensity_z=0.0,
            book_thinning_z=0.0,
            config=cfg,
        )
        return OrderIntent(
            model=self.model_id,
            timestamp_signal=macro.exchange_timestamp,
            macro_contract=macro_contract,
            micro_contract=CONTRACT_PAIRS[macro_contract],
            direction=direction,
            target_price=stale.target_price,
            target_quantity=cfg.order_qty,
            order_type="FAK_LIMIT",
            reason={
                "shadow_decay": asdict(decay),
                "nOFI": nofi,
                "spread_zscore": stale.spread_zscore,
                "expected_edge_ticks": edge,
                "available_depth": stale.available_depth,
                "toxicity_state": tox.toxicity_state,
                "total_latency_us": cfg.total_latency_us,
            },
        )
