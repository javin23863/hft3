"""Tests for per-model event binding."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[2]
BINDING = REPO / "workbench" / "config" / "model_event_binding.yaml"


def test_binding_file_exists():
    assert BINDING.is_file()


def test_hyp_29_requires_flatten_contexts():
    raw = yaml.safe_load(BINDING.read_text(encoding="utf-8"))
    ctx = raw["hypothesis"]["HYP_29"]["required_event_contexts"]
    assert "PROP_FLATTEN_TOPSTEP" in ctx
    assert "CPI_TIGHT" not in ctx


def test_hyp_5_uses_macro_defaults():
    raw = yaml.safe_load(BINDING.read_text(encoding="utf-8"))
    ctx = raw["hypothesis"]["HYP_5"]["default_macro_contexts"]
    assert "CPI_TIGHT" in ctx


def test_pdf_model_5_options_lane():
    raw = yaml.safe_load(BINDING.read_text(encoding="utf-8"))
    cfg = raw["pdf"]["PDF_MODEL_5"]
    assert cfg.get("campaign_mode") == "options_lane"
    assert "options_chain" in cfg.get("required_datasets", [])
