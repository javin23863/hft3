#!/usr/bin/env python
"""Build the leader-lane lake manifest + coverage report (PR-2, leader lane).

Scans the canonical NPZ lake (HFT3_NPZ_ROOT, see specs/DATA_LAKE.md) for
leader-symbol tapes (ES / NQ / ZN / ZB / VIX) and emits:

  1. ``lake_manifest_c1.json`` — rows in the exact schema
     ``scripts/prepare_hftbacktest_only_from_lake_manifest.py`` consumes
     (event_id / symbol / npz_path / event_count / sha256), wrapped in a
     fail-closed envelope. Prepared leader units then feed
     ``hftbacktest_only_campaign_manifest._leader_unit_index`` keyed on
     (product, event_id).
  2. A coverage report counting EVENT ROWS unblocked per cross-asset lane
     (lane = required-leader set from replay.cross_asset_assembly), with gaps
     itemized by trade date. DoD: >=80% of each lane's campaign rows.

Fail-closed: a missing lake index, an empty target-event universe, or zero
leader tapes is an error, never an empty-but-green report.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

REPO = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(REPO), str(REPO / "packages")]

from backtest_pipeline.src.hftbacktest_only_io import write_json_atomic
from data_system.src.lake_manifest import resolve_npz_path
from data_system.src.npz_resolver import npz_root as resolve_npz_root
from replay.cross_asset_assembly import REQUIRED_LEADERS_BY_MODEL

MANIFEST_SCHEMA_VERSION = "hft3_leader_lake_manifest_v1"
COVERAGE_SCHEMA_VERSION = "hft3_leader_lane_coverage_v1"
DEFAULT_LEADER_PRODUCTS = ("ES", "NQ", "ZN", "ZB", "VIX")
DEFAULT_TARGET_PRODUCTS = ("MES", "MNQ")
DOD_ROW_COVERAGE_PCT = 80.0
_DATE_RE = re.compile(r"(20\d{2})_(\d{2})_(\d{2})")


class LeaderLakeManifestError(RuntimeError):
    pass


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _product(symbol: str) -> str:
    return str(symbol or "").split(".")[0].strip().upper()


def _trade_date(event_id: str) -> str:
    match = _DATE_RE.search(str(event_id or ""))
    if not match:
        return "unknown-date"
    return "-".join(match.groups())


def _load_lake_index(npz_root: Path) -> list[dict[str, Any]]:
    index_path = npz_root / "manifest.json"
    if not index_path.is_file():
        raise LeaderLakeManifestError(
            f"lake_index_missing:{index_path} "
            "(rebuild with scripts/build_event_lake.py --manifest-only)"
        )
    payload = json.loads(index_path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise LeaderLakeManifestError(f"lake_index_not_a_list:{index_path}")
    return [dict(row) for row in payload if isinstance(row, Mapping)]


def scan_leader_tapes(
    npz_root: Path,
    *,
    leader_products: Iterable[str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Return (leader rows, stale index rows whose npz is missing on disk)."""
    wanted = {p.strip().upper() for p in leader_products if p.strip()}
    rows: list[dict[str, Any]] = []
    stale: list[dict[str, Any]] = []
    for record in _load_lake_index(npz_root):
        symbol = str(record.get("symbol") or "")
        product = _product(symbol)
        if product not in wanted:
            continue
        event_id = str(record.get("event_id") or "")
        npz_path = resolve_npz_path(REPO, str(record.get("npz_path") or ""))
        row = {
            "event_id": event_id,
            "symbol": symbol,
            "product": product,
            "trade_date": _trade_date(event_id),
            "npz_path": str(npz_path),
            "event_count": int(record.get("event_count") or 0),
            "sha256": str(record.get("sha256") or ""),
            "created_utc": str(record.get("created_utc") or ""),
        }
        if not event_id or not npz_path.is_file():
            stale.append({**row, "gap_reason": "npz_missing_on_disk" if event_id else "event_id_missing"})
            continue
        rows.append(row)
    rows.sort(key=lambda r: (r["product"], r["trade_date"], r["event_id"], r["symbol"]))
    return rows, stale


def target_unit_universe(
    npz_root: Path,
    *,
    target_products: Iterable[str],
    prepared_root: Path | None = None,
) -> tuple[dict[tuple[str, str], str], str]:
    """((product, event_id) -> trade_date, source label) for the MES/MNQ universe.

    Units, not distinct events: the campaign emits one row per prepared unit
    per model, so an event with both an MES and an MNQ unit counts twice —
    exactly how the 12,972-row lanes were counted in the blocked campaign.
    """
    wanted = {p.strip().upper() for p in target_products if p.strip()}
    units: dict[tuple[str, str], str] = {}
    if prepared_root is not None:
        source = f"prepared_root:{prepared_root}"
        for manifest_path in sorted(Path(prepared_root).glob("**/*_manifest.json")):
            unit = json.loads(manifest_path.read_text(encoding="utf-8"))
            product = _product(str(unit.get("symbol") or ""))
            event_id = str(unit.get("event_id") or "")
            if product in wanted and event_id:
                units[(product, event_id)] = str(unit.get("trade_date") or _trade_date(event_id))
    else:
        source = f"lake_index_target_products:{'+'.join(sorted(wanted))}"
        for record in _load_lake_index(npz_root):
            product = _product(str(record.get("symbol") or ""))
            event_id = str(record.get("event_id") or "")
            if product in wanted and event_id:
                units[(product, event_id)] = _trade_date(event_id)
    if not units:
        raise LeaderLakeManifestError(f"target_unit_universe_empty:{source}")
    return units, source


def _lanes() -> dict[str, dict[str, Any]]:
    """Lane key '+'.join(leaders) -> {required_leaders, models} from the SSoT."""
    lanes: dict[str, dict[str, Any]] = {}
    for model_id, leaders in sorted(REQUIRED_LEADERS_BY_MODEL.items()):
        key = "+".join(leaders)
        lane = lanes.setdefault(key, {"required_leaders": list(leaders), "models": []})
        lane["models"].append(model_id)
    return lanes


def build_coverage_report(
    *,
    leader_rows: list[dict[str, Any]],
    stale_rows: list[dict[str, Any]],
    units: Mapping[tuple[str, str], str],
    unit_universe_source: str,
    npz_root: Path,
    leader_products: Iterable[str],
) -> dict[str, Any]:
    scanned = {p.strip().upper() for p in leader_products if p.strip()}
    tapes_by_product: dict[str, set[str]] = {}
    for row in leader_rows:
        tapes_by_product.setdefault(row["product"], set()).add(row["event_id"])

    lanes_out: dict[str, Any] = {}
    for lane_key, lane in _lanes().items():
        leaders = lane["required_leaders"]
        model_count = len(lane["models"])
        units_covered = 0
        gap_units_by_event: dict[tuple[str, str], list[str]] = {}
        for (product, event_id), _trade in sorted(units.items()):
            missing = [l for l in leaders if event_id not in tapes_by_product.get(l, set())]
            if missing:
                gap_units_by_event.setdefault((event_id, "+".join(missing)), []).append(product)
            else:
                units_covered += 1
        gaps_by_date: dict[str, list[dict[str, Any]]] = {}
        for (event_id, missing_key), products in sorted(gap_units_by_event.items()):
            gaps_by_date.setdefault(_trade_date(event_id), []).append(
                {
                    "event_id": event_id,
                    "missing_leaders": missing_key.split("+"),
                    "target_units": sorted(products),
                }
            )
        units_total = len(units)
        rows_total = units_total * model_count
        rows_unblocked = units_covered * model_count
        pct = round(100.0 * units_covered / units_total, 2) if units_total else 0.0
        warnings = [f"leader_not_in_scanned_set:{l}" for l in leaders if l not in scanned]
        lanes_out[lane_key] = {
            "required_leaders": leaders,
            "models": lane["models"],
            "model_count": model_count,
            "target_units_total": units_total,
            "target_units_covered": units_covered,
            "target_units_missing": units_total - units_covered,
            "rows_total": rows_total,
            "rows_unblocked": rows_unblocked,
            "row_coverage_pct": pct,
            "meets_dod": pct >= DOD_ROW_COVERAGE_PCT,
            "warnings": warnings,
            "gaps_by_date": gaps_by_date,
        }

    return {
        "schema_version": COVERAGE_SCHEMA_VERSION,
        "generated_utc": _utc_now(),
        "npz_root": str(npz_root),
        "unit_universe_source": unit_universe_source,
        "target_unit_count": len(units),
        "target_event_count": len({event_id for _product, event_id in units}),
        "dod_threshold_pct": DOD_ROW_COVERAGE_PCT,
        "leader_tape_counts": {p: len(ids) for p, ids in sorted(tapes_by_product.items())},
        "stale_index_row_count": len(stale_rows),
        "stale_index_rows": stale_rows,
        "lanes": lanes_out,
        "lanes_meeting_dod": sorted(k for k, v in lanes_out.items() if v["meets_dod"]),
    }


def build_leader_lake_manifest(
    *,
    npz_root: Path,
    leader_products: Iterable[str] = DEFAULT_LEADER_PRODUCTS,
    target_products: Iterable[str] = DEFAULT_TARGET_PRODUCTS,
    prepared_root: Path | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    npz_root = Path(npz_root)
    if not npz_root.is_dir():
        raise LeaderLakeManifestError(f"npz_root_missing:{npz_root}")
    leader_rows, stale_rows = scan_leader_tapes(npz_root, leader_products=leader_products)
    if not leader_rows:
        raise LeaderLakeManifestError(
            "no_leader_tapes_found:" + ",".join(sorted({p.upper() for p in leader_products}))
        )
    units, universe_source = target_unit_universe(
        npz_root, target_products=target_products, prepared_root=prepared_root
    )
    manifest = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "generated_utc": _utc_now(),
        "npz_root": str(npz_root),
        "source_index": str(npz_root / "manifest.json"),
        "leader_products": sorted({p.strip().upper() for p in leader_products if p.strip()}),
        "row_count": len(leader_rows),
        "rows": leader_rows,
    }
    coverage = build_coverage_report(
        leader_rows=leader_rows,
        stale_rows=stale_rows,
        units=units,
        unit_universe_source=universe_source,
        npz_root=npz_root,
        leader_products=leader_products,
    )
    return manifest, coverage


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--npz-root",
        type=Path,
        default=None,
        help="NPZ lake root (default: HFT3_NPZ_ROOT via data_system.npz_resolver).",
    )
    parser.add_argument("--leader-symbols", default=",".join(DEFAULT_LEADER_PRODUCTS))
    parser.add_argument("--target-symbols", default=",".join(DEFAULT_TARGET_PRODUCTS))
    parser.add_argument(
        "--prepared-root",
        type=Path,
        default=None,
        help="Optional prepared-unit root; when set the target event universe is "
        "read from its *_manifest.json units instead of the lake index.",
    )
    parser.add_argument("--out", type=Path, default=Path("lake_manifest_c1.json"))
    parser.add_argument("--coverage-out", type=Path, default=None)
    args = parser.parse_args(argv)

    npz_root = args.npz_root if args.npz_root is not None else resolve_npz_root(REPO)
    try:
        manifest, coverage = build_leader_lake_manifest(
            npz_root=npz_root,
            leader_products=[s for s in args.leader_symbols.split(",") if s.strip()],
            target_products=[s for s in args.target_symbols.split(",") if s.strip()],
            prepared_root=args.prepared_root,
        )
    except LeaderLakeManifestError as exc:
        print(json.dumps({"status": "failed", "error": str(exc)}), file=sys.stderr)
        return 2

    coverage_out = args.coverage_out or args.out.with_name(args.out.stem + "_coverage.json")
    write_json_atomic(args.out, manifest)
    write_json_atomic(coverage_out, coverage)
    summary = {
        "status": "ok",
        "manifest": str(args.out),
        "coverage": str(coverage_out),
        "leader_row_count": manifest["row_count"],
        "target_unit_count": coverage["target_unit_count"],
        "target_event_count": coverage["target_event_count"],
        "lanes": {
            key: {
                "row_coverage_pct": lane["row_coverage_pct"],
                "rows_unblocked": lane["rows_unblocked"],
                "rows_total": lane["rows_total"],
                "meets_dod": lane["meets_dod"],
            }
            for key, lane in coverage["lanes"].items()
        },
    }
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
