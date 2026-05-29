import numpy as np
from typing import Dict, Any

class BaseHypothesis:
    """Base class for all hypothesis testing modules."""
    def __init__(self, hyp_id: int, name: str):
        self.hyp_id = hyp_id
        self.name = name
        
    def evaluate(self, features: Dict[str, Any]) -> float:
        """
        Returns a raw signal score [-1.0, 1.0] representing the hypothesis prediction.
        Must be overridden by subclasses.
        """
        raise NotImplementedError

class StopRunExhaustionFade(BaseHypothesis):
    """
    Hypothesis 2: Stop-run exhaustion fade
    Behavior: Sweep through level, stop-flow spike, aggressor deceleration, opposite-side refill.
    """
    def __init__(self):
        super().__init__(2, "Stop-run exhaustion fade")
        
    def evaluate(self, features: Dict[str, Any]) -> float:
        # Require heavy aggressor imbalance on one side, but high near-touch cancel pressure on that same side,
        # and a book slope leaning the opposite way.
        agg_imb = features.get('aggressor_volume_imbalance', 0.0)
        cancel_pressure = features.get('near_touch_cancel_pressure', 0.0)
        book_slope = features.get('book_slope', 0.0)
        
        signal = 0.0
        
        # Fade a buy stop run
        if agg_imb > 0.6 and cancel_pressure > 0.5 and book_slope < -0.2:
            signal = -0.8 # Strong sell signal
            
        # Fade a sell stop run
        elif agg_imb < -0.6 and cancel_pressure > 0.5 and book_slope > 0.2:
            signal = 0.8 # Strong buy signal
            
        return signal

class DepthRefillImbalance(BaseHypothesis):
    """
    Hypothesis 4: Depth-refill imbalance
    Behavior: One side rebuilds faster and more persistently after shock.
    """
    def __init__(self):
        super().__init__(4, "Depth-refill imbalance")
        
    def evaluate(self, features: Dict[str, Any]) -> float:
        slope_change = features.get('book_slope_change', 0.0)
        cancel_add_ratio = features.get('cancel_to_add_ratio', 1.0)
        
        # If bids are rebuilding much faster than asks (slope change is positive)
        # and overall cancels are low compared to adds
        if slope_change > 0.1 and cancel_add_ratio < 0.5:
            return 0.6 # Buy
            
        # If asks are rebuilding much faster
        elif slope_change < -0.1 and cancel_add_ratio < 0.5:
            return -0.6 # Sell
            
        return 0.0

class SpreadBlowoutRecompression(BaseHypothesis):
    """
    Hypothesis 5: Spread blowout/recompression
    Behavior: Spread widens violently, then compresses with improving depth.
    """
    def __init__(self):
        super().__init__(5, "Spread blowout/recompression")
        
    def evaluate(self, features: Dict[str, Any]) -> float:
        spread_stress = features.get('spread_stress', 1.0)
        book_slope = features.get('book_slope', 0.0)
        
        # If spread is highly stressed, look to provide liquidity (mean revert)
        # on the side with stronger depth.
        if spread_stress > 2.0:
            if book_slope > 0.2:
                return 0.5 # Buy
            elif book_slope < -0.2:
                return -0.5 # Sell
                
        return 0.0
