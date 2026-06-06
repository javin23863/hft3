"""Crypto validation workflow — orchestrates routing, replay, metrics, and classification."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from research_pipeline.types import CandidateModel

from backtest_pipeline.src.asset_class_routing import (
    CRYPTO_PERP_TO_BITFINEX_ROUTING,
    CRYPTO_PERP_TO_KRAKEN_SPOT,
    CRYPTO_PERP_TO_L2,
    EXEC_CLASS_L2_DEPTH_VALIDATED,
    EXEC_CLASS_L2_PROXY_ONLY,
    EXEC_CLASS_L3_VALIDATED,
    ExecutionCapability,
    ValidationPath,
    _crypto_bitfinex_mbo_npz_path,
    _crypto_kraken_depth_npz_path,
    _crypto_l2_npz_path,
    resolve_validation_path,
)
from backtest_pipeline.src.crypto_hft_builder import (
    BITFINEX_SYMBOL_CONFIGS,
    build_binance_hftbacktest,
    build_bitfinex_hftbacktest,
    build_kraken_hftbacktest,
)

from crypto_lane.src.validation.crypto_execution_validator import (
    CryptoExecutionResult,
    CryptoReplayStrategy,
    compute_crypto_metrics,
    run_crypto_replay,
)


@dataclass
class CryptoValidationReport:
    candidate_id: str
    model_id: str
    asset_class: str
    execution_classification: str
    validation_path: ExecutionCapability
    npz_path: str
    result: CryptoExecutionResult
    notes: List[str] = field(default_factory=list)


def validate_crypto_candidate(
    candidate: CandidateModel,
    data_catalog_root: Path,
    signal_threshold: float = 0.1,
    latency_ms: float = 50.0,
    max_steps: Optional[int] = None,
    signal_sequence: Optional[List[float]] = None,
) -> CryptoValidationReport:
    path = resolve_validation_path(candidate, data_catalog_root)
    symbol = candidate.metadata.get("symbol", "BTCUSDT")
    perp_symbol = symbol.upper()

    if path.execution_capability == ExecutionCapability.L2_PROXY_VALIDATION:
        l2_sym = CRYPTO_PERP_TO_L2.get(perp_symbol, perp_symbol)
        npz_dir = _crypto_l2_npz_path(data_catalog_root, l2_sym)
        npz_files = sorted(npz_dir.glob("*.npz"))
        if not npz_files:
            return CryptoValidationReport(
                candidate_id=candidate.candidate_id,
                model_id=candidate.model_id,
                asset_class="CRYPTO",
                execution_classification="NO_EXECUTION",
                validation_path=ExecutionCapability.NO_EXECUTION_VALIDATION,
                npz_path="",
                result=CryptoExecutionResult(error="No L2 NPZ files found"),
                notes=["No Binance L2 NPZ files in expected path"],
            )
        npz_path = str(npz_files[0])
        hbt = build_binance_hftbacktest(npz_path, symbol=l2_sym, latency_ms=latency_ms)
        strategy = CryptoReplayStrategy(
            signal_threshold=signal_threshold,
            base_quantity=1.0,
            tick_size=0.1,
            max_position=10.0,
            latency_ms=latency_ms,
            model_id=candidate.model_id,
            is_perp=True,
            marketable_limit=True,
        )
        if signal_sequence:
            _attach_signal_sequence(strategy, signal_sequence)
        run_result = run_crypto_replay(
            hbt=hbt,
            strategy=strategy,
            npz_path=npz_path,
            symbol=l2_sym,
            tick_size=0.1,
            latency_ms=latency_ms,
            queue_model="SquareProbQueueModel",
            max_steps=max_steps,
        )
        result = compute_crypto_metrics(run_result, EXEC_CLASS_L2_PROXY_ONLY)
        return CryptoValidationReport(
            candidate_id=candidate.candidate_id,
            model_id=candidate.model_id,
            asset_class="CRYPTO",
            execution_classification=EXEC_CLASS_L2_PROXY_ONLY,
            validation_path=ExecutionCapability.L2_PROXY_VALIDATION,
            npz_path=npz_path,
            result=result,
            notes=[f"Validated via Binance L2 proxy for {l2_sym}", "Execution style: marketable limit replay"],
        )

    elif path.execution_capability == ExecutionCapability.L2_DEPTH_VALIDATION:
        kraken_sym = CRYPTO_PERP_TO_KRAKEN_SPOT.get(perp_symbol, perp_symbol)
        npz_dir = _crypto_kraken_depth_npz_path(data_catalog_root, kraken_sym)
        npz_files = sorted(npz_dir.glob("*.npz"))
        if not npz_files:
            return CryptoValidationReport(
                candidate_id=candidate.candidate_id,
                model_id=candidate.model_id,
                asset_class="CRYPTO",
                execution_classification="NO_EXECUTION",
                validation_path=ExecutionCapability.NO_EXECUTION_VALIDATION,
                npz_path="",
                result=CryptoExecutionResult(error="No Kraken depth NPZ files found"),
                notes=["No Kraken WS book-depth replay NPZ files in expected path"],
            )
        npz_path = str(npz_files[0])
        hbt = build_kraken_hftbacktest(npz_path, symbol=kraken_sym, latency_ms=latency_ms)
        strategy = CryptoReplayStrategy(
            signal_threshold=signal_threshold,
            base_quantity=1.0,
            tick_size=0.1,
            max_position=10.0,
            latency_ms=latency_ms,
            model_id=candidate.model_id,
            is_perp=False,
            marketable_limit=True,
        )
        if signal_sequence:
            _attach_signal_sequence(strategy, signal_sequence)
        run_result = run_crypto_replay(
            hbt=hbt,
            strategy=strategy,
            npz_path=npz_path,
            symbol=kraken_sym,
            tick_size=0.1,
            latency_ms=latency_ms,
            queue_model="SquareProbQueueModel",
            max_steps=max_steps,
        )
        result = compute_crypto_metrics(run_result, EXEC_CLASS_L2_DEPTH_VALIDATED)
        return CryptoValidationReport(
            candidate_id=candidate.candidate_id,
            model_id=candidate.model_id,
            asset_class="CRYPTO",
            execution_classification=EXEC_CLASS_L2_DEPTH_VALIDATED,
            validation_path=ExecutionCapability.L2_DEPTH_VALIDATION,
            npz_path=npz_path,
            result=result,
            notes=[
                f"Validated via Kraken WS book-depth replay for {kraken_sym}",
                "Data class: L2_DEPTH (aggregated levels; synthetic order IDs)",
                "Queue model: SquareProbQueueModel (not L3Fifo — no true order queue)",
            ],
        )

    elif path.execution_capability == ExecutionCapability.L3_VALIDATED:
        bfx_route = CRYPTO_PERP_TO_BITFINEX_ROUTING.get(perp_symbol, perp_symbol)
        npz_dir = _crypto_bitfinex_mbo_npz_path(data_catalog_root, bfx_route)
        npz_files = sorted(npz_dir.glob("*_mbo.npz")) or sorted(npz_dir.glob("*.npz"))
        if not npz_files:
            return CryptoValidationReport(
                candidate_id=candidate.candidate_id,
                model_id=candidate.model_id,
                asset_class="CRYPTO",
                execution_classification="NO_EXECUTION",
                validation_path=ExecutionCapability.NO_EXECUTION_VALIDATION,
                npz_path="",
                result=CryptoExecutionResult(error="No L3 MBO NPZ files found"),
                notes=["No Bitfinex R0 MBO NPZ files in expected path — run scripts/download_crypto_mbo.py"],
            )
        npz_path = str(npz_files[0])
        tick = float(BITFINEX_SYMBOL_CONFIGS.get(bfx_route, {}).get("tick_size", 0.01))
        hbt = build_bitfinex_hftbacktest(npz_path, symbol=bfx_route, latency_ms=latency_ms)
        strategy = CryptoReplayStrategy(
            signal_threshold=signal_threshold,
            base_quantity=1.0,
            tick_size=tick,
            max_position=10.0,
            latency_ms=latency_ms,
            model_id=candidate.model_id,
            is_perp=False,
            marketable_limit=True,
        )
        if signal_sequence:
            _attach_signal_sequence(strategy, signal_sequence)
        run_result = run_crypto_replay(
            hbt=hbt,
            strategy=strategy,
            npz_path=npz_path,
            symbol=bfx_route,
            tick_size=tick,
            latency_ms=latency_ms,
            queue_model="L3FifoQueueModel",
            max_steps=max_steps,
        )
        result = compute_crypto_metrics(run_result, EXEC_CLASS_L3_VALIDATED)
        return CryptoValidationReport(
            candidate_id=candidate.candidate_id,
            model_id=candidate.model_id,
            asset_class="CRYPTO",
            execution_classification=EXEC_CLASS_L3_VALIDATED,
            validation_path=ExecutionCapability.L3_VALIDATED,
            npz_path=npz_path,
            result=result,
            notes=[
                f"Validated via Bitfinex R0 MBO replay for {bfx_route}",
                "Data class: L3_MBO (order-level, native order IDs)",
                "Queue model: L3FifoQueueModel",
            ],
        )

    else:
        return CryptoValidationReport(
            candidate_id=candidate.candidate_id,
            model_id=candidate.model_id,
            asset_class="CRYPTO",
            execution_classification="NO_EXECUTION",
            validation_path=ExecutionCapability.NO_EXECUTION_VALIDATION,
            npz_path="",
            result=CryptoExecutionResult(error="No execution data available for this candidate"),
            notes=path.notes,
        )


def _attach_signal_sequence(strategy: CryptoReplayStrategy, signal_sequence: List[float]) -> None:
    idx = 0

    def next_signal() -> float:
        nonlocal idx
        if idx >= len(signal_sequence):
            return 0.0
        value = float(signal_sequence[idx])
        idx += 1
        return value

    strategy.set_signal_getter(next_signal)
