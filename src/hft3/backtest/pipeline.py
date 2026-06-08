"""Backtest pipeline — consolidated imports from backtest_pipeline.

Usage:
    from hft3.backtest.pipeline import ReplayRunner, run_all_hypotheses_replay
"""

from backtest_pipeline.src.runner import ReplayRunner
from backtest_pipeline.src.replay_matrix import (
    BacktestResult,
    run_all_hypotheses_replay,
    run_hypothesis_replay,
    run_latency_matrix_replay,
)
from backtest_pipeline.src.signal_backtester import SignalBacktester
from backtest_pipeline.src.pipeline_hyp_fanout import fan_out_hyp_reports
from backtest_pipeline.src.pipeline_model_router import EngineRoute, all_model_ids, list_models, route  # id-verified: same function as pipeline_hyp_fanout.route
from backtest_pipeline.src.pipeline_gate_report import finalize_catalog_models, write_catalog_artifacts
from backtest_pipeline.src.hft_backtest_builder import BacktestAsset, HashMapMarketDepthBacktest, build_hftbacktest
from backtest_pipeline.src.pdf_hybrid_strategy import (
    HybridExecutionStrategy,
    DefensiveConfig,
)
from backtest_pipeline.src.pdf_hybrid_ablation import (
    iter_ablation_configs,
    run_defensive_ablation_matrix,
    run_single_mode,
    summarize_replay_result,
)
from backtest_pipeline.src.pdf_defensive_config import all_defensive_configs
from backtest_pipeline.src.event_meta import load_and_parse_events, load_event_row
from backtest_pipeline.src.hypothesis_replay_strategy import HypothesisReplayStrategy
from backtest_pipeline.src.research_runner import (
    HypothesisRegistry,
    run_all_research_cards,
    get_active_hypotheses,
)
from backtest_pipeline.src.fee_model import FeeModel
from backtest_pipeline.src.chi404_latency import (
    load_chi404_speed,
    resolve_replay_latency_ms,
    validate_replay_latency_ms,
)

__all__ = [
    "ReplayRunner",
    "BacktestResult",
    "run_all_hypotheses_replay",
    "run_hypothesis_replay",
    "run_latency_matrix_replay",
    "SignalBacktester",
    "fan_out_hyp_reports",
    "EngineRoute",
    "all_model_ids",
    "list_models",
    "finalize_catalog_models",
    "write_catalog_artifacts",
    "BacktestAsset",
    "HashMapMarketDepthBacktest",
    "build_hftbacktest",
    "HybridExecutionStrategy",
    "DefensiveConfig",
    "iter_ablation_configs",
    "run_defensive_ablation_matrix",
    "run_single_mode",
    "summarize_replay_result",
    "all_defensive_configs",
    "load_and_parse_events",
    "load_event_row",
    "HypothesisReplayStrategy",
    "HypothesisRegistry",
    "run_all_research_cards",
    "get_active_hypotheses",
    "FeeModel",
    "load_chi404_speed",
    "resolve_replay_latency_ms",
    "validate_replay_latency_ms",
]
