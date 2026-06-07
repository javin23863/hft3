#!/usr/bin/env python3
"""Probe Databento catalog for VIX/VVIX index sensors at CME priority event windows."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))
for sub in ("packages", "apps"):
    p = str(_REPO / sub)
    if p not in sys.path:
        sys.path.insert(0, p)

from hft3_bootstrap import setup_repo_paths

setup_repo_paths()

try:
    from dotenv import load_dotenv

    load_dotenv(_REPO / ".env")
except ImportError:
    pass

OUT_PATH = _REPO / "runtime" / "data_downloads" / "vix_vvix_databento_catalog_probe.json"

SYMBOLS = ("VIX", "VVIX")
STYPES_IN = ("parent", "native", "continuous")
LIGHT_SCHEMAS = ("ohlcv-1s", "ohlcv-1m", "bbo-1s", "statistics", "trades", "tbbo")


def _resolve(client, dataset: str, symbol: str, stype_in: str, day: str) -> dict | None:
    try:
        res = client.symbology.resolve(
            dataset=dataset,
            symbols=[symbol],
            stype_in=stype_in,
            stype_out="raw_symbol",
            start_date=day,
            end_date=day,
        )
    except Exception as exc:
        return {"ok": False, "error": str(exc).split("\n")[0][:200]}
    mappings = res.get("result") or res.get("mappings") or res
    if not isinstance(mappings, dict):
        return {"ok": False, "error": "empty resolve payload"}
    mapped = mappings.get(symbol)
    if not mapped:
        return {"ok": False, "error": "symbol not in resolve result"}
    return {"ok": True, "mapped": mapped}


def _probe_cost(client, dataset: str, symbol: str, stype_in: str, schema: str, start, end) -> float | None:
    try:
        return float(
            client.metadata.get_cost(
                dataset=dataset,
                schema=schema,
                symbols=[symbol],
                stype_in=stype_in,
                start=start,
                end=end,
            )
        )
    except Exception:
        return None


def main() -> int:
    import os

    import databento as db
    from economic_event_universe.events_csv_builder import resolve_download_scope_windows
    from mbo_release_lane.constants import PRIORITY_DOWNLOAD_EVENT_TYPES
    from mbo_release_lane.download import filter_windows_by_event_type, resolve_download_exclusions

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=OUT_PATH)
    args = parser.parse_args()

    if not os.getenv("DATABENTO_API_KEY"):
        print("DATABENTO_API_KEY not set", file=sys.stderr)
        return 1

    windows = resolve_download_scope_windows(_REPO, "macro_releases")
    windows = filter_windows_by_event_type(windows, exclude_event_types=resolve_download_exclusions())
    priority = [w for w in windows if w.event_type in PRIORITY_DOWNLOAD_EVENT_TYPES]

    probe_start = datetime(2024, 9, 11, 12, 28, 0, tzinfo=timezone.utc)
    probe_end = datetime(2024, 9, 11, 12, 30, 10, tzinfo=timezone.utc)
    probe_day = probe_start.date().isoformat()

    client = db.Historical(os.environ["DATABENTO_API_KEY"])
    datasets_raw = client.metadata.list_datasets()
    datasets: list[str] = []
    for row in datasets_raw:
        if isinstance(row, dict):
            datasets.append(str(row.get("dataset", "")))
        else:
            datasets.append(str(row))
    datasets = sorted({d for d in datasets if d})

    resolve_hits: list[dict] = []
    cost_hits: list[dict] = []
    dataset_rows: list[dict] = []

    for ds in datasets:
        try:
            schemas = client.metadata.list_schemas(dataset=ds)
        except Exception as exc:
            dataset_rows.append({"dataset": ds, "schemas": None, "error": str(exc)[:120]})
            continue
        dataset_rows.append({"dataset": ds, "schemas": schemas})
        print(f"probe {ds}", flush=True)

        for sym in SYMBOLS:
            for stype in STYPES_IN:
                resolved = _resolve(client, ds, sym, stype, probe_day)
                if not resolved or not resolved.get("ok"):
                    continue
                row = {
                    "dataset": ds,
                    "symbol": sym,
                    "stype_in": stype,
                    "probe_day": probe_day,
                    "mapped": resolved["mapped"],
                }
                resolve_hits.append(row)
                for schema in LIGHT_SCHEMAS:
                    if schema not in schemas:
                        continue
                    cost = _probe_cost(client, ds, sym, stype, schema, probe_start, probe_end)
                    if cost is None:
                        continue
                    cost_hits.append({**row, "schema": schema, "probe_cost_usd": round(cost, 6)})

    cost_estimate = None
    if cost_hits:
        best = cost_hits[0]
        by_type: dict[str, object] = {}
        for w in priority:
            et = w.event_type
            if et not in by_type or w.release_date > by_type[et].release_date:  # type: ignore[index]
                by_type[et] = w
        unit: dict[str, float] = {}
        for et, w in sorted(by_type.items()):
            s = w.start_utc.to_pydatetime() if hasattr(w.start_utc, "to_pydatetime") else w.start_utc
            e = w.end_utc.to_pydatetime() if hasattr(w.end_utc, "to_pydatetime") else w.end_utc
            cost = _probe_cost(
                client,
                best["dataset"],
                best["symbol"],
                best["stype_in"],
                best["schema"],
                s,
                e,
            )
            if cost is not None:
                unit[et] = cost
        per_sensor = sum(unit[et] * len([w for w in priority if w.event_type == et]) for et in unit)
        cost_estimate = {
            "method": "sample_per_event_type_latest_window",
            "config": best,
            "types_priced": len(unit),
            "priority_windows": len(priority),
            "per_sensor_usd": round(per_sensor, 2),
            "vix_and_vvix_usd": round(per_sensor * 2, 2),
        }

    report = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "repo_inventory_expectation": {
            "vix_dataset_id": "futures:databento:CBOE:none:VIX",
            "vvix_dataset_id": "futures:databento:CBOE:none:VVIX",
            "inventory_action": "ignore (unavailable on Databento)",
            "fallback": "Use licensed CBOE index sensor feed; do not force MBO schema",
        },
        "cme_priority_lane": {
            "event_types": len(PRIORITY_DOWNLOAD_EVENT_TYPES),
            "windows": len(priority),
            "vix_vvix_slots_if_available": len(priority) * 2,
            "window_shape": "T-60s to T+10s (same as GLBX.MDP3 MBO lane)",
        },
        "probe_window": {
            "start": probe_start.isoformat(),
            "end": probe_end.isoformat(),
            "note": "Representative CPI macro release window",
        },
        "symbols_probed": list(SYMBOLS),
        "datasets_scanned": len(datasets),
        "symbology_resolve_hits": resolve_hits,
        "get_cost_hits": cost_hits,
        "pullable_on_databento": bool(cost_hits),
        "estimated_download_cost_usd": cost_estimate,
        "conclusion": (
            "VIX/VVIX are pullable via Databento"
            if cost_hits
            else "VIX/VVIX do not resolve on any Databento dataset for this account; download cost N/A"
        ),
        "datasets": dataset_rows,
    }

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"Wrote {args.output}")
    print(f"resolve_hits={len(resolve_hits)} cost_hits={len(cost_hits)} priority_windows={len(priority)}")
    print(report["conclusion"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
