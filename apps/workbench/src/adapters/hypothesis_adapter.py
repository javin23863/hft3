"""Adapter: BaseHypothesis -> WorkbenchModel."""

from __future__ import annotations

import random
import time
from pathlib import Path
from typing import Any, List

from backtest_pipeline.src.replay_matrix import run_hypothesis_replay
from backtest_pipeline.src.signal_backtester import BacktestResult
from features_engine.src.features.npz_feed import iter_mbo_events
from features_engine.src.hypotheses.modules import BaseHypothesis
from features_engine.src.pipeline.market_state_pipeline import MarketStatePipeline

from workbench.src.core.params import DEFAULT_STRATEGY_PARAMS
from workbench.src.core.protocol import Diagnostics, ModelConfig, WorkbenchModel
from workbench.src.core.trade_audit import (
    TradeAuditRecord,
    build_audit_timestamps_ns,
    phase5_latency_chain_ns,
)
from workbench.src.run.run_context import RunContext
from workbench.src.sim.latency_injector import CppLatencyInjector


class HypothesisAdapter(WorkbenchModel):
    def __init__(self, hyp: BaseHypothesis, config: ModelConfig):
        self._hyp = hyp
        self.model_id = config.model_id
        self.config = config

    def _effective_hypothesis(self, ctx: RunContext) -> BaseHypothesis:
        comp_sig = ctx.metadata.get("composition_signal")
        comp_raw = ctx.metadata.get("composition_signal_raw")
        if comp_sig is None or comp_raw is None or abs(float(comp_raw)) < 1e-12:
            return self._hyp
        scale = float(comp_sig) / float(comp_raw)
        if abs(scale - 1.0) < 1e-12:
            return self._hyp

        inner = self._hyp

        class _ScaledHypothesis:
            hyp_id = inner.hyp_id

            def evaluate(self, state: Any) -> float:
                return float(inner.evaluate(state)) * scale

        return _ScaledHypothesis()  # type: ignore[return-value]

    def validate_inputs(self, ctx: RunContext) -> List[str]:
        errs: List[str] = []
        if ctx.events is None or len(ctx.events) == 0:
            errs.append("empty event array")
        elif not ctx.metadata.get("npz_path"):
            errs.append("npz_path required for adapter-backed replay")
        if ctx.metadata.get("data_sufficient") is False:
            errs.append("DATA_INSUFFICIENT")
        return errs

    def build_features(self, ctx: RunContext) -> Any:
        pipeline = MarketStatePipeline(tick_size=0.25, latency_ms=ctx.latency_policy.mean_ms)
        states = []
        for mbo in iter_mbo_events(ctx.events):
            states.append(pipeline.process_event(mbo))
        return states

    def generate_signals(self, features: Any) -> float:
        if not features:
            return 0.0
        return float(self._hyp.evaluate(features[-1]))

    def run_backtest(self, ctx: RunContext) -> BacktestResult:
        params = ctx.metadata.get("strategy_params") or {}
        threshold = float(params.get("signal_threshold", DEFAULT_STRATEGY_PARAMS["signal_threshold"]))
        npz_path = ctx.metadata.get("npz_path")
        if not npz_path:
            import tempfile

            from features_engine.src.features.npz_feed import load_npz_events

            tmp = Path(tempfile.gettempdir()) / f"wb_events_{ctx.run_id}.npz"
            import numpy as np

            np.savez_compressed(tmp, data=ctx.events)
            npz_path = str(tmp)

        rng = random.Random(ctx.seed)
        latency_ms = ctx.latency_policy.total_ms_for_backtest(rng)
        t0 = time.perf_counter()
        result = run_hypothesis_replay(
            self._effective_hypothesis(ctx),
            str(npz_path),
            latency_ms=latency_ms,
            signal_threshold=threshold,
        )
        python_us = (time.perf_counter() - t0) * 1e6
        ctx.metadata["python_research_runtime_us"] = python_us
        ctx.metadata["cpp_backtest_latency_ms"] = latency_ms
        return result

    def produce_diagnostics(self, ctx: RunContext, result: BacktestResult) -> Diagnostics:
        signals = []
        pipeline = MarketStatePipeline(tick_size=0.25, latency_ms=ctx.latency_policy.mean_ms)
        for mbo in iter_mbo_events(ctx.events):
            state = pipeline.process_event(mbo)
            signals.append(self._hyp.evaluate(state))
        self._build_audit(ctx, result)
        return Diagnostics(
            model_id=self.model_id,
            metrics={
                "net_pnl": result.net_pnl,
                "num_trades": result.num_trades,
                "win_rate": result.win_rate,
                "expectancy": result.expectancy,
                "adverse_selection_ticks": result.adverse_selection_ticks,
                "python_research_runtime_us": ctx.metadata.get("python_research_runtime_us"),
                "cpp_backtest_latency_ms": ctx.metadata.get("cpp_backtest_latency_ms"),
            },
            series={"signals": signals[-500:]},
            warnings=[],
        )

    def _build_audit(self, ctx: RunContext, result: BacktestResult) -> List[TradeAuditRecord]:
        injector = ctx.cpp_injector or CppLatencyInjector(
            ctx.latency_policy.cpp_profile,  # type: ignore[arg-type]
            seed=ctx.seed,
        )
        rng = random.Random(ctx.seed)
        records: List[TradeAuditRecord] = []
        python_us_per_fill = (
            ctx.metadata.get("python_research_runtime_us", 0.0) / max(len(result.fills), 1)
        )
        for fill in result.fills:
            injected = injector.sample_decision_latency(
                injection_us=ctx.latency_policy.injection_us,
                percentile="p99",
            )
            exchange_ts = max(0, fill.timestamp_ns - phase5_latency_chain_ns(injected))
            ts = build_audit_timestamps_ns(exchange_ts, injected, fill_ts_ns=fill.timestamp_ns)
            records.append(
                TradeAuditRecord(
                    model_id=self.model_id,
                    **ts,
                    side=fill.side,
                    exec_price=fill.exec_price,
                    qty=fill.qty,
                    signal=fill.signal,
                    mid_at_signal=fill.mid_at_exec,
                    feed_delay_us=injected.feed_delay_us,
                    decision_compute_us=injected.decision_compute_us,
                    decision_to_send_us=injected.decision_to_send_us,
                    send_to_ack_us=injected.send_to_ack_us,
                    python_research_compute_us=python_us_per_fill,
                    latency_injection_us=injected.injection_extra_us,
                )
            )
        ctx.metadata["audit_records"] = records
        return records
