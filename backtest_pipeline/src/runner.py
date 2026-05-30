"""
HftBacktest 2.x replay runner with blueprint-mandated latency bands and queue models.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable, Dict, List, Optional

from hftbacktest import BacktestAsset, HashMapMarketDepthBacktest

from backtest_pipeline.src.fee_model import FeeModel
from backtest_pipeline.src.hft_strategy import CombinedHypothesisStrategy
from features_engine.src.features.npz_feed import load_npz_events
from features_engine.src.hypotheses.registry import get_active_hypotheses

LATENCY_BANDS_MS = [0.5, 1.0, 2.0, 5.0, 10.0]
QUEUE_MODEL_BUILDERS = {
    "LogProbQueueModel2": lambda a: a.log_prob_queue_model2(),
    "SquareProbQueueModel": lambda a: a.power_prob_queue_model2(2),
}
QUEUE_MODELS = list(QUEUE_MODEL_BUILDERS.keys())


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
        self.fee_model = FeeModel(product=product)

    def build_backtest(self, latency_ms: float, queue_model_type: str) -> HashMapMarketDepthBacktest:
        latency_ns = int(latency_ms * 1_000_000)
        if queue_model_type not in QUEUE_MODEL_BUILDERS:
            raise ValueError(f"Unsupported queue model: {queue_model_type}")

        asset = BacktestAsset()
        asset.data(self.data_path)
        asset.tick_size(self.tick_size)
        asset.lot_size(self.lot_size)
        asset.constant_order_latency(latency_ns, latency_ns)
        asset.no_partial_fill_exchange()
        asset.trading_value_fee_model(0.0, self.fee_model.get_fee_per_contract())
        QUEUE_MODEL_BUILDERS[queue_model_type](asset)
        return HashMapMarketDepthBacktest([asset])

    def run_replay(
        self,
        model_logic_callback: Callable | None = None,
        latency_ms: float = 1.0,
        queue_model: str = "LogProbQueueModel2",
        step_ns: int = 100_000,
        use_combined_strategy: bool = True,
        max_steps: Optional[int] = None,
    ) -> Dict:
        hbt = self.build_backtest(latency_ms, queue_model)
        strategy = None
        if use_combined_strategy and model_logic_callback is None:
            hyps = get_active_hypotheses()
            raw_events = load_npz_events(self.data_path)
            strategy = CombinedHypothesisStrategy(
                hyps,
                tick_size=self.tick_size,
                signal_threshold=0.15,
                latency_ms=latency_ms,
                raw_events=raw_events,
                aggregate_mode="max_abs",
            )

        steps = 0
        while True:
            result = hbt.elapse(step_ns)
            if result == 1:
                break
            if result != 0:
                return {"error": int(result), "steps": steps}
            hbt.clear_inactive_orders(0)
            if model_logic_callback is not None:
                model_logic_callback(hbt)
            elif strategy is not None:
                strategy.on_step(hbt)
            steps += 1
            if max_steps is not None and steps >= max_steps:
                break

        state = hbt.state_values(0)
        return {
            "steps": steps,
            "balance": float(state.balance),
            "fee": float(state.fee),
            "num_trades": int(state.num_trades),
            "trading_volume": float(state.trading_volume),
            "position": float(state.position),
        }

    def generate_research_card(
        self,
        hyp_id: str,
        model_logic_callback: Callable | None = None,
        latency_bands: Optional[List[float]] = None,
        queue_model: str = "LogProbQueueModel2",
    ) -> Dict:
        latency_bands = latency_bands or LATENCY_BANDS_MS
        results_by_band = {}

        for latency in latency_bands:
            print(f"Running replay for {hyp_id} at {latency}ms latency ({queue_model})...")
            results_by_band[f"{latency}ms"] = self.run_replay(
                model_logic_callback,
                latency_ms=latency,
                queue_model=queue_model,
            )

        pnls = [r.get("balance", 0.0) for r in results_by_band.values() if "error" not in r]
        avg_net_pnl = sum(pnls) / len(pnls) if pnls else 0.0
        worst_balance = min(pnls) if pnls else 0.0
        total_trades = sum(r.get("num_trades", 0) for r in results_by_band.values())

        return {
            "model_id": hyp_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "latency_bands_tested": latency_bands,
            "queue_model": queue_model,
            "results_by_band": results_by_band,
            "aggregated_net_pnl": avg_net_pnl,
            "tail_risk_es_95": worst_balance,
            "num_trades": total_trades,
            "approval_status": (
                "PASS"
                if avg_net_pnl > 0 and worst_balance > -500 and total_trades > 0
                else "FAIL"
            ),
        }
