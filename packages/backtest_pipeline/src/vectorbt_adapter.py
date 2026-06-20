"""VectorBT adapter — cheap hypothesis/parameter filter using vectorized backtesting.

Consumes HFT3 CandidateModel objects + data from the existing pipeline.
Produces FilterResult with promoted/rejected lists. Every promoted candidate
carries full traceable metadata (PromotedCandidate) for the promotion gate.

VectorBT is mandatory for filtering. If VectorBT is unavailable, or if a scope
requires the Rust engine and it is unavailable, the screen fails closed. Missing
data or missing signal bindings reject candidates; they are never counted as a
filtered pass.
"""
from __future__ import annotations

import copy
import hashlib
import importlib.metadata
import importlib.util
import itertools
import json
import logging
import math
import os
import subprocess
import sys
import time
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Mapping, Optional, Tuple

import numpy as np

from backtest_pipeline.src.promotion_gate import (
    PromotedCandidate,
    PromotionGate,
    RejectedCandidate,
    serialize_promoted,
)
from backtest_pipeline.src.feature_plane import (
    FEATURE_PLANE_ARTIFACT_FIELDS,
    feature_plane_validation_errors,
    build_feature_plane_payload,
    build_feature_usage_manifest,
    build_manifest_from_feature_recipes,
)
from backtest_pipeline.src.research_clock import (
    RESEARCH_CLOCK_SCHEDULED_EVENT,
    ResearchClockError,
    research_clock_validation_errors,
    validate_research_clock,
)
from backtest_pipeline.src.research_pipeline_stages import (
    STAGE_1_VECTORBT_SCREEN,
    pipeline_stage_stamp,
)
from backtest_pipeline.src.robustness_bridge import compute_robustness_evidence
from backtest_pipeline.src.surface_stability import compute_surface_stability
from research_pipeline.types import CandidateModel, ParsedHypothesis

logger = logging.getLogger(__name__)

_REPO = Path(__file__).resolve().parents[3]
_VECTORBT_RUST_SOURCE_LOCK = _REPO / "vendor" / "vectorbt" / "VENDOR.lock"

_has_vectorbt: Optional[bool] = None
_vectorbt_version: Optional[str] = None
_rust_engine_available: Optional[bool] = None
_RUST_REQUIRED_SCOPES = {
    "screen",
    "refine",
    "paid",
    "paid_compute",
    "broad",
    "broad_screen",
    "all_model",
    "all_models",
}

_VECTORBT_ENGINE_RUNTIME_PROOF: Optional[bool] = None


def _vectorbt_engine_runtime_proof() -> bool:
    """Runtime proof that VectorBT actually executed with the Rust engine.

    Source-lock detection and the presence of ``vectorbt.rust`` are static
    signals only. Paid/broad scopes require a successful rust preflight or
  trial before screening proceeds.
    """
    global _VECTORBT_ENGINE_RUNTIME_PROOF
    if _VECTORBT_ENGINE_RUNTIME_PROOF is None:
        _VECTORBT_ENGINE_RUNTIME_PROOF = False
    return _VECTORBT_ENGINE_RUNTIME_PROOF


def _set_vectorbt_engine_runtime_proof(value: bool) -> None:
    global _VECTORBT_ENGINE_RUNTIME_PROOF
    _VECTORBT_ENGINE_RUNTIME_PROOF = value


def _establish_vectorbt_rust_runtime_proof() -> bool:
    """Run a tiny Portfolio.from_signals(..., engine='rust') canary."""
    if _vectorbt_engine_runtime_proof():
        return True
    if not _detect_vectorbt_rust_engine():
        return False
    try:
        import numpy as np
        import vectorbt as vbt  # type: ignore

        close = np.linspace(100.0, 101.0, 32)
        entries = np.zeros(32, dtype=bool)
        exits = np.zeros(32, dtype=bool)
        entries[4] = True
        exits[12] = True
        pf = vbt.Portfolio.from_signals(
            close,
            entries=entries,
            exits=exits,
            init_cash=10000.0,
            freq="1min",
            engine="rust",
        )
        pf.stats()
        _set_vectorbt_engine_runtime_proof(True)
        return True
    except Exception as exc:
        print(f"Warning: VectorBT rust runtime preflight failed: {exc}", file=sys.stderr)
        return False


def _vectorbt_available() -> bool:
    global _has_vectorbt
    if _has_vectorbt is None:
        try:
            import vectorbt  # type: ignore
            _has_vectorbt = hasattr(vectorbt, "__version__")
        except ImportError:
            _has_vectorbt = False
    return _has_vectorbt


def _detect_vectorbt_version() -> str:
    global _vectorbt_version
    if _vectorbt_version is None:
        if not _vectorbt_available():
            _vectorbt_version = "unavailable"
        else:
            try:
                _vectorbt_version = importlib.metadata.version("vectorbt")
            except importlib.metadata.PackageNotFoundError:
                try:
                    import vectorbt  # type: ignore
                    _vectorbt_version = str(getattr(vectorbt, "__version__", "unknown"))
                except ImportError:
                    _vectorbt_version = "unavailable"
    return _vectorbt_version


def _detect_vectorbt_rust_engine() -> bool:
    """Detect official VectorBT Rust when repo source-lock evidence exists."""
    global _rust_engine_available
    if _rust_engine_available is None:
        rust_specs = ("vectorbt.rust", "vectorbt._rust")
        _rust_engine_available = False
        if not _vectorbt_rust_source_lock_verified():
            return _rust_engine_available
        for name in rust_specs:
            try:
                spec = importlib.util.find_spec(name)
                if (
                    spec is not None
                    and _is_official_vectorbt_rust_spec(spec)
                ):
                    _rust_engine_available = True
                    break
            except (ImportError, ModuleNotFoundError, ValueError):
                continue
        if not _rust_engine_available:
            try:
                spec = importlib.util.find_spec("vectorbt_rust")
                if spec is not None and _is_official_vectorbt_rust_extension_spec(spec):
                    _rust_engine_available = True
            except (ImportError, ModuleNotFoundError, ValueError):
                pass
    return _rust_engine_available


def _vectorbt_rust_source_lock_verified() -> bool:
    if not _VECTORBT_RUST_SOURCE_LOCK.exists():
        return False
    try:
        text = _VECTORBT_RUST_SOURCE_LOCK.read_text(encoding="utf-8").lower()
    except OSError:
        return False
    required_tokens = ("polakowo/vectorbt", "vectorbt[rust]", "source-lock", "parity")
    return all(token in text for token in required_tokens)


def _is_official_vectorbt_rust_spec(spec: Any) -> bool:
    origin = getattr(spec, "origin", None)
    if not origin:
        return False
    try:
        dist = importlib.metadata.distribution("vectorbt")
        dist_root = Path(dist.locate_file("")).resolve()
        origin_path = Path(origin).resolve()
    except (importlib.metadata.PackageNotFoundError, OSError, ValueError):
        return False
    return origin_path == dist_root or dist_root in origin_path.parents


def _is_official_vectorbt_rust_extension_spec(spec: Any) -> bool:
    origin = getattr(spec, "origin", None)
    if not origin:
        return False
    try:
        dist = importlib.metadata.distribution("vectorbt-rust")
        dist_root = Path(dist.locate_file("")).resolve()
        origin_path = Path(origin).resolve()
    except (importlib.metadata.PackageNotFoundError, OSError, ValueError):
        return False
    return origin_path == dist_root or dist_root in origin_path.parents


def _normalise_screening_scope(scope: str) -> str:
    value = str(scope or "pilot").strip().lower().replace("-", "_")
    return value or "pilot"


def _rust_required_for_scope(scope: str) -> bool:
    return _normalise_screening_scope(scope) in _RUST_REQUIRED_SCOPES


def _screening_engine_metadata(screening_scope: str = "pilot") -> Dict[str, Any]:
    scope = _normalise_screening_scope(screening_scope)
    vectorbt_available = _vectorbt_available()
    rust_available = _detect_vectorbt_rust_engine() if vectorbt_available else False
    rust_required = _rust_required_for_scope(scope)
    runtime_proof = rust_available and _vectorbt_engine_runtime_proof()
    if not vectorbt_available:
        engine = "unavailable"
        parity_status = (
            "rust_engine_required_unavailable_fail_closed"
            if rust_required
            else "vectorbt_unavailable_fail_closed"
        )
    elif rust_required and not rust_available:
        engine = "numba"
        parity_status = "rust_engine_required_unavailable_fail_closed"
    elif rust_required and not runtime_proof:
        engine = "numba"
        parity_status = "rust_runtime_proof_missing_fail_closed"
    elif rust_available and runtime_proof:
        engine = "rust"
        parity_status = "rust_runtime_proven"
    elif rust_available:
        engine = "numba"
        parity_status = "rust_available_runtime_unproven_pilot_only"
    else:
        engine = "numba"
        parity_status = "rust_unavailable_pilot_only"
    return {
        "vectorbt_available": vectorbt_available,
        "vectorbt_version": _detect_vectorbt_version(),
        "vectorbt_engine": engine,
        "engine_parity_status": parity_status,
        "rust_engine_required_for_scope": rust_required,
        "rust_engine_available": rust_available,
        "vectorbt_engine_runtime_proof": runtime_proof,
        "screening_scope": scope,
    }


DEFAULT_PARAM_GRID = {
    "signal_threshold": [0.10, 0.15, 0.20, 0.25],
    "holding_period_bars": [5, 15, 30, 60],
    "stop_loss_pct": [None, 0.5, 1.0, 2.0],
    "take_profit_pct": [None, 0.5, 1.0, 2.0],
}
_TIER_DEFAULT_MAX_TRIALS = {
    "pilot": 32,
    "screen": 256,
    "refine": 64,
}


class ParameterSpaceArtifactError(ValueError):
    """Raised when a VBT-1 parameter-space artifact fails schema validation."""


class ScreeningArtifactError(ValueError):
    """Raised when the terminal VectorBT screening artifact violates its schema."""


SCREENING_ARTIFACT_REQUIRED_FIELDS = (
    "run_id",
    "created_at_utc",
    "code_commit",
    "screening_backend",
    "vectorbt_version",
    "vectorbt_engine",
    "engine_parity_status",
    "rust_engine_required_for_scope",
    "rust_engine_available",
    "vectorbt_engine_runtime_proof",
    "license_review",
    "research_clock",
    "parameter_space_id",
    "parameter_space_hash",
    "max_trials",
    "trials_run",
    "run_budget_id",
    "max_models",
    "max_symbols",
    "max_feature_sets",
    "max_total_trials",
    "max_wall_clock_seconds",
    "max_peak_memory_mb_or_null",
    "abort_on_budget_exhaustion",
    "screening_scope",
    "candidate_ids",
    "candidate_reasons",
    "promoted_ids",
    "promoted_reasons",
    "rejected_ids",
    "rejected_reasons",
    "stop_reasons",
    "feature_set_id",
    "feature_set_hash",
    "data_manifest_hash",
    "lake_manifest_hash",
    "events_csv_hash_or_not_applicable",
    "split_scheme_id",
    "no_lookahead_signal_shift_proof",
    "fees_model_id",
    "slippage_model_id",
    "bar_construction_id",
    *FEATURE_PLANE_ARTIFACT_FIELDS,
    "screening_artifact_hash",
)
_SCREENING_ARTIFACT_NULLABLE_FIELDS = {
    "max_wall_clock_seconds",
    "max_peak_memory_mb_or_null",
    "target_event_type_or_null",
    "allowed_context_set_id_or_null",
}
SCREENING_CANDIDATE_REQUIRED_FIELDS = (
    "candidate_id",
    "model_id",
    "symbol",
    "research_clock",
    "opportunity_type_or_event_type",
    "parameter_values",
    "parameter_values_hash",
    "trials_budget_tier",
    "in_sample_metrics",
    "out_of_sample_metrics",
    "walk_forward_metrics",
    "wfc_metrics",
    "surface_stability_metrics",
    "robustness_gate_scope",
    "wfc_status",
    "dsr_status",
    "pbo_status",
    "cscv_status",
    "robustness_artifact_staleness",
    "trade_count",
    "gross_return",
    "total_fees",
    "total_slippage",
    "net_return",
    "net_pnl",
    "expectancy_per_trade",
    "profit_factor",
    "sharpe",
    "sortino",
    "max_drawdown",
    "turnover",
    "bootstrap_ci_or_not_run",
    "dsr_or_not_run",
    "pbo_or_not_run",
    "cscv_count_or_not_run",
    "fee_stress_or_not_run",
    "slippage_stress_or_not_run",
    "latency_stress_or_not_run",
    "holm_bh_or_not_run",
    "null_battery_or_not_run",
    "planted_alpha_or_not_run",
    "adversarial_or_not_run",
    "parameter_perturbation_or_not_run",
    "screening_status",
    "replay_eligibility_status",
    "rejection_reason_or_null",
)
REPLAY_ELIGIBILITY_EVIDENCE_FIELDS = (
    "wfc_status",
    "dsr_status",
    "pbo_status",
    "cscv_status",
)
REPLAY_ELIGIBILITY_NOT_RUN_FIELDS = (
    "bootstrap_ci_or_not_run",
    "dsr_or_not_run",
    "pbo_or_not_run",
    "cscv_count_or_not_run",
    "fee_stress_or_not_run",
    "slippage_stress_or_not_run",
    "latency_stress_or_not_run",
    "holm_bh_or_not_run",
    "null_battery_or_not_run",
    "planted_alpha_or_not_run",
    "adversarial_or_not_run",
    "parameter_perturbation_or_not_run",
)
_VBT2_PILOT_NOT_ELIGIBLE_REASON = (
    "vbt2_pilot_screen_only_without_real_wfc_dsr_pbo_cscv_pass_evidence"
)
_VBT2_PILOT_SCREEN_PASS_REASON = "vectorbt_screen_passed_replay_not_eligible"
SURFACE_STABILITY_FORMULA_AUTHORITY_MISSING_REASON = (
    "surface_stability_formula_authority_missing"
)
SURFACE_STABILITY_FORMULA_AUTHORITY_POINTER = (
    "docs/project/ROBUSTNESS_TESTING_SPEC.md#4-in-sample-surface-robustness"
)
SURFACE_STABILITY_FORMULA_CITATION = "docs/project/ROBUSTNESS_TESTING_SPEC.md:130-144"
SURFACE_STABILITY_REQUIRED_CHECKS = (
    "plateau_width",
    "neighbor_stability",
    "cliff_distance_from_loss_regions",
    "parameter_perturbation_sensitivity",
    "peak_vs_plateau_comparison",
    "minimum_sample_size",
)
SURFACE_STABILITY_EVIDENCE_FIELDS = (
    "plateau_score",
    "plateau_width",
    "neighbor_stability",
    "cliff_distance_from_loss_regions",
    "parameter_perturbation_sensitivity",
    "peak_vs_plateau_comparison",
    "minimum_sample_size",
)

SURFACE_STABILITY_REQUIRED_CHECKS = (
    "plateau_width",
    "neighbor_stability",
    "cliff_distance_from_loss_regions",
    "parameter_perturbation_sensitivity",
    "peak_vs_plateau_comparison",
    "minimum_sample_size",
)


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _json_primitive_screening_payload(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): _json_primitive_screening_payload(item)
            for key, item in sorted(value.items(), key=lambda item: str(item[0]))
        }
    if isinstance(value, tuple):
        return [_json_primitive_screening_payload(item) for item in value]
    if isinstance(value, list):
        return [_json_primitive_screening_payload(item) for item in value]
    if isinstance(value, np.ndarray):
        return _json_primitive_screening_payload(value.tolist())
    if isinstance(value, np.generic):
        return _json_primitive_screening_payload(value.item())
    if isinstance(value, (str, int, bool)) or value is None:
        return value
    if isinstance(value, float):
        if np.isfinite(value):
            return value
        return str(value)
    return str(value)


def _hash_payload(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _strip_screening_hash_exclusions(value: Any) -> Any:
    excluded = {"screening_artifact_hash", "created_at_utc", "timestamp_utc", "updated_at_utc"}
    if isinstance(value, Mapping):
        return {
            key: _strip_screening_hash_exclusions(item)
            for key, item in value.items()
            if key not in excluded
        }
    if isinstance(value, list):
        return [_strip_screening_hash_exclusions(item) for item in value]
    return value


def compute_screening_artifact_hash(artifact: Mapping[str, Any]) -> str:
    """Hash terminal screening artifact content excluding identity timestamps/hash."""
    payload = _json_primitive_screening_payload(artifact)
    return _hash_payload(_strip_screening_hash_exclusions(payload))


class ScreeningArtifactError(ValueError):
    """Raised when a VectorBT screening artifact fails validation."""


def validate_screening_artifact(artifact: Mapping[str, Any]) -> list[str]:
    """Public screening artifact validator for cockpit and HftBacktest handoff."""
    from backtest_pipeline.src.hftbacktest_realism import _validate_screening_artifact_hash

    return _validate_screening_artifact_hash(artifact)


def _parameter_values_hash(values: Mapping[str, Any]) -> str:
    return _hash_payload(dict(values))


def _grid_size(grid: Dict[str, List[Any]]) -> int:
    total = 1
    for vals in grid.values():
        total *= len(vals)
    return total


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _json_primitive_screening_payload(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): _json_primitive_screening_payload(item)
            for key, item in sorted(value.items(), key=lambda item: str(item[0]))
        }
    if isinstance(value, tuple):
        return [_json_primitive_screening_payload(item) for item in value]
    if isinstance(value, list):
        return [_json_primitive_screening_payload(item) for item in value]
    if isinstance(value, np.ndarray):
        return _json_primitive_screening_payload(value.tolist())
    if isinstance(value, np.generic):
        return _json_primitive_screening_payload(value.item())
    if isinstance(value, (str, int, bool)) or value is None:
        return value
    if isinstance(value, float):
        if np.isfinite(value):
            return value
        return str(value)
    return str(value)


def _hash_payload(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _strip_screening_hash_exclusions(value: Any) -> Any:
    excluded = {"screening_artifact_hash", "created_at_utc", "timestamp_utc", "updated_at_utc"}
    if isinstance(value, Mapping):
        return {
            key: _strip_screening_hash_exclusions(item)
            for key, item in value.items()
            if key not in excluded
        }
    if isinstance(value, list):
        return [_strip_screening_hash_exclusions(item) for item in value]
    return value


def compute_screening_artifact_hash(artifact: Mapping[str, Any]) -> str:
    """Hash terminal screening artifact content excluding identity timestamps/hash."""
    payload = _json_primitive_screening_payload(artifact)
    return _hash_payload(_strip_screening_hash_exclusions(payload))


def persist_screening_artifact(artifact: Mapping[str, Any], path: Path) -> Path:
    """Validate and persist the terminal VectorBT handoff artifact."""
    payload = _json_primitive_screening_payload(artifact)
    payload["screening_artifact_hash"] = compute_screening_artifact_hash(payload)
    validate_screening_artifact(payload)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def _has_bar_timestamp_column(ohlcv: np.ndarray) -> bool:
    if ohlcv.ndim != 2 or ohlcv.shape[1] < 6 or len(ohlcv) == 0:
        return False
    candidate = np.asarray(ohlcv[:, 0], dtype=float)
    finite = candidate[np.isfinite(candidate)]
    if len(finite) == 0:
        return False
    if float(np.nanmedian(finite)) < 1e8:
        return False
    return bool(np.all(np.diff(finite) >= 0))


def _ohlcv_column_indices(ohlcv: np.ndarray) -> Dict[str, int]:
    if _has_bar_timestamp_column(ohlcv):
        return {"open": 1, "high": 2, "low": 3, "close": 4, "volume": 5}
    return {"open": 0, "high": 1, "low": 2, "close": 3, "volume": 4}


def _ohlcv_column(ohlcv: np.ndarray, name: str) -> np.ndarray:
    return ohlcv[:, _ohlcv_column_indices(ohlcv)[name]]


def _bar_timestamp_ns(ohlcv: np.ndarray, index: int) -> int:
    if _has_bar_timestamp_column(ohlcv):
        raw = float(ohlcv[index, 0])
        return int(raw if raw >= 1e12 else raw * 1_000_000_000)
    return int(index * 60_000_000_000)


def _shift_signal_to_executable_bar(signal: np.ndarray) -> np.ndarray:
    shifted = np.zeros_like(signal, dtype=float)
    if len(signal) > 1:
        shifted[1:] = signal[:-1]
    return shifted


def _apply_holding_period_exit(entries: np.ndarray, exits: np.ndarray, holding_period_bars: int) -> np.ndarray:
    if holding_period_bars <= 0:
        return exits
    adjusted = np.array(exits, dtype=float, copy=True)
    for entry_idx in np.where(entries > 0)[0]:
        exit_idx = int(entry_idx) + int(holding_period_bars)
        if exit_idx < len(adjusted):
            adjusted[exit_idx] = -1.0
    return adjusted


def expand_parameter_grid(grid: Mapping[str, List[Any]]) -> List[Dict[str, Any]]:
    """Expand a finite parameter grid with deterministic parameter-name ordering."""
    if not grid:
        raise ParameterSpaceArtifactError("parameter grid must not be empty")
    keys = sorted(grid.keys())
    for key in keys:
        values = grid[key]
        if not isinstance(values, list) or not values:
            raise ParameterSpaceArtifactError(f"parameter {key!r} must have finite candidate values")
    return [dict(zip(keys, values)) for values in itertools.product(*[grid[k] for k in keys])]


def _parameter_values_hash(values: Mapping[str, Any]) -> str:
    return _hash_payload(dict(values))


def _normalise_parameter_definitions(
    param_grid: Mapping[str, List[Any]],
    parameter_definitions: Mapping[str, Mapping[str, Any]],
) -> List[Dict[str, Any]]:
    parameters: List[Dict[str, Any]] = []
    for name in sorted(param_grid.keys()):
        definition = dict(parameter_definitions.get(name, {}))
        values = list(param_grid[name])
        parameter = {
            "parameter_name": name,
            "parameter_type": definition.get("parameter_type", "categorical"),
            "unit": definition.get("unit"),
            "lower_bound": definition.get("lower_bound"),
            "upper_bound": definition.get("upper_bound"),
            "step_or_candidate_values": definition.get("step_or_candidate_values", values),
            "default_value": definition.get("default_value", values[0] if values else None),
            "range_reason": definition.get("range_reason"),
            "literature_or_ontology_citation": definition.get("literature_or_ontology_citation"),
        }
        parameters.append(parameter)
    return parameters


def _parameter_space_hash_payload(artifact: Mapping[str, Any]) -> Dict[str, Any]:
    return {
        key: copy.deepcopy(value)
        for key, value in artifact.items()
        if key not in {"parameter_space_id", "parameter_space_hash", "created_at_utc"}
    }


def compute_parameter_space_hash(artifact: Mapping[str, Any]) -> str:
    """Hash VBT-1 parameter-space content, excluding identity/time fields."""
    return _hash_payload(_parameter_space_hash_payload(artifact))


def build_parameter_space_artifact(
    *,
    param_grid: Mapping[str, List[Any]],
    parameter_definitions: Mapping[str, Mapping[str, Any]],
    model_id: str,
    feature_set_id: str,
    research_clock: str,
    symbol_universe: List[str],
    data_manifest_hash: str,
    split_scheme_id: str,
    max_trials: int,
    parameter_space_id: Optional[str] = None,
    created_at_utc: Optional[str] = None,
) -> Dict[str, Any]:
    """Build and validate a deterministic VBT-1 parameter-space artifact."""
    candidates = expand_parameter_grid(param_grid)
    try:
        parsed_max_trials = _parse_positive_int(max_trials, "max_trials")
    except ValueError as exc:
        raise ParameterSpaceArtifactError(str(exc)) from exc
    if len(candidates) > parsed_max_trials:
        raise ParameterSpaceArtifactError("parameter grid exceeds max_trials")

    try:
        canonical_research_clock = validate_research_clock(
            research_clock,
            context="parameter_space_artifact.research_clock",
        )
    except ResearchClockError as exc:
        raise ParameterSpaceArtifactError(str(exc)) from exc

    artifact: Dict[str, Any] = {
        "parameter_space_id": parameter_space_id or "",
        "parameter_space_hash": "",
        "model_id": model_id,
        "feature_set_id": feature_set_id,
        "research_clock": canonical_research_clock,
        "symbol_universe": list(symbol_universe),
        "data_manifest_hash": data_manifest_hash,
        "split_scheme_id": split_scheme_id,
        "parameters": _normalise_parameter_definitions(param_grid, parameter_definitions),
        "max_trials": parsed_max_trials,
        "forbidden_post_hoc_change": True,
        "created_at_utc": created_at_utc or datetime.now(timezone.utc).isoformat(),
        "candidates": [
            {
                "candidate_index": index,
                "parameter_values": values,
                "parameter_values_hash": _parameter_values_hash(values),
            }
            for index, values in enumerate(candidates)
        ],
    }
    artifact["parameter_space_hash"] = compute_parameter_space_hash(artifact)
    if not artifact["parameter_space_id"]:
        artifact["parameter_space_id"] = f"vbt_ps_{artifact['parameter_space_hash'][:16]}"
    validate_parameter_space_artifact(artifact)
    return artifact


def validate_parameter_space_artifact(artifact: Mapping[str, Any]) -> None:
    """Validate the bounded VBT-1 parameter-space artifact schema and hash."""
    required_top_level = (
        "parameter_space_id",
        "parameter_space_hash",
        "model_id",
        "feature_set_id",
        "research_clock",
        "symbol_universe",
        "data_manifest_hash",
        "split_scheme_id",
        "parameters",
        "max_trials",
        "forbidden_post_hoc_change",
        "created_at_utc",
        "candidates",
    )
    for field_name in required_top_level:
        if field_name not in artifact or artifact[field_name] in ("", None):
            raise ParameterSpaceArtifactError(f"missing required field: {field_name}")
    for clock_error in research_clock_validation_errors(
        artifact.get("research_clock"),
        context="parameter_space_artifact.research_clock",
    ):
        raise ParameterSpaceArtifactError(clock_error)
    if artifact["forbidden_post_hoc_change"] is not True:
        raise ParameterSpaceArtifactError("forbidden_post_hoc_change must be true")

    parameters = artifact["parameters"]
    if not isinstance(parameters, list) or not parameters:
        raise ParameterSpaceArtifactError("parameters must be a non-empty list")
    required_parameter_fields = (
        "parameter_name",
        "parameter_type",
        "unit",
        "lower_bound",
        "upper_bound",
        "step_or_candidate_values",
        "default_value",
        "range_reason",
        "literature_or_ontology_citation",
    )
    for parameter in parameters:
        for field_name in required_parameter_fields:
            if field_name not in parameter or parameter[field_name] in ("", None, []):
                raise ParameterSpaceArtifactError(
                    f"parameter {parameter.get('parameter_name', '<unknown>')} missing {field_name}"
                )

    candidates = artifact["candidates"]
    if not isinstance(candidates, list) or not candidates:
        raise ParameterSpaceArtifactError("candidates must be a non-empty list")
    try:
        max_trials = _parse_positive_int(artifact["max_trials"], "max_trials")
    except ValueError as exc:
        raise ParameterSpaceArtifactError(str(exc)) from exc
    if len(candidates) > max_trials:
        raise ParameterSpaceArtifactError("candidate count exceeds max_trials")
    for candidate in candidates:
        values = candidate.get("parameter_values")
        expected_hash = _parameter_values_hash(values)
        if candidate.get("parameter_values_hash") != expected_hash:
            raise ParameterSpaceArtifactError("candidate parameter_values_hash mismatch")

    expected_space_hash = compute_parameter_space_hash(artifact)
    if artifact["parameter_space_hash"] != expected_space_hash:
        raise ParameterSpaceArtifactError("parameter_space_hash mismatch")


def _canonical_research_clock_label(value: str) -> str:
    """Return canonical enum label when valid; otherwise echo raw value for later fail-closed."""
    try:
        return validate_research_clock(value)
    except ResearchClockError:
        return str(value)


def _screening_not_run(reason: str) -> Dict[str, str]:
    return {"status": "not_run", "reason": reason}


def _surface_stability_formula_missing() -> Dict[str, Any]:
    return {
        "status": "not_run",
        "reason": SURFACE_STABILITY_FORMULA_AUTHORITY_MISSING_REASON,
        "authority": SURFACE_STABILITY_FORMULA_AUTHORITY_POINTER,
        "formula_authority_status": "missing",
        "literature_or_ontology_citation": SURFACE_STABILITY_FORMULA_CITATION,
        "required_checks": list(SURFACE_STABILITY_REQUIRED_CHECKS),
        "failure_semantics": "SURFACE_STABILITY_FORMULA_MISSING",
    }


def _is_surface_stability_formula_missing(value: Mapping[str, Any]) -> bool:
    return (
        _screening_status_text(value) == "not_run"
        and value.get("reason") == SURFACE_STABILITY_FORMULA_AUTHORITY_MISSING_REASON
        and value.get("formula_authority_status") == "missing"
        and value.get("failure_semantics") == "SURFACE_STABILITY_FORMULA_MISSING"
    )


def _is_surface_stability_defined(value: Mapping[str, Any]) -> bool:
    if _screening_status_text(value) not in {"pass", "fail"}:
        return False
    if _screening_status_text(value.get("formula_authority_status")) not in {"defined", "pass"}:
        return False
    required_checks = value.get("required_checks")
    if not isinstance(required_checks, list):
        return False
    if set(SURFACE_STABILITY_REQUIRED_CHECKS) - {str(item) for item in required_checks}:
        return False
    for field_name in SURFACE_STABILITY_EVIDENCE_FIELDS:
        if field_name not in value or _is_screening_not_run(value.get(field_name)):
            return False
    return True


def _screening_field_missing(value: Any) -> bool:
    return value is None or value == ""


def _screening_status_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, Mapping):
        return str(value.get("status", "")).strip().lower()
    return str(value).strip().lower()


def _is_screening_not_run(value: Any) -> bool:
    if value is None or value == "":
        return True
    if isinstance(value, str):
        return value.strip().lower().startswith("not_run")
    if isinstance(value, Mapping):
        status = _screening_status_text(value)
        return value.get("not_run") is True or status.startswith("not_run")
    return False


# Public aliases for generation_gate_producers (avoid importing underscore helpers).
is_screening_not_run = _is_screening_not_run
is_surface_stability_defined = _is_surface_stability_defined
screening_status_text = _screening_status_text


def _external_robustness_evidence(metrics: Mapping[str, Any]) -> Optional[Dict[str, Any]]:
    for field_name in ("robustness_evidence", "robustness_artifact"):
        value = metrics.get(field_name)
        if isinstance(value, Mapping):
            return dict(value)
    return None


def _mapping_status_pass(value: Any) -> bool:
    return isinstance(value, Mapping) and _screening_status_text(value) == "pass"


def _numeric_mapping_value(mapping: Mapping[str, Any], names: Tuple[str, ...]) -> Optional[float]:
    for name in names:
        if name not in mapping:
            continue
        value = mapping.get(name)
        if isinstance(value, bool):
            continue
        if isinstance(value, (int, float, np.integer, np.floating)):
            value_f = float(value)
            if math.isfinite(value_f):
                return value_f
    return None


def _robustness_pass_map_errors(field_name: str, value: Any) -> List[str]:
    if not isinstance(value, Mapping):
        return [f"{field_name}_malformed"]
    if _screening_status_text(value.get("status")) != "pass":
        return [f"{field_name}_not_pass"]

    errors: List[str] = []
    if field_name == "bootstrap_ci_or_not_run":
        lower = _numeric_mapping_value(value, ("ci_lower", "ci_lo_95", "lower", "ci_low"))
        upper = _numeric_mapping_value(value, ("ci_upper", "ci_hi_95", "upper", "ci_high"))
        if lower is None:
            errors.append(f"{field_name}_missing:lower_bound")
        if upper is None:
            errors.append(f"{field_name}_missing:upper_bound")
        if lower is not None and upper is not None and lower > upper:
            errors.append(f"{field_name}_bounds_inverted")
        if lower is not None and lower <= 0.0:
            errors.append(f"{field_name}_lower_bound_not_positive")
    elif field_name == "dsr_or_not_run":
        if "dsr_pass" not in value:
            errors.append(f"{field_name}_missing:dsr_pass")
        elif value.get("dsr_pass") is not True:
            errors.append(f"{field_name}_not_pass")
        dsr_cdf = _numeric_mapping_value(value, ("dsr_cdf", "deflated_sharpe_ratio_cdf"))
        if dsr_cdf is None:
            errors.append(f"{field_name}_missing:dsr_cdf")
        elif not 0.0 <= dsr_cdf <= 1.0:
            errors.append(f"{field_name}_dsr_cdf_out_of_range")
        elif dsr_cdf < 0.95:
            errors.append(f"{field_name}_dsr_cdf_below_0_95")
    elif field_name == "pbo_or_not_run":
        if "pbo_pass" not in value:
            errors.append(f"{field_name}_missing:pbo_pass")
        elif value.get("pbo_pass") is not True:
            errors.append(f"{field_name}_not_pass")
        pbo = _numeric_mapping_value(
            value,
            ("pbo", "probability_of_backtest_overfitting", "pbo_probability"),
        )
        maximum_pbo = _numeric_mapping_value(value, ("maximum_pbo", "pbo_threshold", "threshold"))
        if pbo is None:
            errors.append(f"{field_name}_missing:pbo")
        if maximum_pbo is None:
            errors.append(f"{field_name}_missing:maximum_pbo")
        if pbo is not None and maximum_pbo is not None and pbo > maximum_pbo:
            errors.append(f"{field_name}_above_threshold")
    elif field_name == "cscv_count_or_not_run":
        partitions = _numeric_mapping_value(value, ("n_partitions", "partitions", "partition_count"))
        configs = _numeric_mapping_value(value, ("n_configs", "configs", "config_count"))
        if partitions is None or partitions <= 0:
            errors.append(f"{field_name}_partition_count_missing_or_not_positive")
        if configs is None:
            errors.append(f"{field_name}_missing:n_configs")
        elif configs < 2:
            errors.append(f"{field_name}_config_count_below_2")
    return errors


def _robustness_evidence_is_replay_eligible(evidence: Mapping[str, Any]) -> bool:
    surface = evidence.get("surface_stability_metrics")
    if not isinstance(surface, Mapping) or not _is_surface_stability_defined(surface):
        return False
    if _screening_status_text(surface) != "pass":
        return False
    if _screening_status_text(evidence.get("robustness_artifact_staleness")) != "fresh":
        return False
    for field_name in REPLAY_ELIGIBILITY_EVIDENCE_FIELDS:
        if _screening_status_text(evidence.get(field_name)) != "pass":
            return False
    for field_name in REPLAY_ELIGIBILITY_NOT_RUN_FIELDS:
        if _robustness_pass_map_errors(field_name, evidence.get(field_name)):
            return False
    if not isinstance(evidence.get("walk_forward_metrics"), Mapping):
        return False
    if not isinstance(evidence.get("wfc_metrics"), Mapping):
        return False
    return True


def _apply_external_robustness_evidence(
    row: Dict[str, Any],
    evidence: Optional[Mapping[str, Any]],
) -> None:
    if not evidence:
        return
    row["external_robustness_evidence"] = copy.deepcopy(evidence)
    row["external_robustness_evidence_status"] = (
        "preserved_not_replay_eligible_until_hftbacktest_native_replay_milestone"
        if _robustness_evidence_is_replay_eligible(evidence)
        else "preserved_not_replay_eligible_incomplete_or_stale"
    )


def _parse_non_negative_int(value: Any, field_name: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{field_name} must be a non-negative integer")
    if isinstance(value, (int, np.integer)):
        parsed = int(value)
    elif isinstance(value, str) and value.strip().isdigit():
        parsed = int(value.strip())
    else:
        raise ValueError(f"{field_name} must be a non-negative integer")
    if parsed < 0:
        raise ValueError(f"{field_name} must be a non-negative integer")
    return parsed


def _parse_positive_int(value: Any, field_name: str) -> int:
    parsed = _parse_non_negative_int(value, field_name)
    if parsed <= 0:
        raise ValueError(f"{field_name} must be a positive integer")
    return parsed


def _parse_optional_non_negative_int(value: Any, field_name: str) -> Optional[int]:
    if value in (None, ""):
        return None
    return _parse_non_negative_int(value, field_name)


def _parse_abort_on_budget_exhaustion(value: Any) -> bool:
    if value is True:
        return True
    raise ValueError("abort_on_budget_exhaustion must be true")


def _candidate_symbol_id(candidate: CandidateModel) -> str:
    return str(candidate.metadata.get("symbol") or "unknown")


def _candidate_has_explicit_symbol(candidate: CandidateModel) -> bool:
    return bool(candidate.metadata.get("symbol"))


def _candidate_feature_set_id(candidate: CandidateModel) -> str:
    return str(candidate.metadata.get("feature_set_id") or "fs_v1_pilot_unknown")


def _candidate_has_explicit_feature_set(candidate: CandidateModel) -> bool:
    return bool(candidate.metadata.get("feature_set_id"))


def _unique_candidate_count(candidates: List[CandidateModel], key_fn: Callable[[CandidateModel], str]) -> int:
    return len({key_fn(candidate) for candidate in candidates})


def _candidate_metric_sources(candidate: PromotedCandidate) -> Dict[str, Any]:
    metrics = dict(candidate.vectorbt_results or {})
    vbt_stats = metrics.get("vbt_stats")
    if isinstance(vbt_stats, Mapping):
        for key, value in vbt_stats.items():
            metrics.setdefault(str(key), value)
    return metrics


def _metric_value(metrics: Mapping[str, Any], *names: str) -> Any:
    for name in names:
        if name in metrics and metrics[name] not in (None, ""):
            return metrics[name]
    return None


def _metric_or_not_run(metrics: Mapping[str, Any], reason: str, *names: str) -> Any:
    value = _metric_value(metrics, *names)
    if value is None:
        return _screening_not_run(reason)
    return value


def _return_fraction(metrics: Mapping[str, Any]) -> Any:
    value = _metric_value(metrics, "net_return", "gross_return")
    if value is not None:
        return value
    pct = _metric_value(metrics, "net_return_pct", "Total Return [%]", "Total Return")
    if pct is None:
        return _screening_not_run("return_metric_not_run_in_vbt2_pilot")
    try:
        return round(float(pct) / 100.0, 8)
    except (TypeError, ValueError):
        return pct


def _net_pnl(metrics: Mapping[str, Any], net_return: Any) -> Any:
    value = _metric_value(metrics, "net_pnl", "Total Profit", "total_profit")
    if value is not None:
        return value
    if isinstance(net_return, (int, float, np.integer, np.floating)):
        return round(float(net_return) * 10000.0, 8)
    return _screening_not_run("cash_pnl_not_run_in_vbt2_pilot")


def _not_run_reason_for_candidate(candidate_id: str) -> str:
    return f"{_VBT2_PILOT_NOT_ELIGIBLE_REASON}:{candidate_id}"


def _compute_surface_stability_for_candidate(
    candidate: PromotedCandidate,
) -> Dict[str, Any]:
    """Compute surface-stability metrics for a promoted candidate.

    Looks for a ``parameter_surface`` dict (mapping parameter-value tuples to
    per-cell metric dicts) inside the candidate's ``in_sample_results`` or
    ``vectorbt_results``.  When present and non-empty, the §4 producer
    (:func:`compute_surface_stability`) is invoked and its defined output is
    returned.  When absent, the fail-closed sentinel is returned so the
    screening-artifact validator recognises the "formula missing" state.

    The ``parameter_surface`` grid may optionally specify producer overrides
    (``performance_metric``, ``tolerance``, ``loss_threshold``,
    ``min_sample_size``) under the ``surface_stability_config`` key alongside
    the grid itself.
    """
    grid = None
    for source in (
        candidate.in_sample_results,
        candidate.vectorbt_results,
    ):
        if not isinstance(source, Mapping):
            continue
        surface = source.get("parameter_surface")
        if isinstance(surface, Mapping) and len(surface) > 0:
            grid = surface
            break
    if grid is None:
        return _surface_stability_formula_missing()

    config: Mapping[str, Any] = {}
    for source in (
        candidate.in_sample_results,
        candidate.vectorbt_results,
    ):
        if isinstance(source, Mapping):
            cfg = source.get("surface_stability_config")
            if isinstance(cfg, Mapping):
                config = cfg
                break

    try:
        metrics = compute_surface_stability(
            grid,
            performance_metric=config.get("performance_metric", "net_return"),
            tolerance=config.get("tolerance", 0.1),
            loss_threshold=config.get("loss_threshold", 0.0),
            min_sample_size=config.get("min_sample_size", 30),
        )
    except (ValueError, TypeError):
        # Malformed / unusable grid → fail closed with the sentinel so the
        # screening-artifact validator does not raise on an undefined shape.
        logger.warning(
            "surface stability computation failed for candidate %s; "
            "falling back to formula-missing sentinel",
            candidate.candidate_id,
        )
        return _surface_stability_formula_missing()
    return metrics


def _normalise_promoted_screening_row(
    candidate: PromotedCandidate,
    result: "FilterResult",
) -> Dict[str, Any]:
    metrics = _candidate_metric_sources(candidate)
    robustness_evidence = _external_robustness_evidence(metrics)
    parameter_values = dict(candidate.param_values or {})
    net_return = _return_fraction(metrics)
    reason = _not_run_reason_for_candidate(candidate.candidate_id)
    row = dict(candidate.to_dict())
    surface_stability_metrics = _compute_surface_stability_for_candidate(candidate)
    if "param_stability_score" in metrics:
        surface_stability_metrics["observed_param_stability_score_or_not_run"] = metrics[
            "param_stability_score"
        ]
    if "slippage_sensitivity" in metrics:
        surface_stability_metrics["observed_slippage_sensitivity_or_not_run"] = metrics[
            "slippage_sensitivity"
        ]

    # VBT-4: compute robustness evidence via the bridge when raw input is supplied.
    robustness_input = metrics.get("robustness_input")
    bridge_evidence: Optional[Dict[str, Any]] = None
    if isinstance(robustness_input, Mapping) and robustness_input:
        try:
            bridge_evidence = compute_robustness_evidence(
                dict(robustness_input), candidate_id=candidate.candidate_id
            )
        except Exception:  # noqa: BLE001 — fail-closed
            logger.warning(
                "robustness bridge computation failed for candidate %s",
                candidate.candidate_id,
            )
            bridge_evidence = None

    # Default robustness fields (fail-closed not_run sentinels).
    default_walk_forward_metrics: Dict[str, Any] = {
        "status": "pilot_summary_only",
        "wf_consistency": metrics.get("wf_consistency"),
        "oos_expectancy": metrics.get("oos_expectancy"),
        "reason": "full_walk_forward_fold_matrix_not_run_in_vbt2_pilot",
    }
    default_wfc_metrics = _screening_not_run(reason)
    default_wfc_status = "not_run"
    default_dsr_status = "not_run"
    default_pbo_status = "not_run"
    default_cscv_status = "not_run"
    default_staleness: Any = _screening_not_run(reason)
    default_bootstrap = _screening_not_run(reason)
    default_dsr = _screening_not_run(reason)
    default_pbo = _screening_not_run(reason)
    default_cscv_count = _screening_not_run(reason)
    # Per Codex P2-7: default not-run for the 8 new §10 evidence maps.
    default_fee_stress = _screening_not_run(reason)
    default_slippage_stress = _screening_not_run(reason)
    default_latency_stress = _screening_not_run(reason)
    default_holm_bh = _screening_not_run(reason)
    default_null_battery = _screening_not_run(reason)
    default_planted_alpha = _screening_not_run(reason)
    default_adversarial = _screening_not_run(reason)
    default_param_perturb = _screening_not_run(reason)

    # When the bridge produced evidence, use its values.
    if bridge_evidence:
        walk_forward_metrics_bridge = bridge_evidence.get("walk_forward_metrics")
        if isinstance(walk_forward_metrics_bridge, Mapping) and walk_forward_metrics_bridge:
            default_walk_forward_metrics = dict(walk_forward_metrics_bridge)
        wfc_metrics_bridge = bridge_evidence.get("wfc_metrics")
        if isinstance(wfc_metrics_bridge, Mapping):
            default_wfc_metrics = dict(wfc_metrics_bridge)
        default_wfc_status = str(bridge_evidence.get("wfc_status", "not_run"))
        default_dsr_status = str(bridge_evidence.get("dsr_status", "not_run"))
        default_pbo_status = str(bridge_evidence.get("pbo_status", "not_run"))
        default_cscv_status = str(bridge_evidence.get("cscv_status", "not_run"))
        default_staleness = bridge_evidence.get("robustness_artifact_staleness", "stale")
        default_bootstrap = bridge_evidence.get("bootstrap_ci_or_not_run", default_bootstrap)
        default_dsr = bridge_evidence.get("dsr_or_not_run", default_dsr)
        default_pbo = bridge_evidence.get("pbo_or_not_run", default_pbo)
        default_cscv_count = bridge_evidence.get(
            "cscv_count_or_not_run", default_cscv_count
        )
        # Per Codex P2-7: surface the 8 new §10 robustness evidence maps
        # in the promoted screening row so downstream consumers receive
        # fee/slippage/latency/Holm/null/planted/adversarial/parameter evidence.
        default_fee_stress = bridge_evidence.get("fee_stress_or_not_run", _screening_not_run(reason))
        default_slippage_stress = bridge_evidence.get("slippage_stress_or_not_run", _screening_not_run(reason))
        default_latency_stress = bridge_evidence.get("latency_stress_or_not_run", _screening_not_run(reason))
        default_holm_bh = bridge_evidence.get("holm_bh_or_not_run", _screening_not_run(reason))
        default_null_battery = bridge_evidence.get("null_battery_or_not_run", _screening_not_run(reason))
        default_planted_alpha = bridge_evidence.get("planted_alpha_or_not_run", _screening_not_run(reason))
        default_adversarial = bridge_evidence.get("adversarial_or_not_run", _screening_not_run(reason))
        default_param_perturb = bridge_evidence.get("parameter_perturbation_or_not_run", _screening_not_run(reason))

    row.update({
        "candidate_id": candidate.candidate_id,
        "base_candidate_id": metrics.get("base_candidate_id", candidate.candidate_id),
        "base_candidate_metadata": metrics.get("base_candidate_metadata", {}),
        "model_id": candidate.hypothesis_id,
        "symbol": candidate.symbol or "unknown",
        "research_clock": _canonical_research_clock_label(result.research_clock),
        "opportunity_type_or_event_type": metrics.get(
            "opportunity_type_or_event_type",
            "not_recorded_in_vbt2_pilot",
        ),
        "parameter_values": parameter_values,
        "parameter_values_hash": _parameter_values_hash(parameter_values),
        "feature_recipe_hash": metrics.get("feature_recipe_hash")
        or (metrics.get("base_candidate_metadata") or {}).get("feature_recipe_hash"),
        "trials_budget_tier": result.screening_scope,
        "in_sample_metrics": (
            dict(candidate.in_sample_results)
            if candidate.in_sample_results
            else _screening_not_run(reason)
        ),
        "out_of_sample_metrics": (
            dict(candidate.out_of_sample_results)
            if candidate.out_of_sample_results
            else _screening_not_run(reason)
        ),
        "walk_forward_metrics": default_walk_forward_metrics,
        "wfc_metrics": default_wfc_metrics,
        "surface_stability_metrics": surface_stability_metrics,
        "robustness_gate_scope": "pilot",
        "wfc_status": default_wfc_status,
        "dsr_status": default_dsr_status,
        "pbo_status": default_pbo_status,
        "cscv_status": default_cscv_status,
        "robustness_artifact_staleness": default_staleness,
        "trade_count": _metric_or_not_run(metrics, reason, "num_trades", "trade_count"),
        "gross_return": net_return,
        "total_fees": _metric_or_not_run(metrics, reason, "total_fees"),
        "total_slippage": _metric_or_not_run(metrics, reason, "total_slippage"),
        "net_return": net_return,
        "net_pnl": _net_pnl(metrics, net_return),
        "expectancy_per_trade": _metric_or_not_run(metrics, reason, "expectancy", "oos_expectancy"),
        "profit_factor": _metric_or_not_run(metrics, reason, "profit_factor", "Profit Factor"),
        "sharpe": _metric_or_not_run(metrics, reason, "sharpe", "Sharpe Ratio"),
        "sortino": _metric_or_not_run(metrics, reason, "sortino", "Sortino Ratio"),
        "max_drawdown": _metric_or_not_run(metrics, reason, "max_drawdown_pct", "Max Drawdown [%]"),
        "turnover": _metric_or_not_run(metrics, reason, "turnover_mean_pct", "turnover"),
        "bootstrap_ci_or_not_run": default_bootstrap,
        "dsr_or_not_run": default_dsr,
        "pbo_or_not_run": default_pbo,
        "cscv_count_or_not_run": default_cscv_count,
        "fee_stress_or_not_run": default_fee_stress,
        "slippage_stress_or_not_run": default_slippage_stress,
        "latency_stress_or_not_run": default_latency_stress,
        "holm_bh_or_not_run": default_holm_bh,
        "null_battery_or_not_run": default_null_battery,
        "planted_alpha_or_not_run": default_planted_alpha,
        "adversarial_or_not_run": default_adversarial,
        "parameter_perturbation_or_not_run": default_param_perturb,
        "screening_status": "pass",
        "replay_eligibility_status": "not_eligible",
        "rejection_reason_or_null": reason,
    })

    # VBT-4: determine replay eligibility from bridge evidence.
    # Eligible when: screening_status == "pass", all four robustness statuses
    # are "pass", staleness == "fresh", and surface_stability status == "pass".
    if bridge_evidence:
        surface_status = _screening_status_text(surface_stability_metrics)
        all_robustness_pass = (
            default_wfc_status == "pass"
            and default_dsr_status == "pass"
            and default_pbo_status == "pass"
            and default_cscv_status == "pass"
        )
        staleness_text = _screening_status_text(default_staleness)
        if (
            row["screening_status"] == "pass"
            and all_robustness_pass
            and staleness_text == "fresh"
            and surface_status == "pass"
        ):
            row["replay_eligibility_status"] = "eligible"
            row["rejection_reason_or_null"] = None

    _apply_external_robustness_evidence(row, robustness_evidence)
    return row


def _normalise_rejected_screening_row(
    candidate: RejectedCandidate,
    result: "FilterResult",
) -> Dict[str, Any]:
    metrics = dict(candidate.metric_values or {})
    metrics.update(dict(candidate.vectorbt_results or {}))
    parameter_values = dict(
        metrics.get("parameter_values")
        or metrics.get("param_values")
        or metrics.get("strategy_params")
        or {}
    )
    reason = candidate.reject_reason or "rejected_by_vectorbt_screen"
    not_run_reason = f"candidate_rejected_before_replay:{reason}"
    row = dict(candidate.to_dict())
    row.update({
        "candidate_id": candidate.candidate_id,
        "base_candidate_id": metrics.get("base_candidate_id", candidate.candidate_id),
        "base_candidate_metadata": metrics.get("base_candidate_metadata", {}),
        "model_id": candidate.hypothesis_id,
        "symbol": str(metrics.get("symbol") or "unknown"),
        "research_clock": _canonical_research_clock_label(result.research_clock),
        "opportunity_type_or_event_type": metrics.get(
            "opportunity_type_or_event_type",
            "not_recorded_candidate_rejected",
        ),
        "parameter_values": parameter_values,
        "parameter_values_hash": _parameter_values_hash(parameter_values),
        "trials_budget_tier": result.screening_scope,
        "in_sample_metrics": metrics.get("in_sample_metrics") or _screening_not_run(not_run_reason),
        "out_of_sample_metrics": metrics.get("out_of_sample_metrics") or _screening_not_run(not_run_reason),
        "walk_forward_metrics": metrics.get("walk_forward_metrics") or _screening_not_run(not_run_reason),
        "wfc_metrics": metrics.get("wfc_metrics") or _screening_not_run(not_run_reason),
        "surface_stability_metrics": _surface_stability_formula_missing(),
        "robustness_gate_scope": "pilot",
        "wfc_status": "not_run",
        "dsr_status": "not_run",
        "pbo_status": "not_run",
        "cscv_status": "not_run",
        "robustness_artifact_staleness": _screening_not_run(not_run_reason),
        "trade_count": _metric_or_not_run(metrics, not_run_reason, "num_trades", "trade_count"),
        "gross_return": _metric_or_not_run(metrics, not_run_reason, "gross_return", "net_return_pct"),
        "total_fees": _metric_or_not_run(metrics, not_run_reason, "total_fees"),
        "total_slippage": _metric_or_not_run(metrics, not_run_reason, "total_slippage"),
        "net_return": _metric_or_not_run(metrics, not_run_reason, "net_return", "net_return_pct"),
        "net_pnl": _metric_or_not_run(metrics, not_run_reason, "net_pnl"),
        "expectancy_per_trade": _metric_or_not_run(metrics, not_run_reason, "expectancy", "oos_expectancy"),
        "profit_factor": _metric_or_not_run(metrics, not_run_reason, "profit_factor"),
        "sharpe": _metric_or_not_run(metrics, not_run_reason, "sharpe"),
        "sortino": _metric_or_not_run(metrics, not_run_reason, "sortino"),
        "max_drawdown": _metric_or_not_run(metrics, not_run_reason, "max_drawdown_pct", "max_drawdown"),
        "turnover": _metric_or_not_run(metrics, not_run_reason, "turnover_mean_pct", "turnover"),
        "bootstrap_ci_or_not_run": _screening_not_run(not_run_reason),
        "dsr_or_not_run": _screening_not_run(not_run_reason),
        "pbo_or_not_run": _screening_not_run(not_run_reason),
        "cscv_count_or_not_run": _screening_not_run(not_run_reason),
        "fee_stress_or_not_run": _screening_not_run(not_run_reason),
        "slippage_stress_or_not_run": _screening_not_run(not_run_reason),
        "latency_stress_or_not_run": _screening_not_run(not_run_reason),
        "holm_bh_or_not_run": _screening_not_run(not_run_reason),
        "null_battery_or_not_run": _screening_not_run(not_run_reason),
        "planted_alpha_or_not_run": _screening_not_run(not_run_reason),
        "adversarial_or_not_run": _screening_not_run(not_run_reason),
        "parameter_perturbation_or_not_run": _screening_not_run(not_run_reason),
        "screening_status": "rejected",
        "replay_eligibility_status": "not_eligible",
        "rejection_reason_or_null": reason,
    })
    for extra_field in (
        "base_candidate_id",
        "base_candidate_metadata",
        "budget_field",
        "observed_models",
        "observed_symbols",
        "observed_feature_sets",
        "max_models",
        "max_symbols",
        "max_feature_sets",
        "missing_budget_dimension",
        "memory_monitor_status",
        "budget_stop_reason",
    ):
        if extra_field in metrics:
            row[extra_field] = metrics[extra_field]
    return row


def _is_rust_required_scope_fail_closed_artifact(artifact: Mapping[str, Any]) -> bool:
    reason = str(artifact.get("engine_parity_status") or "")
    allowed_reasons = {
        "rust_engine_required_unavailable_fail_closed",
        "rust_runtime_proof_missing_fail_closed",
    }
    rejected_rows = artifact.get("rejected")
    if reason not in allowed_reasons:
        return False
    if artifact.get("promoted") or artifact.get("promoted_ids"):
        return False
    if reason not in (artifact.get("stop_reasons") or []):
        return False
    if not isinstance(rejected_rows, list) or not rejected_rows:
        return False
    for row in rejected_rows:
        if not isinstance(row, Mapping):
            return False
        if row.get("rejection_reason_or_null") != reason:
            return False
        if row.get("replay_eligibility_status") != "not_eligible":
            return False
    return True


def _screening_row_reason(row: Mapping[str, Any], collection_name: str) -> str:
    if collection_name == "promoted":
        return str(row.get("pass_reason") or row.get("screening_status"))
    return str(row.get("rejection_reason_or_null") or row.get("screening_status"))


def _validate_screening_reason_map(
    *,
    errors: List[str],
    artifact: Mapping[str, Any],
    mapping_field: str,
    collection_name: str,
    rows: List[Mapping[str, Any]],
) -> None:
    reason_map = artifact.get(mapping_field)
    if not isinstance(reason_map, Mapping):
        return
    expected = {
        str(row["candidate_id"]): _screening_row_reason(row, collection_name)
        for row in rows
        if "candidate_id" in row
    }
    actual = {str(key): str(value) for key, value in reason_map.items()}
    if rows and not actual:
        errors.append(f"{mapping_field}_empty_for_emitted_rows")
        return
    if set(actual) != set(expected):
        errors.append(f"{mapping_field}_ids_do_not_match_{collection_name}_rows")
        return
    mismatched_ids = [
        candidate_id
        for candidate_id, reason in expected.items()
        if actual.get(candidate_id) != reason
    ]
    if mismatched_ids:
        errors.append(f"{mapping_field}_reason_mismatch")


def validate_screening_artifact(artifact: Mapping[str, Any]) -> None:
    """Validate the terminal VectorBT screening artifact and fail closed."""
    errors: List[str] = []
    for field_name in SCREENING_ARTIFACT_REQUIRED_FIELDS:
        if field_name not in artifact:
            errors.append(f"missing required field: {field_name}")
        elif artifact[field_name] == "":
            errors.append(f"missing required field: {field_name}")
        elif artifact[field_name] is None and field_name not in _SCREENING_ARTIFACT_NULLABLE_FIELDS:
            errors.append(f"missing required field: {field_name}")

    for list_field in ("candidate_ids", "promoted_ids", "rejected_ids", "stop_reasons", "promoted", "rejected"):
        if list_field not in artifact:
            errors.append(f"missing required list field: {list_field}")
        elif not isinstance(artifact[list_field], list):
            errors.append(f"field must be a list: {list_field}")

    for mapping_field in ("candidate_reasons", "promoted_reasons", "rejected_reasons"):
        if mapping_field in artifact and not isinstance(artifact[mapping_field], Mapping):
            errors.append(f"field must be a mapping: {mapping_field}")

    try:
        _parse_positive_int(artifact.get("max_trials", 0), "max_trials")
        trials_run = _parse_non_negative_int(artifact.get("trials_run", 0), "trials_run")
        _parse_non_negative_int(artifact.get("max_models", 0), "max_models")
        _parse_non_negative_int(artifact.get("max_symbols", 0), "max_symbols")
        _parse_non_negative_int(artifact.get("max_feature_sets", 0), "max_feature_sets")
        max_total_trials = _parse_non_negative_int(
            artifact.get("max_total_trials", 0),
            "max_total_trials",
        )
        _parse_optional_non_negative_int(
            artifact.get("max_wall_clock_seconds"),
            "max_wall_clock_seconds",
        )
        _parse_optional_non_negative_int(
            artifact.get("max_peak_memory_mb_or_null"),
            "max_peak_memory_mb_or_null",
        )
        _parse_abort_on_budget_exhaustion(artifact.get("abort_on_budget_exhaustion"))
        if trials_run > max_total_trials:
            errors.append("trials_run_exceeds_max_total_trials")
    except (TypeError, ValueError):
        errors.append("trial_budget_fields_malformed")

    if artifact.get("screening_backend") != "vectorbt":
        errors.append("screening_backend must be vectorbt")
    for clock_error in research_clock_validation_errors(
        artifact.get("research_clock"),
        context="screening_artifact.research_clock",
    ):
        errors.append(clock_error)
    artifact_research_clock = ""
    if not any(err.endswith("research_clock_invalid") for err in errors):
        try:
            artifact_research_clock = validate_research_clock(
                str(artifact.get("research_clock", "")),
                context="screening_artifact.research_clock",
            )
        except ResearchClockError:
            artifact_research_clock = ""
    screening_scope = str(artifact.get("screening_scope") or "")
    rust_required_for_scope = _rust_required_for_scope(screening_scope)
    if rust_required_for_scope and artifact.get("rust_engine_required_for_scope") is not True:
        errors.append(f"rust_required_scope_flag_false:{screening_scope}")
    rust_missing_for_scope = (
        rust_required_for_scope
        and (
            artifact.get("vectorbt_engine") != "rust"
            or artifact.get("rust_engine_available") is not True
            or artifact.get("vectorbt_engine_runtime_proof") is not True
        )
    )
    if rust_missing_for_scope and not _is_rust_required_scope_fail_closed_artifact(artifact):
        errors.append(f"rust_required_scope_not_fail_closed:{screening_scope}")

    row_ids_by_collection: Dict[str, List[str]] = {"promoted": [], "rejected": []}
    rows_by_collection: Dict[str, List[Mapping[str, Any]]] = {"promoted": [], "rejected": []}
    for collection_name in ("promoted", "rejected"):
        rows = artifact.get(collection_name)
        if not isinstance(rows, list):
            continue
        for index, candidate in enumerate(rows):
            if not isinstance(candidate, Mapping):
                errors.append(f"{collection_name}[{index}] must be a mapping")
                continue
            candidate_id = str(candidate.get("candidate_id") or f"{collection_name}[{index}]")
            row_ids_by_collection[collection_name].append(candidate_id)
            rows_by_collection[collection_name].append(candidate)
            for field_name in SCREENING_CANDIDATE_REQUIRED_FIELDS:
                if field_name not in candidate:
                    errors.append(f"{candidate_id} missing candidate field: {field_name}")
                elif field_name != "rejection_reason_or_null" and _screening_field_missing(candidate[field_name]):
                    errors.append(f"{candidate_id} empty candidate field: {field_name}")
            if "research_clock" in candidate and not _screening_field_missing(candidate.get("research_clock")):
                for clock_error in research_clock_validation_errors(
                    candidate["research_clock"],
                    context="research_clock",
                ):
                    errors.append(f"{candidate_id}_{clock_error}")
                if artifact_research_clock:
                    try:
                        candidate_clock = validate_research_clock(
                            str(candidate["research_clock"]),
                            context="research_clock",
                        )
                        if candidate_clock != artifact_research_clock:
                            errors.append(f"{candidate_id}_research_clock_mismatch_with_artifact")
                    except ResearchClockError:
                        pass
            if "parameter_values" in candidate and "parameter_values_hash" in candidate:
                parameter_values = candidate.get("parameter_values")
                if not isinstance(parameter_values, Mapping):
                    errors.append(f"{candidate_id} parameter_values_not_mapping")
                else:
                    expected_param_hash = _parameter_values_hash(parameter_values)
                    if candidate.get("parameter_values_hash") != expected_param_hash:
                        errors.append(f"{candidate_id} parameter_values_hash_mismatch")

            surface_stability_metrics = candidate.get("surface_stability_metrics")
            surface_formula_authority_status = ""
            if not isinstance(surface_stability_metrics, Mapping):
                errors.append(f"{candidate_id} surface_stability_metrics_not_mapping")
            else:
                surface_formula_authority_status = _screening_status_text(
                    surface_stability_metrics.get("formula_authority_status")
                )
                surface_formula_missing = _is_surface_stability_formula_missing(
                    surface_stability_metrics
                )
                surface_formula_defined = _is_surface_stability_defined(
                    surface_stability_metrics
                )
                if not surface_formula_missing and not surface_formula_defined:
                    errors.append(f"{candidate_id} surface_stability_formula_authority_missing")
                    if surface_formula_authority_status not in {"defined", "pass"}:
                        for evidence_field in SURFACE_STABILITY_EVIDENCE_FIELDS:
                            if evidence_field in surface_stability_metrics:
                                errors.append(
                                    f"{candidate_id} surface_stability_{evidence_field}_formula_missing"
                                )
                if surface_formula_missing:
                    for evidence_field in SURFACE_STABILITY_EVIDENCE_FIELDS:
                        if evidence_field in surface_stability_metrics:
                            errors.append(f"{candidate_id} surface_stability_{evidence_field}_formula_missing")
                elif surface_formula_defined:
                    for evidence_field in SURFACE_STABILITY_EVIDENCE_FIELDS:
                        if evidence_field not in surface_stability_metrics:
                            errors.append(f"{candidate_id} surface_stability_{evidence_field}_missing")
                        elif _is_screening_not_run(surface_stability_metrics.get(evidence_field)):
                            errors.append(f"{candidate_id} surface_stability_{evidence_field}_not_run")

            if collection_name == "rejected" and not candidate.get("rejection_reason_or_null"):
                errors.append(f"{candidate_id} rejected candidate missing rejection_reason_or_null")

            if _screening_status_text(candidate.get("replay_eligibility_status")) != "eligible":
                continue

            if _screening_status_text(candidate.get("screening_status")) != "pass":
                errors.append(f"eligible candidate {candidate_id} screening_status_not_pass")
            if candidate.get("rejection_reason_or_null") not in (None, "", "null"):
                errors.append(f"eligible candidate {candidate_id} has_rejection_reason")
            if _screening_status_text(candidate.get("robustness_artifact_staleness")) != "fresh":
                errors.append(f"eligible candidate {candidate_id} robustness_artifact_not_fresh")
            if _screening_status_text(surface_stability_metrics) != "pass":
                errors.append(f"eligible candidate {candidate_id} surface_stability_status_not_pass")
            if surface_formula_authority_status not in {"defined", "pass"}:
                errors.append(
                    f"eligible candidate {candidate_id} surface_stability_formula_authority_missing"
                )
            for field_name in REPLAY_ELIGIBILITY_EVIDENCE_FIELDS:
                if _screening_status_text(candidate.get(field_name)) != "pass":
                    errors.append(f"eligible candidate {candidate_id} {field_name}_not_pass")
            for field_name in REPLAY_ELIGIBILITY_NOT_RUN_FIELDS:
                if _is_screening_not_run(candidate.get(field_name)):
                    errors.append(f"eligible candidate {candidate_id} {field_name}_not_run")
                else:
                    for map_error in _robustness_pass_map_errors(field_name, candidate.get(field_name)):
                        errors.append(f"eligible candidate {candidate_id} {map_error}")

    promoted_ids = [str(value) for value in artifact.get("promoted_ids") or []]
    rejected_ids = [str(value) for value in artifact.get("rejected_ids") or []]
    row_candidate_ids = row_ids_by_collection["promoted"] + row_ids_by_collection["rejected"]
    candidate_ids = [str(value) for value in artifact.get("candidate_ids") or []]
    if promoted_ids != row_ids_by_collection["promoted"]:
        errors.append("promoted_ids_do_not_match_promoted_rows")
    if rejected_ids != row_ids_by_collection["rejected"]:
        errors.append("rejected_ids_do_not_match_rejected_rows")
    if candidate_ids != row_candidate_ids:
        errors.append("candidate_ids_do_not_match_emitted_rows")
    for field_name, values in (
        ("promoted_ids", promoted_ids),
        ("rejected_ids", rejected_ids),
        ("candidate_ids", candidate_ids),
    ):
        if len(values) != len(set(values)):
            errors.append(f"{field_name}_not_unique")
    candidate_reasons = artifact.get("candidate_reasons")
    if isinstance(candidate_reasons, Mapping):
        missing_reason_ids = [
            candidate_id
            for candidate_id in row_candidate_ids
            if candidate_id not in candidate_reasons
        ]
        if missing_reason_ids:
            errors.append("candidate_reasons_missing_emitted_rows")
        stale_reason_ids = [
            str(candidate_id)
            for candidate_id in candidate_reasons
            if str(candidate_id) not in row_candidate_ids
        ]
        if stale_reason_ids:
            errors.append("candidate_reasons_stale_emitted_rows")

    _validate_screening_reason_map(
        errors=errors,
        artifact=artifact,
        mapping_field="promoted_reasons",
        collection_name="promoted",
        rows=rows_by_collection["promoted"],
    )
    _validate_screening_reason_map(
        errors=errors,
        artifact=artifact,
        mapping_field="rejected_reasons",
        collection_name="rejected",
        rows=rows_by_collection["rejected"],
    )

    if "screening_artifact_hash" in artifact and artifact.get("screening_artifact_hash"):
        expected_hash = compute_screening_artifact_hash(artifact)
        if artifact["screening_artifact_hash"] != expected_hash:
            errors.append("screening_artifact_hash mismatch")

    errors.extend(feature_plane_validation_errors(artifact))

    if errors:
        raise ScreeningArtifactError("; ".join(errors))


@dataclass(frozen=True)
class RunBudget:
    max_trials: int
    max_models: int
    max_symbols: int
    max_feature_sets: int
    max_total_trials: int
    max_wall_clock_seconds: Optional[int] = None
    max_peak_memory_mb_or_null: Optional[int] = None
    abort_on_budget_exhaustion: bool = True
    symbol_cap_requested: bool = False
    feature_set_cap_requested: bool = False


@dataclass
class FilterResult:
    promoted: List[PromotedCandidate] = field(default_factory=list)
    rejected: List[RejectedCandidate] = field(default_factory=list)
    vectorbt_available: bool = False
    backend: str = ""
    run_id: str = ""
    total_candidates: int = 0
    code_commit: str = ""
    created_at_utc: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    screening_backend: str = "vectorbt"
    vectorbt_version: str = "unknown"
    vectorbt_engine: str = "unknown"
    engine_parity_status: str = "unknown"
    rust_engine_required_for_scope: bool = False
    rust_engine_available: bool = False
    vectorbt_engine_runtime_proof: bool = False
    license_review: str = "pilot_only_license_review_required_before_broad_or_paid_compute"
    research_clock: str = RESEARCH_CLOCK_SCHEDULED_EVENT
    parameter_space_id: str = ""
    parameter_space_hash: str = ""
    max_trials: int = 0
    trials_run: int = 0
    run_budget_id: str = "vbt2_pilot"
    max_models: int = 0
    max_symbols: int = 0
    max_feature_sets: int = 0
    max_total_trials: int = 0
    max_wall_clock_seconds: Optional[int] = None
    max_peak_memory_mb_or_null: Optional[int] = None
    abort_on_budget_exhaustion: bool = True
    screening_scope: str = "pilot"
    split_scheme_id: str = "pilot_walk_forward_split"
    candidate_ids: List[str] = field(default_factory=list)
    candidate_reasons: Dict[str, str] = field(default_factory=dict)
    stop_reasons: List[str] = field(default_factory=list)
    no_lookahead_signal_shift_proof: str = (
        "raw close-derived signals are shifted one executable bar before VectorBT and metric simulation"
    )
    feature_set_id: str = "fs_v1_pilot_unknown"
    feature_set_hash: str = "pilot_requires_feature_manifest_before_screen"
    data_manifest_hash: str = "pilot_requires_data_manifest_before_screen"
    lake_manifest_hash: str = "pilot_requires_lake_manifest_before_screen"
    events_csv_hash_or_not_applicable: str = "not_applicable_for_vectorbt_pilot"
    fees_model_id: str = "fees_zero_pilot"
    slippage_model_id: str = "slippage_zero_pilot"
    bar_construction_id: str = "ohlcv_1m_from_npz_or_supplied_array"
    screening_artifact_hash: str = ""
    feature_plane_overrides: Dict[str, Any] = field(default_factory=dict)
    target_event_type_or_null: Optional[str] = None
    allowed_context_set_id_or_null: Optional[str] = None
    declared_context_sets: List[Any] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        promoted_rows = [_normalise_promoted_screening_row(p, self) for p in self.promoted]
        rejected_rows = [_normalise_rejected_screening_row(r, self) for r in self.rejected]
        promoted_ids = [str(row["candidate_id"]) for row in promoted_rows]
        rejected_ids = [str(row["candidate_id"]) for row in rejected_rows]
        candidate_ids = promoted_ids + rejected_ids
        promoted_reasons = {
            str(row["candidate_id"]): str(row.get("pass_reason") or row.get("screening_status"))
            for row in promoted_rows
        }
        rejected_reasons = {
            str(row["candidate_id"]): str(row.get("rejection_reason_or_null") or row.get("screening_status"))
            for row in rejected_rows
        }
        candidate_reasons = {**promoted_reasons, **rejected_reasons}
        payload = {
            "run_id": self.run_id,
            "created_at_utc": self.created_at_utc,
            "code_commit": self.code_commit,
            "screening_backend": self.screening_backend,
            "vectorbt_available": self.vectorbt_available,
            "vectorbt_version": self.vectorbt_version,
            "vectorbt_engine": self.vectorbt_engine,
            "engine_parity_status": self.engine_parity_status,
            "rust_engine_required_for_scope": self.rust_engine_required_for_scope,
            "rust_engine_available": self.rust_engine_available,
            "vectorbt_engine_runtime_proof": self.vectorbt_engine_runtime_proof,
            "license_review": self.license_review,
            "research_clock": _canonical_research_clock_label(self.research_clock),
            "parameter_space_id": self.parameter_space_id,
            "parameter_space_hash": self.parameter_space_hash,
            "max_trials": self.max_trials,
            "trials_run": self.trials_run,
            "run_budget_id": self.run_budget_id,
            "max_models": self.max_models,
            "max_symbols": self.max_symbols,
            "max_feature_sets": self.max_feature_sets,
            "max_total_trials": self.max_total_trials,
            "max_wall_clock_seconds": self.max_wall_clock_seconds,
            "max_peak_memory_mb_or_null": self.max_peak_memory_mb_or_null,
            "abort_on_budget_exhaustion": self.abort_on_budget_exhaustion,
            "screening_scope": self.screening_scope,
            "split_scheme_id": self.split_scheme_id,
            "candidate_ids": candidate_ids,
            "candidate_reasons": candidate_reasons,
            "promoted_ids": promoted_ids,
            "rejected_ids": rejected_ids,
            "promoted_reasons": promoted_reasons,
            "rejected_reasons": rejected_reasons,
            "stop_reasons": self.stop_reasons,
            "no_lookahead_signal_shift_proof": self.no_lookahead_signal_shift_proof,
            "feature_set_id": self.feature_set_id,
            "feature_set_hash": self.feature_set_hash,
            "data_manifest_hash": self.data_manifest_hash,
            "lake_manifest_hash": self.lake_manifest_hash,
            "events_csv_hash_or_not_applicable": self.events_csv_hash_or_not_applicable,
            "fees_model_id": self.fees_model_id,
            "slippage_model_id": self.slippage_model_id,
            "bar_construction_id": self.bar_construction_id,
            "backend": self.backend,
            "total_candidates": self.total_candidates,
            "promoted_count": len(self.promoted),
            "rejected_count": len(self.rejected),
            "promoted": promoted_rows,
            "rejected": rejected_rows,
        }
        payload.update(pipeline_stage_stamp(STAGE_1_VECTORBT_SCREEN))
        payload.update(
            build_feature_plane_payload(
                bar_construction_id=self.bar_construction_id,
                feature_set_id=self.feature_set_id,
                feature_set_hash=self.feature_set_hash,
                research_clock=self.research_clock,
                screening_scope=self.screening_scope,
                target_event_type=self.target_event_type_or_null,
                allowed_context_set_id=self.allowed_context_set_id_or_null,
                declared_context_sets=self.declared_context_sets,
                overrides=self.feature_plane_overrides,
            )
        )
        payload["screening_artifact_hash"] = compute_screening_artifact_hash(payload)
        validate_screening_artifact(payload)
        return payload


def _screening_parameter_space_id(grid: Mapping[str, List[Any]]) -> str:
    return f"vbt_ps_{_hash_payload({'param_grid': grid})[:16]}"


def _tier_default_max_trials(screening_scope: str, grid_size: int) -> int:
    scope = _normalise_screening_scope(screening_scope)
    return min(grid_size, _TIER_DEFAULT_MAX_TRIALS.get(scope, grid_size))


def _build_run_budget(
    *,
    candidates: List[CandidateModel],
    grid: Mapping[str, List[Any]],
    screening_scope: str,
    max_total_trials: Optional[int] = None,
    run_budget: Optional[Mapping[str, Any]] = None,
) -> RunBudget:
    budget_map = dict(run_budget or {})
    grid_size = _grid_size(dict(grid))
    max_trials = _parse_positive_int(
        budget_map.get("max_trials", _tier_default_max_trials(screening_scope, grid_size)),
        "max_trials",
    )
    if max_trials > grid_size:
        raise ValueError("max_trials must not exceed parameter grid size")
    model_count = _unique_candidate_count(candidates, lambda candidate: str(candidate.model_id))
    symbol_count = _unique_candidate_count(candidates, _candidate_symbol_id)
    feature_set_count = _unique_candidate_count(candidates, _candidate_feature_set_id)
    explicit_total = budget_map.get("max_total_trials", max_total_trials)
    if max_total_trials is not None and "max_total_trials" in budget_map:
        parsed_arg = _parse_non_negative_int(max_total_trials, "max_total_trials")
        parsed_map = _parse_non_negative_int(budget_map["max_total_trials"], "max_total_trials")
        if parsed_arg != parsed_map:
            raise ValueError("max_total_trials conflicts with run_budget max_total_trials")
        explicit_total = parsed_arg
    return RunBudget(
        max_trials=max_trials,
        max_models=_parse_non_negative_int(budget_map.get("max_models", model_count), "max_models"),
        max_symbols=_parse_non_negative_int(budget_map.get("max_symbols", symbol_count), "max_symbols"),
        max_feature_sets=_parse_non_negative_int(
            budget_map.get("max_feature_sets", feature_set_count),
            "max_feature_sets",
        ),
        max_total_trials=(
            _parse_non_negative_int(explicit_total, "max_total_trials")
            if explicit_total is not None
            else len(candidates) * max_trials
        ),
        max_wall_clock_seconds=_parse_optional_non_negative_int(
            budget_map.get("max_wall_clock_seconds"),
            "max_wall_clock_seconds",
        ),
        max_peak_memory_mb_or_null=_parse_optional_non_negative_int(
            budget_map.get("max_peak_memory_mb_or_null"),
            "max_peak_memory_mb_or_null",
        ),
        abort_on_budget_exhaustion=_parse_abort_on_budget_exhaustion(
            budget_map.get("abort_on_budget_exhaustion", True)
        ),
        symbol_cap_requested="max_symbols" in budget_map,
        feature_set_cap_requested="max_feature_sets" in budget_map,
    )


def _run_budget_fail_closed_reason(
    candidates: List[CandidateModel],
    budget: RunBudget,
) -> Optional[Tuple[str, Dict[str, Any]]]:
    model_count = _unique_candidate_count(candidates, lambda candidate: str(candidate.model_id))
    symbol_count = _unique_candidate_count(candidates, _candidate_symbol_id)
    feature_set_count = _unique_candidate_count(candidates, _candidate_feature_set_id)
    counts = {
        "observed_models": model_count,
        "observed_symbols": symbol_count,
        "observed_feature_sets": feature_set_count,
        "max_models": budget.max_models,
        "max_symbols": budget.max_symbols,
        "max_feature_sets": budget.max_feature_sets,
    }
    if budget.symbol_cap_requested and any(not _candidate_has_explicit_symbol(candidate) for candidate in candidates):
        return "RUN_BUDGET_REACHED", {
            **counts,
            "budget_field": "max_symbols",
            "missing_budget_dimension": "symbol",
        }
    if budget.feature_set_cap_requested and any(
        not _candidate_has_explicit_feature_set(candidate) for candidate in candidates
    ):
        return "RUN_BUDGET_REACHED", {
            **counts,
            "budget_field": "max_feature_sets",
            "missing_budget_dimension": "feature_set_id",
        }
    if model_count > budget.max_models:
        return "RUN_BUDGET_REACHED", {**counts, "budget_field": "max_models"}
    if symbol_count > budget.max_symbols:
        return "RUN_BUDGET_REACHED", {**counts, "budget_field": "max_symbols"}
    if feature_set_count > budget.max_feature_sets:
        return "RUN_BUDGET_REACHED", {**counts, "budget_field": "max_feature_sets"}
    if budget.max_peak_memory_mb_or_null is not None:
        # Per Codex review finding 8: memory caps are currently
        # unsupported-fail-closed.  tracemalloc / resource measurement is not
        # wired into the screening loop, so any non-null memory budget is
        # treated as an immediate stop (fail-closed) rather than being
        # enforced at runtime.  This fail-closed behavior is intentional and
        # must NOT be relaxed until peak-memory measurement is actually wired.
        # max_peak_memory_mb_or_null must be left null (None) for all runs
        # until that measurement is available.
        return "MEMORY_BUDGET_REACHED", {
            **counts,
            "budget_field": "max_peak_memory_mb_or_null",
            "memory_monitor_status": "unsupported_fail_closed",
        }
    return None


def _append_candidate_budget_rejections(
    result: FilterResult,
    candidates: List[CandidateModel],
    reason: str,
    metric_values: Optional[Mapping[str, Any]] = None,
) -> None:
    if reason not in result.stop_reasons:
        result.stop_reasons.append(reason)
    for cand in candidates:
        row_id = _pretrial_rejection_id(cand, reason)
        result.rejected.append(RejectedCandidate(
            candidate_id=row_id,
            hypothesis_id=cand.model_id,
            reject_reason=reason,
            metric_values={
                **_base_candidate_metric_values(cand),
                **dict(metric_values or {}),
            },
        ))


def _new_filter_result(
    *,
    backend: str,
    run_id: str,
    candidates: List[CandidateModel],
    grid: Mapping[str, List[Any]],
    trials_run: int = 0,
    stop_reasons: Optional[List[str]] = None,
    screening_scope: str = "pilot",
    max_total_trials: Optional[int] = None,
    run_budget: Optional[RunBudget] = None,
) -> FilterResult:
    engine_meta = _screening_engine_metadata(screening_scope)
    budget = run_budget or _build_run_budget(
        candidates=candidates,
        grid=grid,
        screening_scope=screening_scope,
        max_total_trials=max_total_trials,
    )
    candidate_ids = [candidate.candidate_id for candidate in candidates]
    scope = str(engine_meta["screening_scope"])
    return FilterResult(
        vectorbt_available=bool(engine_meta["vectorbt_available"]),
        backend=backend,
        run_id=run_id,
        total_candidates=len(candidates) * budget.max_trials,
        code_commit=_resolve_git_commit(),
        vectorbt_version=str(engine_meta["vectorbt_version"]),
        vectorbt_engine=str(engine_meta["vectorbt_engine"]),
        engine_parity_status=str(engine_meta["engine_parity_status"]),
        rust_engine_required_for_scope=bool(engine_meta["rust_engine_required_for_scope"]),
        rust_engine_available=bool(engine_meta["rust_engine_available"]),
        vectorbt_engine_runtime_proof=bool(engine_meta["vectorbt_engine_runtime_proof"]),
        parameter_space_id=_screening_parameter_space_id(grid),
        parameter_space_hash=_hash_payload({"param_grid": dict(grid)}),
        max_trials=budget.max_trials,
        trials_run=trials_run,
        run_budget_id=f"vbt_{scope}",
        max_models=budget.max_models,
        max_symbols=budget.max_symbols,
        max_feature_sets=budget.max_feature_sets,
        max_total_trials=budget.max_total_trials,
        max_wall_clock_seconds=budget.max_wall_clock_seconds,
        max_peak_memory_mb_or_null=budget.max_peak_memory_mb_or_null,
        abort_on_budget_exhaustion=budget.abort_on_budget_exhaustion,
        screening_scope=scope,
        candidate_ids=candidate_ids,
        candidate_reasons={candidate_id: "queued_for_vectorbt_screen" for candidate_id in candidate_ids},
        stop_reasons=list(stop_reasons or []),
    )


def _default_data_loader(
    event_id: str,
    repo_root: Path,
) -> Optional[np.ndarray]:
    """Load OHLCV bars from existing HFT3 data pipeline.
    Falls back to building bars from the NPZ MBO data.
    Returns None if no data is available.
    """
    npz_dir = repo_root / "data" / "npz"
    candidates = list(npz_dir.glob(f"*{event_id}*_mbo.npz")) if npz_dir.exists() else []
    candidates.extend(list(repo_root.glob(f"data/npz/*{event_id}*_mbo.npz")))
    if not candidates:
        return None
    npz_path = str(candidates[0])
    try:
        from features_engine.src.features.npz_feed import load_npz_events
        raw = load_npz_events(npz_path)
        if len(raw) < 2:
            return None
        ts = raw["local_ts"].astype(np.int64)
        px = raw["px"].astype(np.float64)
        qty = raw["qty"].astype(np.float64)
        side_flags = raw["ev"].astype(np.int64)
        buy_mask = (side_flags & 0x1) == 1

        bar_interval_ns = 60_000_000_000
        start_ts = ts[0]
        end_ts = ts[-1]
        n_bars = max(1, int((end_ts - start_ts) / bar_interval_ns))
        o = np.zeros(n_bars)
        h = np.zeros(n_bars)
        l = np.full(n_bars, np.inf)
        c = np.zeros(n_bars)
        v = np.zeros(n_bars)

        # Per Codex review finding 12: replace the per-bar O(n*m) mask scan with
        # a vectorized np.searchsorted bar-boundary lookup.  Each bar b covers
        # [bar_start, bar_end); searchsorted gives the half-open event index
        # range [lo, hi) for each bar, preserving the same causal semantics
        # (each bar uses only events whose timestamps fall in [bar_start,
        # bar_end)).  ts is assumed non-decreasing (the upstream validator
        # already rejects non-monotonic local_ts).
        bar_starts = start_ts + np.arange(n_bars, dtype=np.int64) * bar_interval_ns
        # lo[b] = first event index with ts >= bar_starts[b]
        lo = np.searchsorted(ts, bar_starts, side="left")
        # hi[b] = first event index with ts >= bar_starts[b] + bar_interval_ns
        bar_ends = bar_starts + bar_interval_ns
        hi = np.searchsorted(ts, bar_ends, side="left")
        for i in range(n_bars):
            s = int(lo[i])
            e = int(hi[i])
            if s >= e:
                o[i] = c[i - 1] if i > 0 else px[0]
                h[i] = o[i]
                l[i] = o[i]
                c[i] = o[i]
                continue
            bar_px = px[s:e]
            bar_qty = qty[s:e]
            o[i] = bar_px[0]
            h[i] = bar_px.max()
            l[i] = bar_px.min()
            c[i] = bar_px[-1]
            v[i] = bar_qty.sum()

        l[l == np.inf] = o[l == np.inf]
        return np.column_stack([o, h, l, c, v])
    except Exception as exc:
        print(f"Warning: NPZ data load failed: {exc}", file=sys.stderr)
        return None


def _compute_metrics_for_params(
    ohlcv: np.ndarray,
    entry_signal: np.ndarray,
    exit_signal: np.ndarray,
    stop_loss_pct: Optional[float],
    take_profit_pct: Optional[float],
    price_col: int = 3,
    start_index: int = 1,
    end_index: Optional[int] = None,
) -> Dict[str, Any]:
    if price_col == 3 and ohlcv.ndim == 2 and ohlcv.shape[1] >= 5:
        close = _ohlcv_column(ohlcv, "close")
    else:
        close = ohlcv[:, price_col]
    n = len(close)
    if n == 0:
        return {
            "net_return_pct": 0.0,
            "expectancy": 0.0,
            "win_rate": 0.0,
            "num_trades": 0,
            "max_drawdown_pct": 0.0,
        }
    start = max(1, min(int(start_index), n - 1))
    end = n if end_index is None else max(start + 1, min(int(end_index), n))
    position = 0.0
    entry_price = 0.0
    trades: List[float] = []
    cum_return = 0.0
    peak = 0.0
    max_dd = 0.0

    for i in range(1, start):
        if position != 0:
            ret = (close[i] - entry_price) / entry_price
            hit_stop = stop_loss_pct is not None and ret < -stop_loss_pct / 100.0
            hit_target = take_profit_pct is not None and ret > take_profit_pct / 100.0
            if hit_stop or hit_target or (position > 0 and exit_signal[i] < 0):
                entry_price = 0.0
                position = 0.0
                continue
        if position == 0 and entry_signal[i] > 0:
            entry_price = close[i]
            position = 1.0

    if position != 0:
        entry_price = close[start - 1]

    for i in range(start, end):
        if position != 0:
            ret = (close[i] - entry_price) / entry_price
            hit_stop = stop_loss_pct is not None and ret < -stop_loss_pct / 100.0
            hit_target = take_profit_pct is not None and ret > take_profit_pct / 100.0
            if hit_stop:
                exit_return = -stop_loss_pct / 100.0
            elif hit_target:
                exit_return = take_profit_pct / 100.0
            else:
                exit_return = None
            if exit_return is not None:
                trades.append(exit_return)
                cum_return += exit_return
                entry_price = 0.0
                position = 0.0
                peak = max(peak, cum_return)
                max_dd = max(max_dd, peak - cum_return)
                continue

            if position > 0:
                unrealized = (close[i] - entry_price) / entry_price
            else:
                unrealized = (entry_price - close[i]) / entry_price
            cum_return_unrealized = cum_return + unrealized
            peak = max(peak, cum_return_unrealized)
            max_dd = max(max_dd, peak - cum_return_unrealized)

        if position == 0 and entry_signal[i] > 0:
            entry_price = close[i]
            position = 1.0
        elif position > 0 and exit_signal[i] < 0:
            trade_return = (close[i] - entry_price) / entry_price
            trades.append(trade_return)
            cum_return += trade_return
            entry_price = 0.0
            position = 0.0

    if position != 0:
        trade_return = (close[end - 1] - entry_price) / entry_price
        trades.append(trade_return)
        cum_return += trade_return

    n_trades = len([t for t in trades if abs(t) > 1e-12])
    expectancy = float(np.mean(trades)) if n_trades > 0 else 0.0
    win_rate = float(np.mean([t > 0 for t in trades])) if n_trades > 0 else 0.0
    total_return = float(cum_return)
    return {
        "net_return_pct": round(total_return * 100, 4),
        "expectancy": round(expectancy, 6),
        "win_rate": round(win_rate, 4),
        "num_trades": n_trades,
        "max_drawdown_pct": -round(max_dd * 100, 4),
    }


def _simulate_walk_forward(
    ohlcv: np.ndarray,
    entry_signal: np.ndarray,
    exit_signal: np.ndarray,
    n_windows: int = 4,
    train_ratio: float = 0.6,
) -> Dict[str, Any]:
    n = len(ohlcv)
    if n < 20:
        return {"wf_consistency": 0.0, "oos_expectancy": 0.0}

    oos_expectancies: List[float] = []
    first_oos = int(n * train_ratio)
    if first_oos < 10 or first_oos >= n - 5:
        return {"wf_consistency": 0.0, "oos_expectancy": 0.0}
    bounds = np.linspace(first_oos, n, n_windows + 1, dtype=int)
    for start, end in zip(bounds[:-1], bounds[1:]):
        if end - start < 5:
            continue
        metrics_oos = _compute_metrics_for_params(
            ohlcv,
            entry_signal,
            exit_signal,
            None,
            None,
            start_index=int(start),
            end_index=int(end),
        )
        oos_expectancies.append(float(metrics_oos["expectancy"]) if metrics_oos["num_trades"] > 0 else 0.0)

    consistency = 0.0
    if oos_expectancies:
        positive = sum(1 for e in oos_expectancies if e > 0)
        consistency = positive / len(oos_expectancies)
    oos_exp = float(np.mean(oos_expectancies)) if oos_expectancies else 0.0
    return {
        "wf_consistency": round(consistency, 4),
        "oos_expectancy": round(oos_exp, 6),
    }


_VBT_GATE_REQUIRED_STATS = (
    "Total Trades",
    "Expectancy",
    "Max Drawdown [%]",
)
_VBT2_PILOT_SKIPPED_UNMEASURED_GATE_FIELDS = (
    "wf_consistency",
    "turnover_mean_pct",
    "param_stability_score",
    "slippage_sensitivity",
)


def _finite_float(value: Any) -> Optional[float]:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if not np.isfinite(parsed):
        return None
    return parsed


def _stats_float(stats: Mapping[str, Any], *names: str) -> Optional[float]:
    for name in names:
        if name not in stats:
            continue
        value = _finite_float(stats[name])
        if value is not None:
            return value
    return None


def _normalise_vectorbt_stats_for_gate(vbt_stats: Mapping[str, Any]) -> Tuple[Dict[str, Any], List[str]]:
    """Map official ``Portfolio.stats()`` fields to the pilot gate surface.

    The VBT-2 pilot gate must not pass candidates on the internal numpy helper.
    Fields unavailable from official VectorBT stats are marked unmeasured for
    the pilot gate; helper-derived diagnostics are attached separately by the
    caller.
    """
    missing = [
        field_name
        for field_name in _VBT_GATE_REQUIRED_STATS
        if _stats_float(vbt_stats, field_name) is None
    ]
    if missing:
        return {}, missing

    total_trades = _stats_float(vbt_stats, "Total Trades")
    expectancy = _stats_float(vbt_stats, "Expectancy")
    total_return_pct = float(_stats_float(vbt_stats, "Total Return [%]") or 0.0)
    max_drawdown_pct = float(_stats_float(vbt_stats, "Max Drawdown [%]") or 0.0)
    if max_drawdown_pct > 0:
        max_drawdown_pct = -max_drawdown_pct
    total_trades_i = int(round(float(total_trades or 0.0)))
    expectancy_f = float(expectancy or 0.0)
    return {
        "official_vectorbt_stats_source": "pf.stats()",
        "official_vectorbt_stats_status": "complete",
        "net_return_pct": round(total_return_pct, 8),
        "gross_return": round(total_return_pct / 100.0, 8),
        "net_return": round(total_return_pct / 100.0, 8),
        "expectancy": round(expectancy_f, 8),
        "oos_expectancy": round(expectancy_f, 8),
        "num_trades": total_trades_i,
        "trade_count": total_trades_i,
        "max_drawdown_pct": round(max_drawdown_pct, 8),
        "profit_factor": _stats_float(vbt_stats, "Profit Factor"),
        "sharpe": _stats_float(vbt_stats, "Sharpe Ratio"),
        "sortino": _stats_float(vbt_stats, "Sortino Ratio"),
        "gate_metric_authority": "official_vectorbt_portfolio_stats",
        "gate_metric_non_stats_status": {
            field_name: "not_measured_not_used_by_vbt2_pilot_gate"
            for field_name in _VBT2_PILOT_SKIPPED_UNMEASURED_GATE_FIELDS
        },
    }, []


def _evaluate_vbt2_pilot_stats_gate(
    candidate: PromotedCandidate,
    gates: PromotionGate,
) -> Tuple[bool, Dict[str, Any]]:
    """Evaluate the VBT-2 pilot gate using only official VectorBT stats fields."""
    metrics = candidate.vectorbt_results
    failures: List[str] = []
    oos_expectancy = _finite_float(metrics.get("oos_expectancy"))
    max_drawdown_pct = _finite_float(metrics.get("max_drawdown_pct"))
    num_trades = _finite_float(metrics.get("num_trades"))

    if oos_expectancy is None:
        failures.append("missing_oos_expectancy_from_official_expectancy")
    elif oos_expectancy < gates.min_oos_expectancy:
        failures.append("oos_expectancy_below_threshold")

    if max_drawdown_pct is None:
        failures.append("missing_max_drawdown_pct_from_official_stats")
    elif abs(max_drawdown_pct) > abs(gates.max_drawdown_pct):
        failures.append("max_drawdown_above_threshold")

    if num_trades is None:
        failures.append("missing_num_trades_from_official_total_trades")
    elif int(round(num_trades)) < gates.min_trades:
        failures.append("num_trades_below_threshold")

    return not failures, {
        "scope": "vbt2_pilot_official_vectorbt_stats_only",
        "used_fields": {
            "oos_expectancy": "Expectancy",
            "max_drawdown_pct": "Max Drawdown [%]",
            "num_trades": "Total Trades",
        },
        "skipped_unmeasured_fields": list(_VBT2_PILOT_SKIPPED_UNMEASURED_GATE_FIELDS),
        "failure_semantics": "screening_only_not_replay_or_robustness_eligible",
        "failures": failures,
    }


def _default_signal_computer(
    cand: CandidateModel,
    ohlcv: np.ndarray,
    parsed: ParsedHypothesis,
    repo_root: Path,
) -> Tuple[np.ndarray, np.ndarray]:
    from features_engine.src.model_registry import get_hyp_id_for_slug, resolve_model_id
    from features_engine.src.hypotheses.registry import get_active_hypotheses
    from features_engine.src.pipeline.market_state_pipeline import MarketStatePipeline

    resolved = resolve_model_id(cand.model_id)
    hyp_id = get_hyp_id_for_slug(resolved)
    by_hyp_id = {h.hyp_id: h for h in get_active_hypotheses()}
    hypothesis_cls = by_hyp_id.get(hyp_id)
    if hypothesis_cls is None:
        raise ValueError(f"model_id {cand.model_id} (hyp_id={hyp_id}) not in active hypotheses")

    pipeline = MarketStatePipeline()
    n_bars = len(ohlcv)
    close = _ohlcv_column(ohlcv, "close")
    signal = np.zeros(n_bars)
    signal_threshold = float(cand.strategy_params.get("signal_threshold", 0.0) or 0.0)

    for i in range(n_bars):
        pipeline.process_event({"local_ts": _bar_timestamp_ns(ohlcv, i), "close": close[i]})
        state = pipeline.latest_state
        if state is not None:
            sig = hypothesis_cls.evaluate(state)
            signal[i] = sig

    entry_signal = np.where(signal > signal_threshold, 1.0, 0.0)
    exit_signal = np.where(signal < -signal_threshold, -1.0, 0.0)
    return entry_signal, exit_signal


def _grid_iter(grid: Dict[str, List[Any]]) -> Iterator[Dict[str, Any]]:
    yield from expand_parameter_grid(grid)


def _append_budget_skipped_trials(
    result: FilterResult,
    candidates: List[CandidateModel],
    grid_trials: List[Dict[str, Any]],
    candidate_index: int,
    param_index: int,
    reason: str = "RUN_BUDGET_REACHED",
) -> None:
    if reason not in result.stop_reasons:
        result.stop_reasons.append(reason)
    for offset, cand in enumerate(candidates[candidate_index:]):
        start_index = param_index if offset == 0 else 0
        for params in grid_trials[start_index:]:
            merged = dict(cand.strategy_params)
            merged.update(params)
            result.rejected.append(RejectedCandidate(
                candidate_id=_candidate_id(cand, merged),
                hypothesis_id=cand.model_id,
                reject_reason=reason,
                metric_values={
                    **_base_candidate_metric_values(cand),
                    "parameter_values": dict(merged),
                    "param_values": dict(merged),
                    "budget_stop_reason": reason,
                },
            ))


def _run_vectorbt_simulation(
    ohlcv: np.ndarray,
    candidates: List[CandidateModel],
    parsed: ParsedHypothesis,
    grid: Dict[str, List[Any]],
    repo_root: Path,
    signal_computer: Optional[Callable] = None,
    screening_scope: str = "pilot",
    max_total_trials: Optional[int] = None,
    run_budget: Optional[RunBudget] = None,
) -> FilterResult:
    """Run VectorBT simulation when the library is available.

    ``parsed`` is used by the ``signal_computer`` to configure feature parameters.
    The actual usage happens inside ``signal_computer``, not directly in this function.
    """
    from backtest_pipeline.src.asset_class_routing import resolve_validation_path

    signal_computer = signal_computer or _default_signal_computer
    budget = run_budget or _build_run_budget(
        candidates=candidates,
        grid=grid,
        screening_scope=screening_scope,
        max_total_trials=max_total_trials,
    )

    engine_meta = _screening_engine_metadata(screening_scope)
    vectorbt_available = bool(engine_meta["vectorbt_available"])
    rust_required = bool(engine_meta["rust_engine_required_for_scope"])
    rust_available = bool(engine_meta["rust_engine_available"])
    rust_runtime_proof = bool(engine_meta["vectorbt_engine_runtime_proof"])
    if rust_required and rust_available and not rust_runtime_proof:
        rust_runtime_proof = _establish_vectorbt_rust_runtime_proof()
        if rust_runtime_proof:
            engine_meta = _screening_engine_metadata(screening_scope)
    if not vectorbt_available or (rust_required and (not rust_available or not rust_runtime_proof)):
        stop_reason = "vectorbt_unavailable_fail_closed"
        if vectorbt_available and rust_required and not rust_available:
            stop_reason = "rust_engine_required_unavailable_fail_closed"
        elif vectorbt_available and rust_required and not rust_runtime_proof:
            stop_reason = "rust_runtime_proof_missing_fail_closed"
        result = _new_filter_result(
            backend="vectorbt_rust_unavailable" if vectorbt_available and rust_required else "vectorbt_unavailable",
            run_id=f"{'vbt_rust_unavailable' if vectorbt_available and rust_required else 'vbt_unavailable'}_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}",
            candidates=candidates,
            grid=grid,
            trials_run=0,
            stop_reasons=[stop_reason],
            screening_scope=screening_scope,
            max_total_trials=max_total_trials,
            run_budget=budget,
        )
        for cand in candidates:
            row_id = _pretrial_rejection_id(cand, stop_reason)
            result.rejected.append(RejectedCandidate(
                candidate_id=row_id,
                hypothesis_id=cand.model_id,
                reject_reason=stop_reason,
                metric_values={
                    **_base_candidate_metric_values(cand),
                    "vectorbt_available": vectorbt_available,
                    "engine_parity_status": result.engine_parity_status,
                    "rust_engine_required_for_scope": result.rust_engine_required_for_scope,
                    "rust_engine_available": result.rust_engine_available,
                    "vectorbt_engine_runtime_proof": result.vectorbt_engine_runtime_proof,
                },
            ))
        return result

    import vectorbt as vbt  # type: ignore[no-redef]
    portfolio_engine = str(engine_meta.get("vectorbt_engine") or "numba")
    if portfolio_engine != "rust":
        portfolio_engine = "numba"
    close = _ohlcv_column(ohlcv, "close")
    open_p = _ohlcv_column(ohlcv, "open")
    high = _ohlcv_column(ohlcv, "high")
    low = _ohlcv_column(ohlcv, "low")
    volume = _ohlcv_column(ohlcv, "volume")

    result = _new_filter_result(
        backend="vectorbt",
        run_id=f"vbt_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}",
        candidates=candidates,
        grid=grid,
        screening_scope=screening_scope,
        max_total_trials=max_total_trials,
        run_budget=budget,
    )

    grid_trials = list(_grid_iter(grid))[:budget.max_trials]
    trial_budget = budget.max_total_trials
    started_at = time.monotonic()

    for candidate_index, cand in enumerate(candidates):
        for param_index, params in enumerate(grid_trials):
            if (
                budget.max_wall_clock_seconds is not None
                and time.monotonic() - started_at >= budget.max_wall_clock_seconds
            ):
                _append_budget_skipped_trials(
                    result,
                    candidates,
                    grid_trials,
                    candidate_index,
                    param_index,
                    reason="WALL_CLOCK_BUDGET_REACHED",
                )
                return result
            if (
                result.trials_run >= trial_budget
            ):
                _append_budget_skipped_trials(
                    result,
                    candidates,
                    grid_trials,
                    candidate_index,
                    param_index,
                )
                return result
            result.trials_run += 1
            merged = dict(cand.strategy_params)
            merged.update(params)
            cand_id = _candidate_id(cand, merged)
            trial_candidate = replace(cand, candidate_id=cand_id, strategy_params=merged)
            signal_thresh = float(merged.get("signal_threshold", 0.15))
            holding_period = int(merged.get("holding_period_bars", 15))
            stop_loss = merged.get("stop_loss_pct")
            take_profit = merged.get("take_profit_pct")
            stop_loss_f = float(stop_loss) if stop_loss is not None else None
            take_profit_f = float(take_profit) if take_profit is not None else None

            try:
                raw_entry_signal, raw_exit_signal = signal_computer(trial_candidate, ohlcv, parsed, repo_root)
            except Exception as exc:
                print(f"Warning: signal computer failed for {cand_id}: {exc}", file=sys.stderr)
                result.rejected.append(RejectedCandidate(
                    candidate_id=cand_id,
                    hypothesis_id=cand.model_id,
                    reject_reason="unresolvable_model_id",
                    metric_values={
                        **_base_candidate_metric_values(cand),
                        "error": str(exc),
                        "error_type": type(exc).__name__,
                        "parameter_values": dict(merged),
                        "param_values": dict(merged),
                    },
                ))
                continue
            entry_signal = _shift_signal_to_executable_bar(np.asarray(raw_entry_signal, dtype=float))
            exit_signal = _shift_signal_to_executable_bar(np.asarray(raw_exit_signal, dtype=float))
            exit_signal = _apply_holding_period_exit(entry_signal, exit_signal, holding_period)
            vbt_stats = {}
            try:
                entries = entry_signal > 0
                exits = exit_signal < 0
                pf = vbt.Portfolio.from_signals(
                    close, entries=entries, exits=exits,
                    init_cash=10000.0, freq="1min",
                    sl_stop=stop_loss_f / 100.0 if stop_loss_f else None,
                    tp_stop=take_profit_f / 100.0 if take_profit_f else None,
                    engine=portfolio_engine,
                )
                vbt_stats = dict(pf.stats())
            except Exception as exc:
                print(f"Warning: VectorBT portfolio sim failed for {cand.candidate_id}: {exc}", file=sys.stderr)
                result.rejected.append(RejectedCandidate(
                    candidate_id=cand_id,
                    hypothesis_id=cand.model_id,
                    reject_reason="vectorbt_simulation_failed",
                    metric_values={
                        **_base_candidate_metric_values(cand),
                        "error": str(exc),
                        "error_type": type(exc).__name__,
                        "filter_backend": result.backend,
                        "parameter_values": dict(merged),
                        "param_values": dict(merged),
                    },
                ))
                continue

            gate_metrics, missing_gate_stats = _normalise_vectorbt_stats_for_gate(vbt_stats)
            if missing_gate_stats:
                result.rejected.append(RejectedCandidate(
                    candidate_id=cand_id,
                    hypothesis_id=cand.model_id,
                    reject_reason="vectorbt_stats_missing_gate_fields",
                    metric_values={
                        **_base_candidate_metric_values(cand),
                        "filter_backend": result.backend,
                        "parameter_values": dict(merged),
                        "param_values": dict(merged),
                        "vbt_stats": vbt_stats,
                        "missing_vectorbt_stats_fields": list(missing_gate_stats),
                        "gate_metric_authority": "official_vectorbt_portfolio_stats",
                    },
                ))
                continue

            auxiliary_metrics = _compute_metrics_for_params(
                ohlcv, entry_signal, exit_signal, stop_loss_f, take_profit_f,
            )
            wf = _simulate_walk_forward(ohlcv, entry_signal, exit_signal)

            vectorbt_results = {
                "base_candidate_id": cand.candidate_id,
                "base_candidate_metadata": dict(cand.metadata),
                "signal_threshold": signal_thresh,
                "holding_period_bars": holding_period,
                "stop_loss_pct": stop_loss_f,
                "take_profit_pct": take_profit_f,
                "vbt_stats": vbt_stats,
                "filter_backend": result.backend,
                **gate_metrics,
                "auxiliary_numpy_metrics": auxiliary_metrics,
                "auxiliary_numpy_walk_forward": wf,
                "surface_stability_metrics": _surface_stability_formula_missing(),
            }
            robustness_evidence = cand.metadata.get("robustness_evidence")
            if isinstance(robustness_evidence, Mapping):
                vectorbt_results["robustness_evidence"] = copy.deepcopy(robustness_evidence)
            if getattr(cand, "feature_recipe_hash", None):
                vectorbt_results["feature_recipe_hash"] = cand.feature_recipe_hash
            if getattr(cand, "feature_recipe", None):
                vectorbt_results["feature_recipe"] = copy.deepcopy(cand.feature_recipe)

            candidate_path = resolve_validation_path(cand)
            promoted = PromotedCandidate(
                candidate_id=cand_id,
                hypothesis_id=cand.model_id,
                strategy_family=cand.metadata.get("strategy_family", cand.model_id),
                asset_class=candidate_path.asset_class,
                symbol=candidate_path.symbol,
                timeframe="1m",
                param_values=merged,
                vectorbt_run_id=result.run_id,
                vectorbt_results=vectorbt_results,
                pass_reason="vectorbt_simulated",
                in_sample_results={"expectancy": gate_metrics.get("expectancy", 0.0)},
                out_of_sample_results={"expectancy": wf.get("oos_expectancy", 0.0)},
            )
            result.promoted.append(promoted)

    return result


def _candidate_id(cand: CandidateModel, params: Dict[str, Any]) -> str:
    meta_clock = cand.metadata.get("research_clock")
    if meta_clock is not None:
        meta_clock = _canonical_research_clock_label(str(meta_clock))
    identity = {
        "base_candidate_id": cand.candidate_id,
        "model_id": cand.model_id,
        "params": params,
        "symbol": cand.metadata.get("symbol"),
        "idea_id": cand.metadata.get("idea_id"),
        "feature_set_id": cand.metadata.get("feature_set_id")
        or cand.metadata.get("_candidate_feature_set_id"),
        "candidate_symbol_id": cand.metadata.get("_candidate_symbol_id"),
        "research_clock": meta_clock,
        "feature_recipe_hash": getattr(cand, "feature_recipe_hash", None)
        or cand.metadata.get("feature_recipe_hash"),
    }
    raw = json.dumps(identity, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def _pretrial_rejection_id(cand: CandidateModel, reason: str) -> str:
    return _candidate_id(cand, {"pretrial_rejection_reason": reason})


def _base_candidate_metric_values(cand: CandidateModel) -> Dict[str, Any]:
    return {
        "base_candidate_id": cand.candidate_id,
        "base_candidate_metadata": dict(cand.metadata),
        "symbol": cand.metadata.get("symbol"),
    }


def _resolve_git_commit() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=_REPO, stderr=subprocess.DEVNULL, timeout=5,
        ).decode().strip()
    except Exception as exc:
        print(f"Warning: git commit resolution failed: {exc}", file=sys.stderr)
        return ""


def _resolve_screen_symbol(candidates: List[CandidateModel], symbol: str | None) -> str:
    if symbol:
        return symbol
    if candidates:
        return load_validation_path(candidates[0]).symbol
    return "MES.v.0"


def _resolve_research_clock(candidates: List[CandidateModel]) -> str:
    for cand in candidates:
        meta_clock = (cand.metadata or {}).get("research_clock")
        if meta_clock is not None:
            try:
                return validate_research_clock(str(meta_clock))
            except ResearchClockError:
                pass
        recipe = getattr(cand, "feature_recipe", None) or (cand.metadata or {}).get("feature_recipe")
        if isinstance(recipe, Mapping) and recipe.get("research_clock"):
            try:
                return validate_research_clock(str(recipe["research_clock"]))
            except ResearchClockError:
                pass
    return RESEARCH_CLOCK_SCHEDULED_EVENT


def _apply_fs_v1_screen_metadata(
    result: FilterResult,
    ctx: Any,
    candidates: List[CandidateModel],
    *,
    research_clock: str,
    screening_scope: str,
) -> None:
    from backtest_pipeline.src.fs_v1_screen_path import (
        FS_V1_BAR_CONSTRUCTION_ID,
        fs_v1_feature_set_hash,
        fs_v1_feature_set_id,
    )

    base_manifest = build_feature_usage_manifest(
        bar_construction_id=FS_V1_BAR_CONSTRUCTION_ID,
        feature_set_id=fs_v1_feature_set_id(),
        feature_set_hash=fs_v1_feature_set_hash(),
        research_clock=research_clock,
        screening_scope=screening_scope,
    )
    recipe_manifest = build_manifest_from_feature_recipes(
        candidates,
        fs_v1_row_loop_active=True,
        vix_injected=ctx.has_vix,
    )
    result.bar_construction_id = FS_V1_BAR_CONSTRUCTION_ID
    result.feature_set_id = fs_v1_feature_set_id()
    result.feature_set_hash = fs_v1_feature_set_hash()
    result.data_manifest_hash = ctx.manifest_hash or ctx.content_hash or "fs_v1_store"
    result.research_clock = research_clock
    result.feature_plane_overrides = {
        "feature_usage_manifest": {**base_manifest, **recipe_manifest},
        "model_feature_usage_status": "partial_observed",
    }
    result.no_lookahead_signal_shift_proof = (
        "fs_v1_row_loop_visible_index_j_with_ts[j]<=ts[i]-feature_latency_ns;"
        " signals shifted one executable bar before VectorBT portfolio simulation"
    )


def filter_candidates(
    candidates: List[CandidateModel],
    parsed: ParsedHypothesis,
    event_id: str,
    repo_root: Optional[Path] = None,
    gates: Optional[PromotionGate] = None,
    param_grid: Optional[Dict[str, List[Any]]] = None,
    data_loader: Optional[Callable[[str, Path], Optional[np.ndarray]]] = None,
    signal_computer: Optional[Callable] = None,
    persist_promotions: bool = False,
    screening_scope: str = "pilot",
    max_total_trials: Optional[int] = None,
    run_budget: Optional[Mapping[str, Any]] = None,
    feature_store_root: Optional[Path] = None,
    symbol: Optional[str] = None,
    feature_latency_ms: float = 1.0,
    prefer_fs_v1_path: bool = True,
) -> FilterResult:
    """Run VectorBT filter on candidates. Returns promoted+rejected lists.

    If VectorBT is unavailable, or if the requested screening scope requires the
    Rust engine and runtime proof is unavailable, the screen fails closed. Missing OHLCV
    data rejects candidates. VectorBT pilot/schema-proof rows are terminal
    screening rows only; they are not persisted as production promotion
    artifacts until HftBacktest/native hot-path replay eligibility exists.
    """
    gates = gates or PromotionGate()
    repo_root = repo_root or _REPO
    data_loader = data_loader or _default_data_loader
    grid = param_grid or DEFAULT_PARAM_GRID
    signal_computer = signal_computer or _default_signal_computer
    budget = _build_run_budget(
        candidates=candidates,
        grid=grid,
        screening_scope=screening_scope,
        max_total_trials=max_total_trials,
        run_budget=run_budget,
    )
    max_total_trials = budget.max_total_trials
    engine_meta = _screening_engine_metadata(screening_scope)
    rust_required = bool(engine_meta["rust_engine_required_for_scope"])
    rust_available = bool(engine_meta["rust_engine_available"])
    rust_runtime_proof = bool(engine_meta["vectorbt_engine_runtime_proof"])
    vectorbt_available = bool(engine_meta["vectorbt_available"])
    if rust_required and rust_available and not rust_runtime_proof:
        rust_runtime_proof = _establish_vectorbt_rust_runtime_proof()
        if rust_runtime_proof:
            engine_meta = _screening_engine_metadata(screening_scope)
    if rust_required and (not vectorbt_available or not rust_available or not rust_runtime_proof):
        stop_reason = (
            "rust_runtime_proof_missing_fail_closed"
            if vectorbt_available and rust_available and not rust_runtime_proof
            else "rust_engine_required_unavailable_fail_closed"
        )
        result = _new_filter_result(
            backend="vectorbt_rust_unavailable",
            run_id=f"rust_fail_closed_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}",
            candidates=candidates,
            grid=grid,
            trials_run=0,
            stop_reasons=[stop_reason],
            screening_scope=screening_scope,
            max_total_trials=max_total_trials,
            run_budget=budget,
        )
        for cand in candidates:
            row_id = _pretrial_rejection_id(cand, stop_reason)
            result.rejected.append(RejectedCandidate(
                candidate_id=row_id,
                hypothesis_id=cand.model_id,
                reject_reason=stop_reason,
                metric_values={
                    **_base_candidate_metric_values(cand),
                    "rust_engine_required_for_scope": True,
                    "rust_engine_available": rust_available,
                    "vectorbt_engine_runtime_proof": rust_runtime_proof,
                },
            ))
        return result

    budget_fail_closed = _run_budget_fail_closed_reason(candidates, budget)
    if budget_fail_closed is not None:
        stop_reason, metric_values = budget_fail_closed
        result = _new_filter_result(
            backend="run_budget_fail_closed",
            run_id=f"run_budget_fail_closed_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}",
            candidates=candidates,
            grid=grid,
            trials_run=0,
            stop_reasons=[stop_reason],
            screening_scope=screening_scope,
            max_total_trials=max_total_trials,
            run_budget=budget,
        )
        _append_candidate_budget_rejections(result, candidates, stop_reason, metric_values)
        return result

    fs_v1_ctx = None
    research_clock = _resolve_research_clock(candidates)
    screen_symbol = _resolve_screen_symbol(candidates, symbol)
    if prefer_fs_v1_path and candidates:
        from backtest_pipeline.src.fs_v1_screen_path import (
            build_fs_v1_signal_computer,
            ohlcv_from_feature_store,
            resolve_fs_v1_screen_context,
        )

        fs_v1_ctx = resolve_fs_v1_screen_context(
            repo_root=repo_root,
            event_id=event_id,
            symbol=screen_symbol,
            feature_store_root_override=feature_store_root,
            feature_latency_ms=feature_latency_ms,
        )

    if fs_v1_ctx is not None:
        ohlcv = ohlcv_from_feature_store(fs_v1_ctx.store)
        signal_computer = build_fs_v1_signal_computer(fs_v1_ctx)
    else:
        ohlcv = data_loader(event_id, repo_root)

    if ohlcv is None:
        logger.warning("No OHLCV data for %s — rejecting all candidates", event_id)
        result = _new_filter_result(
            backend="no_ohlcv_data",
            run_id=f"no_data_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}",
            candidates=candidates,
            grid=grid,
            trials_run=0,
            stop_reasons=["no_ohlcv_data"],
            screening_scope=screening_scope,
            max_total_trials=max_total_trials,
            run_budget=budget,
        )
        ignored_escape = os.environ.get("HFT3_ALLOW_UNFILTERED", "").lower() in ("1", "true")
        for cand in candidates:
            row_id = _pretrial_rejection_id(cand, "no_ohlcv_data")
            result.rejected.append(RejectedCandidate(
                candidate_id=row_id,
                hypothesis_id=cand.model_id,
                reject_reason="no_ohlcv_data",
                metric_values={
                    **_base_candidate_metric_values(cand),
                    **({"operator_escape_ignored": ignored_escape} if ignored_escape else {}),
                },
            ))
        return result

    result = _run_vectorbt_simulation(
        ohlcv,
        candidates,
        parsed,
        grid,
        repo_root,
        signal_computer,
        screening_scope=screening_scope,
        max_total_trials=max_total_trials,
        run_budget=budget,
    )
    if fs_v1_ctx is not None:
        _apply_fs_v1_screen_metadata(
            result,
            fs_v1_ctx,
            candidates,
            research_clock=research_clock,
            screening_scope=screening_scope,
        )
    promoted_out: List[PromotedCandidate] = []
    rejected_out: List[RejectedCandidate] = list(result.rejected)

    git_commit = result.code_commit or _resolve_git_commit()

    for prom in result.promoted:
        prom.git_commit = git_commit
        prom.config_path = str(
            repo_root / "packages" / "features_engine" / "config" / "model_registry.yaml"
        )
        prom.seed = 42
        prom.timestamp_utc = datetime.now(timezone.utc).isoformat()

        if (
            _normalise_screening_scope(screening_scope) == "pilot"
            and prom.vectorbt_results.get("gate_metric_authority")
            == "official_vectorbt_portfolio_stats"
        ):
            gate_pass, gate_evaluation = _evaluate_vbt2_pilot_stats_gate(prom, gates)
            prom.vectorbt_results["pilot_gate_evaluation"] = gate_evaluation
        else:
            gate_pass = gates.evaluate(prom)
        if gate_pass:
            prom.pass_reason = _VBT2_PILOT_SCREEN_PASS_REASON
            prom.in_sample_results["gate_pass"] = True
            if persist_promotions:
                logger.warning(
                    "Skipping promotion artifact persistence for replay-ineligible "
                    "VectorBT screening row %s",
                    prom.candidate_id,
                )
            promoted_out.append(prom)
        else:
            rejected_out.append(RejectedCandidate(
                candidate_id=prom.candidate_id,
                hypothesis_id=prom.hypothesis_id,
                reject_reason="promotion_gate_failed",
                metric_values={
                    **prom.vectorbt_results,
                    "parameter_values": dict(prom.param_values),
                    "param_values": dict(prom.param_values),
                },
            ))

    result.promoted = promoted_out
    result.rejected = rejected_out
    return result


def load_validation_path(cand: CandidateModel) -> Any:
    from backtest_pipeline.src.asset_class_routing import resolve_validation_path
    return resolve_validation_path(cand)
