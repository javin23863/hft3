"""Asset-class routing — determines correct validation path per asset class and data availability.

Rules from integration spec:
- CME futures with MBO NPZ → VectorBT filter → HftBacktest execution gate
- Crypto with normalized OHLCV → VectorBT filter → L2 proxy or L3 execution gate
- Equities with bar data → VectorBT filter → no_execution (mark)
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
    L2_DEPTH_VALIDATION = auto()  # Kraken WS aggregated book depth (not order-level)
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

CRYPTO_LANE_MODEL_PREFIXES = ("CRYPTO_", "BTC_", "ETH_")

# Crypto symbols that may have Binance L2 depth data
CRYPTO_BINANCE_L2_SYMBOLS = {"BTCUSDT", "ETHUSDT", "SOLUSDT"}

# Kraken spot symbols with WS book-depth replay NPZ (aggregated levels, not true MBO)
CRYPTO_KRAKEN_DEPTH_SYMBOLS = {"BTC/USD", "ETH/USD", "SOL/USD"}
CRYPTO_KRAKEN_L3_SYMBOLS = CRYPTO_KRAKEN_DEPTH_SYMBOLS  # deprecated alias

# Mapping from perp symbol (as used by hypotheses) to Binance L2 symbol
CRYPTO_PERP_TO_L2 = {
    "BTCUSDT": "BTCUSDT",
    "ETHUSDT": "ETHUSDT",
    "SOLUSDT": "SOLUSDT",
}

# Mapping from perp symbol to Kraken spot symbol for L2 depth replay
CRYPTO_PERP_TO_KRAKEN_SPOT = {
    "BTCUSDT": "BTC/USD",
    "ETHUSDT": "ETH/USD",
    "SOLUSDT": "SOL/USD",
}

# Mapping from perp symbol to Bitfinex symbol for true MBO (R0 raw book, public)
CRYPTO_PERP_TO_BITFINEX = {
    "BTCUSDT": "tBTCUSD",
    "ETHUSDT": "tETHUSD",
    "SOLUSDT": "tSOLUSD",
}

BITFINEX_MBO_SYMBOLS = set(CRYPTO_PERP_TO_BITFINEX.values())

# Mapping from perp symbol to Coinbase Exchange product id (requires WS auth since 2024)
CRYPTO_PERP_TO_COINBASE = {
    "BTCUSDT": "BTC-USD",
    "ETHUSDT": "ETH-USD",
    "SOLUSDT": "SOL-USD",
}

COINBASE_MBO_SYMBOLS = set(CRYPTO_PERP_TO_COINBASE.values())

EQUITIES_LANE_MODEL_PREFIXES = ("EQUITY_", "LOW_FLOAT_")
OPTIONS_LANE_MODEL_PREFIXES = ("OPTIONS_", "PARITY_")


def resolve_asset_class(candidate: CandidateModel) -> str:
    model_id = candidate.model_id.upper()
    for prefix in CRYPTO_LANE_MODEL_PREFIXES:
        if model_id.startswith(prefix):
            return "CRYPTO"
    for prefix in EQUITIES_LANE_MODEL_PREFIXES:
        if model_id.startswith(prefix):
            return "EQUITIES"
    for prefix in OPTIONS_LANE_MODEL_PREFIXES:
        if model_id.startswith(prefix):
            return "OPTIONS"
    if candidate.metadata.get("asset_class"):
        return candidate.metadata["asset_class"].upper()
    return "CME_FUTURES"


def resolve_symbol(candidate: CandidateModel) -> str:
    return candidate.metadata.get("symbol", "MES")


def _crypto_l2_npz_path(data_catalog_root: Path, symbol: str) -> Path:
    return data_catalog_root / "data" / "replay" / "hftbacktest" / "crypto" / "binance" / symbol.lower()


def _crypto_kraken_depth_npz_path(data_catalog_root: Path, symbol: str) -> Path:
    return data_catalog_root / "data" / "replay" / "hftbacktest" / "crypto" / "kraken" / symbol.replace("/", "_")


def _crypto_bitfinex_mbo_npz_path(data_catalog_root: Path, routing_symbol: str) -> Path:
    safe = routing_symbol.replace("-", "_").replace("/", "_")
    return data_catalog_root / "data" / "replay" / "hftbacktest" / "crypto" / "bitfinex" / safe


def _crypto_coinbase_mbo_npz_path(data_catalog_root: Path, product_id: str) -> Path:
    safe = product_id.replace("-", "_")
    return data_catalog_root / "data" / "replay" / "hftbacktest" / "crypto" / "coinbase" / safe


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


def _crypto_l3_mbo_npz_exists(data_catalog_root: Optional[Path], routing_symbol: str) -> bool:
    if not data_catalog_root:
        return False
    npz_dir = _crypto_bitfinex_mbo_npz_path(data_catalog_root, routing_symbol)
    for npz in npz_dir.glob("*.npz"):
        data_class = _read_replay_meta_data_class(npz)
        if data_class == "L3_MBO" or npz.name.endswith("_mbo.npz"):
            return True
    return False


def _crypto_mbo_npz_exists(data_catalog_root: Optional[Path], product_id: str) -> bool:
    if not data_catalog_root:
        return False
    npz_dir = _crypto_coinbase_mbo_npz_path(data_catalog_root, product_id)
    for npz in npz_dir.glob("*.npz"):
        data_class = _read_replay_meta_data_class(npz)
        if data_class == "L3_MBO" or npz.name.endswith("_mbo.npz"):
            return True
    return False


_crypto_l3_npz_path = _crypto_kraken_depth_npz_path  # deprecated alias


def _crypto_l2_npz_exists(data_catalog_root: Optional[Path], symbol: str) -> bool:
    if not data_catalog_root:
        return False
    npz_dir = _crypto_l2_npz_path(data_catalog_root, symbol)
    return npz_dir.exists() and any(npz_dir.glob("*.npz"))


def _crypto_kraken_depth_npz_exists(data_catalog_root: Optional[Path], symbol: str) -> bool:
    if not data_catalog_root:
        return False
    npz_dir = _crypto_kraken_depth_npz_path(data_catalog_root, symbol)
    return npz_dir.exists() and any(npz_dir.glob("*.npz"))


_crypto_l3_npz_exists = _crypto_kraken_depth_npz_exists  # deprecated alias


# Bitfinex routing dir uses BTC_USD style keys
CRYPTO_PERP_TO_BITFINEX_ROUTING = {
    "BTCUSDT": "BTC_USD",
    "ETHUSDT": "ETH_USD",
    "SOLUSDT": "SOL_USD",
}


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

    elif asset_class == "CRYPTO":
        has_order_book = False
        has_tick = False
        perp_symbol = symbol.upper()
        l2_sym = CRYPTO_PERP_TO_L2.get(perp_symbol)
        bitfinex_sym = CRYPTO_PERP_TO_BITFINEX.get(perp_symbol)
        bitfinex_route = CRYPTO_PERP_TO_BITFINEX_ROUTING.get(perp_symbol)
        kraken_sym = CRYPTO_PERP_TO_KRAKEN_SPOT.get(perp_symbol)
        coinbase_sym = CRYPTO_PERP_TO_COINBASE.get(perp_symbol)

        if bitfinex_route and _crypto_l3_mbo_npz_exists(data_catalog_root, bitfinex_route):
            has_order_book = True
            has_tick = True
            exec_cap = ExecutionCapability.L3_VALIDATED
            notes.append(
                f"Bitfinex R0 MBO NPZ found for {bitfinex_route}; "
                f"{EXEC_CLASS_L3_VALIDATED} (order-level, native order IDs)"
            )
        elif coinbase_sym and _crypto_mbo_npz_exists(data_catalog_root, coinbase_sym):
            has_order_book = True
            has_tick = True
            exec_cap = ExecutionCapability.L3_VALIDATED
            notes.append(
                f"Coinbase Exchange MBO NPZ found for {coinbase_sym}; "
                f"{EXEC_CLASS_L3_VALIDATED} (order-level full channel)"
            )
        elif kraken_sym and _crypto_kraken_depth_npz_exists(data_catalog_root, kraken_sym):
            has_order_book = True
            has_tick = True
            exec_cap = ExecutionCapability.L2_DEPTH_VALIDATION
            notes.append(
                f"Kraken WS book-depth replay NPZ found for {kraken_sym}; "
                f"{EXEC_CLASS_L2_DEPTH_VALIDATED} (aggregated depth, not order-level MBO)"
            )
        elif l2_sym and _crypto_l2_npz_exists(data_catalog_root, l2_sym):
            has_order_book = True
            has_tick = True
            exec_cap = ExecutionCapability.L2_PROXY_VALIDATION
            notes.append(f"Binance L2 depth NPZ found for {l2_sym}; L2_PROXY_VALIDATION")
        else:
            exec_cap = ExecutionCapability.NO_EXECUTION_VALIDATION
            if data_catalog_root and (data_catalog_root / "data" / "crypto" / "normalized").exists():
                notes.append("Crypto: normalized data exists but no L2/L3 NPZ; marking NO_EXECUTION_VALIDATION")
            else:
                notes.append("Crypto: no order-book NPZ; marking NO_EXECUTION_VALIDATION")

    elif asset_class == "EQUITIES":
        exec_cap = ExecutionCapability.NO_EXECUTION_VALIDATION
        notes.append("Equities: no order-book NPZ; marking NO_EXECUTION_VALIDATION")

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
