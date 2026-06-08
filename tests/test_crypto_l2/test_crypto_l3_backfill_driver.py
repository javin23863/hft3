from __future__ import annotations

import csv
import json
import sys
from pathlib import Path


_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO / "scripts"))
sys.path.insert(0, str(_REPO / "packages"))

import backfill_crypto_l3_from_manifest as driver
try:
    from crypto_lane.src.data_io.kraken_level3_converter import order_id_sidecar_path
except ModuleNotFoundError:

    def order_id_sidecar_path(npz_path: Path) -> Path:
        return npz_path.with_suffix(npz_path.suffix + ".order_ids.json")


try:
    from validate_crypto_l3_manifest import validate_manifest
except ModuleNotFoundError:

    def validate_manifest(manifest_path: Path) -> dict:
        rows = _read_rows(manifest_path)
        violations = []
        for row in rows:
            if row["status"] == "canonical_converted_found":
                if row["local_raw_found"] != "true" or row["canonical_npz_found"] != "true":
                    violations.append({"reason": "promoted_l3_status_lacks_evidence"})
                if not row["canonical_raw_glob"].startswith("data/crypto/kraken_level3_raw/"):
                    violations.append({"reason": "promoted_l3_status_lacks_canonical_raw_glob"})
        return {"ok": not violations, "violations": violations}


FIELDNAMES = [
    "target_date",
    "asset",
    "asset_scope",
    "venue",
    "required_level",
    "l3_symbol",
    "binance_l2_symbol",
    "canonical_raw_glob",
    "canonical_npz_path",
    "legacy_sample_npz_path",
    "local_raw_found",
    "canonical_npz_found",
    "legacy_sample_npz_found",
    "lake_true_l3_found",
    "status",
    "notes",
]


def _row(**overrides: str) -> dict[str, str]:
    row = {
        "target_date": "2026-06-01",
        "asset": "BTC",
        "asset_scope": "mandatory_crypto_alpha_lane",
        "venue": "kraken",
        "required_level": "L3_order_level_DOM",
        "l3_symbol": "BTC/USD",
        "binance_l2_symbol": "BTCUSDT",
        "canonical_raw_glob": (
            "data/crypto/kraken_level3_raw/"
            "kraken_level3_BTC_USD_20260601_*.ndjson"
        ),
        "canonical_npz_path": (
            "data/replay/hftbacktest/crypto/kraken/"
            "BTC_USD/BTC_USD_20260601_l3.npz"
        ),
        "legacy_sample_npz_path": "",
        "local_raw_found": "false",
        "canonical_npz_found": "false",
        "legacy_sample_npz_found": "false",
        "lake_true_l3_found": "false",
        "status": "missing_l3",
        "notes": "No true exchange L3 found locally.",
    }
    row.update(overrides)
    return row


def _write_manifest(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(rows)


def _read_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def _level3(message_type: str, bids: list[dict], asks: list[dict]) -> dict:
    return {
        "channel": "level3",
        "type": message_type,
        "timestamp": "2026-06-01T00:00:00.000000000Z",
        "data": [{"symbol": "BTC/USD", "bids": bids, "asks": asks}],
    }


def _order(order_id: str, price: str, qty: str, event: str | None = None) -> dict:
    order = {
        "order_id": order_id,
        "limit_price": price,
        "order_qty": qty,
        "timestamp": "2026-06-01T00:00:00.000000001Z",
    }
    if event:
        order["event"] = event
    return order


def _write_ndjson(path: Path, messages: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(msg) for msg in messages) + "\n", encoding="utf-8")


def _run(repo_root: Path, manifest: Path, *extra: str) -> dict:
    return driver.run_backfill(
        driver.build_parser().parse_args(
            ["--repo-root", str(repo_root), "--manifest", str(manifest), *extra]
        )
    )


def test_write_manifest_converts_true_l3_and_validates(tmp_path: Path) -> None:
    manifest = tmp_path / "runtime/data_audits/crypto_l3_backfill_manifest.csv"
    raw = tmp_path / "data/crypto/kraken_level3_raw/kraken_level3_BTC_USD_20260601_a.ndjson"
    npz = tmp_path / "data/replay/hftbacktest/crypto/kraken/BTC_USD/BTC_USD_20260601_l3.npz"
    _write_manifest(manifest, [_row()])
    _write_ndjson(
        raw,
        [
            _level3(
                "snapshot",
                [_order("BID-1", "100.0", "1.0")],
                [_order("ASK-1", "101.0", "2.0")],
            )
        ],
    )

    summary = _run(tmp_path, manifest, "--write-manifest")

    row = _read_rows(manifest)[0]
    assert summary["converted_rows"] == 1
    assert npz.exists()
    assert order_id_sidecar_path(npz).exists()
    assert row["status"] == "canonical_converted_found"
    assert row["local_raw_found"] == "true"
    assert row["canonical_npz_found"] == "true"
    assert "true Kraken WS v2 level3 conversion" in row["notes"]
    assert "raw_files=1" in row["notes"]
    assert validate_manifest(manifest)["ok"] is True


def test_dry_run_does_not_write_npz_or_mutate_manifest(tmp_path: Path) -> None:
    manifest = tmp_path / "runtime/data_audits/crypto_l3_backfill_manifest.csv"
    raw = tmp_path / "data/crypto/kraken_level3_raw/kraken_level3_BTC_USD_20260601_a.ndjson"
    npz = tmp_path / "data/replay/hftbacktest/crypto/kraken/BTC_USD/BTC_USD_20260601_l3.npz"
    _write_manifest(manifest, [_row()])
    original = manifest.read_text(encoding="utf-8")
    _write_ndjson(
        raw,
        [_level3("snapshot", [_order("BID-1", "100.0", "1.0")], [])],
    )

    summary = _run(tmp_path, manifest)

    assert summary["rows_with_raw"] == 1
    assert summary["converted_rows"] == 0
    assert summary["updated_manifest_written"] is False
    assert not npz.exists()
    assert manifest.read_text(encoding="utf-8") == original


def test_legacy_old_path_raw_glob_is_refused(tmp_path: Path) -> None:
    manifest = tmp_path / "runtime/data_audits/crypto_l3_backfill_manifest.csv"
    raw = tmp_path / "data/crypto/kraken_l3_raw/kraken_l3_BTC_USD_20260601.ndjson"
    npz = tmp_path / "data/replay/hftbacktest/crypto/kraken/BTC_USD/BTC_USD_20260601_l3.npz"
    _write_manifest(
        manifest,
        [
            _row(
                canonical_raw_glob="data/crypto/kraken_l3_raw/kraken_l3_BTC_USD_*.ndjson",
                status="legacy_l2_mislabeled_sample_found",
            )
        ],
    )
    _write_ndjson(
        raw,
        [_level3("snapshot", [_order("BID-1", "100.0", "1.0")], [])],
    )

    summary = _run(tmp_path, manifest, "--write-manifest")

    row = _read_rows(manifest)[0]
    assert summary["rows_with_raw"] == 0
    assert summary["converted_rows"] == 0
    assert not npz.exists()
    assert row["status"] == "legacy_l2_mislabeled_sample_found"
    assert row["local_raw_found"] == "false"


def test_bad_book_1000_raw_records_conversion_failure(tmp_path: Path) -> None:
    manifest = tmp_path / "runtime/data_audits/crypto_l3_backfill_manifest.csv"
    raw = tmp_path / "data/crypto/kraken_level3_raw/kraken_level3_BTC_USD_20260601_a.ndjson"
    npz = tmp_path / "data/replay/hftbacktest/crypto/kraken/BTC_USD/BTC_USD_20260601_l3.npz"
    _write_manifest(manifest, [_row()])
    _write_ndjson(
        raw,
        [
            {
                "channel": "book-1000",
                "type": "snapshot",
                "data": [{"symbol": "BTC/USD", "bids": [], "asks": []}],
            }
        ],
    )

    summary = _run(tmp_path, manifest, "--write-manifest")

    row = _read_rows(manifest)[0]
    assert summary["failed_rows"] == 1
    assert not npz.exists()
    assert row["status"] == "missing_l3"
    assert row["local_raw_found"] == "true"
    assert row["canonical_npz_found"] == "false"
    assert "conversion_failed" in row["notes"]


def test_multiple_raw_fragments_are_sorted_and_combined(tmp_path: Path) -> None:
    manifest = tmp_path / "runtime/data_audits/crypto_l3_backfill_manifest.csv"
    raw_b = tmp_path / "data/crypto/kraken_level3_raw/kraken_level3_BTC_USD_20260601_b.ndjson"
    raw_a = tmp_path / "data/crypto/kraken_level3_raw/kraken_level3_BTC_USD_20260601_a.ndjson"
    _write_manifest(manifest, [_row()])
    _write_ndjson(raw_b, [_level3("update", [_order("BID-2", "99.0", "1.0", "add")], [])])
    _write_ndjson(raw_a, [_level3("snapshot", [_order("BID-1", "100.0", "1.0")], [])])

    summary = _run(tmp_path, manifest, "--write-manifest")

    row = _read_rows(manifest)[0]
    assert summary["converted_rows"] == 1
    assert "raw_files=2" in row["notes"]
