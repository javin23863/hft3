import pandas as pd
import numpy as np

class TelemetryMetrics:
    """
    Computes required reporting metrics for the dashboard.
    """
    
    @staticmethod
    def expected_shortfall(pnl_array: np.ndarray, alpha: float = 5.0) -> float:
        """
        Calculates Expected Shortfall (CVaR) at the given alpha percentile.
        """
        threshold = np.percentile(pnl_array, alpha)
        tail_losses = pnl_array[pnl_array <= threshold]
        if len(tail_losses) == 0:
            return 0.0
        return np.mean(tail_losses)
        
    @staticmethod
    def adverse_selection(fills_df: pd.DataFrame, horizons_ms=[100, 500, 1000, 5000]) -> dict:
        """
        Calculates post-fill markout (adverse selection) at multiple horizons.
        Requires 'side', 'exec_price', and 'mid_price_t+h' columns.
        """
        results = {}
        for h in horizons_ms:
            col = f'mid_price_plus_{h}ms'
            if col in fills_df.columns:
                # Long: (markout - exec)
                # Short: (exec - markout)
                longs = fills_df[fills_df['side'] == 'BUY']
                shorts = fills_df[fills_df['side'] == 'SELL']
                
                as_long = (longs[col] - longs['exec_price']).mean() if not longs.empty else 0
                as_short = (shorts['exec_price'] - shorts[col]).mean() if not shorts.empty else 0
                
                results[f'{h}ms'] = (as_long + as_short) / 2.0
                
        return results
        
    @staticmethod
    def sim_live_disagreement(sim_fills: pd.DataFrame, live_fills: pd.DataFrame) -> dict:
        """
        Compares sim vs live fills to detect disagreement.
        """
        # Simplistic stub for metric generation
        if sim_fills.empty or live_fills.empty:
            return {"disagreement_rate": 0.0}
            
        merged = pd.merge(sim_fills, live_fills, on='order_id', suffixes=('_sim', '_live'))
        price_diff = np.abs(merged['exec_price_sim'] - merged['exec_price_live'])
        
        return {
            "disagreement_rate": len(merged[price_diff > 0]) / len(merged),
            "avg_slippage_diff": price_diff.mean()
        }
