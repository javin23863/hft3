"""Promote paper_latency daemon NDJSON into trial latency reports."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..reports.emit_reports import build_latency_profile, build_paper_order_summary
from ..reports.waterfall import write_waterfall_report
from ..schema.paper_latency_record_v1 import PaperLatencyRecordV1


def records_to_events(records: list[PaperLatencyRecordV1]) -> list[dict[str, Any]]:
    """Synthetic event list for latency_profile dimensional stats."""
    events: list[dict[str, Any]] = []
    for rec in records:
        if rec.rithmic_submit_mono_ns is None:
            continue
        events.append(
            {
                "event_type": "order_submit",
                "order_id": rec.order_id,
                "symbol": rec.symbol,
                "order_type": rec.order_type,
                "market_state": rec.market_state,
                "session_tag": rec.session_tag,
                "local_monotonic_receive_ns": rec.rithmic_submit_mono_ns,
            }
        )
        if rec.rithmic_ack_mono_ns is None or rec.rithmic_ack_mono_ns <= rec.rithmic_submit_mono_ns:
            continue
        events.append(
            {
                "event_type": "order_ack",
                "order_id": rec.order_id,
                "symbol": rec.symbol,
                "order_type": rec.order_type,
                "market_state": rec.market_state,
                "session_tag": rec.session_tag,
                "local_monotonic_receive_ns": rec.rithmic_ack_mono_ns,
            }
        )
    return events


def load_records(path: Path) -> list[PaperLatencyRecordV1]:
    out: list[PaperLatencyRecordV1] = []
    if not path.is_file():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(PaperLatencyRecordV1.from_dict(json.loads(line)))
        except json.JSONDecodeError:
            continue
    return out


def promote_from_records(records_path: Path, reports_dir: Path) -> dict[str, str]:
    records = load_records(records_path)
    events = records_to_events(records)
    latency = build_latency_profile(events)
    paper_summary = build_paper_order_summary(events)
    reports_dir.mkdir(parents=True, exist_ok=True)

    paths = {
        "latency_profile.json": reports_dir / "latency_profile.json",
        "paper_order_summary.json": reports_dir / "paper_order_summary.json",
        "latency_waterfall.json": reports_dir / "latency_waterfall.json",
        "data_capture_report.json": reports_dir / "data_capture_report.json",
    }
    paths["latency_profile.json"].write_text(
        json.dumps({"input_files": [str(records_path)], **latency}, indent=2) + "\n",
        encoding="utf-8",
    )
    paths["paper_order_summary.json"].write_text(json.dumps(paper_summary, indent=2) + "\n", encoding="utf-8")
    write_waterfall_report(records_path, paths["latency_waterfall.json"])
    paths["data_capture_report.json"].write_text(
        json.dumps(
            {
                "status": "pass" if paper_summary.get("paired_count", 0) > 0 else "warn",
                "manifest": {
                    "known_limitations": {
                        "connector": "rtrader_bridge",
                        "note": "paper_latency_daemon records.ndjson promotion",
                    }
                },
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return {k: str(v) for k, v in paths.items()}


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Promote paper latency NDJSON to trial reports")
    parser.add_argument("--records", type=Path, required=True)
    parser.add_argument("--reports-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    paths = promote_from_records(args.records.resolve(), args.reports_dir.resolve())
    for name, path in paths.items():
        print(f"Wrote {name}: {path}")
    paired = json.loads(Path(paths["paper_order_summary.json"]).read_text())["paired_count"]
    return 0 if paired > 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
