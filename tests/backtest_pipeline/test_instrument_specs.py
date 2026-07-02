"""Instrument execution specs: exact CME contract math, fail-closed lookups,
and parity with the workbench hot-memory universe registry."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from backtest_pipeline.src.fee_model import FeeModel, FeeModelError
from backtest_pipeline.src.instrument_specs import (
    INSTRUMENT_SPECS,
    InstrumentSpecError,
    normalize_product,
    resolve_instrument_spec,
)

REPO = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize(
    ("symbol", "tick_size", "tick_value", "multiplier"),
    [
        ("ES", 0.25, 12.50, 50.0),
        ("NQ", 0.25, 5.00, 20.0),
        ("RTY", 0.10, 5.00, 50.0),
        ("YM", 1.00, 5.00, 5.0),
        ("MES", 0.25, 1.25, 5.0),
        ("MNQ", 0.25, 0.50, 2.0),
        ("M2K", 0.10, 0.50, 5.0),
        ("MYM", 1.00, 0.50, 0.5),
        ("ZT", 0.00390625, 7.8125, 2000.0),
        ("ZF", 0.0078125, 7.8125, 1000.0),
        ("ZN", 0.015625, 15.625, 1000.0),
        ("ZB", 0.03125, 31.25, 1000.0),
        ("UB", 0.03125, 31.25, 1000.0),
    ],
)
def test_spec_values_are_exact_cme_contract_math(symbol, tick_size, tick_value, multiplier) -> None:
    spec = INSTRUMENT_SPECS[symbol]
    assert spec.tick_size == tick_size
    assert spec.tick_value == tick_value
    assert spec.contract_multiplier == multiplier
    assert spec.tick_size * spec.contract_multiplier == pytest.approx(spec.tick_value)


def test_micro_vs_mini_multipliers_differ_ten_fold() -> None:
    assert INSTRUMENT_SPECS["ES"].contract_multiplier == 10 * INSTRUMENT_SPECS["MES"].contract_multiplier
    assert INSTRUMENT_SPECS["NQ"].contract_multiplier == 10 * INSTRUMENT_SPECS["MNQ"].contract_multiplier
    assert INSTRUMENT_SPECS["RTY"].contract_multiplier == 10 * INSTRUMENT_SPECS["M2K"].contract_multiplier
    assert INSTRUMENT_SPECS["YM"].contract_multiplier == 10 * INSTRUMENT_SPECS["MYM"].contract_multiplier


def test_resolve_normalizes_research_symbols() -> None:
    assert resolve_instrument_spec("MES.v.0").symbol == "MES"
    assert resolve_instrument_spec("mes.c.0").symbol == "MES"
    assert normalize_product("ZN.v.0") == "ZN"


def test_unknown_or_empty_symbol_fails_closed() -> None:
    with pytest.raises(InstrumentSpecError, match="instrument_spec_missing:6E"):
        resolve_instrument_spec("6E.v.0")
    with pytest.raises(InstrumentSpecError, match="instrument_spec_missing:empty_symbol"):
        resolve_instrument_spec("")


def test_fee_model_micro_vs_mini_pairs() -> None:
    # exchange-only (no broker/NFA) per side
    assert FeeModel("MES", "non_member", 0.0, 0.0).get_fee_per_contract() == 0.25
    assert FeeModel("ES", "non_member", 0.0, 0.0).get_fee_per_contract() == 1.25
    assert FeeModel("M2K", "non_member", 0.0, 0.0).get_fee_per_contract() == 0.25
    assert FeeModel("RTY", "non_member", 0.0, 0.0).get_fee_per_contract() == 1.25
    assert FeeModel("MYM", "non_member", 0.0, 0.0).get_fee_per_contract() == 0.25
    assert FeeModel("YM", "non_member", 0.0, 0.0).get_fee_per_contract() == 1.25


def test_fee_model_rates_products_have_own_schedule() -> None:
    assert FeeModel("ZN", "non_member", 0.0, 0.0).get_fee_per_contract() == 0.80
    assert FeeModel("ZB", "member", 0.0, 0.0).get_fee_per_contract() == 0.40
    assert FeeModel("SR3", "non_member", 0.0, 0.0).get_fee_per_contract() == 0.50


def test_fee_model_unknown_product_fails_closed() -> None:
    with pytest.raises(FeeModelError, match="fee_model_unknown_product:CL"):
        FeeModel("CL").get_fee_per_contract()
    with pytest.raises(FeeModelError, match="fee_model_unknown_tier"):
        FeeModel("ES", tier="vip").get_fee_per_contract()
    with pytest.raises(FeeModelError, match="fee_model_unknown_product"):
        FeeModel("UNKNOWN").calculate_trade_cost(1, is_market_order=True, slippage_ticks=1)


def test_fee_model_tick_values_come_from_instrument_specs() -> None:
    assert FeeModel.TICK_VALUES["ZN"] == 15.625
    assert FeeModel.TICK_VALUES["NQ"] == 5.00
    cost = FeeModel("ZN", "non_member", 0.0, 0.0).calculate_trade_cost(
        1, is_market_order=True, slippage_ticks=2
    )
    assert cost == pytest.approx(0.80 + 2 * 15.625)


def test_lake_product_metadata_agrees_with_instrument_specs() -> None:
    raw = yaml.safe_load(
        (REPO / "config" / "hftbacktest" / "cme_lake_product_metadata.yaml").read_text(
            encoding="utf-8"
        )
    )
    checked = 0
    for research_symbol, entry in (raw.get("symbols") or {}).items():
        if not entry.get("tradable"):
            continue
        product = str(entry.get("canonical_internal_symbol") or research_symbol.split(".")[0])
        spec = INSTRUMENT_SPECS.get(product)
        if spec is None:
            continue
        assert float(entry["tick_size"]) == pytest.approx(spec.tick_size), product
        assert float(entry["tick_value"]) == pytest.approx(spec.tick_value), product
        assert float(entry["contract_size"]) == pytest.approx(spec.contract_multiplier), product
        checked += 1
    assert checked >= 13


def test_registry_yaml_agrees_with_instrument_specs() -> None:
    raw = yaml.safe_load(
        (REPO / "apps" / "workbench" / "config" / "hot_memory_universe.yaml").read_text(
            encoding="utf-8"
        )
    )
    defaults = raw.get("defaults") or {}
    for entry in raw.get("instruments") or []:
        symbol = str(entry["canonical_internal_symbol"])
        spec = INSTRUMENT_SPECS.get(symbol)
        if spec is None:
            continue
        merged = {**defaults, **entry}
        assert float(merged["tick_size"]) == pytest.approx(spec.tick_size), symbol
        assert float(merged["tick_value"]) == pytest.approx(spec.tick_value), symbol
        assert float(merged["contract_multiplier"]) == pytest.approx(
            spec.contract_multiplier
        ), symbol
