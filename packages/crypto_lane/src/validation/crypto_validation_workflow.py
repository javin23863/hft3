"""Crypto validation workflow — orchestrates routing, replay, metrics, and classification."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from research_pipeline.types import CandidateModel

from backtest_pipeline.src.asset_class_routing import (
    ExecutionCapability,
    ValidationPath,
    _crypto_l2_npz_path,
    _crypto_l3_npz_path,
    resolve_validation_path,
)
from backtest_pipeline.src.crypto_hft_builder import (
    build_binance_hftbacktest,
    build_kraken_hftbacktest,
    CRYPTO_PERP_TO_L2,
    CRYPTO_PERP_TO_KRAKEN_SPOT,
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
        )
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
        result = compute_crypto_metrics(run_result, "L2_PROXY_ONLY")
        return CryptoValidationReport(
            candidate_id=candidate.candidate_id,
            model_id=candidate.model_id,
            asset_class="CRYPTO",
            execution_classification="L2_PROXY_ONLY",
            validation_path=ExecutionCapability.L2_PROXY_VALIDATION,
            npz_path=npz_path,
            result=result,
            notes=[f"Validated via Binance L2 proxy for {l2_sym}"],
        )

    elif path.execution_capability == ExecutionCapability.L3_VALIDATED:
        kraken_sym = CRYPTO_PERP_TO_KRAKEN_SPOT.get(perp_symbol, perp_symbol)
        npz_dir = _crypto_l3_npz_path(data_catalog_root, kraken_sym)
        npz_files = sorted(npz_dir.glob("*.npz"))
        if not npz_files:
            return CryptoValidationReport(
                candidate_id=candidate.candidate_id,
                model_id=candidate.model_id,
                asset_class="CRYPTO",
                execution_classification="NO_EXECUTION",
                validation_path=ExecutionCapability.NO_EXECUTION_VALIDATION,
                npz_path="",
                result=CryptoExecutionResult(error="No L3 NPZ files found"),
                notes=["No Kraken L3 NPZ files in expected path"],
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
        )
        run_result = run_crypto_replay(
            hbt=hbt,
            strategy=strategy,
            npz_path=npz_path,
            symbol=kraken_sym,
            tick_size=0.1,
            latency_ms=latency_ms,
            queue_model="LogProbQueueModel2",
            max_steps=max_steps,
        )
        result = compute_crypto_metrics(run_result, "L3_VALIDATED")
        return CryptoValidationReport(
            candidate_id=candidate.candidate_id,
            model_id=candidate.model_id,
            asset_class="CRYPTO",
            execution_classification="L3_VALIDATED",
            validation_path=ExecutionCapability.L3_VALIDATED,
            npz_path=npz_path,
            result=result,
            notes=[f"Validated via Kraken L3 MBO for {kraken_sym}"],
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
