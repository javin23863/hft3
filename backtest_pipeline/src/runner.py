from hftbacktest import HftBacktest, HashMapMarketDepthBacktest, models
import numpy as np

class ReplayRunner:
    """
    Runs HftBacktest replays with parameterized latency bands and strict queue/exchange models.
    """
    def __init__(self, data_path: str, latency_ms: float = 1.0, queue_model: str = "LogProbQueueModel2"):
        self.data_path = data_path
        self.latency_ms = latency_ms
        self.queue_model_type = queue_model
        
    def _get_queue_model(self):
        if self.queue_model_type == "LogProbQueueModel2":
            return models.queue.LogProbQueueModel2()
        elif self.queue_model_type == "SquareProbQueueModel":
            return models.queue.SquareProbQueueModel()
        else:
            raise ValueError(f"Unsupported queue model: {self.queue_model_type}")
            
    def build_backtest(self):
        """
        Constructs the HftBacktest instance with the blueprint's mandated models.
        """
        # Convert ms to nanoseconds for the latency model
        latency_ns = int(self.latency_ms * 1_000_000)
        
        # Fixed latency band
        latency_model = models.latency.ConstantLatency(latency_ns, latency_ns)
        
        # Queue model
        queue_model = self._get_queue_model()
        
        # NoPartialFillExchange (HftBacktest provides Exchange/Queue models)
        # Using the standard HftBacktest structure
        
        hbt = HftBacktest(
            [self.data_path],
            tick_size=0.25, # Default to ES tick size, should be parameterized
            lot_size=1.0,
            maker_fee=0.0,  # Handled by separate fee model
            taker_fee=0.0,
            order_latency=latency_model,
            queue_model=queue_model,
            asset_type=models.AssetType.LINEAR,
            trade_list_size=10000
        )
        
        return hbt
        
    def run_replay(self, model_logic_callback):
        """
        Executes the backtest using a provided model logic callback.
        """
        hbt = self.build_backtest()
        
        # Basic loop structure
        while hbt.elapse(100_000):  # 100 microseconds steps
            # Clear old orders
            hbt.clear_inactive_orders()
            
            # Pass state to the model logic
            model_logic_callback(hbt)
            
        return hbt.generate_stats()
