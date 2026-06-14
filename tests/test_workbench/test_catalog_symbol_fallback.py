"""Tests for Databento symbol fallback in catalog backfill."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from workbench.src.data.catalog_backfill import resolve_download_symbol
from workbench.src.data.event_catalog import EventSpec, resolve_npz_for_event

REPO = Path(__file__).resolve().parents[2]


def _ev(**kwargs) -> EventSpec:
    defaults = dict(
        event_id="NFP_2018_01_05_TIGHT",
        event_type="NFP",
        release_date="2018-01-05",
        event_context="NFP_TIGHT",
        symbol="MES.v.0",
        npz_path=REPO / "data" / "npz" / "MES.v.0_NFP_2018_01_05_TIGHT_mbo.npz",
        npz_present=False,
        start_utc="2018-01-05T13:29:30",
        end_utc="2018-01-05T13:35:00",
        parsed_symbols=("MES.v.0", "ES.v.0", "NQ.v.0"),
    )
    defaults.update(kwargs)
    return EventSpec(**defaults)


def test_resolve_download_symbol_falls_back_to_es():
    client = MagicMock()
    sym_err = Exception("422 symbology_invalid_request")

    def get_cost(**kwargs):
        if kwargs["symbols"] == ["MES.v.0"]:
            raise sym_err
        if kwargs["symbols"] == ["ES.v.0"]:
            return 0.05
        raise sym_err

    client.client.metadata.get_cost.side_effect = get_cost
    sym, cost = resolve_download_symbol(client, _ev())
    assert sym == "ES.v.0"
    assert cost == 0.05


def test_resolve_download_symbol_uses_primary_when_available():
    client = MagicMock()
    client.client.metadata.get_cost.return_value = 0.024
    sym, cost = resolve_download_symbol(client, _ev())
    assert sym == "MES.v.0"
    assert cost == 0.024


def test_resolve_npz_for_event_detects_es_fallback_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.delenv("HFT3_NPZ_ROOT", raising=False)
    event_id = "CPI_2018_01_11_TIGHT"
    parsed = ("MES.v.0", "ES.v.0")
    es_path = tmp_path / "data" / "npz" / f"ES.v.0_{event_id}_mbo.npz"
    es_path.parent.mkdir(parents=True)
    es_path.write_bytes(b"npz")
    path, present, sym_used = resolve_npz_for_event(tmp_path, event_id, "MES.v.0", parsed)
    assert present
    assert sym_used == "ES.v.0"
    assert path == es_path
