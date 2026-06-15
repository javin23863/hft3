"""Tests for per-model event binding."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
import yaml

from economic_event_universe.registry import default_cme_symbols

REPO = Path(__file__).resolve().parents[2]
BINDING = REPO / "apps" / "workbench" / "config" / "model_event_binding.yaml"
GENERATOR = REPO / "apps" / "workbench" / "scripts" / "generate_model_event_binding.py"
SCRIPT_PATHS = [
    REPO / "scripts" / "run_pipeline.py",
    REPO / "scripts" / "run_full_pipeline_gate.py",
    REPO / "scripts" / "run_hybrid_pipeline_gate.py",
    REPO / "scripts" / "run_pdf_hybrid_replay.py",
    REPO / "scripts" / "run_pdf_hybrid_ablation.py",
    REPO / "scripts" / "run_offline_pipeline.py",
    REPO / "scripts" / "run_single_hyp_backtest.py",
    REPO / "scripts" / "workbench_pipeline_trial.py",
    REPO / "scripts" / "run_l3_cross_asset_event_replay.sh",
    REPO / "scripts" / "run_l3_cross_asset_event_ablation.sh",
    REPO / "scripts" / "run_replay_execution_parity_proof.sh",
]


def _generated_binding_doc() -> dict:
    spec = importlib.util.spec_from_file_location("generate_model_event_binding", GENERATOR)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module.build_doc()


def test_binding_file_exists():
    assert BINDING.is_file()


def test_hyp_29_requires_flatten_contexts():
    raw = yaml.safe_load(BINDING.read_text(encoding="utf-8"))
    ctx = raw["hypothesis"]["END_OF_DAY_FORCED_FLATTEN_FLOW"]["required_event_contexts"]
    assert "PROP_FLATTEN_TOPSTEP" in ctx
    assert "CPI_TIGHT" not in ctx


def test_no_model_uses_default_macro_contexts():
    raw = yaml.safe_load(BINDING.read_text(encoding="utf-8"))
    for section in ("hypothesis", "pdf"):
        for model_id, cfg in raw[section].items():
            assert "default_macro_contexts" not in cfg, model_id


def test_operator_scripts_do_not_default_to_one_macro_event():
    forbidden = (
        'default="CPI_2024_09_11_TIGHT"',
        'DEFAULT_EVENT = "CPI_2024_09_11_TIGHT"',
        'DEFAULT_EVENT_ID = "CPI_2024_09_11_TIGHT"',
        ":-CPI_2024_09_11_TIGHT",
    )
    for path in SCRIPT_PATHS:
        text = path.read_text(encoding="utf-8")
        for needle in forbidden:
            assert needle not in text, f"{path.relative_to(REPO)} still has {needle}"


def test_hyp_5_uses_catalog_context_policy():
    raw = yaml.safe_load(BINDING.read_text(encoding="utf-8"))
    cfg = raw["hypothesis"]["SPREAD_BLOWOUT_RECOMPRESSION"]
    assert cfg["event_context_policy"] == "catalog_all_contexts"


def test_pdf_model_5_options_lane():
    # DEALER_HEDGING (PDF_MODEL_5) runs in the options lane.
    raw = yaml.safe_load(BINDING.read_text(encoding="utf-8"))
    cfg = raw["pdf"]["DEALER_HEDGING"]
    assert cfg.get("campaign_mode") == "options_lane"
    assert "options_chain" in cfg.get("required_datasets", [])


def test_generator_adds_default_research_symbol_and_canonical_universe():
    raw = _generated_binding_doc()
    assert "packages/economic_event_universe/config/event_universe.yaml" in raw["authority"]
    cfg = raw["hypothesis"]["SPREAD_BLOWOUT_RECOMPRESSION"]
    assert cfg["research_symbol"] == "MES.v.0"
    assert cfg["symbol_universe"] == list(default_cme_symbols())
    assert cfg["symbol_source"] == "economic_event_universe.defaults.symbol_universe_default"


def test_generator_adds_model_specific_research_symbols():
    raw = _generated_binding_doc()
    assert raw["hypothesis"]["NQ_MNQ_LEAD_LAG"]["research_symbol"] == "MNQ.v.0"
    assert raw["hypothesis"]["ZN_ZB_ES_NQ_MACRO_IMPULSE"]["research_symbol"] == "ZN.v.0"


def test_generator_keeps_pdf_structural_models_without_research_symbol():
    raw = _generated_binding_doc()
    for model_id in ("BOOK_PRESSURE", "TREASURY_CTD", "DOW_YM_INDEX", "DEALER_HEDGING"):
        cfg = raw["pdf"][model_id]
        assert "research_symbol" not in cfg
        assert "symbol_universe" not in cfg
    assert raw["pdf"]["DEALER_HEDGING"].get("campaign_mode") == "options_lane"
