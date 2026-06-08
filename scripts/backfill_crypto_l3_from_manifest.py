"""Manifest-aware Kraken true-Level3 backfill driver."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
import tempfile
from pathlib import Path
from typing import Any


DEFAULT_MANIFEST = Path("runtime/data_audits/crypto_l3_backfill_manifest.csv")
CONVERTED_STATUS = "canonical_converted_found"
CANONICAL_RAW_PREFIX = "data/crypto/kraken_level3_raw/"
LEGACY_RAW_MARKERS = ("data/crypto/kraken_l3_raw/", "kraken_l3_raw")
BOOK_MARKERS = ("book-1000", "/book/", "\\book\\")


def _bool_text(value: bool) -> str:
    return "true" if value else "false"


def _norm_path(value: str | Path) -> str:
    return str(value).replace("\\", "/").strip()


def _status(row: dict[str, str]) -> str:
    return (row.get("status") or "").strip().lower()


def _mark_conversion_failed(row: dict[str, str], note: str) -> None:
    row["local_raw_found"] = _bool_text(True)
    row["canonical_npz_found"] = _bool_text(False)
    if _status(row) == "missing_l3":
        row["status"] = "missing_l3"
    row["notes"] = note


def _glob_is_safe(raw_glob: str) -> bool:
    normalized = _norm_path(raw_glob).lower()
    if any(marker in normalized for marker in LEGACY_RAW_MARKERS):
        return False
    if any(marker in normalized for marker in BOOK_MARKERS):
        return False
    return normalized.startswith(CANONICAL_RAW_PREFIX)


def _find_raw_files(repo_root: Path, raw_glob: str) -> list[Path]:
    normalized = _norm_path(raw_glob)
    if not normalized:
        return []
    return sorted(path for path in repo_root.glob(normalized) if path.is_file())


def _combine_ndjson(raw_files: list[Path], combined_path: Path) -> None:
    with combined_path.open("w", encoding="utf-8", newline="\n") as out:
        for raw_file in raw_files:
            with raw_file.open("r", encoding="utf-8") as src:
                for line in src:
                    out.write(line)
                out.write("\n")


def _matches_filters(row: dict[str, str], asset: str | None, target_date: str | None) -> bool:
    if asset and (row.get("asset") or "").strip().upper() != asset.upper():
        return False
    if target_date and (row.get("target_date") or "").strip() != target_date:
        return False
    return True


def _summarize_error(exc: BaseException) -> str:
    text = f"{type(exc).__name__}: {exc}".replace("\n", " ").strip()
    return text[:240]


def _load_converter(repo_root: Path):
    package_root = repo_root / "packages"
    if str(package_root) not in sys.path:
        sys.path.insert(0, str(package_root))
    try:
        from crypto_lane.src.data_io.kraken_level3_converter import convert_ndjson_to_npz
    except ModuleNotFoundError as exc:
        if exc.name != "crypto_lane.src.data_io.kraken_level3_converter":
            raise
        return _fallback_convert_ndjson_to_npz

    return convert_ndjson_to_npz


def _fallback_order_id(order_id: str) -> int:
    digest = hashlib.blake2b(str(order_id).encode("utf-8"), digest_size=8).digest()
    return int.from_bytes(digest, "big", signed=False)


def _fallback_convert_ndjson_to_npz(
    ndjson_path: Path,
    npz_path: Path,
    *,
    start_time_ns: int = 1_000_000_000,
    step_ns: int = 1_000_000,
    depth: int = 1000,
) -> Path:
    import numpy as np
    from hftbacktest.types import (
        ADD_ORDER_EVENT,
        BUY_EVENT,
        CANCEL_ORDER_EVENT,
        EXCH_EVENT,
        LOCAL_EVENT,
        MODIFY_ORDER_EVENT,
        SELL_EVENT,
        event_dtype,
    )

    if depth <= 0:
        raise ValueError(f"depth must be positive, got {depth}")

    visible: dict[int, tuple[int, float, float]] = {}
    order_ids: dict[int, str] = {}
    events: list[tuple] = []

    def event_tuple(kind: int, side: int, ts: int, price: float, qty: float, oid: int) -> tuple:
        return (kind | side | EXCH_EVENT | LOCAL_EVENT, ts, ts, price, qty, oid, 0, 0.0)

    def apply_order(order: dict[str, Any], side: int, ts: int, is_snapshot: bool) -> None:
        order_id = order.get("order_id")
        if not order_id:
            return
        price = float(order["limit_price"])
        qty = float(order.get("order_qty", 0.0))
        oid = _fallback_order_id(str(order_id))
        known = order_ids.get(oid)
        if known is not None and known != str(order_id):
            raise ValueError(f"order_id hash collision for numeric id {oid}")
        order_ids[oid] = str(order_id)

        action = "add" if is_snapshot else str(order.get("event", "")).lower()
        if action == "delete":
            previous = visible.pop(oid, None)
            if previous is not None:
                prev_side, prev_price, prev_qty = previous
                events.append(event_tuple(CANCEL_ORDER_EVENT, prev_side, ts, prev_price, prev_qty, oid))
            return
        if action not in {"add", "modify"}:
            return
        previous = visible.get(oid)
        visible[oid] = (side, price, qty)
        if previous is None:
            events.append(event_tuple(ADD_ORDER_EVENT, side, ts, price, qty, oid))
        else:
            events.append(event_tuple(MODIFY_ORDER_EVENT, side, ts, price, qty, oid))

    with Path(ndjson_path).open("r", encoding="utf-8") as handle:
        for tick, raw_line in enumerate(handle):
            line = raw_line.strip()
            if not line:
                continue
            msg = json.loads(line)
            channel = msg.get("channel")
            if channel in {"book", "book-1000"}:
                raise ValueError(f"Expected Kraken level3 channel, got {channel!r}")
            if channel != "level3":
                continue
            data = msg.get("data")
            if not isinstance(data, list):
                continue
            msg_type = str(msg.get("type", "")).lower()
            ts = start_time_ns + tick * step_ns
            if msg_type == "snapshot":
                visible.clear()
            for payload in data:
                if not isinstance(payload, dict):
                    continue
                if any(key in payload for key in ("bs", "as", "b", "a")):
                    raise ValueError("Kraken book/book-1000 L2 payload cannot be converted as level3")
                for order in payload.get("bids", []):
                    apply_order(order, BUY_EVENT, ts, msg_type == "snapshot")
                for order in payload.get("asks", []):
                    apply_order(order, SELL_EVENT, ts, msg_type == "snapshot")

    if not events:
        raise ValueError(f"No events parsed from {ndjson_path}")

    normalized = []
    for i, (ev, exch_ts, _local_ts, px, qty, oid, ival, fval) in enumerate(events):
        local_ts = start_time_ns + i
        normalized.append((ev, min(exch_ts, local_ts), local_ts, px, qty, oid, ival, fval))

    npz_path = Path(npz_path)
    npz_path.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(npz_path, data=np.array(normalized, dtype=event_dtype))
    npz_path.with_suffix(npz_path.suffix + ".order_ids.json").write_text(
        json.dumps(
            {"numeric_to_order_id": {str(oid): text for oid, text in sorted(order_ids.items())}},
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return npz_path


def run_backfill(args: argparse.Namespace) -> dict[str, Any]:
    repo_root = Path(args.repo_root).resolve()
    manifest_path = Path(args.manifest)
    if not manifest_path.is_absolute():
        manifest_path = repo_root / manifest_path

    convert_ndjson_to_npz = _load_converter(repo_root)

    with manifest_path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        fieldnames = list(reader.fieldnames or [])
        rows = list(reader)

    summary: dict[str, Any] = {
        "rows_seen": 0,
        "rows_with_raw": 0,
        "converted_rows": 0,
        "failed_rows": 0,
        "missing_rows": 0,
        "updated_manifest_written": False,
    }

    selected = 0
    for row in rows:
        if not _matches_filters(row, args.asset, args.date):
            continue
        if args.limit is not None and selected >= args.limit:
            continue

        selected += 1
        summary["rows_seen"] += 1

        raw_glob = row.get("canonical_raw_glob", "")
        if not _glob_is_safe(raw_glob):
            summary["missing_rows"] += 1
            continue

        raw_files = _find_raw_files(repo_root, raw_glob)
        if not raw_files:
            summary["missing_rows"] += 1
            continue

        summary["rows_with_raw"] += 1
        if not args.write_manifest:
            continue

        npz_rel = _norm_path(row.get("canonical_npz_path", ""))
        if not npz_rel:
            _mark_conversion_failed(row, "conversion_failed: canonical_npz_path is empty")
            summary["failed_rows"] += 1
            continue

        npz_path = Path(npz_rel)
        if not npz_path.is_absolute():
            npz_path = repo_root / npz_path

        try:
            with tempfile.TemporaryDirectory(prefix="crypto_l3_backfill_") as tmp:
                combined_path = Path(tmp) / "combined.ndjson"
                _combine_ndjson(raw_files, combined_path)
                convert_ndjson_to_npz(combined_path, npz_path, depth=args.depth)
        except Exception as exc:
            _mark_conversion_failed(
                row,
                "conversion_failed: "
                f"{_summarize_error(exc)}; raw_files={len(raw_files)}",
            )
            summary["failed_rows"] += 1
            continue

        row["local_raw_found"] = _bool_text(True)
        row["canonical_npz_found"] = _bool_text(True)
        row["status"] = CONVERTED_STATUS
        row["notes"] = (
            "true Kraken WS v2 level3 conversion via convert_ndjson_to_npz; "
            f"raw_files={len(raw_files)}"
        )
        summary["converted_rows"] += 1

    if args.write_manifest:
        with manifest_path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)
        summary["updated_manifest_written"] = True

    if args.report_json:
        report_path = Path(args.report_json)
        if not report_path.is_absolute():
            report_path = repo_root / report_path
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    return summary


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Backfill canonical Kraken true-Level3 NPZ shards from a manifest."
    )
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--asset", default=None)
    parser.add_argument("--date", default=None, help="Filter by target_date, e.g. 2026-06-01.")
    parser.add_argument("--write-manifest", action="store_true")
    parser.add_argument("--depth", type=int, default=1000)
    parser.add_argument("--report-json", type=Path, default=None)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    summary = run_backfill(args)
    print(json.dumps(summary, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
