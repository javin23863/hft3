"""HftBacktest campaign integration tests (require hftbacktest)."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

pytest.importorskip("hftbacktest")
from hftbacktest.types import (
    ADD_ORDER_EVENT,
    BUY_EVENT,
    CANCEL_ORDER_EVENT,
    EXCH_EVENT,
    LOCAL_EVENT,
    SELL_EVENT,
    event_dtype,
)

from backtest_pipeline.src.hft_campaign.manifest import ManifestGenerationConfig, generate_scenario_manifest
from backtest_pipeline.src.hft_campaign.prepared_data import prepare_replay_data, validate_prepared_data_dir
from backtest_pipeline.src.replay_npz_fixture import build_minimal_mbo_npz
from apps.cockpit.backend.tests.test_cockpit import _write_screening_artifact
from tests.backtest_pipeline.hft_campaign.test_hft_campaign_core import _write_latency_queue


def test_prepared_data_content_addressed(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("HFT3_ARTIFACTS_ROOT", str(tmp_path / "artifacts"))
    npz = tmp_path / "src.npz"
    build_minimal_mbo_npz(npz)
    first = prepare_replay_data(source_npz_path=npz, repo_root=tmp_path, symbol="MES", event_id="E1")
    second = prepare_replay_data(source_npz_path=npz, repo_root=tmp_path, symbol="MES", event_id="E1")
    assert first.prepared_data_hash == second.prepared_data_hash
    assert validate_prepared_data_dir(first.path.parent, expected_hash=first.prepared_data_hash) == []


def test_l3_orphan_filter_accounting(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("HFT3_ARTIFACTS_ROOT", str(tmp_path / "artifacts"))
    rows = [
        (CANCEL_ORDER_EVENT | BUY_EVENT | EXCH_EVENT | LOCAL_EVENT, 1, 1, 100.0, 1.0, 999, 0, 0.0),
        (ADD_ORDER_EVENT | BUY_EVENT | EXCH_EVENT | LOCAL_EVENT, 2, 2, 100.0, 1.0, 1000, 0, 0.0),
    ]
    npz = tmp_path / "orphan.npz"
    np.savez_compressed(npz, data=np.array(rows, dtype=event_dtype))
    prepared = prepare_replay_data(
        source_npz_path=npz,
        repo_root=tmp_path,
        symbol="MES",
        event_id="ORPHAN",
    )
    assert prepared.original_row_count == 2
    assert prepared.final_row_count == 1
    assert prepared.removed_orphan_count >= 1


def test_manifest_select_all_replay_eligible(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("HFT3_ARTIFACTS_ROOT", str(tmp_path / "artifacts"))
    screening = _write_screening_artifact(
        tmp_path,
        "run1",
        "2026-01-01T00:00:00Z",
        replay_eligible=True,
        surface_defined=True,
    )
    npz = tmp_path / "data.npz"
    build_minimal_mbo_npz(npz)
    latency, queue = _write_latency_queue(tmp_path)
    cfg = ManifestGenerationConfig(
        screening_artifact_path=screening,
        repo_root=tmp_path,
        event_id="E1",
        source_npz_path=npz,
        latency_model_path=latency,
        fill_queue_model_path=queue,
        select_all_replay_eligible=True,
    )
    scenarios, _reasons = generate_scenario_manifest(cfg)
    assert len(scenarios) == 1
    assert scenarios[0].replay_tier == "stage2_individual"
