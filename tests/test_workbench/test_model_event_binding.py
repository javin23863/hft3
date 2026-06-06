"""Tests for per-model event binding."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[2]
BINDING = REPO / "apps" / "workbench" / "config" / "model_event_binding.yaml"


def test_binding_file_exists():
    assert BINDING.is_file()


def test_hyp_29_requires_flatten_contexts():
    raw = yaml.safe_load(BINDING.read_text(encoding="utf-8"))
    ctx = raw["hypothesis"]["END_OF_DAY_FORCED_FLATTEN_FLOW"]["required_event_contexts"]
    assert "PROP_FLATTEN_TOPSTEP" in ctx
    assert "CPI_TIGHT" not in ctx


def test_hyp_5_has_no_hardcoded_context_filter():
    raw = yaml.safe_load(BINDING.read_text(encoding="utf-8"))
    cfg = raw["hypothesis"]["SPREAD_BLOWOUT_RECOMPRESSION"]
    assert "default_macro_contexts" not in cfg
    assert "required_event_contexts" not in cfg


def test_pdf_model_5_options_lane():
    raw = yaml.safe_load(BINDING.read_text(encoding="utf-8"))
    cfg = raw["pdf"]["DEALER_HEDGING"]
    assert cfg.get("campaign_mode") == "options_lane"
    assert "options_chain" in cfg.get("required_datasets", [])
