"""Typed config and result objects for equities lane."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Anti-lookahead / optimistic-alpha guard: backtest sim latency may never be
# set below this floor.  Claims below the floor inflate alpha by simulating
# fills that are unrealistically fast for a Web API (non-DMA) execution path.
LATENCY_FLOOR_MS: float = 5.0


@dataclass
class DatabentoConfig:
    dataset: str
    schema_primary: str
    schema_degraded: str
    stype_in: str
    session_start: str
    session_end: str
    premarket_start: str


@dataclass
class SessionSpec:
    id: str
    symbol: str
    date: str
    fixture: str | None = None


@dataclass
class ChainRules:
    reference_price: str = "premarket_open"
    strike_window_pct: float = 0.30
    max_strikes_per_side: int = 10
    expiry_policy: str = "nearest_listed"
    max_dte_days: int = 45


@dataclass
class OptionsChainSpec:
    enabled: bool = True
    dataset: str = "OPRA.PILLAR"
    schema: str = "cbbo-1m"
    stype_in: str = "parent"
    inherit_window: bool = True
    chain_rules: ChainRules = field(default_factory=ChainRules)


@dataclass
class DecadalSession:
    id: str
    symbol: str
    date: str
    dataset: str
    schema: str = "mbo"
    stype_in: str = "raw_symbol"
    premarket_start: str = "04:00"
    session_end: str = "16:00"
    catalyst: str = ""
    notes: str = ""
    expected_degraded: bool = False
    resolved_symbol: str | None = None
    skip_pull: bool = False
    skip_reason: str | None = None
    options: OptionsChainSpec | None = None

    def raw_filename(self) -> str:
        sym = self.resolved_symbol or self.symbol
        return f"{sym}_{self.date}_{self.schema}.dbn.zst"

    def normalized_filename(self) -> str:
        sym = self.resolved_symbol or self.symbol
        return f"{sym}_{self.date}.ndjson"


@dataclass
class DecadalCatalog:
    repo_root: Path
    sessions: list[DecadalSession]
    paths: dict[str, Path]
    daily_lookback_days: int = 756
    l3_only: bool = True


@dataclass
class FilterConfig:
    float_max_shares: tuple[bool, float]
    min_premarket_gap_pct: tuple[bool, float]
    min_rvol: tuple[bool, float]
    rvol_lookback_days: int
    min_premarket_volume: tuple[bool, float]
    min_float_rotation: tuple[bool, float]
    prior_runner: bool
    min_prior_run_pct: float
    overhead_resistance: bool
    consolidation_days: int


@dataclass
class FeatureToggles:
    ofi: bool = True
    vpin: bool = True
    hawkes: bool = True
    hmm: bool = True
    l3_queue: bool = True
    l3_cancellation: bool = True
    l3_iceberg: bool = True

    def with_ablation(self, disabled: str) -> FeatureToggles:
        data = {
            "ofi": self.ofi,
            "vpin": self.vpin,
            "hawkes": self.hawkes,
            "hmm": self.hmm,
            "l3_queue": self.l3_queue,
            "l3_cancellation": self.l3_cancellation,
            "l3_iceberg": self.l3_iceberg,
        }
        if disabled in data:
            data[disabled] = False
        return FeatureToggles(**data)


@dataclass
class PatternConfig:
    orb_window_minutes: int
    consolidation_pullback_pct: float


@dataclass
class SignalConfig:
    min_mlofi_pc1: float
    min_hmm_markup_prob: float
    max_vpin_exit_percentile: float
    min_hawkes_score: float


@dataclass
class ExecutionConfig:
    latency_ms: float
    slippage_bps: float
    fee_per_share: float
    allow_short: bool
    borrow_fee_bps: float
    halt_on_limit_up: bool


@dataclass
class WalkForwardConfig:
    train_days: int
    val_days: int
    test_days: int
    step_days: int


@dataclass
class ExperimentConfig:
    ablation_modules: list[str]


@dataclass
class UniverseConfig:
    databento: DatabentoConfig
    sessions: list[SessionSpec]
    filters: FilterConfig
    features: FeatureToggles
    patterns: PatternConfig
    signals: SignalConfig
    execution: ExecutionConfig
    walk_forward: WalkForwardConfig
    experiment: ExperimentConfig
    l3_only: bool = True


@dataclass
class DegradedModeFlags:
    degraded_mode: bool = False
    assumptions: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"degraded_mode": self.degraded_mode, "assumptions": list(self.assumptions)}


@dataclass
class FilterResult:
    name: str
    enabled: bool
    status: str  # pass | fail | skip
    value: float | None
    threshold: float | None
    reason: str = ""


@dataclass
class ScreenResult:
    symbol: str
    session_date: str
    passed: bool
    filters: list[FilterResult] = field(default_factory=list)
    assumptions: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "session_date": self.session_date,
            "passed": self.passed,
            "filters": [
                {
                    "name": f.name,
                    "enabled": f.enabled,
                    "status": f.status,
                    "value": f.value,
                    "threshold": f.threshold,
                    "reason": f.reason,
                }
                for f in self.filters
            ],
            "assumptions": self.assumptions,
        }


@dataclass
class SessionMeta:
    symbol: str
    session_date: str
    prior_close: float
    premarket_open: float
    degraded: DegradedModeFlags = field(default_factory=DegradedModeFlags)

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "session_date": self.session_date,
            "prior_close": self.prior_close,
            "premarket_open": self.premarket_open,
            **self.degraded.to_dict(),
        }


@dataclass
class TradeFill:
    ts_ns: int
    side: str
    price: float
    qty: int
    fees: float
    slippage: float


@dataclass
class BacktestResult:
    symbol: str
    session_date: str
    net_pnl: float
    num_trades: int
    max_drawdown: float
    turnover: float
    tail_loss: float
    fills: list[TradeFill] = field(default_factory=list)
    degraded: DegradedModeFlags = field(default_factory=DegradedModeFlags)
    failure_notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "session_date": self.session_date,
            "net_pnl": self.net_pnl,
            "num_trades": self.num_trades,
            "max_drawdown": self.max_drawdown,
            "turnover": self.turnover,
            "tail_loss": self.tail_loss,
            "fills": [
                {
                    "ts_ns": f.ts_ns,
                    "side": f.side,
                    "price": f.price,
                    "qty": f.qty,
                    "fees": f.fees,
                    "slippage": f.slippage,
                }
                for f in self.fills
            ],
            **self.degraded.to_dict(),
            "failure_notes": self.failure_notes,
        }
