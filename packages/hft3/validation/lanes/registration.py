"""Register all lane adapters with the LaneRegistry.

Importing this module populates the LaneRegistry singleton with all four
lanes. The unified certification runner and promotion gate depend on
this registration.
"""
from __future__ import annotations

from .adapters.cme_adapter import CMEBacktester, CMEConfig, load_cme_config
from .adapters.crypto_adapter import CryptoBacktester, CryptoConfig, load_crypto_config
from .adapters.equities_adapter import EquitiesBacktester, EquitiesConfig, load_equities_config
from .adapters.options_adapter import OptionsBacktester, OptionsConfig, load_options_config
from .lane import Lane
from .lane_registry import LaneRegistry, register_lane


def _cme_validator() -> "CMEBacktester":
    return CMEBacktester(load_cme_config())


def _crypto_validator() -> "CryptoBacktester":
    return CryptoBacktester(load_crypto_config())


def _equities_validator() -> "EquitiesBacktester":
    return EquitiesBacktester(load_equities_config())


def _options_validator() -> "OptionsBacktester":
    return OptionsBacktester(load_options_config())


def register_all_lanes() -> None:
    """Register all four lanes. Idempotent: safe to call multiple times."""
    reg = LaneRegistry.instance()
    if reg.get(Lane.CME_FUTURES) is None:
        register_lane(
            lane=Lane.CME_FUTURES,
            adapter_factory=lambda: CMEBacktester(load_cme_config()),
            config_loader=load_cme_config,
            validator=_cme_validator,
            test_paths=["tests/backtester_validation/fast", "tests/backtester_validation/full"],
            model_id_prefixes=(),
        )
    if reg.get(Lane.CRYPTO) is None:
        register_lane(
            lane=Lane.CRYPTO,
            adapter_factory=lambda: CryptoBacktester(load_crypto_config()),
            config_loader=load_crypto_config,
            validator=_crypto_validator,
            test_paths=["tests/test_crypto_lane"],
            model_id_prefixes=("CRYPTO_",),
        )
    if reg.get(Lane.EQUITIES) is None:
        register_lane(
            lane=Lane.EQUITIES,
            adapter_factory=lambda: EquitiesBacktester(load_equities_config()),
            config_loader=load_equities_config,
            validator=_equities_validator,
            test_paths=["tests/test_equities_lane"],
            model_id_prefixes=("EQUITY_", "LOW_FLOAT_"),
        )
    if reg.get(Lane.OPTIONS) is None:
        register_lane(
            lane=Lane.OPTIONS,
            adapter_factory=lambda: OptionsBacktester(load_options_config()),
            config_loader=load_options_config,
            validator=_options_validator,
            test_paths=["tests/test_options_lane"],
            model_id_prefixes=("OPTIONS_", "PARITY_"),
        )


# Auto-register on import so consumers can use the registry without an explicit call.
register_all_lanes()
