"""Paid data inventory and non-destructive sync tests."""

from __future__ import annotations

import json

from scripts.paid_data_inventory import build_report, write_reports


def test_paid_data_inventory_sync_reports_runnable_and_raw_separately(tmp_path):
    repo = tmp_path / "repo"
    source = tmp_path / "paid" / "data"
    npz = source / "npz" / "MES.v.0_CPI_2025_02_12_TIGHT_mbo.npz"
    raw = source / "replay" / "mbp10" / "MES.v.0_CPI_2025_03_12_TIGHT_mbp-10.dbn.zst"
    root_raw = source / "NFP_2025_04_04_TIGHT_mbo.dbn.zst"
    equity = source / "equities" / "daily" / "ABCD.parquet"
    crypto = source / "crypto" / "gold" / "BTCUSDT_2024-01-01.parquet"
    option = source / "options" / "equity_chains" / "raw" / "gme_2021" / "gme_2021_cbbo-1m.dbn.zst"
    for path in (npz, raw, root_raw, equity, crypto, option):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"paid-data")

    report = build_report(repo_root=repo, source_root=source, sync=True, dry_run=False)

    assert (repo / "data" / "npz" / npz.name).is_file()
    assert (repo / "data" / "replay" / "mbp10" / raw.name).is_file()
    assert (repo / "data" / "raw" / "databento_mbo" / root_raw.name).is_file()
    assert (repo / "data" / "equities" / "daily" / equity.name).is_file()
    assert (repo / "data" / "crypto" / "gold" / crypto.name).is_file()
    assert report["official_coverage_status"] == "RUNNABLE_CME_NPZ_PRESENT"
    assert report["runnable_npz_days"]["MES"] == 1
    assert report["raw_download_days"]["MES"] == 1
    assert report["raw_download_days"]["unspecified"] == 1
    assert report["missing_conversion_days"]["MES"] == 1


def test_paid_data_inventory_does_not_overwrite_conflicts(tmp_path):
    repo = tmp_path / "repo"
    source = tmp_path / "paid" / "data"
    source_npz = source / "npz" / "MES.v.0_CPI_2025_02_12_TIGHT_mbo.npz"
    dest_npz = repo / "data" / "npz" / source_npz.name
    source_npz.parent.mkdir(parents=True, exist_ok=True)
    dest_npz.parent.mkdir(parents=True, exist_ok=True)
    source_npz.write_bytes(b"source-paid-data")
    dest_npz.write_bytes(b"existing")

    report = build_report(repo_root=repo, source_root=source, sync=True, dry_run=False)

    assert dest_npz.read_bytes() == b"existing"
    assert any(row["action"] == "conflict_not_overwritten" for row in report["synced_files"])


def test_paid_data_inventory_dry_run_reports_planned_copy_not_copied(tmp_path):
    repo = tmp_path / "repo"
    source = tmp_path / "paid" / "data"
    source_npz = source / "npz" / "MES.v.0_CPI_2025_02_12_TIGHT_mbo.npz"
    source_npz.parent.mkdir(parents=True, exist_ok=True)
    source_npz.write_bytes(b"source-paid-data")

    report = build_report(repo_root=repo, source_root=source, sync=True, dry_run=True)

    assert not (repo / "data" / "npz" / source_npz.name).exists()
    assert any(row["action"] == "planned_copy" for row in report["synced_files"])
    assert not any(row["action"] == "copied" for row in report["synced_files"])


def test_paid_data_inventory_writes_runtime_reports(tmp_path):
    report = {
        "generated_at_utc": "2026-06-05T00:00:00+00:00",
        "data_root_used": str(tmp_path / "repo" / "data"),
        "source_root": str(tmp_path / "paid" / "data"),
        "sync_requested": False,
        "dry_run": True,
        "official_coverage_status": "NO_RUNNABLE_CME_NPZ",
        "runnable_npz_days": {},
        "raw_download_days": {},
        "missing_conversion_days": {},
        "source_inventory": {"categories": {}},
        "synced_files": [],
    }
    json_path, md_path = write_reports(report, tmp_path / "runtime" / "data_audits")

    assert json.loads(json_path.read_text(encoding="utf-8"))["official_coverage_status"] == "NO_RUNNABLE_CME_NPZ"
    assert "Raw DBN/MBP10 files are downloaded backlog" in md_path.read_text(encoding="utf-8")
