"""Validation-layer facade for institutional model metrics.

The reusable implementation lives in `model_metrics`; this module keeps HFT3
validation and promotion code importing from the validation namespace.
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any

from model_metrics.backfill import backfill_model_metrics, generate_bundle_for_run_dir
from model_metrics.envelope import generate_behavior_envelope
from model_metrics.persistence import write_metric_bundle
from model_metrics.registry import calculate_metric_values
from model_metrics.scorecard import generate_model_scorecard
from model_metrics.schemas import (
    MetricValue,
    ModelBehaviorEnvelope,
    ModelScorecard,
    strict_json_dumps,
)


MODEL_SCORECARDS_REL = Path("runtime/validation/model_scorecards")
MODEL_METRIC_VALUES_REL = Path("runtime/validation/model_metric_values.jsonl")
MODEL_CALCULATION_LOG_REL = Path("runtime/validation/model_metric_calculation_logs.jsonl")


def build_post_robustness_scorecard(inputs: dict[str, Any]) -> ModelScorecard:
    metrics = calculate_metric_values(inputs)
    return generate_model_scorecard(inputs, metrics=metrics)


def write_model_scorecard(root: Path | str, scorecard: ModelScorecard) -> Path:
    repo = Path(root)
    path = repo / MODEL_SCORECARDS_REL / f"{scorecard.scorecard_id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.{time.time_ns()}.tmp")
    tmp.write_text(strict_json_dumps(scorecard.to_dict(), indent=2), encoding="utf-8")
    os.replace(tmp, path)
    return path


def append_model_metric_values(root: Path | str, scorecard: ModelScorecard) -> Path:
    repo = Path(root)
    path = repo / MODEL_METRIC_VALUES_REL
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        for metric in scorecard.metrics:
            payload = {
                "scorecard_id": scorecard.scorecard_id,
                "model_id": scorecard.model_id,
                "model_version": scorecard.model_version,
                "run_id": scorecard.run_id,
                **metric.to_dict(),
            }
            handle.write(strict_json_dumps(payload) + "\n")
    return path


__all__ = [
    "MetricValue",
    "ModelBehaviorEnvelope",
    "ModelScorecard",
    "append_model_metric_values",
    "backfill_model_metrics",
    "build_post_robustness_scorecard",
    "calculate_metric_values",
    "generate_behavior_envelope",
    "generate_bundle_for_run_dir",
    "generate_model_scorecard",
    "write_metric_bundle",
    "write_model_scorecard",
]
