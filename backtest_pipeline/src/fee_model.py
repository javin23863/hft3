class FeeModel:
    """
    CME Fee and Slippage modeling.
    Calculates execution realism costs for edge evaluation.
    """
    def __init__(self, product: str = "ES", tier: str = "member"):
        # Default clearing and exchange fees for CME Equity Index futures
        self.fees = {
            "ES": {"member": 0.35, "non_member": 1.25},
            "NQ": {"member": 0.35, "non_member": 1.25},
            "MES": {"member": 0.10, "non_member": 0.25},
            "MNQ": {"member": 0.10, "non_member": 0.25}
        }
        self.product = product
        self.tier = tier
        
    def get_fee_per_contract(self) -> float:
        return self.fees.get(self.product, {}).get(self.tier, 1.25)
        
    def calculate_trade_cost(self, contracts: int, is_market_order: bool = False, slippage_ticks: int = 0, tick_value: float = 12.50) -> float:
        """
        Calculates total cost including fees and slippage (adverse selection is separate).
        """
        base_fee = self.get_fee_per_contract() * contracts
        slippage_cost = slippage_ticks * tick_value * contracts if is_market_order else 0.0
        
        return base_fee + slippage_cost
