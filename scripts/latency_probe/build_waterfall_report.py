#!/usr/bin/env python3
"""Build human-readable + JSON latency waterfall from paper_latency records.ndjson."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from data_system.rithmic_trial.reports.waterfall import build_waterfall_report, load_records, write_waterfall_report


def render_markdown(report: dict) -> str:
    lines = [
        "# Paper order latency waterfall",
        "",
        f"- Records: {report.get('record_count', 0)}",
        f"- Paired submit→ack: {report.get('paired_submit_ack_count', 0)}",
        "",
        "| Stage | p50 µs | p90 µs | p99 µs | p99.9 µs | count |",
        "|-------|--------|--------|--------|----------|-------|",
    ]
    for stage in report.get("stages") or []:
        lines.append(
            f"| {stage.get('stage')} "
            f"| {stage.get('p50_us')} "
            f"| {stage.get('p90_us')} "
            f"| {stage.get('p99_us')} "
            f"| {stage.get('p999_us')} "
            f"| {stage.get('count')} |"
        )
    sa = report.get("submit_to_ack_us") or {}
    lines.extend(
        [
            "",
            f"**submit→ack p99:** {sa.get('p99_us')} µs",
        ]
    )
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build latency waterfall report")
    parser.add_argument("--records", type=Path, required=True, help="records.ndjson path")
    parser.add_argument("--out-json", type=Path, default=None)
    parser.add_argument("--out-md", type=Path, default=None)
    args = parser.parse_args(argv)

    records = load_records(args.records.resolve())
    report = build_waterfall_report(records)

    out_json = args.out_json or args.records.parent / "latency_waterfall.json"
    write_waterfall_report(args.records.resolve(), out_json)
    print(f"Wrote {out_json}")

    if args.out_md:
        args.out_md.write_text(render_markdown(report), encoding="utf-8")
        print(f"Wrote {args.out_md}")

    print(json.dumps(report.get("submit_to_ack_us"), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
