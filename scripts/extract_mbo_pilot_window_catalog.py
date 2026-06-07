"""Build mbo_pilot_window_catalog.json from pilot runtime report + raw DBN filenames."""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

_REPO = Path(__file__).resolve().parents[1]
DEFAULT_REPORT = Path(r"C:\Users\MSI\Documents\New project\runtime\data_downloads\mbo_pilot_basket_20260605.json")
DEFAULT_RAW_DIR = Path(r"C:\Users\MSI\Documents\New project\data\raw\databento_mbo\mbo_pilot_basket_20260605")
OUT_PATH = _REPO / "packages" / "data_system" / "config" / "mbo_pilot_window_catalog.json"

EVENT_ID_RE = re.compile(
    r"^(?P<etype>.+?)_(?P<y>\d{4})_(?P<m>\d{2})_(?P<d>\d{2})_TIGHT$"
)


def _parse_event_id(event_id: str) -> tuple[str, str] | None:
    match = EVENT_ID_RE.match(event_id)
    if not match:
        return None
    etype = match.group("etype")
    release_date = f"{match.group('y')}-{match.group('m')}-{match.group('d')}"
    return etype, release_date


def _load_records(report_path: Path) -> dict[str, dict[str, Any]]:
    data = json.loads(report_path.read_text(encoding="utf-8"))
    out: dict[str, dict[str, Any]] = {}
    for rec in data.get("records", []):
        eid = rec.get("event_id")
        if eid:
            out[eid] = rec
    return out


def _load_events_csv() -> dict[str, dict[str, Any]]:
    import sys

    if str(_REPO) not in sys.path:
        sys.path.insert(0, str(_REPO))
    for sub in ("packages", "apps"):
        p = str(_REPO / sub)
        if p not in sys.path:
            sys.path.insert(0, p)
    from data_system.src.events_parser import load_and_parse_events

    csv = _REPO / "packages" / "data_system" / "config" / "events.csv"
    df = load_and_parse_events(str(csv))
    return {str(row["event_id"]): row.to_dict() for _, row in df.iterrows()}


def _template_times(records: dict[str, dict[str, Any]]) -> dict[str, dict[str, str]]:
    templates: dict[str, dict[str, str]] = {}
    for rec in records.values():
        et = rec.get("event_type")
        if et and rec.get("start_utc") and et not in templates:
            templates[et] = {
                "start_utc": rec["start_utc"],
                "end_utc": rec["end_utc"],
                "duration_seconds": rec.get("duration_seconds"),
            }
    return templates


def _timing_from_template(
    event_type: str,
    release_date: str,
    templates: dict[str, dict[str, str]],
) -> dict[str, Any] | None:
    tpl = templates.get(event_type)
    if not tpl or not tpl.get("start_utc"):
        return None
    tpl_start = datetime.fromisoformat(tpl["start_utc"].replace("Z", "+00:00"))
    tpl_end = datetime.fromisoformat(tpl["end_utc"].replace("Z", "+00:00"))
    y, m, d = (int(x) for x in release_date.split("-"))
    start = tpl_start.replace(year=y, month=m, day=d)
    end = tpl_end.replace(year=y, month=m, day=d)
    return {
        "start_utc": start.isoformat(),
        "end_utc": end.isoformat(),
        "duration_seconds": int((end - start).total_seconds()),
    }


def _event_ids_from_raw(raw_dir: Path) -> list[str]:
    ids: list[str] = []
    for path in sorted(raw_dir.glob("*_mbo.dbn.zst")):
        name = path.name[: -len("_mbo.dbn.zst")]
        ids.append(name)
    return ids


def build_catalog(
    report_path: Path = DEFAULT_REPORT,
    raw_dir: Path = DEFAULT_RAW_DIR,
) -> dict[str, Any]:
    records = _load_records(report_path)
    csv_rows = _load_events_csv()
    templates = _template_times(records)
    event_ids = _event_ids_from_raw(raw_dir) if raw_dir.is_dir() else sorted(records.keys())

    windows: list[dict[str, Any]] = []
    for event_id in event_ids:
        parsed = _parse_event_id(event_id)
        if not parsed:
            continue
        event_type, release_date = parsed
        row: dict[str, Any] = {
            "event_id": event_id,
            "event_type": event_type,
            "release_date": release_date,
            "window_name": "TIGHT",
        }
        if event_id in records:
            rec = records[event_id]
            row["start_utc"] = rec["start_utc"]
            row["end_utc"] = rec["end_utc"]
            row["duration_seconds"] = rec.get("duration_seconds")
            row["timing_source"] = "runtime_report"
        elif event_id in csv_rows:
            ev = csv_rows[event_id]
            row["start_utc"] = ev["start_utc"].isoformat() if hasattr(ev["start_utc"], "isoformat") else str(ev["start_utc"])
            row["end_utc"] = ev["end_utc"].isoformat() if hasattr(ev["end_utc"], "isoformat") else str(ev["end_utc"])
            row["duration_seconds"] = int((ev["end_utc"] - ev["start_utc"]).total_seconds())
            row["timing_source"] = "events_csv"
        else:
            timing = _timing_from_template(event_type, release_date, templates)
            if not timing:
                continue
            row.update(timing)
            row["timing_source"] = "event_type_template"
        windows.append(row)

    windows.sort(key=lambda w: (w["event_type"], w["release_date"], w["event_id"]))
    return {
        "schema_version": 1,
        "source_run_id": "mbo_pilot_basket_20260605",
        "source_report": str(report_path),
        "source_raw_dir": str(raw_dir),
        "window_count": len(windows),
        "event_types": sorted({w["event_type"] for w in windows}),
        "windows": windows,
    }


def main() -> int:
    catalog = build_catalog()
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(catalog, indent=2), encoding="utf-8")
    print(f"wrote {OUT_PATH} windows={catalog['window_count']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
