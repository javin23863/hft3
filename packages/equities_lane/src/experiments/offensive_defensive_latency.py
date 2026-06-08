"""Deterministic offensive-defensive latency harness for stock/options routing.

This is a deterministic control-flow and latency harness around the equities
lane route-comparator seam.  The repository does not currently expose a live
broker ``order_send`` implementation, so the final send step is an instrumented
``OrderSendProbe`` test adapter.  The route decision itself uses the real
``equities_lane.src.route.comparator.compare_routes`` implementation.

The harness is designed to answer three narrow questions:

1. Does every sent order have defense and risk timestamps strictly before send?
2. Do stale, synthetic-only, missing-NBBO, stale-quote, and wide-spread option
   states fail closed before an option route can be sent?
3. Are the latency reports deterministic enough to compare defense modes against
   an ALPHA_ONLY baseline?
"""
from __future__ import annotations

import argparse
import json
import math
import random
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


def _find_repo_root() -> Path:
    p = Path(__file__).resolve()
    for parent in p.parents:
        if (parent / ".git").exists() or (parent / "pyproject.toml").exists():
            return parent
    return p.parents[4]


_REPO = _find_repo_root()
for _path in (str(_REPO), str(_REPO / "packages")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from equities_lane.src.ontology.payoff import (  # noqa: E402
    ROUTE_NO_TRADE,
    ROUTE_OPTION_ONLY,
    ROUTE_STOCK_AND_OPTION,
    ROUTE_STOCK_ONLY,
)
from equities_lane.src.route.comparator import RouteInputs, compare_routes  # noqa: E402


IV_SUCCESS = "SUCCESS"
IV_NO_VALID_MARKET = "NO_VALID_MARKET"
IV_NO_ATM_COVERAGE = "NO_ATM_COVERAGE"
IV_SYNTHETIC_LOW_CONFIDENCE = "SYNTHETIC_LOW_CONFIDENCE"
IV_BLOCKED = "BLOCKED"

DEF_NONE = "NONE"
DEF_BLOCK = "BLOCK"
DEF_SIZE_DOWN = "SIZE_DOWN"
DEF_ROUTE_SHIFT = "ROUTE_SHIFT"
DEF_SHADOW = "SHADOW"

ALL_MODES = [
    "ALPHA_ONLY",
    "DEFENSE_SHADOW",
    "DEFENSE_HARD_BLOCK",
    "DEFENSE_SIZE_DOWN",
    "DEFENSE_ROUTE_SHIFT",
    "OPTION_STRESS",
    "SYNTHETIC_OPTION_ONLY_STRESS",
    "TOXIC_BOOK_STRESS",
    "STALE_DATA_STRESS",
    "BURST_LOAD_STRESS",
]

OPTION_ROUTES = {ROUTE_OPTION_ONLY, ROUTE_STOCK_AND_OPTION}


@dataclass
class DeterministicClock:
    """Monotonic deterministic nanosecond clock used for replayable reports."""

    now_ns: int

    def mark(self) -> int:
        return self.now_ns

    def advance(self, delta_ns: int) -> int:
        if delta_ns < 0:
            raise ValueError(f"negative deterministic clock advance: {delta_ns}")
        self.now_ns += delta_ns
        return self.now_ns


@dataclass
class MarketState:
    """Scripted market state for one tick."""

    symbol: str
    session_date: str
    exchange_ts_ns: int
    stock_expected_value: float
    option_expected_value: float
    convexity_exposure: float = 0.0
    gamma_exposure: float = 0.0
    delta_exposure: float = 0.0
    stock_spread_cost: float = 0.03
    option_spread_cost: float = 0.03
    stock_slippage: float = 0.02
    option_slippage: float = 0.02
    stock_fill_probability: float = 0.8
    option_fill_probability: float = 0.8
    liquidity_score_stock: float = 0.8
    liquidity_score_option: float = 0.8
    real_option_nbbo_available: bool = True
    option_quote_age_ns: int = 10_000_000
    option_spread_bps: float = 20.0
    option_size_available: int = 100
    synthetic_option_surface_used: bool = False
    synthetic_option_confidence: float = 1.0
    option_iv_status: str = IV_SUCCESS
    stale_market: bool = False
    toxic_flow: bool = False
    pit_passed: bool = True
    data_isolation_passed: bool = True


@dataclass
class LatencyTrace:
    """One market-event to send/block trace."""

    run_id: str = ""
    session_id: str = ""
    symbol: str = ""
    asset_class: str = "equity_options"
    event_seq_no: int = 0
    mode: str = ""
    exchange_ts_ns: int = 0
    local_recv_ts_ns: int = 0
    decision_ts_ns: int = 0
    decode_done_ts_ns: int = 0
    book_update_done_ts_ns: int = 0
    feature_ready_ts_ns: int = 0
    offensive_signal_ts_ns: int = 0
    defensive_start_ts_ns: int = 0
    defensive_done_ts_ns: int = 0
    route_selected_ts_ns: int = 0
    risk_start_ts_ns: int = 0
    risk_done_ts_ns: int = 0
    execution_eligibility_done_ts_ns: int = 0
    order_serialized_ts_ns: int = 0
    order_send_ts_ns: int = 0
    ack_recv_ts_ns: int = 0
    fill_recv_ts_ns: int = 0
    cancel_recv_ts_ns: int = 0

    offensive_signal: str = "none"
    offensive_ev: float = 0.0
    defensive_action: str = DEF_NONE
    defensive_reason: str = ""
    defensive_confidence: float = 0.0
    route_candidate: str = ROUTE_NO_TRADE
    final_route: str = ROUTE_NO_TRADE
    route_reason: str = ""
    comparator_reason_codes: list[str] = field(default_factory=list)
    route_comparator_used: bool = False
    risk_status: str = "not_run"
    risk_reason: str = ""
    synthetic_data_used: bool = False
    synthetic_option_surface_used: bool = False
    synthetic_option_confidence: float = 1.0
    real_option_nbbo_available: bool = False
    option_iv_status: str = IV_NO_VALID_MARKET
    option_quote_age_ns: int = 0
    option_spread_bps: float = 0.0
    option_size_available: int = 0
    option_execution_eligible: bool = False
    stock_execution_eligible: bool = True
    stale_data_flag: bool = False
    pit_passed: bool = True
    data_isolation_passed: bool = True
    initial_order_qty: int = 0
    final_order_qty: int = 0
    order_sent: bool = False
    order_blocked: bool = False
    late_veto_flag: bool = False
    risk_bypass_flag: bool = False
    route_flip_after_risk_flag: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class InvariantCounters:
    late_veto_count: int = 0
    risk_bypass_count: int = 0
    stale_decision_count: int = 0
    pit_violation_count: int = 0
    data_isolation_violation_count: int = 0
    synthetic_option_executable_violation_count: int = 0
    route_flip_after_risk_count: int = 0
    option_route_without_real_nbbo_count: int = 0
    option_route_with_stale_quote_count: int = 0
    option_route_with_wide_spread_count: int = 0
    order_sent_before_defense_count: int = 0
    order_sent_before_risk_count: int = 0
    nondeterministic_replay_count: int = 0
    bad_trades_blocked: int = 0
    good_trades_blocked: int = 0
    toxic_trade_block_rate: float = 0.0
    good_trade_block_rate: float = 0.0
    orders_sent: int = 0
    orders_blocked: int = 0
    hard_blocks: int = 0
    shadow_alerts: int = 0
    size_down_count: int = 0
    route_shift_count: int = 0
    option_routes_sent: int = 0
    stock_and_option_routes_sent: int = 0
    synthetic_option_downgrade_count: int = 0
    stale_quote_downgrade_count: int = 0
    wide_spread_downgrade_count: int = 0
    missing_nbbo_downgrade_count: int = 0
    toxic_events_detected: int = 0
    toxic_events_blocked: int = 0
    risk_budget_breach_count: int = 0
    final_route_distribution: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class OrderSendProbe:
    """Instrumented send-boundary adapter used because no broker sender exists."""

    def __init__(self, clock: DeterministicClock, harness: "OffensiveDefensiveLatencyHarness") -> None:
        self.clock = clock
        self.harness = harness

    def send(self, t: LatencyTrace) -> None:
        if self.harness.inject_violation == "send_before_defense" and t.event_seq_no == 1:
            send_ts = max(t.local_recv_ts_ns + 1, t.defensive_start_ts_ns - 1)
        elif self.harness.inject_violation == "send_before_risk" and t.event_seq_no == 1:
            send_ts = max(t.defensive_done_ts_ns + 1, t.risk_start_ts_ns - 1)
        else:
            send_ts = self.clock.advance(self.harness._stage_latency_ns("send_path", t.event_seq_no))
        t.order_send_ts_ns = send_ts
        t.order_sent = True
        t.decision_ts_ns = send_ts
        t.ack_recv_ts_ns = send_ts + self.harness.ACK_LATENCY_NS
        t.fill_recv_ts_ns = t.ack_recv_ts_ns + self.harness.FILL_LATENCY_NS
        self.harness._record_send_boundary_invariants(t)


class OffensiveDefensiveLatencyHarness:
    """Deterministic stock/options latency harness.

    The harness uses the real route comparator, then applies defense/risk gates
    before an instrumented test send adapter.  It does not claim broker-level
    execution coverage.
    """

    CLOCK_START_NS = 1_700_000_000_000_000_000
    ACK_LATENCY_NS = 200_000
    FILL_LATENCY_NS = 500_000
    STALE_QUOTE_AGE_NS = 2_000_000_000
    WIDE_SPREAD_BPS = 50.0
    RISK_BUDGET_NS = 12_000

    STAGE_BUDGETS_NS = {
        "decode": 800,
        "book_update": 400,
        "feature_compute": 1_500,
        "offensive_model": 600,
        "defensive_eval": 1_200,
        "route_arb": 300,
        "risk_check": 2_000,
        "exec_eligibility": 200,
        "serialize": 400,
        "send_path": 600,
    }

    def __init__(
        self,
        mode: str,
        seed: int = 42,
        bad_event_ratio: float = 0.05,
        burst_mode: bool = False,
        inject_violation: str | None = None,
    ) -> None:
        if mode not in ALL_MODES:
            raise ValueError(f"unknown latency mode: {mode}")
        if inject_violation not in {None, "send_before_defense", "send_before_risk", "route_flip_after_risk"}:
            raise ValueError(f"unknown violation injection: {inject_violation}")
        self.mode = mode
        self.seed = seed
        self.bad_event_ratio = bad_event_ratio
        self.burst_mode = burst_mode or mode == "BURST_LOAD_STRESS"
        self.inject_violation = inject_violation
        self.rng = random.Random(seed)
        self.clock = DeterministicClock(self.CLOCK_START_NS + seed * 1_000_000)
        self.sender = OrderSendProbe(self.clock, self)
        self.counters = InvariantCounters()
        self.traces: list[LatencyTrace] = []
        self.failure_reasons: list[str] = []

    def run(
        self,
        n_ticks: int,
        run_id: str = "latency-run-1",
        *,
        baseline_latency: dict[str, float] | None = None,
        compute_baseline: bool = True,
    ) -> dict[str, Any]:
        if n_ticks <= 0:
            raise ValueError("n_ticks must be positive")
        self._reset()
        for seq in range(1, n_ticks + 1):
            self.clock.advance(1_000_000)
            market = self._market_for_seq(seq)
            self._process_tick(market, seq, run_id)
        if compute_baseline and self.mode != "ALPHA_ONLY" and baseline_latency is None:
            baseline_latency = self._baseline_latency(n_ticks)
        return self._build_report(run_id, n_ticks, baseline_latency)

    def _reset(self) -> None:
        self.rng = random.Random(self.seed)
        self.clock = DeterministicClock(self.CLOCK_START_NS + self.seed * 1_000_000)
        self.sender = OrderSendProbe(self.clock, self)
        self.counters = InvariantCounters()
        self.traces = []
        self.failure_reasons = []

    def _baseline_latency(self, n_ticks: int) -> dict[str, float]:
        baseline = OffensiveDefensiveLatencyHarness("ALPHA_ONLY", seed=self.seed)
        report = baseline.run(n_ticks, run_id="lat-ALPHA_ONLY-baseline", compute_baseline=False)
        return report["latency"]

    def _stage_latency_ns(self, stage: str, seq: int) -> int:
        base = self.STAGE_BUDGETS_NS[stage]
        deterministic_jitter = (seq * 37 + len(stage) * 11 + self.seed) % 70
        mult = 6 if self.burst_mode else 1
        return (base + deterministic_jitter) * mult

    def _market_for_seq(self, seq: int) -> MarketState:
        symbol = "AAPL" if seq % 2 else "GME"
        stock_ev = 8.0 + (seq % 4) * 0.5
        option_ev = 0.0
        if seq % 5 == 0:
            stock_ev = -1.0

        market = MarketState(
            symbol=symbol,
            session_date="2024-01-02",
            exchange_ts_ns=self.clock.mark(),
            stock_expected_value=stock_ev,
            option_expected_value=option_ev,
        )

        if self.mode == "OPTION_STRESS":
            market.stock_expected_value = -0.25 if seq % 4 else 4.0
            market.option_expected_value = 16.0 + (seq % 3)
            market.convexity_exposure = 5.0
            if seq % 6 == 0:
                market.option_quote_age_ns = self.STALE_QUOTE_AGE_NS + 1
            elif seq % 7 == 0:
                market.option_spread_bps = 85.0
            elif seq % 11 == 0:
                market.real_option_nbbo_available = False
                market.option_iv_status = IV_NO_VALID_MARKET

        elif self.mode == "SYNTHETIC_OPTION_ONLY_STRESS":
            market.stock_expected_value = 7.0
            market.option_expected_value = 50.0
            market.real_option_nbbo_available = False
            market.synthetic_option_surface_used = True
            market.synthetic_option_confidence = 0.35
            market.option_iv_status = IV_SYNTHETIC_LOW_CONFIDENCE

        elif self.mode == "TOXIC_BOOK_STRESS":
            market.stock_expected_value = 12.0
            market.toxic_flow = True

        elif self.mode == "STALE_DATA_STRESS":
            market.stock_expected_value = 12.0
            market.stale_market = True
            market.option_quote_age_ns = self.STALE_QUOTE_AGE_NS + 10_000_000

        elif self.mode == "BURST_LOAD_STRESS":
            market.stock_expected_value = 9.0 if seq % 4 else -1.0

        elif self.mode == "DEFENSE_SHADOW":
            market.toxic_flow = seq % 3 == 0
            market.stock_expected_value = 10.0

        elif self.mode == "DEFENSE_HARD_BLOCK":
            market.toxic_flow = seq % 3 == 0
            market.stock_expected_value = 10.0

        elif self.mode == "DEFENSE_SIZE_DOWN":
            market.stock_expected_value = 14.0

        elif self.mode == "DEFENSE_ROUTE_SHIFT":
            market.stock_expected_value = 9.0
            market.option_expected_value = 10.0
            market.convexity_exposure = 80.0

        if market.synthetic_option_surface_used:
            market.option_iv_status = IV_SYNTHETIC_LOW_CONFIDENCE
        elif not market.real_option_nbbo_available:
            market.option_iv_status = IV_NO_VALID_MARKET
        elif market.option_quote_age_ns >= self.STALE_QUOTE_AGE_NS:
            market.option_iv_status = IV_NO_VALID_MARKET
        elif market.option_spread_bps >= self.WIDE_SPREAD_BPS:
            market.option_iv_status = IV_BLOCKED
        else:
            market.option_iv_status = IV_SUCCESS
        return market

    def _process_tick(self, market: MarketState, seq: int, run_id: str) -> None:
        t = LatencyTrace(
            run_id=run_id,
            session_id=f"sess-{run_id}",
            symbol=market.symbol,
            event_seq_no=seq,
            mode=self.mode,
            exchange_ts_ns=market.exchange_ts_ns,
            local_recv_ts_ns=self.clock.mark(),
            offensive_signal="long" if market.stock_expected_value > 0 or market.option_expected_value > 0 else "none",
            offensive_ev=max(market.stock_expected_value, market.option_expected_value),
            synthetic_data_used=market.synthetic_option_surface_used,
            synthetic_option_surface_used=market.synthetic_option_surface_used,
            synthetic_option_confidence=market.synthetic_option_confidence,
            real_option_nbbo_available=market.real_option_nbbo_available,
            option_iv_status=market.option_iv_status,
            option_quote_age_ns=market.option_quote_age_ns,
            option_spread_bps=market.option_spread_bps,
            option_size_available=market.option_size_available,
            option_execution_eligible=self._option_execution_eligible(market),
            stale_data_flag=market.stale_market,
            pit_passed=market.pit_passed,
            data_isolation_passed=market.data_isolation_passed,
            initial_order_qty=100,
            final_order_qty=100,
        )

        t.decode_done_ts_ns = self.clock.advance(self._stage_latency_ns("decode", seq))
        t.book_update_done_ts_ns = self.clock.advance(self._stage_latency_ns("book_update", seq))
        t.feature_ready_ts_ns = self.clock.advance(self._stage_latency_ns("feature_compute", seq))
        t.offensive_signal_ts_ns = self.clock.advance(self._stage_latency_ns("offensive_model", seq))

        t.defensive_start_ts_ns = self.clock.mark()
        t.defensive_action, t.defensive_reason, t.defensive_confidence = self._defensive_eval(market)
        if t.defensive_action != DEF_NONE:
            self.clock.advance(self._stage_latency_ns("defensive_eval", seq))
        t.defensive_done_ts_ns = self.clock.mark()
        if t.defensive_action == DEF_SHADOW:
            self.counters.shadow_alerts += 1
        if market.toxic_flow:
            self.counters.toxic_events_detected += 1

        self.clock.advance(self._stage_latency_ns("route_arb", seq))
        decision = compare_routes(self._route_inputs(market, seq))
        decision.validate()
        t.route_selected_ts_ns = self.clock.mark()
        t.route_candidate = decision.final_route_decision
        t.final_route = decision.final_route_decision
        t.route_reason = ";".join(decision.reason_codes)
        t.comparator_reason_codes = list(decision.reason_codes)
        t.route_comparator_used = True

        self._record_option_downgrades(market, t)
        self._apply_defense(t)
        if t.defensive_action == DEF_BLOCK:
            self._block(t, "defense_block")
            self.counters.hard_blocks += 1
            if market.toxic_flow:
                self.counters.toxic_events_blocked += 1
            return

        t.risk_start_ts_ns = self.clock.mark()
        risk_ok, risk_reason = self._risk_check(t)
        self.clock.advance(self._stage_latency_ns("risk_check", seq))
        t.risk_done_ts_ns = self.clock.mark()
        t.risk_status = "ok" if risk_ok else "blocked"
        t.risk_reason = risk_reason

        if self.inject_violation == "route_flip_after_risk" and seq == 1 and risk_ok:
            t.final_route = ROUTE_OPTION_ONLY if t.final_route != ROUTE_OPTION_ONLY else ROUTE_STOCK_ONLY
            t.route_flip_after_risk_flag = True

        if not risk_ok:
            self._block(t, risk_reason)
            return

        t.execution_eligibility_done_ts_ns = self.clock.advance(self._stage_latency_ns("exec_eligibility", seq))
        if t.final_route == ROUTE_NO_TRADE:
            self._block(t, "no_trade_route")
            return

        t.order_serialized_ts_ns = self.clock.advance(self._stage_latency_ns("serialize", seq))
        self.sender.send(t)
        self.counters.orders_sent += 1
        self._record_counters(t)
        self.traces.append(t)

    def _option_execution_eligible(self, market: MarketState) -> bool:
        return (
            market.real_option_nbbo_available
            and not market.synthetic_option_surface_used
            and market.option_iv_status == IV_SUCCESS
            and market.option_quote_age_ns < self.STALE_QUOTE_AGE_NS
            and market.option_spread_bps < self.WIDE_SPREAD_BPS
            and market.option_size_available > 0
        )

    def _route_inputs(self, market: MarketState, seq: int) -> RouteInputs:
        option_reasons = self._option_block_reasons(market)
        return RouteInputs(
            underlying_symbol=market.symbol,
            session_date=market.session_date,
            decision_timestamp_ns=market.exchange_ts_ns,
            stock_expected_value=market.stock_expected_value,
            option_expected_value=market.option_expected_value,
            expected_slippage_stock=market.stock_slippage,
            expected_slippage_option=market.option_slippage,
            spread_cost_stock=market.stock_spread_cost,
            spread_cost_option=market.option_spread_cost,
            fill_probability_stock=market.stock_fill_probability,
            fill_probability_option=market.option_fill_probability,
            latency_assumption_stock_us=self._stage_latency_ns("send_path", seq) / 1_000.0,
            latency_assumption_option_us=self._stage_latency_ns("send_path", seq) / 1_000.0,
            max_loss_stock=100.0,
            max_loss_option=50.0,
            convexity_exposure=market.convexity_exposure,
            gamma_exposure=market.gamma_exposure,
            delta_exposure=market.delta_exposure,
            theta_decay_window_seconds=3600.0,
            liquidity_score_stock=market.liquidity_score_stock,
            liquidity_score_option=market.liquidity_score_option,
            borrow_shortability_constraint="long_only",
            selected_option_contracts=("AAPL 2024-01-19 100 C",) if not option_reasons else (),
            equity_features_used=("ofi_zscore", "vpin_value"),
            option_features_used=("iv_atm", "gex_net", "dex_net") if not option_reasons else (),
            option_route_eligible=not option_reasons,
            option_route_block_reasons=option_reasons,
        )

    def _option_block_reasons(self, market: MarketState) -> tuple[str, ...]:
        reasons: list[str] = []
        if market.synthetic_option_surface_used:
            reasons.append("synthetic_option_surface_only")
        if not market.real_option_nbbo_available:
            reasons.append("no_real_executable_option_nbbo")
        if market.option_iv_status != IV_SUCCESS:
            reasons.append(f"iv_status_{market.option_iv_status.lower()}")
        if market.option_quote_age_ns >= self.STALE_QUOTE_AGE_NS:
            reasons.append("option_quote_stale")
        if market.option_spread_bps >= self.WIDE_SPREAD_BPS:
            reasons.append("option_spread_too_wide")
        if market.option_size_available <= 0:
            reasons.append("option_size_unavailable")
        return tuple(dict.fromkeys(reasons))

    def _defensive_eval(self, market: MarketState) -> tuple[str, str, float]:
        if self.mode == "ALPHA_ONLY":
            return DEF_NONE, "alpha_only_no_defense", 0.0
        if self.mode == "DEFENSE_SHADOW" and market.toxic_flow:
            return DEF_SHADOW, "would_block_toxic_flow", 0.9
        if self.mode == "DEFENSE_HARD_BLOCK" and market.toxic_flow:
            return DEF_BLOCK, "toxic_flow_pretrade_block", 0.95
        if self.mode == "TOXIC_BOOK_STRESS" and market.toxic_flow:
            return DEF_BLOCK, "toxic_book_stress_block", 0.99
        if self.mode == "STALE_DATA_STRESS" and market.stale_market:
            return DEF_BLOCK, "stale_market_state", 1.0
        if self.mode == "DEFENSE_SIZE_DOWN":
            return DEF_SIZE_DOWN, "toxicity_size_reduction", 0.75
        if self.mode == "DEFENSE_ROUTE_SHIFT":
            return DEF_ROUTE_SHIFT, "option_liquidity_shift_to_stock", 0.7
        return DEF_NONE, "no_action", 0.0

    def _record_option_downgrades(self, market: MarketState, t: LatencyTrace) -> None:
        if not t.option_execution_eligible and market.option_expected_value > 0:
            if market.synthetic_option_surface_used:
                self.counters.synthetic_option_downgrade_count += 1
            if not market.real_option_nbbo_available:
                self.counters.missing_nbbo_downgrade_count += 1
            if market.option_quote_age_ns >= self.STALE_QUOTE_AGE_NS:
                self.counters.stale_quote_downgrade_count += 1
            if market.option_spread_bps >= self.WIDE_SPREAD_BPS:
                self.counters.wide_spread_downgrade_count += 1

    def _apply_defense(self, t: LatencyTrace) -> None:
        if t.defensive_action == DEF_SIZE_DOWN:
            t.final_order_qty = 25
            self.counters.size_down_count += 1
            return
        if t.defensive_action == DEF_ROUTE_SHIFT and t.final_route in OPTION_ROUTES:
            t.final_route = ROUTE_STOCK_ONLY
            t.route_reason = "defense_route_shift_to_stock_only"
            self.counters.route_shift_count += 1

    def _risk_check(self, t: LatencyTrace) -> tuple[bool, str]:
        if t.stale_data_flag:
            return False, "stale_market_state"
        if not t.pit_passed:
            return False, "pit_violation"
        if not t.data_isolation_passed:
            return False, "data_isolation_violation"
        if t.route_flip_after_risk_flag:
            return False, "route_flip_after_risk_detected"
        if t.final_route in OPTION_ROUTES:
            if t.synthetic_option_surface_used:
                return False, "synthetic_option_surface_only"
            if not t.real_option_nbbo_available:
                return False, "no_real_option_nbbo"
            if t.option_quote_age_ns >= self.STALE_QUOTE_AGE_NS:
                return False, "stale_option_quote"
            if t.option_spread_bps >= self.WIDE_SPREAD_BPS:
                return False, "wide_option_spread"
        return True, "passed"

    def _block(self, t: LatencyTrace, reason: str) -> None:
        t.order_blocked = True
        t.final_route = ROUTE_NO_TRADE
        t.route_reason = reason
        if not t.risk_reason:
            t.risk_reason = reason
        self.counters.orders_blocked += 1
        self._record_counters(t)
        self.traces.append(t)

    def _record_send_boundary_invariants(self, t: LatencyTrace) -> None:
        if t.order_send_ts_ns <= 0:
            return
        if t.defensive_done_ts_ns <= 0 or t.order_send_ts_ns < t.defensive_done_ts_ns:
            self.counters.order_sent_before_defense_count += 1
        if t.risk_done_ts_ns <= 0 or t.order_send_ts_ns < t.risk_done_ts_ns:
            self.counters.order_sent_before_risk_count += 1
        if t.final_route in OPTION_ROUTES:
            if t.synthetic_option_surface_used:
                self.counters.synthetic_option_executable_violation_count += 1
            if not t.real_option_nbbo_available:
                self.counters.option_route_without_real_nbbo_count += 1
            if t.option_quote_age_ns >= self.STALE_QUOTE_AGE_NS:
                self.counters.option_route_with_stale_quote_count += 1
            if t.option_spread_bps >= self.WIDE_SPREAD_BPS:
                self.counters.option_route_with_wide_spread_count += 1

    def _record_counters(self, t: LatencyTrace) -> None:
        if t.late_veto_flag:
            self.counters.late_veto_count += 1
        if t.risk_bypass_flag:
            self.counters.risk_bypass_count += 1
        if t.stale_data_flag:
            self.counters.stale_decision_count += 1
        if not t.pit_passed:
            self.counters.pit_violation_count += 1
        if not t.data_isolation_passed:
            self.counters.data_isolation_violation_count += 1
        if t.route_flip_after_risk_flag:
            self.counters.route_flip_after_risk_count += 1
        if t.order_sent and t.final_route == ROUTE_OPTION_ONLY:
            self.counters.option_routes_sent += 1
        if t.order_sent and t.final_route == ROUTE_STOCK_AND_OPTION:
            self.counters.stock_and_option_routes_sent += 1
        self.counters.final_route_distribution[t.final_route] = (
            self.counters.final_route_distribution.get(t.final_route, 0) + 1
        )
        if t.order_blocked and t.defensive_action == DEF_BLOCK:
            if t.defensive_confidence >= 0.9:
                self.counters.bad_trades_blocked += 1
            else:
                self.counters.good_trades_blocked += 1
        total_blocks = self.counters.good_trades_blocked + self.counters.bad_trades_blocked
        if total_blocks > 0:
            self.counters.toxic_trade_block_rate = self.counters.bad_trades_blocked / total_blocks
            self.counters.good_trade_block_rate = self.counters.good_trades_blocked / total_blocks
        if t.risk_done_ts_ns and t.local_recv_ts_ns:
            if t.risk_done_ts_ns - t.local_recv_ts_ns > self.RISK_BUDGET_NS:
                self.counters.risk_budget_breach_count += 1

    def _latency_metrics(self, end_attr: str) -> dict[str, float]:
        vals: list[int] = []
        for t in self.traces:
            start = t.local_recv_ts_ns
            end = getattr(t, end_attr)
            if end and end >= start:
                vals.append(end - start)
        return _metrics(vals)

    def _duration_metrics(self, start_attr: str, end_attr: str) -> dict[str, float]:
        vals: list[int] = []
        for t in self.traces:
            start = getattr(t, start_attr)
            end = getattr(t, end_attr)
            if end and start and end >= start:
                vals.append(end - start)
        return _metrics(vals)

    def _build_report(
        self,
        run_id: str,
        n_ticks: int,
        baseline_latency: dict[str, float] | None,
    ) -> dict[str, Any]:
        send_metrics = self._latency_metrics("order_send_ts_ns")
        defense_metrics = self._duration_metrics("defensive_start_ts_ns", "defensive_done_ts_ns")
        risk_metrics = self._duration_metrics("risk_start_ts_ns", "risk_done_ts_ns")
        if self.mode == "ALPHA_ONLY":
            overhead_p50 = 0.0
            overhead_p99 = 0.0
        elif baseline_latency:
            overhead_p50 = max(0.0, send_metrics["p50"] - baseline_latency["p50_tick_to_send_ns"])
            overhead_p99 = max(0.0, send_metrics["p99"] - baseline_latency["p99_tick_to_send_ns"])
        else:
            overhead_p50 = 0.0
            overhead_p99 = 0.0

        report = {
            "run_id": run_id,
            "mode": self.mode,
            "generated_at_utc": "1970-01-01T00:00:00Z",
            "deterministic_seed": self.seed,
            "sessions": 1,
            "ticks_processed": n_ticks,
            "uses_real_route_comparator": True,
            "send_boundary_adapter": "OrderSendProbe",
            "counters": self.counters.to_dict(),
            "latency": {
                "p50_tick_to_send_ns": send_metrics["p50"],
                "p95_tick_to_send_ns": send_metrics["p95"],
                "p99_tick_to_send_ns": send_metrics["p99"],
                "p99_9_tick_to_send_ns": send_metrics["p99_9"],
                "max_tick_to_send_ns": send_metrics["max"],
                "p50_defensive_eval_ns": defense_metrics["p50"],
                "p95_defensive_eval_ns": defense_metrics["p95"],
                "p99_defensive_eval_ns": defense_metrics["p99"],
                "p99_9_defensive_eval_ns": defense_metrics["p99_9"],
                "p50_risk_check_ns": risk_metrics["p50"],
                "p99_risk_check_ns": risk_metrics["p99"],
                "defense_overhead_p50_ns": overhead_p50,
                "defense_overhead_p99_ns": overhead_p99,
            },
            "routes": self.counters.final_route_distribution,
            "pass_fail_status": "PASS",
            "failure_reasons": self.failure_reasons,
            "summary_table": [],
            "trace_count": len(self.traces),
            "trace_sample": [t.to_dict() for t in self.traces[:20]],
        }
        self._check_invariants(report)
        report["summary_table"] = [self._summary_row(report)]
        return report

    def _summary_row(self, report: dict[str, Any]) -> dict[str, Any]:
        counters = report["counters"]
        latency = report["latency"]
        return {
            "Mode": report["mode"],
            "Sessions": report["sessions"],
            "Orders Sent": counters["orders_sent"],
            "Orders Blocked": counters["orders_blocked"],
            "p50 tick-to-send": latency["p50_tick_to_send_ns"],
            "p99 tick-to-send": latency["p99_tick_to_send_ns"],
            "p99.9 tick-to-send": latency["p99_9_tick_to_send_ns"],
            "Defense overhead p99": latency["defense_overhead_p99_ns"],
            "Late vetoes": counters["late_veto_count"],
            "Risk bypasses": counters["risk_bypass_count"],
            "Stale decisions": counters["stale_decision_count"],
            "Synthetic option exec violations": counters["synthetic_option_executable_violation_count"],
            "Final route distribution": report["routes"],
            "Pass/Fail": report["pass_fail_status"],
        }

    def _check_invariants(self, report: dict[str, Any]) -> None:
        c = self.counters
        if c.order_sent_before_defense_count > 0:
            self.failure_reasons.append(
                f"INVARIANT_1_VIOLATION: {c.order_sent_before_defense_count} orders sent before defense"
            )
        if c.order_sent_before_risk_count > 0:
            self.failure_reasons.append(
                f"INVARIANT_2_VIOLATION: {c.order_sent_before_risk_count} orders sent before risk"
            )
        if self.mode == "STALE_DATA_STRESS" and c.orders_sent > 0:
            self.failure_reasons.append(
                f"INVARIANT_3_VIOLATION: stale-data stress produced {c.orders_sent} orders"
            )
        if self.mode != "STALE_DATA_STRESS" and c.stale_decision_count > 0:
            self.failure_reasons.append(
                f"INVARIANT_3_VIOLATION: stale decisions outside stale stress={c.stale_decision_count}"
            )
        if c.synthetic_option_executable_violation_count > 0:
            self.failure_reasons.append(
                f"INVARIANT_4_VIOLATION: {c.synthetic_option_executable_violation_count} synthetic option routes sent"
            )
        if c.option_route_without_real_nbbo_count > 0:
            self.failure_reasons.append(
                f"INVARIANT_6_VIOLATION: {c.option_route_without_real_nbbo_count} option routes without real NBBO"
            )
        if c.option_route_with_stale_quote_count > 0:
            self.failure_reasons.append(
                f"INVARIANT_6_VIOLATION: {c.option_route_with_stale_quote_count} option routes with stale quotes"
            )
        if c.option_route_with_wide_spread_count > 0:
            self.failure_reasons.append(
                f"INVARIANT_6_VIOLATION: {c.option_route_with_wide_spread_count} option routes with wide spreads"
            )
        for attr, name in [
            ("pit_violation_count", "PIT"),
            ("data_isolation_violation_count", "data_isolation"),
            ("late_veto_count", "late_veto"),
            ("risk_bypass_count", "risk_bypass"),
            ("route_flip_after_risk_count", "route_flip_after_risk"),
        ]:
            v = getattr(c, attr)
            if v > 0:
                self.failure_reasons.append(f"INVARIANT_10_VIOLATION: {name}={v}")
        for t in self.traces:
            if not t.order_sent:
                continue
            ordered = [
                t.local_recv_ts_ns,
                t.decode_done_ts_ns,
                t.book_update_done_ts_ns,
                t.feature_ready_ts_ns,
                t.offensive_signal_ts_ns,
                t.defensive_start_ts_ns,
                t.defensive_done_ts_ns,
                t.route_selected_ts_ns,
                t.risk_start_ts_ns,
                t.risk_done_ts_ns,
                t.execution_eligibility_done_ts_ns,
                t.order_serialized_ts_ns,
                t.order_send_ts_ns,
            ]
            if any(a > b for a, b in zip(ordered, ordered[1:])):
                self.failure_reasons.append(
                    f"TIMESTAMP_ORDER_VIOLATION: event_seq_no={t.event_seq_no}"
                )
        if self.failure_reasons:
            report["pass_fail_status"] = "FAIL"
            report["failure_reasons"] = self.failure_reasons


def _metrics(vals: list[int | float]) -> dict[str, float]:
    if not vals:
        return {"p50": 0.0, "p95": 0.0, "p99": 0.0, "p99_9": 0.0, "max": 0.0}
    vals = sorted(vals)
    return {
        "p50": _percentile(vals, 50),
        "p95": _percentile(vals, 95),
        "p99": _percentile(vals, 99),
        "p99_9": _percentile(vals, 99.9),
        "max": float(max(vals)),
    }


def _percentile(sorted_vals: list[int | float], pct: float) -> float:
    if not sorted_vals:
        return 0.0
    k = (len(sorted_vals) - 1) * (pct / 100.0)
    f = math.floor(k)
    c = math.ceil(k)
    if f == c:
        return float(sorted_vals[int(k)])
    d0 = sorted_vals[int(f)] * (c - k)
    d1 = sorted_vals[int(c)] * (k - f)
    return float(d0 + d1)


def run_latency_suite(
    *,
    modes: list[str] | None = None,
    ticks: int = 500,
    seed: int = 42,
    run_id: str = "latency-suite",
) -> dict[str, Any]:
    selected_modes = modes or ALL_MODES
    baseline_harness = OffensiveDefensiveLatencyHarness("ALPHA_ONLY", seed=seed)
    baseline = baseline_harness.run(ticks, run_id="lat-ALPHA_ONLY", compute_baseline=False)
    baseline_latency = baseline["latency"]
    reports: list[dict[str, Any]] = []
    for mode in selected_modes:
        if mode == "ALPHA_ONLY":
            report = baseline
        else:
            report = OffensiveDefensiveLatencyHarness(mode, seed=seed).run(
                ticks,
                run_id=f"lat-{mode}",
                baseline_latency=baseline_latency,
                compute_baseline=False,
            )
        reports.append(report)
    return {
        "run_id": run_id,
        "generated_at_utc": "1970-01-01T00:00:00Z",
        "deterministic_seed": seed,
        "ticks_per_mode": ticks,
        "modes": selected_modes,
        "summary_table": [row for report in reports for row in report["summary_table"]],
        "reports": reports,
        "pass_fail_status": "PASS" if all(r["pass_fail_status"] == "PASS" for r in reports) else "FAIL",
    }


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")


def _print_report(report: dict[str, Any]) -> None:
    if "reports" in report:
        print(f"\n=== {report['run_id']} ===")
        print(f"Status: {report['pass_fail_status']}")
        for row in report["summary_table"]:
            print(
                f"{row['Mode']}: {row['Pass/Fail']} "
                f"sent={row['Orders Sent']} blocked={row['Orders Blocked']} "
                f"p99={row['p99 tick-to-send'] / 1000:.1f}us "
                f"overhead_p99={row['Defense overhead p99'] / 1000:.1f}us "
                f"routes={row['Final route distribution']}"
            )
        return
    row = report["summary_table"][0]
    print(f"\n=== {report['mode']} ===")
    print(f"Status: {report['pass_fail_status']}")
    print(f"Ticks: {report['ticks_processed']}")
    print(f"Orders sent: {row['Orders Sent']}")
    print(f"Orders blocked: {row['Orders Blocked']}")
    print(f"p50 tick-to-send: {row['p50 tick-to-send'] / 1000:.1f} us")
    print(f"p99 tick-to-send: {row['p99 tick-to-send'] / 1000:.1f} us")
    print(f"p99.9 tick-to-send: {row['p99.9 tick-to-send'] / 1000:.1f} us")
    print(f"Defense overhead p99: {row['Defense overhead p99'] / 1000:.1f} us")
    print(f"Final route distribution: {row['Final route distribution']}")
    if report["failure_reasons"]:
        print(f"Failures: {report['failure_reasons']}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Deterministic offensive-defensive latency harness for stock/options lane"
    )
    parser.add_argument("--mode", choices=ALL_MODES, default="ALPHA_ONLY")
    parser.add_argument("--all-modes", action="store_true", help="Run all modes and emit a suite report")
    parser.add_argument("--ticks", type=int, default=500)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("runtime/data_audits/latency_offensive_defensive.json"),
    )
    args = parser.parse_args()

    if args.all_modes:
        report = run_latency_suite(ticks=args.ticks, seed=args.seed)
    else:
        report = OffensiveDefensiveLatencyHarness(args.mode, seed=args.seed).run(
            args.ticks,
            run_id=f"lat-{args.mode}",
        )
    _write_json(args.output, report)
    _print_report(report)
    print(f"\nWrote: {args.output}")
    return 0 if report["pass_fail_status"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
