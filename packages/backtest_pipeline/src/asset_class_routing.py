"""Asset-class routing — determines correct validation path per asset class and data availability.

Rules from integration spec:
- CME futures with MBO NPZ → VectorBT filter → HftBacktest execution gate
- Options lane → VectorBT filter → no_execution (mark)
- Any lane missing tick/book data → no_execution (mark, not pretend-pass)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, auto
from pathlib import Path
from typing import List, Optional

from research_pipeline.types import CandidateModel


class ExecutionCapability(Enum):
    FULL_EXECUTION = auto()
    L3_VALIDATED = auto()  # True order-level MBO replay only
    L2_DEPTH_VALIDATION = auto()  # Aggregated book depth (not order-level)
    L2_PROXY_VALIDATION = auto()
    NO_EXECUTION_VALIDATION = auto()


# Human-readable execution_classification strings (validation reports)
EXEC_CLASS_L3_VALIDATED = "L3_VALIDATED"
EXEC_CLASS_L2_DEPTH_VALIDATED = "L2_DEPTH_VALIDATED"
EXEC_CLASS_L2_PROXY_ONLY = "L2_PROXY_ONLY"


@dataclass
class ValidationPath:
    candidate: CandidateModel
    asset_class: str
    symbol: str
    has_order_book_data: bool
    has_tick_data: bool
    execution_capability: ExecutionCapability
    route_to_vectorbt: bool
    route_to_hftbacktest: bool
    notes: List[str] = field(default_factory=list)


SUPPORTED_CME_SYMBOLS = {
    "MES", "ES", "NQ", "MNQ", "ZN", "ZB", "YM", "CL", "GC", "SI", "HG",
    "MES.v.0", "ES.v.0", "NQ.v.0", "MNQ.v.0", "ZN.v.0", "ZB.v.0",
}

OPTIONS_LANE_MODEL_PREFIXES = ("OPTIONS_", "PARITY_")


def resolve_asset_class(candidate: CandidateModel) -> str:
    model_id = candidate.model_id.upper()
    for prefix in OPTIONS_LANE_MODEL_PREFIXES:
        if model_id.startswith(prefix):
            return "OPTIONS"
    if candidate.metadata.get("asset_class"):
        return candidate.metadata["asset_class"].upper()
    return "CME_FUTURES"


def resolve_symbol(candidate: CandidateModel) -> str:
    # No default symbol: a candidate without one routes to
    # NO_EXECUTION_VALIDATION downstream instead of silently validating as MES.
    return str(candidate.metadata.get("symbol") or "")


def _read_replay_meta_data_class(npz_path: Path) -> str | None:
    meta_path = npz_path.with_name(npz_path.stem + ".meta.json")
    if not meta_path.is_file():
        return None
    try:
        import json

        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        return str(meta.get("data_class") or "")
    except (OSError, ValueError, TypeError):
        return None


def resolve_validation_path(
    candidate: CandidateModel,
    data_catalog_root: Optional[Path] = None,
) -> ValidationPath:
    asset_class = resolve_asset_class(candidate)
    symbol = resolve_symbol(candidate)
    has_order_book = False
    has_tick = False
    notes: List[str] = []

    if asset_class == "CME_FUTURES":
        if symbol in SUPPORTED_CME_SYMBOLS or symbol.split(".")[0] in SUPPORTED_CME_SYMBOLS:
            has_order_book = True
            has_tick = True
        else:
            notes.append(f"Unrecognized CME symbol: {symbol}")
        if has_order_book and has_tick:
            exec_cap = ExecutionCapability.FULL_EXECUTION
        else:
            exec_cap = ExecutionCapability.NO_EXECUTION_VALIDATION
            notes.append(f"No tick/order-book data for {symbol}")

    elif asset_class == "OPTIONS":
        exec_cap = ExecutionCapability.NO_EXECUTION_VALIDATION
        notes.append("Options: no order-book NPZ; marking NO_EXECUTION_VALIDATION")

    else:
        exec_cap = ExecutionCapability.NO_EXECUTION_VALIDATION
        notes.append(f"Unknown asset class {asset_class}; NO_EXECUTION_VALIDATION")

    # VectorBT always runs — OHLCV data is universal. HftBacktest routing is conditional.
    route_to_vbt = True
    route_to_hft = exec_cap in (
        ExecutionCapability.FULL_EXECUTION,
        ExecutionCapability.L3_VALIDATED,
        ExecutionCapability.L2_DEPTH_VALIDATION,
        ExecutionCapability.L2_PROXY_VALIDATION,
    )

    return ValidationPath(
        candidate=candidate,
        asset_class=asset_class,
        symbol=symbol,
        has_order_book_data=has_order_book,
        has_tick_data=has_tick,
        execution_capability=exec_cap,
        route_to_vectorbt=route_to_vbt,
        route_to_hftbacktest=route_to_hft,
        notes=notes,
    )
