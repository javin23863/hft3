"""
HftBacktest 2.x replay runner with blueprint-mandated latency bands and queue models.
Delegates to ReplaySession + HftBacktestSimulatedExchangeAdapter for execution parity.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable, Dict, List, Optional

from backtest_pipeline.src.hft_backtest_builder import LATENCY_BANDS_MS, QUEUE_MODELS, build_hftbacktest
from backtest_pipeline.src.hypothesis_replay_strategy import CombinedHypothesisReplayStrategy
from features_engine.src.features.npz_feed import load_npz_events
from features_engine.src.hypotheses.registry import get_active_hypotheses
from replay.replay_session import ReplaySession, ReplaySessionConfig

_MIN_PNL = 0.0
_MIN_BALANCE = -500.0


class ReplayRunner:
    def __init__(
        self,
        data_path: str,
        tick_size: float = 0.25,
        lot_size: float = 1.0,
        product: str = "MES",
    ):
        self.data_path = data_path
        self.tick_size = tick_size
        self.lot_size = lot_size
        self.product = product

    def build_backtest(self, latency_ms: float, queue_model_type: str):
        return build_hftbacktest(
            self.data_path,
            latency_ms=latency_ms,
            queue_model_type=queue_model_type,
            tick_size=self.tick_size,
            lot_size=self.lot_size,
            product=self.product,
        )

    def run_replay(
        self,
        latency_ms: float = 1.0,
        queue_model: str = "LogProbQueueModel2",
        step_ns: int = 100_000,
        use_combined_strategy: bool = True,
        max_steps: Optional[int] = None,
        run_id: str | None = None,
        model_logic_callback: Optional[Callable] = None,
    ) -> Dict:
        raw_events = load_npz_events(self.data_path)
        certification_override = {}
        if model_logic_callback is not None:
            class _CallbackStrategy:
                def on_step(self, ctx):
                    if ctx.hbt_handle is None:
                        raise RuntimeError(
                            "legacy_model_logic_callback requires uncertified hbt handle access"
                        )
                    actions = model_logic_callback(ctx.hbt_handle)
                    return [] if actions is None else actions

            strategy = _CallbackStrategy()
            certification_override = {
                "certification_allowed": False,
                "certification_status": "callback_mode_uncertified_no_lifecycle",
                "certification_block_reason": (
                    "legacy_model_logic_callback_bypasses_replay_adapter_lifecycle"
                ),
            }
        elif use_combined_strategy:
            hyps = get_active_hypotheses()
            strategy = CombinedHypothesisReplayStrategy(
                hyps,
                raw_events,
                tick_size=self.tick_size,
                signal_threshold=0.15,
                latency_ms=latency_ms,
            )
        else:
            from backtest_pipeline.src.hypothesis_replay_strategy import ToyAlwaysLongStrategy

            strategy = ToyAlwaysLongStrategy()

        cfg = ReplaySessionConfig(
            npz_path=self.data_path,
            run_id=run_id or "",
            latency_ms=latency_ms,
            queue_model=queue_model,
            tick_size=self.tick_size,
            lot_size=self.lot_size,
            product=self.product,
            step_ns=step_ns,
            max_steps=max_steps,
            certification_override=certification_override,
            allow_uncertified_hbt_handle=model_logic_callback is not None,
        )
        return ReplaySession(cfg, strategy).run()

    def generate_research_card(
        self,
        hyp_id: str,
        latency_bands: Optional[List[float]] = None,
        queue_model: str = "LogProbQueueModel2",
    ) -> Dict:
        latency_bands = latency_bands or LATENCY_BANDS_MS
        results_by_band = {}

        for latency in latency_bands:
            print(f"Running replay for {hyp_id} at {latency}ms latency ({queue_model})...")
            results_by_band[f"{latency}ms"] = self.run_replay(
                latency_ms=latency,
                queue_model=queue_model,
            )

        pnls = [r.get("balance", 0.0) for r in results_by_band.values() if "error" not in r]
        avg_net_pnl = sum(pnls) / len(pnls) if pnls else 0.0
        worst_balance = min(pnls) if pnls else 0.0
        total_trades = sum(r.get("num_trades", 0) for r in results_by_band.values())
        total_intents = sum(r.get("order_intent_count", 0) for r in results_by_band.values())

        return {
            "model_id": hyp_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "latency_bands_tested": latency_bands,
            "queue_model": queue_model,
            "results_by_band": results_by_band,
            "aggregated_net_pnl": avg_net_pnl,
            "tail_risk_es_95": worst_balance,
            "num_trades": total_trades,
            "order_intent_count": total_intents,
            "approval_status": (
                "PASS"
                if avg_net_pnl > _MIN_PNL and worst_balance > _MIN_BALANCE and total_trades > 0
                else "FAIL"
            ),
        }
