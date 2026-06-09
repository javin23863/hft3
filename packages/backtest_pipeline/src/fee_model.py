class FeeModel:
    """
    CME Fee and Slippage modeling.
    Calculates execution realism costs for edge evaluation.

    get_fee_per_contract returns the all-in per-side cost: exchange+clearing
    for the given tier, plus broker commission and NFA regulatory fee.
    Exchange-only "member" rates understate realistic retail cost several-fold;
    pass broker_commission=0.0, nfa_fee=0.0 to recover exchange-only numbers.
    """

    TICK_VALUES = {
        "ES": 12.50,
        "NQ": 5.00,
        "MES": 1.25,
        "MNQ": 0.50,
    }

    def __init__(
        self,
        product: str = "ES",
        tier: str = "non_member",
        broker_commission: float = 0.25,
        nfa_fee: float = 0.02,
    ):
        # Exchange + clearing fees per side for CME Equity Index futures
        self.fees = {
            "ES": {"member": 0.35, "non_member": 1.25},
            "NQ": {"member": 0.35, "non_member": 1.25},
            "MES": {"member": 0.10, "non_member": 0.25},
            "MNQ": {"member": 0.10, "non_member": 0.25}
        }
        self.product = product
        self.tier = tier
        self.broker_commission = broker_commission
        self.nfa_fee = nfa_fee

    def get_fee_per_contract(self) -> float:
        exchange = self.fees.get(self.product, {}).get(self.tier, 1.25)
        return exchange + self.broker_commission + self.nfa_fee

    def calculate_trade_cost(self, contracts: int, is_market_order: bool = False, slippage_ticks: int = 0, tick_value: float = None) -> float:
        """
        Calculates total cost including fees and slippage (adverse selection is separate).
        """
        if tick_value is None:
            tick_value = self.TICK_VALUES.get(self.product, 12.50)
        base_fee = self.get_fee_per_contract() * contracts
        slippage_cost = slippage_ticks * tick_value * contracts if is_market_order else 0.0

        return base_fee + slippage_cost
