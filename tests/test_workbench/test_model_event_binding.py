"""Tests for per-model event binding."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[2]
BINDING = REPO / "apps" / "workbench" / "config" / "model_event_binding.yaml"
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


def test_hyp_5_uses_macro_cpi_nfp_contexts():
    raw = yaml.safe_load(BINDING.read_text(encoding="utf-8"))
    cfg = raw["hypothesis"]["SPREAD_BLOWOUT_RECOMPRESSION"]
    assert cfg["required_event_contexts"] == ["CPI_TIGHT", "NFP_TIGHT"]


def test_pdf_model_5_options_lane():
    raw = yaml.safe_load(BINDING.read_text(encoding="utf-8"))
    cfg = raw["pdf"]["DEALER_HEDGING"]
    assert cfg.get("campaign_mode") == "options_lane"
    assert "options_chain" in cfg.get("required_datasets", [])
