"""PR-2 leader-lane tests: lake scanner, coverage report, prepare driver, and
the campaign-manifest leader_tape_missing contract."""

from __future__ import annotations

import hashlib
import importlib.util
import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pytest

from backtest_pipeline.src.hftbacktest_only_campaign_manifest import (
    _leader_unit_index,
    _load_prepared_units,
    build_campaign_manifest_rows,
)

_REPO = Path(__file__).resolve().parents[2]


def _load_script(name: str):
    script = _REPO / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"{name}_cli", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
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
        rows[index]["px"] = 5000.0 + index * 0.25
        rows[index]["qty"] = 1.0
        rows[index]["order_id"] = 1000 + index
    np.savez_compressed(path, data=rows)
    return path


def _index_row(npz_root: Path, symbol: str, event_id: str, *, present: bool = True) -> dict:
    path = npz_root / f"{symbol}_{event_id}_mbo.npz"
    if present:
        _write_source_npz(path)
    return {
        "event_id": event_id,
        "symbol": symbol,
        "npz_path": str(path),
        "event_count": 3,
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest() if present else "",
        "created_utc": "2026-06-19T12:00:00+00:00",
    }


def _fake_lake(tmp_path: Path) -> Path:
    """Fake lake: three target units (MES x2 events, MNQ x1); ES+NQ leaders
    cover only the first event; a stale ES index row (file missing on disk)
    on the second."""
    npz_root = tmp_path / "lake" / "npz"
    npz_root.mkdir(parents=True)
    rows = [
        _index_row(npz_root, "MES.v.0", "CPI_2024_09_11_TIGHT"),
        _index_row(npz_root, "MES.v.0", "PPI_2024_09_12_TIGHT"),
        _index_row(npz_root, "MNQ.v.0", "CPI_2024_09_11_TIGHT"),
        _index_row(npz_root, "ES.v.0", "CPI_2024_09_11_TIGHT"),
        _index_row(npz_root, "NQ.v.0", "CPI_2024_09_11_TIGHT"),
        _index_row(npz_root, "ES.v.0", "PPI_2024_09_12_TIGHT", present=False),
    ]
    (npz_root / "manifest.json").write_text(json.dumps(rows), encoding="utf-8")
    return npz_root


# ---------------------------------------------------------------------------
# 1. Lake scanner + coverage report
# ---------------------------------------------------------------------------


def test_lake_scanner_builds_manifest_and_coverage(tmp_path: Path) -> None:
    module = _load_script("build_leader_lake_manifest")
    npz_root = _fake_lake(tmp_path)

    manifest, coverage = module.build_leader_lake_manifest(npz_root=npz_root)

    # Targets are not leaders; only on-disk leader tapes become rows.
    assert manifest["schema_version"] == "hft3_leader_lake_manifest_v1"
    keyed = {(row["product"], row["event_id"]) for row in manifest["rows"]}
    assert keyed == {("ES", "CPI_2024_09_11_TIGHT"), ("NQ", "CPI_2024_09_11_TIGHT")}
    assert all(Path(row["npz_path"]).is_file() for row in manifest["rows"])
    assert all(row["trade_date"] == "2024-09-11" for row in manifest["rows"])

    # Stale index row (npz missing on disk) is itemized, never silently kept.
    assert coverage["stale_index_row_count"] == 1
    assert coverage["stale_index_rows"][0]["gap_reason"] == "npz_missing_on_disk"
    assert coverage["stale_index_rows"][0]["product"] == "ES"

    # Lanes come from REQUIRED_LEADERS_BY_MODEL: ES / NQ / ES+NQ / ZN.
    lanes = coverage["lanes"]
    assert set(lanes) == {"ES", "NQ", "ES+NQ", "ZN"}
    # Units, not distinct events: MES x2 + MNQ x1.
    assert coverage["target_unit_count"] == 3
    assert coverage["target_event_count"] == 2

    es = lanes["ES"]
    assert es["models"] == ["ES_MES_LEAD_LAG", "MICRO_CONTRACT_RETAIL_LAG"]
    assert es["model_count"] == 2
    assert es["target_units_total"] == 3
    assert es["target_units_covered"] == 2  # MES+MNQ units of the CPI event
    assert es["rows_total"] == 6  # 3 units x 2 models
    assert es["rows_unblocked"] == 4
    assert es["row_coverage_pct"] == 66.67
    assert es["meets_dod"] is False
    assert es["gaps_by_date"] == {
        "2024-09-12": [
            {
                "event_id": "PPI_2024_09_12_TIGHT",
                "missing_leaders": ["ES"],
                "target_units": ["MES"],
            }
        ]
    }

    assert lanes["ES+NQ"]["target_units_covered"] == 2
    assert lanes["ES+NQ"]["rows_unblocked"] == 2
    assert lanes["NQ"]["target_units_covered"] == 2
    assert lanes["ZN"]["target_units_covered"] == 0
    zn_gaps = lanes["ZN"]["gaps_by_date"]
    assert {g["event_id"] for gaps in zn_gaps.values() for g in gaps} == {
        "CPI_2024_09_11_TIGHT",
        "PPI_2024_09_12_TIGHT",
    }
    assert coverage["lanes_meeting_dod"] == []
    assert coverage["leader_tape_counts"] == {"ES": 1, "NQ": 1}


def test_lake_scanner_resolves_relative_npz_path_under_repo_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """SSoT (data_system.src.lake_manifest.resolve_npz_path): relative
    npz_path entries resolve under REPO ROOT, not under npz_root."""
    module = _load_script("build_leader_lake_manifest")
    npz_root = tmp_path / "lake" / "npz"
    npz_root.mkdir(parents=True)
    repo_root = tmp_path / "repo"
    rel = Path("data") / "npz" / "ES.v.0_CPI_2024_09_11_TIGHT_mbo.npz"
    leader_file = repo_root / rel
    leader_file.parent.mkdir(parents=True)
    _write_source_npz(leader_file)
    rows = [
        _index_row(npz_root, "MES.v.0", "CPI_2024_09_11_TIGHT"),
        {
            "event_id": "CPI_2024_09_11_TIGHT",
            "symbol": "ES.v.0",
            "npz_path": str(rel),  # repo-relative, per lake_manifest schema
            "event_count": 3,
            "sha256": hashlib.sha256(leader_file.read_bytes()).hexdigest(),
            "created_utc": "2026-06-19T12:00:00+00:00",
        },
    ]
    (npz_root / "manifest.json").write_text(json.dumps(rows), encoding="utf-8")
    monkeypatch.setattr(module, "REPO", repo_root)

    manifest, _coverage = module.build_leader_lake_manifest(npz_root=npz_root)

    (es_row,) = [row for row in manifest["rows"] if row["product"] == "ES"]
    assert Path(es_row["npz_path"]) == leader_file  # under repo root
    assert not str(es_row["npz_path"]).startswith(str(npz_root))
    assert Path(es_row["npz_path"]).is_file()


def test_lake_scanner_fails_closed_without_index_or_targets(tmp_path: Path) -> None:
    module = _load_script("build_leader_lake_manifest")

    empty_root = tmp_path / "empty" / "npz"
    empty_root.mkdir(parents=True)
    with pytest.raises(module.LeaderLakeManifestError, match="lake_index_missing"):
        module.build_leader_lake_manifest(npz_root=empty_root)

    # Leader tapes exist but no MES/MNQ target events -> explicit error.
    npz_root = tmp_path / "lake" / "npz"
    npz_root.mkdir(parents=True)
    rows = [_index_row(npz_root, "ES.v.0", "CPI_2024_09_11_TIGHT")]
    (npz_root / "manifest.json").write_text(json.dumps(rows), encoding="utf-8")
    with pytest.raises(module.LeaderLakeManifestError, match="target_unit_universe_empty"):
        module.build_leader_lake_manifest(npz_root=npz_root)


def test_lake_scanner_event_universe_from_prepared_root(tmp_path: Path) -> None:
    module = _load_script("build_leader_lake_manifest")
    npz_root = _fake_lake(tmp_path)

    prepared_root = tmp_path / "prepared"
    unit_dir = prepared_root / "MES" / "2024-09-11"
    unit_dir.mkdir(parents=True)
    unit_dir.joinpath("CPI_2024_09_11_TIGHT_manifest.json").write_text(
        json.dumps(
            {
                "symbol": "MES.v.0",
                "event_id": "CPI_2024_09_11_TIGHT",
                "trade_date": "2024-09-11",
            }
        ),
        encoding="utf-8",
    )

    _manifest, coverage = module.build_leader_lake_manifest(
        npz_root=npz_root, prepared_root=prepared_root
    )
    assert coverage["target_unit_count"] == 1
    assert coverage["unit_universe_source"].startswith("prepared_root:")
    assert coverage["lanes"]["ES"]["row_coverage_pct"] == 100.0
    assert coverage["lanes"]["ES"]["meets_dod"] is True
    assert coverage["lanes_meeting_dod"] == ["ES", "ES+NQ", "NQ"]


# ---------------------------------------------------------------------------
# 2. Campaign manifest contract: leader npz present vs absent
# ---------------------------------------------------------------------------


def _write_registry(path: Path) -> Path:
    path.write_text(
        """
models:
  ES_MES_LEAD_LAG:
    kind: hypothesis
    legacy_id: HYP_16
    display_name: ES leads MES
    class: EsMesLeadLag
""".lstrip(),
        encoding="utf-8",
    )
    return path


def _write_target_unit(root: Path) -> Path:
    prepared_dir = root / "MES" / "2024-09-11"
    prepared_dir.mkdir(parents=True)
    normalized = prepared_dir / "event_l3.npz"
    snapshot = prepared_dir / "initial_snapshot.npz"
    normalized.write_bytes(b"hbt-normalized")
    snapshot.write_bytes(b"hbt-initial-snapshot")
    manifest = {
        "schema_version": "hft3_hbt_only_lake_prepare_v1",
        "symbol": "MES",
        "contract": "MESU4",
        "event_id": "CPI_2024_09_11_TIGHT",
        "trade_date": "2024-09-11",
        "normalized_npz": str(normalized),
        "initial_snapshot": str(snapshot),
        "tick_size": 0.25,
        "lot_size": 1.0,
        "contract_size": 5.0,
        "product_metadata_source": "config/hftbacktest/cme_lake_product_metadata.yaml",
        "metadata_policy": "explicit_per_symbol_contract_tick_lot_contract_required",
        "authority_refs": [
            "config/hftbacktest/cme_lake_product_metadata.yaml",
            "product-authority:MES",
        ],
    }
    manifest_path = prepared_dir / "CPI_2024_09_11_TIGHT_manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    return manifest_path


def _write_leader_unit(root: Path) -> Path:
    leader_dir = root / "ES" / "2024-09-11"
    leader_dir.mkdir(parents=True)
    leader_npz = leader_dir / "event_l3.npz"
    leader_npz.write_bytes(b"leader-normalized")
    leader_dir.joinpath("CPI_2024_09_11_TIGHT_manifest.json").write_text(
        json.dumps(
            {
                "schema_version": "hft3_hbt_only_lake_prepare_v1",
                "symbol": "ES",
                "event_id": "CPI_2024_09_11_TIGHT",
                "trade_date": "2024-09-11",
                "normalized_npz": str(leader_npz),
                "replay_mode": "full_l3_event_replay",
            }
        ),
        encoding="utf-8",
    )
    return leader_npz


def test_leader_npz_present_sets_cross_asset_npz_and_no_blocker(tmp_path: Path) -> None:
    registry = _write_registry(tmp_path / "model_registry.yaml")
    prepared_root = tmp_path / "prepared"
    _write_target_unit(prepared_root)
    leader_npz = _write_leader_unit(prepared_root)

    rows = build_campaign_manifest_rows(
        campaign_id="hbt_leader_lane_test",
        prepared_root=prepared_root,
        registry_path=registry,
        adapter_status_by_model={"ES_MES_LEAD_LAG": "available"},
    )
    mes_row = next(
        row
        for row in rows
        if row["symbol"] == "MES" and row["canonical_model_id"] == "ES_MES_LEAD_LAG"
    )
    assert mes_row["cross_asset_npz"] == {"ES": str(leader_npz)}
    assert "leader_tape_missing" not in mes_row["blocker_code"]
    assert "missing_leader_tapes" not in mes_row["blocker_detail"]
    assert mes_row["admissibility_status"] == "admissible"


def test_leader_npz_absent_blocks_leader_tape_missing_es(tmp_path: Path) -> None:
    registry = _write_registry(tmp_path / "model_registry.yaml")
    prepared_root = tmp_path / "prepared"
    _write_target_unit(prepared_root)

    rows = build_campaign_manifest_rows(
        campaign_id="hbt_leader_lane_test",
        prepared_root=prepared_root,
        registry_path=registry,
        adapter_status_by_model={"ES_MES_LEAD_LAG": "available"},
    )
    mes_row = next(
        row
        for row in rows
        if row["symbol"] == "MES" and row["canonical_model_id"] == "ES_MES_LEAD_LAG"
    )
    assert mes_row["blocker_code"] == "pipeline_blocker:leader_tape_missing:ES"
    assert "missing_leader_tapes=ES" in mes_row["blocker_detail"]
    assert mes_row["cross_asset_npz"] == {}


# ---------------------------------------------------------------------------
# 3. Prepare driver: selection, full_l3 replay mode, leader-index handoff
# ---------------------------------------------------------------------------


def test_prepare_leader_units_emits_full_l3_units_feeding_leader_index(tmp_path: Path) -> None:
    scanner = _load_script("build_leader_lake_manifest")
    driver = _load_script("prepare_leader_units")
    npz_root = _fake_lake(tmp_path)

    manifest, _coverage = scanner.build_leader_lake_manifest(npz_root=npz_root)
    manifest_path = tmp_path / "lake_manifest_c1.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    out_root = tmp_path / "hbt"
    summary = driver.prepare_leader_units(
        leader_manifest=manifest_path,
        out_root=out_root,
        symbols=["ES"],
        warmup_seconds=1,
    )
    assert summary["replay_mode"] == "full_l3_event_replay"
    assert summary["selected_count"] == 1
    prepare_summary = summary["prepare_summary"]
    assert prepare_summary["prepared_count"] == 1
    assert prepare_summary["blocked_count"] == 0
    assert prepare_summary["replay_mode"] == "full_l3_event_replay"

    prepared_root = out_root / "prepared"
    manifests = sorted(prepared_root.glob("**/*_manifest.json"))
    assert len(manifests) == 1
    payload = json.loads(manifests[0].read_text(encoding="utf-8"))
    assert payload["status"] == "hbt_full_l3_event_replay_prepared"
    assert payload["replay_mode"] == "full_l3_event_replay"
    assert payload["symbol"] == "ES.v.0"

    # The prepared unit must feed the campaign manifest's leader-unit index
    # under (product, event_id) so leader_tape_missing clears for ES.
    index = _leader_unit_index(_load_prepared_units(prepared_root))
    assert ("ES", "CPI_2024_09_11_TIGHT") in index
    assert Path(index[("ES", "CPI_2024_09_11_TIGHT")]).is_file()


def test_prepare_leader_units_dry_run_and_fail_closed_selection(tmp_path: Path) -> None:
    scanner = _load_script("build_leader_lake_manifest")
    driver = _load_script("prepare_leader_units")
    npz_root = _fake_lake(tmp_path)

    manifest, _coverage = scanner.build_leader_lake_manifest(npz_root=npz_root)
    manifest_path = tmp_path / "lake_manifest_c1.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    out_root = tmp_path / "hbt"

    dry = driver.prepare_leader_units(
        leader_manifest=manifest_path,
        out_root=out_root,
        symbols=["ES", "NQ"],
        dates=["2024-09-11"],
        dry_run=True,
    )
    assert dry["dry_run"] is True
    assert dry["selected_count"] == 2
    assert dry["prepare_summary"] is None
    assert not out_root.exists()

    # Empty selection is an explicit error, never a silent no-op.
    with pytest.raises(driver.LeaderPrepareError, match="leader_selection_empty"):
        driver.prepare_leader_units(
            leader_manifest=manifest_path,
            out_root=out_root,
            symbols=["ZB"],
        )

    # Schema mismatch fails closed.
    bad = tmp_path / "bad_manifest.json"
    bad.write_text(json.dumps({"schema_version": "other", "rows": []}), encoding="utf-8")
    with pytest.raises(driver.LeaderPrepareError, match="leader_manifest_schema_mismatch"):
        driver.prepare_leader_units(
            leader_manifest=bad, out_root=out_root, symbols=["ES"]
        )
