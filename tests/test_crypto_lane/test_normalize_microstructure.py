"""Normalize derives microstructure_quality_flag column."""
from __future__ import annotations

from crypto_lane.src.ingest.normalize import _microstructure_quality_for_range
from datetime import date


def test_microstructure_quality_flag_real_vs_synthetic(monkeypatch):
    from crypto_lane.src.ingest import normalize as norm_mod

    def fake_classify(day: date, symbol: str | None = None) -> str:
        if day == date(2024, 1, 1):
            return "b2_real"
        if day == date(2024, 1, 2):
            return "synthetic"
        return "missing"

    monkeypatch.setattr(norm_mod, "classify_bookticker_day", fake_classify)
    assert _microstructure_quality_for_range("BTCUSDT", date(2024, 1, 1), date(2024, 1, 1)) == 0
    assert _microstructure_quality_for_range("BTCUSDT", date(2024, 1, 2), date(2024, 1, 2)) == 3
    assert _microstructure_quality_for_range("BTCUSDT", date(2024, 1, 3), date(2024, 1, 3)) == 2
