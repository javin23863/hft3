#!/usr/bin/env python
"""Prepare cross-asset LEADER units from a leader lake manifest (PR-2).

Thin driver over the existing prepare path
(``scripts/prepare_hftbacktest_only_from_lake_manifest.py`` →
``prepare_hftbacktest_only_l3_from_lake``). Selection is explicit
(symbols + optional dates/event-ids); nothing is prepared implicitly.

Leaders are ALWAYS prepared with ``replay_mode=full_l3_event_replay``
(PR #72 decision): leader tapes are feature-side inputs and
``_leader_unit_index`` ranks the full stream above filtered variants.

Output lands in the runtime layout the campaign manifest expects:
``<out_root>/prepared/<SYMBOL>/<trade_date>/<event_id>..._manifest.json``,
i.e. pass ``<out_root>/prepared`` as the campaign ``prepared_root``.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any, Mapping

REPO = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(REPO), str(REPO / "packages")]

from backtest_pipeline.src.hftbacktest_only_io import write_json_atomic

LEADER_MANIFEST_SCHEMA = "hft3_leader_lake_manifest_v1"
SUMMARY_SCHEMA = "hft3_leader_unit_prepare_summary_v1"
# PR #72: leaders are full-stream feature inputs — never added_orders_only.
LEADER_REPLAY_MODE = "full_l3_event_replay"


class LeaderPrepareError(RuntimeError):
    pass


def _load_prepare_module():
    script = REPO / "scripts" / "prepare_hftbacktest_only_from_lake_manifest.py"
    spec = importlib.util.spec_from_file_location("prepare_hbt_from_lake_manifest_cli", script)
    if spec is None or spec.loader is None:
        raise LeaderPrepareError(f"prepare_module_unloadable:{script}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_leader_manifest(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise LeaderPrepareError(f"leader_manifest_not_an_envelope:{path}")
    schema = str(payload.get("schema_version") or "")
    if schema != LEADER_MANIFEST_SCHEMA:
        raise LeaderPrepareError(
            f"leader_manifest_schema_mismatch:expected={LEADER_MANIFEST_SCHEMA}:got={schema or '<missing>'}"
        )
    rows = payload.get("rows")
    if not isinstance(rows, list):
        raise LeaderPrepareError(f"leader_manifest_rows_missing:{path}")
    return [dict(row) for row in rows if isinstance(row, Mapping)]


def select_leader_rows(
    rows: list[dict[str, Any]],
    *,
    products: list[str],
    dates: list[str] | None = None,
    event_ids: list[str] | None = None,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    wanted_products = {p.strip().upper() for p in products if p.strip()}
    if not wanted_products:
        raise LeaderPrepareError("no_symbols_selected")
    wanted_dates = {d.strip() for d in dates or [] if d.strip()} or None
    wanted_events = {e.strip() for e in event_ids or [] if e.strip()} or None
    selected: list[dict[str, Any]] = []
    for row in rows:
        product = str(row.get("product") or str(row.get("symbol") or "").split(".")[0]).upper()
        if product not in wanted_products:
            continue
        if wanted_dates is not None and str(row.get("trade_date") or "") not in wanted_dates:
            continue
        if wanted_events is not None and str(row.get("event_id") or "") not in wanted_events:
            continue
        selected.append(row)
    selected.sort(key=lambda r: (str(r.get("symbol")), str(r.get("trade_date")), str(r.get("event_id"))))
    if limit is not None:
        selected = selected[: max(0, int(limit))]
    if not selected:
        raise LeaderPrepareError(
            "leader_selection_empty:"
            f"symbols={'+'.join(sorted(wanted_products))}"
            f":dates={'+'.join(sorted(wanted_dates)) if wanted_dates else 'all'}"
        )
    return selected


def prepare_leader_units(
    *,
    leader_manifest: Path,
    out_root: Path,
    symbols: list[str],
    dates: list[str] | None = None,
    event_ids: list[str] | None = None,
    limit: int | None = None,
    instrument_registry: Path | None = None,
    warmup_seconds: int = 30,
    force_rebuild: bool = False,
    dry_run: bool = False,
) -> dict[str, Any]:
    module = _load_prepare_module()
    registry = instrument_registry or module.DEFAULT_PRODUCT_METADATA
    rows = _load_leader_manifest(leader_manifest)
    selected = select_leader_rows(
        rows, products=symbols, dates=dates, event_ids=event_ids, limit=limit
    )

    out_root = Path(out_root)
    summary: dict[str, Any] = {
        "schema_version": SUMMARY_SCHEMA,
        "leader_manifest": str(leader_manifest),
        "out_root": str(out_root),
        "prepared_root": str(out_root / "prepared"),
        "replay_mode": LEADER_REPLAY_MODE,
        "selected_count": len(selected),
        "selected_symbols": sorted({str(r.get("symbol")) for r in selected}),
        "selected_dates": sorted({str(r.get("trade_date")) for r in selected}),
        "dry_run": dry_run,
    }
    if dry_run:
        summary["prepare_summary"] = None
        return summary

    out_root.mkdir(parents=True, exist_ok=True)
    # The existing prepare path is manifest-driven; hand it exactly the
    # selected rows (its input schema is a JSON list of lake rows).
    selection_path = out_root / "leader_prepare_selection.json"
    tmp = selection_path.with_name(selection_path.name + ".tmp")
    tmp.write_text(json.dumps(selected, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(selection_path)
    summary["selection_manifest"] = str(selection_path)

    summary["prepare_summary"] = module.prepare_from_lake_manifest(
        lake_manifest=selection_path,
        out_root=out_root,
        instrument_registry=Path(registry),
        warmup_seconds=warmup_seconds,
        replay_mode=LEADER_REPLAY_MODE,
        force_rebuild=force_rebuild,
    )
    return summary


def _split_csv(value: str | None) -> list[str]:
    return [item for item in (value or "").split(",") if item.strip()]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--leader-manifest", type=Path, required=True,
                        help="lake_manifest_c1.json from scripts/build_leader_lake_manifest.py")
    parser.add_argument("--out-root", type=Path, required=True,
                        help="Runtime root; prepared units land under <out-root>/prepared/.")
    parser.add_argument("--symbols", required=True,
                        help="Comma-separated leader products, e.g. ES,NQ,ZN (explicit, no default).")
    parser.add_argument("--dates", default=None, help="Optional comma-separated trade dates (YYYY-MM-DD).")
    parser.add_argument("--event-ids", type=Path, default=None,
                        help="Optional JSON file with a list of event_ids to restrict to.")
    parser.add_argument("--limit", type=int, default=None, help="Optional cap on units prepared.")
    parser.add_argument("--instrument-registry", type=Path, default=None)
    parser.add_argument("--warmup-seconds", type=int, default=30)
    parser.add_argument("--force-rebuild", action="store_true")
    parser.add_argument("--dry-run", action="store_true", help="Report selection only; prepare nothing.")
    parser.add_argument("--summary-out", type=Path, default=None)
    args = parser.parse_args(argv)

    event_ids: list[str] | None = None
    if args.event_ids is not None:
        payload = json.loads(args.event_ids.read_text(encoding="utf-8"))
        if not isinstance(payload, list):
            print(json.dumps({"status": "failed", "error": "event_ids_file_not_a_list"}), file=sys.stderr)
            return 2
        event_ids = [str(item) for item in payload]

    try:
        summary = prepare_leader_units(
            leader_manifest=args.leader_manifest,
            out_root=args.out_root,
            symbols=_split_csv(args.symbols),
            dates=_split_csv(args.dates),
            event_ids=event_ids,
            limit=args.limit,
            instrument_registry=args.instrument_registry,
            warmup_seconds=args.warmup_seconds,
            force_rebuild=args.force_rebuild,
            dry_run=args.dry_run,
        )
    except LeaderPrepareError as exc:
        print(json.dumps({"status": "failed", "error": str(exc)}), file=sys.stderr)
        return 2

    if args.summary_out is not None:
        write_json_atomic(args.summary_out, summary)
    print(json.dumps(summary, indent=2, sort_keys=True, default=str))
    prepare_summary = summary.get("prepare_summary")
    if summary["dry_run"]:
        return 0
    return 0 if prepare_summary and prepare_summary.get("prepared_count", 0) > 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
