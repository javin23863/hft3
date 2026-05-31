import numpy as np

class MultiLossObjective:
    """
    Multi-target loss function as specified in the blueprint.
    Combines return, fill, PnL, adverse-selection, and tail loss.
    """
    def __init__(self, w_return=1.0, w_fill=1.0, w_pnl=2.0, w_as=1.5, w_tail=2.0):
        self.weights = {
            "return": w_return,
            "fill": w_fill,
            "pnl": w_pnl,
            "adverse_selection": w_as,
            "tail": w_tail
        }
        
    def calculate_loss(self, preds: dict, targets: dict) -> float:
        """
        Calculates the combined loss.
        """
        l_return = np.mean((preds['return'] - targets['return'])**2)
        
        # Binary cross entropy for fill probability
        p_fill = np.clip(preds['fill_prob'], 1e-7, 1 - 1e-7)
        l_fill = -np.mean(targets['filled'] * np.log(p_fill) + (1 - targets['filled']) * np.log(1 - p_fill))
        
        l_pnl = -np.mean(preds['pnl']) # Maximize PnL -> Minimize -PnL
        
        l_as = np.mean((preds['adverse_selection'] - targets['adverse_selection'])**2)
        
        # Tail loss penalty (Expected Shortfall)
        tail_threshold = np.percentile(preds['pnl'], 5)
        l_tail = -np.mean(preds['pnl'][preds['pnl'] < tail_threshold]) if len(preds['pnl'][preds['pnl'] < tail_threshold]) > 0 else 0
        
        total_loss = (
            self.weights["return"] * l_return +
            self.weights["fill"] * l_fill +
            self.weights["pnl"] * l_pnl +
            self.weights["adverse_selection"] * l_as +
            self.weights["tail"] * l_tail
        )
        
        return total_loss
