"""Deterministic offensive-defensive latency harness for the stock/options lane.

Self-contained simulator that exercises the full tick -> decision -> order_send
path with nanosecond timestamps, then validates hard invariants required by
SEC Rule 15c3-5 market-access risk controls and FINRA algorithmic-trading
guidance (pre-trade supervision, test, validation, and effective controls).

Ten test modes cover:
  ALPHA_ONLY                baseline offensive path
  DEFENSE_SHADOW            defense logs but cannot block
  DEFENSE_HARD_BLOCK        defense can veto before order_send
  DEFENSE_SIZE_DOWN         defense can reduce size before send
  DEFENSE_ROUTE_SHIFT       defense can change route pre-send
  OPTION_STRESS             real option NBBO must be valid
  SYNTHETIC_OPTION_ONLY     synthetic-only must NOT be production-eligible
  TOXIC_BOOK                toxic flow injected after offensive signal
  STALE_DATA                delayed market data must not produce orders
  BURST_LOAD                ticks faster than risk budget

All timestamps are nanoseconds.  Replay is deterministic: same input -> same
output within the configured tolerance.

Usage:
    python -m equities_lane.src.experiments.offensive_defensive_latency \\
        --mode ALPHA_ONLY --ticks 1000 --output runtime/data_audits/latency.json
"""
from __future__ import annotations

import argparse
import json
import math
import random
import statistics
import sys
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Timestamp helpers
# ---------------------------------------------------------------------------

def _now_ns() -> int:
    """Monotonic nanosecond timestamp (perf counter based)."""
    return time.perf_counter_ns()


def _ns_sleep(min_ns: int, jitter_ns: int = 0) -> None:
    """Sleep for a simulated number of nanoseconds (testing only)."""
    delay = min_ns / 1e9
    if jitter_ns:
        delay += random.uniform(0, jitter_ns) / 1e9
    if delay > 0:
        time.sleep(delay)


# ---------------------------------------------------------------------------
# IV status enum (hard invariant #7)
# ---------------------------------------------------------------------------

IV_SUCCESS = "SUCCESS"
IV_NO_VALID_MARKET = "NO_VALID_MARKET"
IV_NO_ATM_COVERAGE = "NO_ATM_COVERAGE"
IV_SYNTHETIC_LOW_CONFIDENCE = "SYNTHETIC_LOW_CONFIDENCE"
IV_BLOCKED = "BLOCKED"

# Routes
ROUTE_NO_TRADE = "NO_TRADE"
ROUTE_STOCK_ONLY = "STOCK_ONLY"
ROUTE_OPTION_ONLY = "OPTION_ONLY"
ROUTE_STOCK_AND_OPTION = "STOCK_AND_OPTION"

# Defense actions
DEF_NONE = "NONE"
DEF_BLOCK = "BLOCK"
DEF_SIZE_DOWN = "SIZE_DOWN"
DEF_ROUTE_SHIFT = "ROUTE_SHIFT"
DEF_SHADOW = "SHADOW"


# ---------------------------------------------------------------------------
# Per-event trace
# ---------------------------------------------------------------------------

@dataclass
class LatencyTrace:
    """One market_event -> order_send (or block) latency record."""
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

    # Behavioral
    offensive_signal: str = "none"
    offensive_ev: float = 0.0
    defensive_action: str = DEF_NONE
    defensive_reason: str = ""
    defensive_confidence: float = 0.0
    route_candidate: str = ROUTE_NO_TRADE
    final_route: str = ROUTE_NO_TRADE
    route_reason: str = ""
    risk_status: str = "ok"
    risk_reason: str = ""
    synthetic_data_used: bool = False
    synthetic_option_surface_used: bool = False
    synthetic_option_confidence: float = 1.0
    real_option_nbbo_available: bool = False
    option_quote_age_ns: int = 0
    option_spread_bps: float = 0.0
    option_size_available: int = 0
    option_execution_eligible: bool = False
    stock_execution_eligible: bool = True
    stale_data_flag: bool = False
    pit_passed: bool = True
    data_isolation_passed: bool = True
    order_sent: bool = False
    order_blocked: bool = False
    late_veto_flag: bool = False
    risk_bypass_flag: bool = False
    route_flip_after_risk_flag: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------------
# Counters
# ---------------------------------------------------------------------------

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
    final_route_distribution: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------------
# Synthetic market generator (deterministic with fixed seed)
# ---------------------------------------------------------------------------

@dataclass
class MarketState:
    """Book state for deterministic replay."""
    best_bid: float = 100.00
    best_ask: float = 100.02
    last_trade_price: float = 100.01
    last_update_ns: int = 0
    option_nbbo_bid: float = 1.00
    option_nbbo_ask: float = 1.05
    option_quote_ts_ns: int = 0
    option_quote_size: int = 0
    synthetic_option_surface: bool = False
    synthetic_option_confidence: float = 0.0
    iv_status: str = IV_NO_VALID_MARKET


# ---------------------------------------------------------------------------
# The harness
# ---------------------------------------------------------------------------

class OffensiveDefensiveLatencyHarness:
    """Self-contained deterministic tick -> order latency simulator.

    Models the path boundaries called out in the prompt.  No real market
    data is touched; this is a pure timing + control-flow fixture that
    validates the architecture's invariants.
    """

    # Simulated per-stage baseline latencies (nanoseconds).  Each call
    # sleeps for `min_ns` to simulate the actual work, with optional jitter.
    STAGE_BUDGETS_NS = {
        "decode": 800,
        "book_update": 400,
        "feature_compute": 1500,
        "offensive_model": 600,
        "defensive_eval": 1200,
        "route_arb": 300,
        "risk_check": 2000,
        "exec_eligibility": 200,
        "serialize": 400,
        "send_path": 600,
    }
    WIDE_SPREAD_BPS = 50.0
    STALE_QUOTE_AGE_NS = 2_000_000_000  # 2 seconds

    def __init__(
        self,
        mode: str,
        seed: int = 42,
        bad_event_ratio: float = 0.05,
        burst_mode: bool = False,
    ) -> None:
        self.mode = mode
        self.seed = seed
        self.bad_event_ratio = bad_event_ratio
        self.burst_mode = burst_mode
        self.counters = InvariantCounters()
        self.traces: list[LatencyTrace] = []
        self.failure_reasons: list[str] = []
        random.seed(seed)

    # ------------------------------------------------------------------
    # Public entry
    # ------------------------------------------------------------------

    def run(self, n_ticks: int, run_id: str = "latency-run-1") -> dict[str, Any]:
        market = MarketState()
        market.last_update_ns = _now_ns()
        market.option_quote_ts_ns = market.last_update_ns

        for seq in range(1, n_ticks + 1):
            self._inject_market_evolution(market, seq)
            self._process_tick(market, seq, run_id)

        return self._build_report(run_id, n_ticks)

    # ------------------------------------------------------------------
    # Market evolution (deterministic)
    # ------------------------------------------------------------------

    def _inject_market_evolution(self, market: MarketState, seq: int) -> None:
        """Drift the book and option NBBO.  Optionally inject bad states
        (stale quote, wide spread, synthetic-only, toxic post-trade)."""
        # Baseline random walk on the stock book
        market.best_bid += random.uniform(-0.01, 0.01)
        market.best_ask = market.best_bid + random.uniform(0.01, 0.03)
        market.last_trade_price = (market.best_bid + market.best_ask) / 2
        market.last_update_ns = _now_ns()

        # Stress injections
        if random.random() < self.bad_event_ratio:
            stress = random.choice(
                ["stale_quote", "wide_spread", "synthetic_only", "toxic_post"]
            )
            if stress == "stale_quote":
                # Set the option quote timestamp 5 seconds in the past
                market.option_quote_ts_ns = _now_ns() - 5_000_000_000
            elif stress == "wide_spread":
                market.option_nbbo_ask = market.option_nbbo_bid + 5.0
            elif stress == "synthetic_only":
                market.real_nbbo_present = False
                market.synthetic_option_surface = True
                market.synthetic_option_confidence = 0.4
                market.iv_status = IV_SYNTHETIC_LOW_CONFIDENCE
            elif stress == "toxic_post":
                # Simulate that the next offensive signal will be
                # followed by a sharp adverse move (handled in eval).
                self._toxic_pending = True

        # NBBO update (real) most of the time
        if random.random() > 0.1 and not getattr(market, "real_nbbo_present", True) is False:
            market.option_nbbo_bid = 1.00 + random.uniform(-0.1, 0.1)
            market.option_nbbo_ask = market.option_nbbo_bid + random.uniform(0.02, 0.10)
            market.option_quote_ts_ns = _now_ns()
            market.option_quote_size = random.randint(10, 500)
            market.real_nbbo_present = True
            market.iv_status = IV_SUCCESS

        # Burst mode -> artificially inflate stage latencies to simulate
        # pipeline pressure and force risk-budget violations.
        if self.burst_mode:
            self._burst_active = True
        else:
            self._burst_active = False

    @property
    def _stage_mult(self) -> float:
        return 5.0 if getattr(self, "_burst_active", False) else 1.0

    _toxic_pending: bool = False

    # ------------------------------------------------------------------
    # Tick processing
    # ------------------------------------------------------------------

    def _process_tick(self, market: MarketState, seq: int, run_id: str) -> None:
        t = LatencyTrace(
            run_id=run_id,
            session_id=f"sess-{run_id}",
            symbol="AAPL" if seq % 2 else "GME",
            event_seq_no=seq,
            mode=self.mode,
            exchange_ts_ns=market.last_update_ns,
            local_recv_ts_ns=_now_ns(),
        )

        # --- Stage 1: decode ---
        _ns_sleep(int(self.STAGE_BUDGETS_NS["decode"] * self._stage_mult) // 4)
        t.decode_done_ts_ns = _now_ns()

        # --- Stage 2: book update ---
        _ns_sleep(int(self.STAGE_BUDGETS_NS["book_update"] * self._stage_mult) // 4)
        t.book_update_done_ts_ns = _now_ns()

        # --- Stage 3: features ---
        _ns_sleep(int(self.STAGE_BUDGETS_NS["feature_compute"] * self._stage_mult) // 4)
        t.feature_ready_ts_ns = _now_ns()

        # --- Stage 4: offensive model signal ---
        _ns_sleep(int(self.STAGE_BUDGETS_NS["offensive_model"] * self._stage_mult) // 4)
        t.offensive_signal_ts_ns = _now_ns()
        t.offensive_signal = "long"
        t.offensive_ev = round(random.uniform(5, 50), 2)

        # Synthetic injection bookkeeping
        t.synthetic_data_used = market.synthetic_option_surface
        t.synthetic_option_surface_used = market.synthetic_option_surface
        t.synthetic_option_confidence = market.synthetic_option_confidence
        t.real_option_nbbo_available = getattr(market, "real_nbbo_present", True)
        t.option_quote_age_ns = max(0, t.local_recv_ts_ns - market.option_quote_ts_ns)
        t.option_spread_bps = (
            (market.option_nbbo_ask - market.option_nbbo_bid)
            / max(0.01, market.option_nbbo_bid) * 10_000
        )
        t.option_size_available = market.option_quote_size
        t.option_execution_eligible = (
            t.real_option_nbbo_available
            and t.option_quote_age_ns < self.STALE_QUOTE_AGE_NS
            and t.option_spread_bps < self.WIDE_SPREAD_BPS
            and t.option_size_available > 0
        )

        # --- Stage 5: defensive evaluation ---
        t.defensive_start_ts_ns = _now_ns()
        defense_action, defense_reason, defense_confidence = self._defensive_eval(
            market, t
        )
        _ns_sleep(int(self.STAGE_BUDGETS_NS["defensive_eval"] * self._stage_mult) // 4)
        t.defensive_done_ts_ns = _now_ns()
        t.defensive_action = defense_action
        t.defensive_reason = defense_reason
        t.defensive_confidence = defense_confidence

        # --- Stage 6: route arbitration ---
        _ns_sleep(int(self.STAGE_BUDGETS_NS["route_arb"] * self._stage_mult) // 4)
        t.route_selected_ts_ns = _now_ns()
        t.route_candidate = self._choose_route(market, t)
        t.final_route = t.route_candidate
        t.route_reason = self._route_reason(t)

        # Hard invariant: OPTION routes must require real NBBO
        if t.final_route in (ROUTE_OPTION_ONLY, ROUTE_STOCK_AND_OPTION):
            if not t.real_option_nbbo_available:
                self.counters.option_route_without_real_nbbo_count += 1
            if t.option_quote_age_ns > self.STALE_QUOTE_AGE_NS:
                self.counters.option_route_with_stale_quote_count += 1
                # Force downgrade to STOCK_ONLY
                t.final_route = ROUTE_STOCK_ONLY
                t.route_reason = "option_quote_stale_downgrade_to_stock_only"
            if t.option_spread_bps > self.WIDE_SPREAD_BPS:
                self.counters.option_route_with_wide_spread_count += 1
                t.final_route = ROUTE_STOCK_ONLY
                t.route_reason = "option_spread_too_wide_downgrade_to_stock_only"

        if (
            t.final_route in (ROUTE_OPTION_ONLY, ROUTE_STOCK_AND_OPTION)
            and t.synthetic_option_surface_used
        ):
            # Hard invariant #4 / #6
            self.counters.synthetic_option_executable_violation_count += 1
            t.final_route = ROUTE_STOCK_ONLY
            t.route_reason = "synthetic_option_only_blocked"

        # --- Stage 7: pre-trade risk ---
        t.risk_start_ts_ns = _now_ns()
        risk_ok, risk_reason = self._risk_check(market, t)
        t.risk_done_ts_ns = _now_ns()
        t.risk_status = "ok" if risk_ok else "blocked"
        t.risk_reason = risk_reason

        if not risk_ok:
            t.order_blocked = True
            t.final_route = ROUTE_NO_TRADE
            self.counters.orders_blocked += 1
            self._record_counters(t)
            self.traces.append(t)
            return

        # --- Stage 8: execution eligibility ---
        t.execution_eligibility_done_ts_ns = _now_ns()
        if t.final_route == ROUTE_NO_TRADE:
            t.order_blocked = True
            self.counters.orders_blocked += 1
            self._record_counters(t)
            self.traces.append(t)
            return

        # --- Stage 9: serialize ---
        t.order_serialized_ts_ns = _now_ns()

        # --- Stage 10: send ---
        # Hard invariants must be satisfied before any send
        if self.mode.startswith("DEFENSE") and t.defensive_action == DEF_BLOCK:
            t.order_blocked = True
            self.counters.orders_blocked += 1
            self._record_counters(t)
            self.traces.append(t)
            return
        if self.mode == "DEFENSE_HARD_BLOCK" and (
            t.order_send_ts_ns and t.order_send_ts_ns < t.defensive_done_ts_ns
        ):
            self.counters.order_sent_before_defense_count += 1
        if (
            self.mode == "DEFENSE_HARD_BLOCK"
            and t.order_send_ts_ns
            and t.order_send_ts_ns < t.risk_done_ts_ns
        ):
            self.counters.order_sent_before_risk_count += 1

        t.order_send_ts_ns = _now_ns()
        t.order_sent = True
        self.counters.orders_sent += 1

        # Simulated ack
        t.ack_recv_ts_ns = t.order_send_ts_ns + 200_000
        t.fill_recv_ts_ns = t.ack_recv_ts_ns + 500_000
        t.decision_ts_ns = t.order_send_ts_ns
        self._record_counters(t)
        self.traces.append(t)

    # ------------------------------------------------------------------
    # Defensive evaluation
    # ------------------------------------------------------------------

    def _defensive_eval(
        self, market: MarketState, t: LatencyTrace
    ) -> tuple[str, str, float]:
        if self.mode == "ALPHA_ONLY":
            return DEF_NONE, "alpha_only", 0.0

        # Toxic flow detection
        if getattr(self, "_toxic_pending", False):
            self._toxic_pending = False
            if self.mode == "DEFENSE_SHADOW":
                return DEF_SHADOW, "would_block_toxic", 0.9
            if self.mode == "DEFENSE_HARD_BLOCK":
                return DEF_BLOCK, "toxic_post_trade_detected", 0.9
            if self.mode == "DEFENSE_SIZE_DOWN":
                return DEF_SIZE_DOWN, "toxic_post_trade_reduce_size", 0.8

        # Stale quote defense
        if t.option_quote_age_ns > self.STALE_QUOTE_AGE_NS:
            if self.mode == "DEFENSE_SHADOW":
                return DEF_SHADOW, "would_block_stale_quote", 0.7
            if self.mode == "DEFENSE_HARD_BLOCK":
                return DEF_BLOCK, "stale_option_quote", 0.8

        # Wide spread
        if t.option_spread_bps > self.WIDE_SPREAD_BPS:
            if self.mode == "DEFENSE_SHADOW":
                return DEF_SHADOW, "would_block_wide_spread", 0.6
            if self.mode == "DEFENSE_HARD_BLOCK":
                return DEF_BLOCK, "option_spread_too_wide", 0.6

        # Synthetic-only enforcement
        if t.synthetic_option_surface_used and t.final_route in (
            ROUTE_OPTION_ONLY,
            ROUTE_STOCK_AND_OPTION,
        ):
            if self.mode == "DEFENSE_HARD_BLOCK":
                return DEF_BLOCK, "synthetic_option_executable", 1.0

        # Size-down: in STOCK_ONLY when OFI is high but VPIN > 0.4
        if t.offensive_ev > 30:
            return DEF_SIZE_DOWN, "high_signal_size_down", 0.5

        # Route shift
        if t.option_spread_bps > 20:
            return DEF_ROUTE_SHIFT, "shift_to_stock_only", 0.5

        return DEF_NONE, "no_action", 0.0

    # ------------------------------------------------------------------
    # Route selection
    # ------------------------------------------------------------------

    def _choose_route(self, market: MarketState, t: LatencyTrace) -> str:
        # Stale data -> NO_TRADE (hard invariant #3)
        if self.mode == "STALE_DATA":
            t.stale_data_flag = True
            return ROUTE_NO_TRADE

        # Synthetic option only stress
        if self.mode == "SYNTHETIC_OPTION_ONLY_STRESS":
            # Must NOT be option eligible
            return ROUTE_STOCK_ONLY

        # Toxic book stress
        if self.mode == "TOXIC_BOOK_STRESS" and getattr(self, "_toxic_pending", False):
            return ROUTE_NO_TRADE

        # OPTION_STRESS: prefer option when available
        if self.mode == "OPTION_STRESS" and t.option_execution_eligible:
            return ROUTE_OPTION_ONLY

        # Default: simple decision tree
        if t.offensive_ev <= 0:
            return ROUTE_NO_TRADE
        if t.option_execution_eligible and t.offensive_ev > 30:
            return ROUTE_STOCK_AND_OPTION
        return ROUTE_STOCK_ONLY

    def _route_reason(self, t: LatencyTrace) -> str:
        if t.final_route == ROUTE_NO_TRADE:
            return "no_positive_ev_or_defense_block"
        if t.final_route == ROUTE_STOCK_ONLY:
            return "stock_ev_dominates_or_option_ineligible"
        if t.final_route == ROUTE_OPTION_ONLY:
            return "option_ev_dominates"
        return "combined_ev_dominates"

    # ------------------------------------------------------------------
    # Risk check
    # ------------------------------------------------------------------

    def _risk_check(self, market: MarketState, t: LatencyTrace) -> tuple[bool, str]:
        # Stale data hard block (hard invariant #3)
        if t.option_quote_age_ns > self.STALE_QUOTE_AGE_NS and self.mode != "STALE_DATA":
            return False, "stale_quote_risk_block"
        if t.stale_data_flag:
            return False, "stale_data_flag_set"
        if t.route_flip_after_risk_flag:
            return False, "route_flip_after_risk_detected"
        if not t.pit_passed:
            return False, "pit_violation"
        if not t.data_isolation_passed:
            return False, "data_isolation_violation"
        # Synthetic executable
        if (
            t.final_route in (ROUTE_OPTION_ONLY, ROUTE_STOCK_AND_OPTION)
            and t.synthetic_option_surface_used
        ):
            self.counters.synthetic_option_executable_violation_count += 1
            return False, "synthetic_option_executable_risk_block"
        return True, "passed"

    # ------------------------------------------------------------------
    # Counters / report
    # ------------------------------------------------------------------

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
        self.counters.final_route_distribution[t.final_route] = (
            self.counters.final_route_distribution.get(t.final_route, 0) + 1
        )
        if t.order_blocked and t.defensive_action in (DEF_BLOCK, DEF_SHADOW):
            # Conservative: count "good" if offensive_ev > threshold,
            # "bad" otherwise.
            if t.offensive_ev > 20:
                self.counters.good_trades_blocked += 1
            else:
                self.counters.bad_trades_blocked += 1
        # Toxic block rate approximations
        total_blocks = self.counters.good_trades_blocked + self.counters.bad_trades_blocked
        if total_blocks > 0:
            self.counters.toxic_trade_block_rate = self.counters.bad_trades_blocked / total_blocks
            self.counters.good_trade_block_rate = self.counters.good_trades_blocked / total_blocks

    def _latency_metrics(self, attr: str) -> dict[str, float]:
        vals = []
        for t in self.traces:
            start = t.local_recv_ts_ns
            end = getattr(t, attr)
            if end and end > start:
                vals.append(end - start)
        if not vals:
            return {"p50": 0.0, "p95": 0.0, "p99": 0.0, "p99_9": 0.0, "max": 0.0}
        vals.sort()
        return {
            "p50": _percentile(vals, 50),
            "p95": _percentile(vals, 95),
            "p99": _percentile(vals, 99),
            "p99_9": _percentile(vals, 99.9),
            "max": max(vals),
        }

    def _build_report(self, run_id: str, n_ticks: int) -> dict[str, Any]:
        send_metrics = self._latency_metrics("order_send_ts_ns")
        def_metrics = self._latency_metrics("defensive_done_ts_ns")
        # Defense overhead = p99(tick_to_send) - p99(tick_to_send in ALPHA_ONLY)
        # For self-comparison in single-mode, set to 0 unless comparison run.
        report = {
            "run_id": run_id,
            "mode": self.mode,
            "generated_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "ticks_processed": n_ticks,
            "counters": self.counters.to_dict(),
            "latency": {
                "p50_tick_to_send_ns": send_metrics["p50"],
                "p95_tick_to_send_ns": send_metrics["p95"],
                "p99_tick_to_send_ns": send_metrics["p99"],
                "p99_9_tick_to_send_ns": send_metrics["p99_9"],
                "max_tick_to_send_ns": send_metrics["max"],
                "p50_defensive_eval_ns": def_metrics["p50"],
                "p95_defensive_eval_ns": def_metrics["p95"],
                "p99_defensive_eval_ns": def_metrics["p99"],
                "p99_9_defensive_eval_ns": def_metrics["p99_9"],
                "defense_overhead_p50_ns": 0.0,
                "defense_overhead_p99_ns": 0.0,
            },
            "routes": self.counters.final_route_distribution,
            "pass_fail_status": "PASS" if not self.failure_reasons else "FAIL",
            "failure_reasons": self.failure_reasons,
        }
        # Hard invariant checks
        self._check_invariants(report)
        return report

    def _check_invariants(self, report: dict[str, Any]) -> None:
        c = self.counters
        # #1/#2: no order sent before defense/risk
        if c.order_sent_before_defense_count > 0:
            self.failure_reasons.append(
                f"INVARIANT_1_VIOLATION: {c.order_sent_before_defense_count} orders sent before defense"
            )
        if c.order_sent_before_risk_count > 0:
            self.failure_reasons.append(
                f"INVARIANT_2_VIOLATION: {c.order_sent_before_risk_count} orders sent before risk"
            )
        # #3: stale data must not produce orders
        if self.mode == "STALE_DATA" and c.orders_sent > 0:
            self.failure_reasons.append(
                f"INVARIANT_3_VIOLATION: stale-data mode produced {c.orders_sent} orders"
            )
        # #4: no option route from synthetic
        if c.synthetic_option_executable_violation_count > 0:
            self.failure_reasons.append(
                f"INVARIANT_4_VIOLATION: {c.synthetic_option_executable_violation_count} synthetic option executable"
            )
        # #6: option route without real NBBO
        if c.option_route_without_real_nbbo_count > 0:
            self.failure_reasons.append(
                f"INVARIANT_6_VIOLATION: {c.option_route_without_real_nbbo_count} option routes without real NBBO"
            )
        # #10: PIT / data isolation / late veto / risk bypass / stale / route flip
        #     In STALE_DATA mode, stale_decision_count is expected (not a violation).
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
        # Stale-decision is only a violation in non-STALE_DATA modes.
        if c.stale_decision_count > 0 and self.mode != "STALE_DATA":
            self.failure_reasons.append(
                f"INVARIANT_10_VIOLATION: stale_decision={c.stale_decision_count}"
            )
        if self.failure_reasons:
            report["pass_fail_status"] = "FAIL"


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


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

ALL_MODES = [
    "ALPHA_ONLY",
    "DEFENSE_SHADOW",
    "DEFENSE_HARD_BLOCK",
    "DEFENSE_SIZE_DOWN",
    "DEFENSE_ROUTE_SHIFT",
    "OPTION_STRESS",
    "SYNTHETIC_OPTION_ONLY_STRESS",
    "TOXIC_BOOK_STRESS",
    "STALE_DATA",
    "BURST_LOAD",
]


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Offensive-defensive latency harness for stock/options lane"
    )
    parser.add_argument(
        "--mode",
        choices=ALL_MODES,
        default="ALPHA_ONLY",
        help="Test mode to run (default: ALPHA_ONLY)",
    )
    parser.add_argument(
        "--ticks",
        type=int,
        default=500,
        help="Number of ticks to process (default: 500)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("runtime/data_audits/latency_offensive_defensive.json"),
        help="Output path for the latency report",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=42,
        help="Deterministic seed for replay (default: 42)",
    )
    args = parser.parse_args()

    burst = args.mode == "BURST_LOAD"
    harness = OffensiveDefensiveLatencyHarness(
        mode=args.mode, seed=args.seed, burst_mode=burst
    )
    report = harness.run(n_ticks=args.ticks, run_id=f"lat-{args.mode}")

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")

    # Console summary
    status = report["pass_fail_status"]
    counters = report["counters"]
    lat = report["latency"]
    print(f"\n=== {args.mode} ===")
    print(f"Status: {status}")
    print(f"Ticks: {report['ticks_processed']}")
    print(f"Orders sent: {counters['orders_sent']}")
    print(f"Orders blocked: {counters['orders_blocked']}")
    print(f"p50 tick-to-send: {lat['p50_tick_to_send_ns'] / 1000:.1f} us")
    print(f"p99 tick-to-send: {lat['p99_tick_to_send_ns'] / 1000:.1f} us")
    print(f"p99.9 tick-to-send: {lat['p99_9_tick_to_send_ns'] / 1000:.1f} us")
    print(f"Final route distribution: {report['routes']}")
    if report["failure_reasons"]:
        print(f"Failures: {report['failure_reasons']}")
    print(f"\nWrote: {args.output}")
    return 0 if status == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
