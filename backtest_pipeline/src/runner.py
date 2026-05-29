from hftbacktest import HftBacktest, models
import numpy as np
from typing import Callable, List, Dict
from datetime import datetime

class ReplayRunner:
    """
    Runs HftBacktest replays with parameterized latency bands and strict queue/exchange models.
    Generates outputs ready for Research Cards.
    """
    def __init__(self, data_path: str, tick_size: float = 0.25):
        self.data_path = data_path
        self.tick_size = tick_size
        
    def _get_queue_model(self, queue_model_type: str):
        if queue_model_type == "LogProbQueueModel2":
            return models.queue.LogProbQueueModel2()
        elif queue_model_type == "SquareProbQueueModel":
            return models.queue.SquareProbQueueModel()
        else:
            raise ValueError(f"Unsupported queue model: {queue_model_type}")
            
    def _get_exchange_model(self):
        # Enforcing NoPartialFillExchange logic as per spec
        return models.exchange.NoPartialFillExchange()
            
    def build_backtest(self, latency_ms: float, queue_model_type: str):
        """
        Constructs the HftBacktest instance with the blueprint's mandated models.
        """
        latency_ns = int(latency_ms * 1_000_000)
        latency_model = models.latency.ConstantLatency(latency_ns, latency_ns)
        queue_model = self._get_queue_model(queue_model_type)
        exchange_model = self._get_exchange_model()
        
        hbt = HftBacktest(
            [self.data_path],
            tick_size=self.tick_size,
            lot_size=1.0,
            maker_fee=0.0,
            taker_fee=0.0,
            order_latency=latency_model,
            queue_model=queue_model,
            exchange_model=exchange_model,
            asset_type=models.AssetType.LINEAR,
            trade_list_size=100000
        )
        return hbt
        
    def run_replay(self, model_logic_callback: Callable, latency_ms: float = 1.0, queue_model: str = "LogProbQueueModel2") -> Dict:
        """
        Executes the backtest for a specific latency and queue model.
        Returns a metrics dictionary.
        """
        hbt = self.build_backtest(latency_ms, queue_model)
        
        while hbt.elapse(100_000):  # 100 microseconds steps
            hbt.clear_inactive_orders()
            model_logic_callback(hbt)
            
        # Collect statistics
        # Note: In a real hftbacktest integration, we would pull the internal stats object.
        # This is a structured representation of what that would extract.
        stats = hbt.generate_stats() if hasattr(hbt, 'generate_stats') else {}
        
        return stats
        
    def generate_research_card(self, hyp_id: str, model_logic_callback: Callable, 
                               latency_bands: List[float] = [0.5, 1.0, 2.0, 5.0, 10.0],
                               queue_model: str = "LogProbQueueModel2") -> Dict:
        """
        Runs the model across all required latency bands and generates a comprehensive Research Card.
        """
        results_by_band = {}
        
        for latency in latency_bands:
            print(f"Running replay for {hyp_id} at {latency}ms latency...")
            stats = self.run_replay(model_logic_callback, latency_ms=latency, queue_model=queue_model)
            results_by_band[f"{latency}ms"] = stats
            
        # Aggregate logic
        # Dummy aggregation for the card structure
        avg_net_pnl = 0.0
        worst_es = 0.0
        
        card = {
            "model_id": hyp_id,
            "timestamp": datetime.utcnow().isoformat(),
            "latency_bands_tested": latency_bands,
            "queue_model": queue_model,
            "results_by_band": results_by_band,
            "aggregated_net_pnl": avg_net_pnl,
            "tail_risk_es_95": worst_es,
            "approval_status": "PASS" if avg_net_pnl > 0 and worst_es > -500 else "FAIL"
        }
        
        return card
