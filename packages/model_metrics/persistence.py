"""Idempotent artifact persistence for model metric bundles."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

from model_metrics.schemas import ModelBehaviorEnvelope, ModelScorecard, strict_json_dumps


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.{time.time_ns()}.tmp")
    tmp.write_text(strict_json_dumps(payload, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def write_metric_bundle(
    output_dir: Path | str,
    scorecard: ModelScorecard,
    envelope: ModelBehaviorEnvelope,
    *,
    logs: list[dict[str, Any]] | None = None,
    force: bool = True,
) -> dict[str, str]:
    """Persist metric values, scorecard, envelope, and calculation logs."""

    root = Path(output_dir)
    paths = {
        "model_metric_values": root / "model_metric_values.json",
        "model_scorecard": root / "model_scorecard.json",
        "model_behavior_envelope": root / "model_behavior_envelope.json",
        "model_metric_calculation_logs": root / "model_metric_calculation_logs.json",
    }
    if not force:
        existing = [str(path) for path in paths.values() if path.exists()]
        if existing:
            raise FileExistsError("metric artifacts already exist: " + ", ".join(existing))
    metric_payload = {
        "schema_version": "model_metrics.schema.v1",
        "scorecard_id": scorecard.scorecard_id,
        "model_id": scorecard.model_id,
        "run_id": scorecard.run_id,
        "metrics": [metric.to_dict() for metric in scorecard.metrics],
    }
    log_payload = {
        "schema_version": "model_metrics.schema.v1",
        "scorecard_id": scorecard.scorecard_id,
        "envelope_id": envelope.envelope_id,
        "calculation_version": scorecard.calculation_version,
        "logs": logs or [],
        "warnings": list(scorecard.warnings) + list(envelope.warnings),
        "errors": list(scorecard.errors),
    }
    _atomic_write_json(paths["model_metric_values"], metric_payload)
    _atomic_write_json(paths["model_scorecard"], scorecard.to_dict())
    _atomic_write_json(paths["model_behavior_envelope"], envelope.to_dict())
    _atomic_write_json(paths["model_metric_calculation_logs"], log_payload)
    return {key: str(path) for key, path in paths.items()}
