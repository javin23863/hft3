from typing import List
from .modules import (
    BaseHypothesis, StopRunExhaustionFade, DepthRefillImbalance, SpreadBlowoutRecompression,
    LiquidityVacuumContinuation, BookSlopeCollapse, DOMIllusionTrap, ThinBookContinuation, SpreadRegimeChange,
    SecondWaveContinuation, AggressorDecelerationFade, ForcedLiquidationCascade, LateCandleEntryFade, PanicMarketOrderSpreadTax,
    FalseBreakoutTrap, AbsorptionFade, IcebergReloadDetection, LiquidityDefenseBreak, OneSidedAddCancelImbalance, RoundNumberStopSweep, PriorHighLowBreakoutTrap, PassiveTrapFill, RebateTrapAvoidance,
    EsToMesLeadLag, NqToMnqLeadLag, EsNqDivergenceSnapback, ZnZbToEsNqMacroImpulse, MicroContractRetailLag, MaxContractCrowding,
    CancelStormBeforeMove, QueueDepletionTrigger, OpeningCandleChase, VWAPDefenseBreak, StopLossCascadeContinuation,
    EndOfDayForcedFlatten, CutoffPanicExits, NoOvernightInventorySqueeze, DailyLossLimitDefense, TrailingDrawdownPressure,
    ProfitLockBehavior, PropResetReopenWindow, FridayWeekendDerisking, EconomicEventRestrictionFlattening,
    QuotePullBeforeVolatility, RequoteRaceAfterShock
)

def get_active_hypotheses() -> List[BaseHypothesis]:
    return [
        StopRunExhaustionFade(),
        DepthRefillImbalance(),
        SpreadBlowoutRecompression(),
        LiquidityVacuumContinuation(),
        BookSlopeCollapse(),
        DOMIllusionTrap(),
        ThinBookContinuation(),
        SpreadRegimeChange(),
        SecondWaveContinuation(),
        AggressorDecelerationFade(),
        ForcedLiquidationCascade(),
        LateCandleEntryFade(),
        PanicMarketOrderSpreadTax(),
        FalseBreakoutTrap(),
        AbsorptionFade(),
        IcebergReloadDetection(),
        LiquidityDefenseBreak(),
        OneSidedAddCancelImbalance(),
        RoundNumberStopSweep(),
        PriorHighLowBreakoutTrap(),
        PassiveTrapFill(),
        RebateTrapAvoidance(),
        EsToMesLeadLag(),
        NqToMnqLeadLag(),
        EsNqDivergenceSnapback(),
        ZnZbToEsNqMacroImpulse(),
        MicroContractRetailLag(),
        MaxContractCrowding(),
        CancelStormBeforeMove(),
        QueueDepletionTrigger(),
        OpeningCandleChase(),
        VWAPDefenseBreak(),
        StopLossCascadeContinuation(),
        EndOfDayForcedFlatten(),
        CutoffPanicExits(),
        NoOvernightInventorySqueeze(),
        DailyLossLimitDefense(),
        TrailingDrawdownPressure(),
        ProfitLockBehavior(),
        PropResetReopenWindow(),
        FridayWeekendDerisking(),
        EconomicEventRestrictionFlattening(),
        QuotePullBeforeVolatility(),
        RequoteRaceAfterShock()
    ]

class HypothesisRegistry:
    """
    Registry for all 44 hypothesis families specified in the A+ Developer Handoff.
    Each hypothesis module returns a 'research card'.
    """
    def __init__(self):
        self.families = {
            1: "Second-wave continuation",
            2: "Stop-run exhaustion fade",
            3: "Liquidity vacuum continuation",
            4: "Depth-refill imbalance",
            5: "Spread blowout/recompression",
            6: "Aggressor deceleration fade",
            7: "Forced liquidation cascade",
            8: "False breakout trap",
            9: "Cancel storm before move",
            10: "Queue depletion trigger",
            11: "Book slope collapse",
            12: "Absorption fade",
            13: "Iceberg/reload detection",
            14: "Liquidity defense break",
            15: "One-sided add/cancel imbalance",
            16: "ES -> MES lead-lag",
            17: "NQ -> MNQ lead-lag",
            18: "ES/NQ divergence snapback",
            19: "ZN/ZB -> ES/NQ macro impulse",
            20: "Micro contract retail lag",
            21: "Round-number stop sweep",
            22: "Prior high/low breakout trap",
            23: "Opening candle chase",
            24: "VWAP defense/break",
            25: "DOM illusion trap",
            26: "Late candle entry fade",
            27: "Stop-loss cascade continuation",
            28: "Panic market-order spread tax",
            29: "End-of-day forced flatten flow",
            30: "Cutoff panic exits",
            31: "No-overnight inventory squeeze",
            32: "Daily loss-limit defense",
            33: "Trailing drawdown pressure",
            34: "Profit-lock behavior",
            35: "Max-contract crowding in micros",
            36: "Prop reset/reopen window",
            37: "Friday/weekend de-risking",
            38: "Economic-event restriction flattening",
            39: "Quote pull before volatility",
            40: "Re-quote race after shock",
            41: "Thin-book continuation",
            42: "Passive trap fill",
            43: "Rebate trap avoidance",
            44: "Spread regime change"
        }
        
    def get_hypothesis_name(self, id: int) -> str:
        return self.families.get(id, "Unknown Hypothesis")
        
    def generate_research_card(self, id: int, results: dict) -> dict:
        """
        Creates the standardized research card for a model/hypothesis.
        """
        return {
            "model_id": f"HYP_{id}",
            "alpha_family_or_discovered_behavior": self.get_hypothesis_name(id),
            "years_tested": results.get("years_tested", "N/A"),
            "events_tested": results.get("events_tested", 0),
            "latency_bands": results.get("latency_bands", []),
            "queue_model": results.get("queue_model", "Unknown"),
            "net_pnl": results.get("net_pnl", 0.0),
            "expectancy": results.get("expectancy", 0.0),
            "win_rate": results.get("win_rate", 0.0),
            "adverse_selection": results.get("adverse_selection", 0.0),
            "tail_loss": results.get("tail_loss", 0.0),
            "approval_status": "PASS" if results.get("net_pnl", 0) > 0 and results.get("tail_loss", 0) > -500 else "FAIL"
        }
