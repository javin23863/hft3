"""Paid data inventory and non-destructive sync tests."""

from __future__ import annotations

import hashlib
import json
import os

from scripts.paid_data_inventory import build_q001_cme_data_inventory, build_report, write_reports


def _manifest_row(path, event_id, symbol, event_count=1):
    payload = b"npz-bytes"
    path.write_bytes(payload)
    return {
        "event_id": event_id,
        "symbol": symbol,
        "npz_path": str(path),
        "event_count": event_count,
        "sha256": hashlib.sha256(payload).hexdigest(),
        "created_utc": "2026-06-14T00:00:00+00:00",
    }


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


def test_paid_data_inventory_markdown_splits_options_strict_and_study_coverage(tmp_path):
    report = {
        "generated_at_utc": "2026-06-15T00:00:00+00:00",
        "data_root_used": str(tmp_path / "repo" / "data"),
        "source_root": str(tmp_path / "paid" / "data"),
        "sync_requested": False,
        "dry_run": True,
        "official_coverage_status": "RUNNABLE_CME_NPZ_PRESENT",
        "runnable_npz_days": {},
        "raw_download_days": {},
        "missing_conversion_days": {},
        "source_inventory": {"categories": {}},
        "synced_files": [],
        "q001_cme_data_inventory": {
            "status": "INVENTORIED_WITH_WARNINGS",
            "scope": "read-only local inventory",
            "event_catalog": {"status": "OK", "symbols": ["MES.v.0"]},
            "futures": {
                "active_npz_manifest": {
                    "status": "OK",
                    "record_count": 1,
                    "date_min": "2024-01-01",
                    "date_max": "2024-01-01",
                    "sha256_validation_mode": "content_verified",
                },
                "mbo_pilot_basket": {
                    "status": "completed_with_gaps",
                    "present_runnable_npz_slots": 3,
                    "expected_event_symbol_slots": 4,
                    "coverage_pct": 75.0,
                    "missing_or_unavailable_slots": 1,
                },
            },
            "options": {
                "data_doctor_status": "WARN",
                "options_lane": {
                    "fixing_mbo": {
                        "dates_covered": 275,
                        "study_dates_covered": 782,
                        "study_date_list": ["2023-09-15"],
                        "trade_only_dates": 507,
                        "first_date": "2023-05-01",
                        "last_date": "2024-06-03",
                    },
                    "expiry_coverage": {
                        "expected_dates": 784,
                        "dates_covered": 784,
                        "covered_elsewhere": ["2023-09-15", "2024-09-18", "2025-06-20"],
                        "gap_count": 0,
                        "strict_mbo_gap_count": 507,
                        "strict_mbo_stale_gap_count": 503,
                    },
                },
            },
            "gaps": [],
        },
    }
    _, md_path = write_reports(report, tmp_path / "runtime" / "data_audits")
    markdown = md_path.read_text(encoding="utf-8")

    assert "strict_quote_dates=275" in markdown
    assert "study_file_dates=782" in markdown
    assert "coverage_dates=784/784" in markdown
    assert "trade_only_dates=507" in markdown
    assert "covered_elsewhere=3 (net_new=2; overlap=1)" in markdown
    assert "study_gap_count=0" in markdown
    assert "strict_quote_gap_count=507" in markdown
    assert "strict_quote_stale=503" in markdown
    assert "| CME options fixing MBO | WARN | 275 dates;" not in markdown


def test_q001_inventory_reports_futures_options_and_gaps(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    npz_root = repo / "data" / "npz"
    npz_root.mkdir(parents=True)
    (repo / "packages" / "data_system" / "config").mkdir(parents=True)
    (repo / "runtime").mkdir(parents=True)
    monkeypatch.delenv("HFT3_NPZ_ROOT", raising=False)

    (npz_root / "manifest.json").write_text(
        json.dumps(
            [
                _manifest_row(npz_root / "MES.v.0_CPI_2024_09_11_TIGHT_mbo.npz", "CPI_2024_09_11_TIGHT", "MES.v.0", 10),
                _manifest_row(npz_root / "ES.v.0_NFP_2024_10_04_TIGHT_mbo.npz", "NFP_2024_10_04_TIGHT", "ES.v.0", 20),
            ]
        ),
        encoding="utf-8",
    )
    events_csv = repo / "packages" / "data_system" / "config" / "events.csv"
    events_csv.write_text(
        "event_id,event_type,symbols\n"
        'CPI_2024_09_11_TIGHT,CPI,"MES.v.0,ES.v.0"\n',
        encoding="utf-8",
    )
    mbo_manifest = repo / "packages" / "data_system" / "config" / "mbo_pilot_basket_20260605_manifest.json"
    mbo_manifest.write_text(
        json.dumps(
            {
                "run_id": "mbo_pilot",
                "status": "completed_with_gaps",
                "databento_request": {
                    "dataset": "GLBX.MDP3",
                    "schema": "mbo",
                    "stype_in": "continuous",
                    "range_start_utc": "2024-01-01T00:00:00+00:00",
                    "range_end_utc": "2024-01-02T00:00:00+00:00",
                },
                "coverage": {
                    "expected_event_symbol_slots": 4,
                    "present_runnable_npz_slots": 3,
                    "missing_or_unavailable_slots": 1,
                },
                "partial_windows": [{"event_id": "FED_H41_2024_06_19_TIGHT"}],
                "no_market_windows": ["FOMC_PRESS_2024_09_15_TIGHT"],
            }
        ),
        encoding="utf-8",
    )
    data_doctor = repo / "runtime" / "data_doctor_report.json"
    data_doctor.write_text(
        json.dumps(
            {
                "run_utc": "2026-06-14T00:00:00+00:00",
                "failed": 0,
                "warned": 1,
                "checks": [{"name": "options-fixing-coverage", "status": "WARN", "detail": "gap_count=1"}],
                "options_lane": {
                    "fixing_mbo": {"dates_covered": 3, "first_date": "2026-01-01", "last_date": "2026-01-03"},
                    "expiry_coverage": {"gap_count": 1},
                },
            }
        ),
        encoding="utf-8",
    )

    report = build_q001_cme_data_inventory(repo_root=repo, verify_hashes=True)

    assert report["status"] == "INVENTORIED_WITH_WARNINGS"
    assert report["cme_symbol_universe"] == ["ES.v.0", "MES.v.0"]
    assert report["futures"]["active_npz_manifest"]["record_count"] == 2
    assert report["futures"]["mbo_pilot_basket"]["coverage_pct"] == 75.0
    assert report["options"]["options_lane"]["fixing_mbo"]["dates_covered"] == 3
    assert {gap["source"] for gap in report["gaps"]} == {"mbo_pilot_manifest", "data_doctor"}


def test_q001_inventory_honors_external_npz_root(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    external = tmp_path / "lake" / "npz"
    external.mkdir(parents=True)
    monkeypatch.setenv("HFT3_NPZ_ROOT", os.fspath(external))
    (external / "manifest.json").write_text(
        json.dumps([
            _manifest_row(
                external / "MES.v.0_CPI_2024_09_11_TIGHT_mbo.npz",
                "CPI_2024_09_11_TIGHT",
                "MES.v.0",
            )
        ]),
        encoding="utf-8",
    )

    report = build_q001_cme_data_inventory(repo_root=repo)

    assert report["futures"]["active_npz_manifest"]["path"] == str(external / "manifest.json")
    assert report["futures"]["active_npz_manifest"]["record_count"] == 1
    assert any("sha256_content_not_verified" in gap["detail"] for gap in report["gaps"])
    assert any(gap["source"] == "mbo_pilot_manifest" for gap in report["gaps"])


def test_q001_inventory_blocks_on_manifest_schema_violation(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    npz_root = repo / "data" / "npz"
    npz_root.mkdir(parents=True)
    monkeypatch.delenv("HFT3_NPZ_ROOT", raising=False)
    (npz_root / "manifest.json").write_text(
        json.dumps([{"event_id": "CPI_2024_09_11_TIGHT", "symbol": "MES.v.0"}]),
        encoding="utf-8",
    )

    report = build_q001_cme_data_inventory(repo_root=repo, verify_hashes=True)

    assert report["status"] == "BLOCKED"
    active = report["futures"]["active_npz_manifest"]
    assert active["status"] == "FAIL_SCHEMA_OR_PATH_VALIDATION"
    assert active["missing_required_field_rows"] == 1
    assert any(gap["source"] == "active_npz_manifest" and gap["severity"] == "FAIL" for gap in report["gaps"])


def test_q001_inventory_blocks_on_manifest_sha_mismatch(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    npz_root = repo / "data" / "npz"
    npz_root.mkdir(parents=True)
    monkeypatch.delenv("HFT3_NPZ_ROOT", raising=False)
    row = _manifest_row(
        npz_root / "MES.v.0_CPI_2024_09_11_TIGHT_mbo.npz",
        "CPI_2024_09_11_TIGHT",
        "MES.v.0",
    )
    row["sha256"] = "b" * 64
    (npz_root / "manifest.json").write_text(json.dumps([row]), encoding="utf-8")

    report = build_q001_cme_data_inventory(repo_root=repo, verify_hashes=True)

    assert report["status"] == "BLOCKED"
    active = report["futures"]["active_npz_manifest"]
    assert active["invalid_sha256_rows"] == 1
    assert "sha256 mismatch" in active["validation_error_examples"][0]


def test_q001_hash_verification_does_not_crash_on_missing_npz_path(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    npz_root = repo / "data" / "npz"
    npz_root.mkdir(parents=True)
    monkeypatch.delenv("HFT3_NPZ_ROOT", raising=False)
    row = _manifest_row(
        npz_root / "MES.v.0_CPI_2024_09_11_TIGHT_mbo.npz",
        "CPI_2024_09_11_TIGHT",
        "MES.v.0",
    )
    (npz_root / "MES.v.0_CPI_2024_09_11_TIGHT_mbo.npz").unlink()
    (npz_root / "manifest.json").write_text(json.dumps([row]), encoding="utf-8")

    report = build_q001_cme_data_inventory(repo_root=repo, verify_hashes=True)

    assert report["status"] == "BLOCKED"
    active = report["futures"]["active_npz_manifest"]
    assert active["missing_npz_files"] == 1


def test_q001_inventory_blocks_on_data_doctor_top_level_failures(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    npz_root = repo / "data" / "npz"
    npz_root.mkdir(parents=True)
    (repo / "runtime").mkdir(parents=True)
    monkeypatch.delenv("HFT3_NPZ_ROOT", raising=False)
    (npz_root / "manifest.json").write_text(
        json.dumps([
            _manifest_row(
                npz_root / "MES.v.0_CPI_2024_09_11_TIGHT_mbo.npz",
                "CPI_2024_09_11_TIGHT",
                "MES.v.0",
            )
        ]),
        encoding="utf-8",
    )
    (repo / "runtime" / "data_doctor_report.json").write_text(
        json.dumps({"failed": 1, "warned": 0, "checks": []}),
        encoding="utf-8",
    )

    report = build_q001_cme_data_inventory(repo_root=repo)

    assert report["status"] == "BLOCKED"
    assert report["options"]["data_doctor_status"] == "FAIL"
    assert any(gap["source"] == "data_doctor" and gap["severity"] == "FAIL" for gap in report["gaps"])


def test_q001_inventory_reports_missing_events_csv_without_defaulting(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    npz_root = repo / "data" / "npz"
    npz_root.mkdir(parents=True)
    monkeypatch.delenv("HFT3_NPZ_ROOT", raising=False)
    (npz_root / "manifest.json").write_text(
        json.dumps([
            _manifest_row(
                npz_root / "MES.v.0_CPI_2024_09_11_TIGHT_mbo.npz",
                "CPI_2024_09_11_TIGHT",
                "MES.v.0",
            )
        ]),
        encoding="utf-8",
    )

    report = build_q001_cme_data_inventory(repo_root=repo)

    assert report["cme_symbol_universe"] == []
    assert report["expected_canonical_cme_symbols"] == ["MES.v.0", "MNQ.v.0", "ES.v.0", "NQ.v.0", "ZN.v.0", "ZB.v.0", "RTY.v.0"]
    assert any(gap["source"] == "events_csv" and gap["detail"] == "MISSING" for gap in report["gaps"])


def test_q001_inventory_reports_malformed_json_without_crashing(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    npz_root = repo / "data" / "npz"
    npz_root.mkdir(parents=True)
    monkeypatch.delenv("HFT3_NPZ_ROOT", raising=False)
    (npz_root / "manifest.json").write_text("{bad", encoding="utf-8")

    report = build_q001_cme_data_inventory(repo_root=repo)

    assert report["status"] == "BLOCKED"
    assert report["futures"]["active_npz_manifest"]["status"] == "MALFORMED_JSON"
    assert any(gap["source"] == "active_npz_manifest" and gap["severity"] == "FAIL" for gap in report["gaps"])


def test_q001_inventory_blocks_on_incomplete_mbo_pilot_manifest(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    npz_root = repo / "data" / "npz"
    npz_root.mkdir(parents=True)
    (repo / "packages" / "data_system" / "config").mkdir(parents=True)
    monkeypatch.delenv("HFT3_NPZ_ROOT", raising=False)
    (npz_root / "manifest.json").write_text(
        json.dumps([
            _manifest_row(
                npz_root / "MES.v.0_CPI_2024_09_11_TIGHT_mbo.npz",
                "CPI_2024_09_11_TIGHT",
                "MES.v.0",
            )
        ]),
        encoding="utf-8",
    )
    mbo_manifest = repo / "packages" / "data_system" / "config" / "mbo_pilot_basket_20260605_manifest.json"
    mbo_manifest.write_text(json.dumps({"status": "completed"}), encoding="utf-8")

    report = build_q001_cme_data_inventory(repo_root=repo)

    assert report["status"] == "BLOCKED"
    assert report["futures"]["mbo_pilot_basket"]["status"] == "MALFORMED_SCHEMA"
    assert any(gap["source"] == "mbo_pilot_manifest" and gap["severity"] == "FAIL" for gap in report["gaps"])


def test_q001_inventory_blocks_on_impossible_mbo_coverage_math(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    npz_root = repo / "data" / "npz"
    npz_root.mkdir(parents=True)
    (repo / "packages" / "data_system" / "config").mkdir(parents=True)
    monkeypatch.delenv("HFT3_NPZ_ROOT", raising=False)
    (npz_root / "manifest.json").write_text(
        json.dumps([
            _manifest_row(
                npz_root / "MES.v.0_CPI_2024_09_11_TIGHT_mbo.npz",
                "CPI_2024_09_11_TIGHT",
                "MES.v.0",
            )
        ]),
        encoding="utf-8",
    )
    mbo_manifest = repo / "packages" / "data_system" / "config" / "mbo_pilot_basket_20260605_manifest.json"
    mbo_manifest.write_text(
        json.dumps(
            {
                "status": "completed",
                "databento_request": {
                    "dataset": "GLBX.MDP3",
                    "schema": "mbo",
                    "stype_in": "continuous",
                    "range_start_utc": "2024-01-01T00:00:00+00:00",
                    "range_end_utc": "2024-01-02T00:00:00+00:00",
                },
                "coverage": {
                    "expected_event_symbol_slots": 1,
                    "present_runnable_npz_slots": 2,
                    "missing_or_unavailable_slots": 0,
                },
            }
        ),
        encoding="utf-8",
    )

    report = build_q001_cme_data_inventory(repo_root=repo)

    assert report["status"] == "BLOCKED"
    pilot = report["futures"]["mbo_pilot_basket"]
    assert pilot["status"] == "MALFORMED_SCHEMA"
    assert "coverage.present_runnable_npz_slots_gt_expected" in pilot["validation_errors"]
