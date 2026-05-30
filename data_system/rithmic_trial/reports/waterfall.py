"""Build latency waterfall JSON from paper_latency_record_v1 NDJSON."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from data_system.rithmic_trial.latency.percentile_stats import stats_us
from data_system.rithmic_trial.schema.paper_latency_record_v1 import (
    PaperLatencyRecordV1,
    stage_deltas_us,
)

STAGE_ORDER = [
    "tick_to_feature_us",
    "feature_to_decision_us",
    "decision_to_construct_us",
    "construct_to_risk_us",
    "risk_to_submit_us",
    "submit_to_ack_us",
    "ack_to_exchange_status_us",
    "ack_to_fill_us",
    "tick_to_ack_us",
]


def load_records(path: Path) -> list[PaperLatencyRecordV1]:
    records: list[PaperLatencyRecordV1] = []
    if not path.is_file():
        return records
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            records.append(PaperLatencyRecordV1.from_dict(json.loads(line)))
        except json.JSONDecodeError:
            continue
    return records


def build_waterfall_report(records: list[PaperLatencyRecordV1]) -> dict[str, Any]:
    stage_values: dict[str, list[float]] = {s: [] for s in STAGE_ORDER}
    submit_ack_us: list[float] = []

    for rec in records:
        deltas = stage_deltas_us(rec)
        for stage, val in deltas.items():
            if val is not None and stage in stage_values:
                stage_values[stage].append(val)
        sa = deltas.get("submit_to_ack_us")
        if sa is not None:
            submit_ack_us.append(sa)

    stages: list[dict[str, Any]] = []
    for name in STAGE_ORDER:
        vals = stage_values[name]
        if vals:
            stages.append({"stage": name, **stats_us(vals)})

    return {
        "schema_version": "latency_waterfall_v1",
        "record_count": len(records),
        "paired_submit_ack_count": len(submit_ack_us),
        "stages": stages,
        "submit_to_ack_us": stats_us(submit_ack_us),
        "stage_order": STAGE_ORDER,
    }


def write_waterfall_report(records_path: Path, out_path: Path) -> dict[str, Any]:
    report = build_waterfall_report(load_records(records_path))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return report
