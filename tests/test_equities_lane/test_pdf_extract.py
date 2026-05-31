"""PDF authority smoke test."""
from __future__ import annotations

from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
PDF = REPO / "docs" / "references" / "low_float_momentum_anomaly_research_pack.pdf"


def test_extract_text_low_float_pdf():
    if not PDF.is_file():
        pytest.skip("low_float PDF not in repo")
    from research_pipeline.document_ingestion import extract_text

    text = extract_text(PDF)
    assert "low-float" in text.lower() or "Low-Float" in text
    assert len(text) > 500
