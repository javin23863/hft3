from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import importlib.util
import json
from pathlib import Path

import numpy as np

from backtest_pipeline.src.hftbacktest_only_campaign_manifest import build_campaign_manifest_rows


def _load_prepare_module():
    repo = Path(__file__).resolve().parents[2]
    script = repo / "scripts" / "prepare_hftbacktest_only_from_lake_manifest.py"
    spec = importlib.util.spec_from_file_location("prepare_hbt_from_lake_manifest_cli", script)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _write_source_npz(path: Path) -> Path:
    dtype = np.dtype(
        [
            ("ev", np.uint32),
            ("exch_ts", np.int64),
            ("local_ts", np.int64),
            ("px", np.float64),
            ("qty", np.float64),
            ("order_id", np.uint64),
            ("ival", np.int64),
            ("fval", np.float64),
        ]
    )
    base_ns = int(datetime(2024, 9, 11, 12, 29, tzinfo=timezone.utc).timestamp() * 1_000_000_000)
    rows = np.zeros(3, dtype=dtype)
    for index, offset_ns in enumerate((0, 1_000_000_000, 1_000_000_100)):
        rows[index]["ev"] = 10 | (1 << 8) | (1 << 9)
        rows[index]["exch_ts"] = base_ns + offset_ns
        rows[index]["local_ts"] = base_ns + offset_ns + 100
        rows[index]["px"] = 17000.0 + index * 0.25
        rows[index]["qty"] = 1.0
        rows[index]["order_id"] = 1000 + index
    np.savez_compressed(path, data=rows)
    return path


def _write_registry(path: Path) -> Path:
    path.write_text(
        """
models:
  SPREAD_BLOWOUT_RECOMPRESSION:
    kind: hypothesis
    legacy_id: HYP_5
    display_name: Spread blowout/recompression
    class: SpreadBlowoutRecompression
""".lstrip(),
        encoding="utf-8",
    )
    return path


def test_prepare_from_lake_manifest_is_symbol_manifest_driven_and_blocks_missing_authority(
    tmp_path: Path,
) -> None:
    module = _load_prepare_module()
    nq_source = _write_source_npz(tmp_path / "NQ.v.0_CPI_2024_09_11_TIGHT_mbo.npz")
    zn_source = _write_source_npz(tmp_path / "ZN.v.0_CPI_2024_09_11_TIGHT_mbo.npz")
    lake_manifest = tmp_path / "manifest.json"
    lake_rows = [
        {
            "event_id": "CPI_2024_09_11_TIGHT",
            "symbol": "NQ.v.0",
            "npz_path": str(nq_source),
            "event_count": 3,
            "sha256": hashlib.sha256(nq_source.read_bytes()).hexdigest(),
        },
        {
            "event_id": "CPI_2024_09_11_TIGHT",
            "symbol": "ZN.v.0",
            "npz_path": str(zn_source),
            "event_count": 3,
            "sha256": hashlib.sha256(zn_source.read_bytes()).hexdigest(),
        },
    ]
    lake_manifest.write_text(json.dumps(lake_rows), encoding="utf-8")
    instrument_registry = tmp_path / "hot_memory_universe.yaml"
    instrument_registry.write_text(
        """
defaults:
  instrument_type: futures
  tradable: true
  order_book_available: true
  tick_size: 0.25
  contract_multiplier: 50
instruments:
  - canonical_internal_symbol: NQ
    research_symbol: NQ.v.0
    data_vendor_symbol: NQ.c.0
    exchange: CME
    venue: GLBX
    hot_memory_tier: HOT_EXECUTABLE
    contract: NQ.v.0
    tick_size: 0.25
    lot_size: 1.0
    contract_multiplier: 20
  - canonical_internal_symbol: ZN
    research_symbol: ZN.v.0
    data_vendor_symbol: ZN.c.0
    exchange: CBOT
    venue: GLBX
    hot_memory_tier: HOT_EXECUTABLE
""".lstrip(),
        encoding="utf-8",
    )

    summary = module.prepare_from_lake_manifest(
        lake_manifest=lake_manifest,
        out_root=tmp_path / "hbt",
        instrument_registry=instrument_registry,
        warmup_seconds=1,
    )

    assert summary["row_count"] == 2
    assert summary["prepared_count"] == 1
    assert summary["blocked_count"] == 1
    assert summary["replay_mode"] == "full_l3_event_replay"
    assert summary["metadata_policy"] == "explicit_per_symbol_contract_tick_lot_contract_required"
    assert summary["blocker_counts"] == {"authority_missing": 1}

    manifests = sorted((tmp_path / "hbt" / "prepared").glob("*/*/*_manifest.json"))
    assert len(manifests) == 2
    payloads = [json.loads(path.read_text(encoding="utf-8")) for path in manifests]
    prepared = next(payload for payload in payloads if payload["symbol"] == "NQ.v.0")
    blocked = next(payload for payload in payloads if payload["symbol"] == "ZN.v.0")
    assert prepared["status"] == "hbt_full_l3_event_replay_prepared"
    assert prepared["contract"] == "NQ.v.0"
    assert prepared["contract_size"] == 20.0
    assert prepared["metadata_policy"] == "explicit_per_symbol_contract_tick_lot_contract_required"
    assert blocked["status"] == "blocked"
    assert blocked["blocker_code"] == "authority_missing"
    assert "instrument_metadata_missing" in blocked["blocker_detail"]

    campaign_rows = build_campaign_manifest_rows(
        campaign_id="hbt_campaign_test",
        prepared_root=tmp_path / "hbt" / "prepared",
        registry_path=_write_registry(tmp_path / "model_registry.yaml"),
        adapter_status_by_model={"SPREAD_BLOWOUT_RECOMPRESSION": "available"},
    )

    assert {row["symbol"] for row in campaign_rows} == {"NQ.v.0", "ZN.v.0"}
    assert {row["admissibility_status"] for row in campaign_rows} == {
        "admissible",
        "authority_missing",
    }
    assert "model_" + "rejected" not in json.dumps(campaign_rows)


def test_default_product_metadata_covers_lake_symbols_and_blocks_vix_options() -> None:
    module = _load_prepare_module()
    metadata = module._load_instrument_metadata(module.DEFAULT_PRODUCT_METADATA)
    expected_lake_symbols = {
        "CL.v.0",
        "ES.v.0",
        "GC.v.0",
        "M2K.v.0",
        "MCL.v.0",
        "MES.v.0",
        "MGC.v.0",
        "MNQ.v.0",
        "MYM.v.0",
        "NG.v.0",
        "NQ.v.0",
        "RTY.v.0",
        "SR3.v.0",
        "UB.v.0",
        "VIX.OPT",
        "YM.v.0",
        "ZB.v.0",
        "ZF.v.0",
        "ZN.v.0",
        "ZQ.v.0",
        "ZT.v.0",
    }

    assert expected_lake_symbols <= set(metadata)
    assert "NQ" not in metadata
    assert "ZN" not in metadata
    assert metadata["CL.v.0"]["tick_size"] == 0.01
    assert metadata["GC.v.0"]["contract_size"] == 100.0
    assert metadata["ZN.v.0"]["tick_size"] == 0.015625
    executable_symbols = expected_lake_symbols - {"VIX.OPT"}
    assert all(metadata[symbol].get("authority_refs") for symbol in executable_symbols)
    assert metadata["VIX.OPT"]["tradable"] is False
    assert metadata["VIX.OPT"]["order_book_available"] is False
    assert metadata["VIX.OPT"]["blocker_code"] == "authority_missing:vix_options_clue_not_hbt_executable_target"
    assert metadata["VIX.OPT"]["blocker_detail"] == "vix_options_clue_not_hbt_executable_target"
