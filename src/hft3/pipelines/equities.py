"""Equities lane — consolidated imports from the equities_lane package.

Backward-compat: the original equities_lane package under packages/ remains
in place. This module provides a cleaner import path for new code.

Usage:
    from hft3.pipelines.equities import LowFloatBacktester, load_universe
"""

from equities_lane.src.backtest.low_float_backtester import LowFloatBacktester
from equities_lane.src.backtest.walk_forward import (
    WalkForwardFold,
    generate_folds,
    run_walk_forward_session,
)
from equities_lane.src.config_loader import load_universe, load_session_by_id
from equities_lane.src.features.feature_registry import run_feature_pipeline, ablation_modules
from equities_lane.src.features.book_adapter import compute_features, FeatureSnapshot
from equities_lane.src.features.hmm_regime import infer_regime
from equities_lane.src.features.l3_stubs import L3FeatureSnapshot, compute_l3_features
from equities_lane.src.ingest.session_io import load_session, save_session
from equities_lane.src.ingest.normalize import normalize_fixture, normalize_dbn
from equities_lane.src.ingest.decadal_pull import estimate_catalog_cost, pull_catalog
from equities_lane.src.ingest.databento_equities import download_session, collect_download_specs
from equities_lane.src.types import (
    SessionMeta,
    UniverseConfig,
    FeatureToggles,
    BacktestResult,
    DegradedModeFlags,
)
from equities_lane.src.models import DailyBar, FloatRecord

__all__ = [
    "LowFloatBacktester",
    "WalkForwardFold",
    "generate_folds",
    "run_walk_forward_session",
    "load_universe",
    "load_session_by_id",
    "run_feature_pipeline",
    "ablation_modules",
    "compute_features",
    "FeatureSnapshot",
    "infer_regime",
    "L3FeatureSnapshot",
    "compute_l3_features",
    "load_session",
    "save_session",
    "normalize_fixture",
    "normalize_dbn",
    "estimate_catalog_cost",
    "pull_catalog",
    "download_session",
    "collect_download_specs",
    "SessionMeta",
    "UniverseConfig",
    "FeatureToggles",
    "BacktestResult",
    "DegradedModeFlags",
    "DailyBar",
    "FloatRecord",
]
