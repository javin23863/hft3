from __future__ import annotations

from backtest_pipeline.src.instrument_specs import INSTRUMENT_SPECS


class FeeModelError(ValueError):
    """Raised when fees are requested for a product the model does not know."""


class FeeModel:
    """
    CME Fee and Slippage modeling.
    Calculates execution realism costs for edge evaluation.

    get_fee_per_contract returns the all-in per-side cost: exchange+clearing
    for the given tier, plus broker commission and NFA regulatory fee.
    Exchange-only "member" rates understate realistic retail cost several-fold;
    pass broker_commission=0.0, nfa_fee=0.0 to recover exchange-only numbers.

    Unknown products raise FeeModelError. A backtest must never price one
    instrument with another instrument's fee or tick schedule — micros vs
    e-minis differ ~5x per side and rates ticks differ up to 25x from the old
    ES defaults.
    """

    TICK_VALUES = {symbol: spec.tick_value for symbol, spec in INSTRUMENT_SPECS.items()}

    # Exchange + clearing fees per side, USD per contract (CME fee schedule,
    # https://www.cmegroup.com/company/clearing-fees.html). Tick values come
    # from instrument_specs, parity-tested against the campaign prepare
    # authority config/hftbacktest/cme_lake_product_metadata.yaml.
    FEES = {
        # equity index e-minis
        "ES": {"member": 0.35, "non_member": 1.25},
        "NQ": {"member": 0.35, "non_member": 1.25},
        "RTY": {"member": 0.35, "non_member": 1.25},
        "YM": {"member": 0.35, "non_member": 1.25},
        # equity index micros
        "MES": {"member": 0.10, "non_member": 0.25},
        "MNQ": {"member": 0.10, "non_member": 0.25},
        "M2K": {"member": 0.10, "non_member": 0.25},
        "MYM": {"member": 0.10, "non_member": 0.25},
        # CBOT treasuries
        "ZT": {"member": 0.40, "non_member": 0.80},
        "ZF": {"member": 0.40, "non_member": 0.80},
        "ZN": {"member": 0.40, "non_member": 0.80},
        "ZB": {"member": 0.40, "non_member": 0.80},
        "UB": {"member": 0.40, "non_member": 0.80},
        # STIR
        "SR3": {"member": 0.20, "non_member": 0.50},
        "ZQ": {"member": 0.20, "non_member": 0.50},
    }

    def __init__(
        self,
        product: str = "ES",
        tier: str = "non_member",
        broker_commission: float = 0.25,
        nfa_fee: float = 0.02,
    ):
        self.fees = dict(self.FEES)
        self.product = product
        self.tier = tier
        self.broker_commission = broker_commission
        self.nfa_fee = nfa_fee

    def get_fee_per_contract(self) -> float:
        tiers = self.fees.get(self.product)
        if tiers is None:
            raise FeeModelError(f"fee_model_unknown_product:{self.product}")
        exchange = tiers.get(self.tier)
        if exchange is None:
            raise FeeModelError(f"fee_model_unknown_tier:{self.product}:{self.tier}")
        return exchange + self.broker_commission + self.nfa_fee

    def calculate_trade_cost(self, contracts: int, is_market_order: bool = False, slippage_ticks: int = 0, tick_value: float | None = None) -> float:
        """
        Calculates total cost including fees and slippage (adverse selection is separate).
        """
        if tick_value is None:
            tick_value = self.TICK_VALUES.get(self.product)
            if tick_value is None:
                raise FeeModelError(f"fee_model_unknown_product:{self.product}")
        base_fee = self.get_fee_per_contract() * contracts
        slippage_cost = slippage_ticks * tick_value * contracts if is_market_order else 0.0

        return base_fee + slippage_cost
