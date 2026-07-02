#!/usr/bin/env python
"""Train per-model meta-label filters with embargoed chronological splits.

Meta-labeling per Lopez de Prado (AFML ch.3): the model filters primary
signals, never generates them. Splits are chronological at EVENT level
with an embargo gap (AFML ch.7) — the LightGBM filter trains on events
up to the split date and is evaluated only on events after
split + embargo. Every artifact ships a `.meta.json` sidecar with the
ordered feature schema and the split receipt; inference fails closed on
any schema mismatch.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(REPO), str(REPO / "packages")]

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

SCHEMA_VERSION = "hft3_meta_model_training_v1"
_EVENT_DATE_RE = re.compile(r"(\d{4})_(\d{2})_(\d{2})")


def _event_date(event_id: str) -> str:
    match = _EVENT_DATE_RE.search(str(event_id))
    if not match:
        raise ValueError(f"meta_training_event_date_unparseable:{event_id}")
    return "-".join(match.groups())


def _auc(y_true: np.ndarray, scores: np.ndarray) -> float | None:
    positives = scores[y_true == 1.0]
    negatives = scores[y_true == 0.0]
    if len(positives) == 0 or len(negatives) == 0:
        return None
    greater = (positives[:, None] > negatives[None, :]).sum()
    ties = (positives[:, None] == negatives[None, :]).sum()
    return float((greater + 0.5 * ties) / (len(positives) * len(negatives)))


def train_model(
    table_path: Path,
    feature_names: list[str],
    out_dir: Path,
    model_id: str,
    *,
    eval_fraction: float,
    embargo_days: int,
    lightgbm_params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    import lightgbm

    table = (
        pd.read_parquet(table_path)
        if table_path.suffix == ".parquet"
        else pd.read_pickle(table_path)
    )
    labeled = table[table["y_meta"].notna()].copy()
    if labeled.empty:
        raise ValueError(f"meta_training_no_labeled_rows:{model_id}")
    labeled["event_date"] = labeled["event_id"].map(_event_date)
    event_dates = sorted(labeled["event_date"].unique())
    if len(event_dates) < 4:
        raise ValueError(f"meta_training_insufficient_events:{len(event_dates)}<4")
    split_index = max(1, int(len(event_dates) * (1.0 - eval_fraction)))
    split_date = event_dates[split_index - 1]
    embargo_end = (
        pd.Timestamp(split_date) + pd.Timedelta(days=embargo_days)
    ).strftime("%Y-%m-%d")
    train_rows = labeled[labeled["event_date"] <= split_date]
    eval_rows = labeled[labeled["event_date"] > embargo_end]
    if train_rows.empty or eval_rows.empty:
        raise ValueError(
            f"meta_training_empty_split:train={len(train_rows)},eval={len(eval_rows)}"
        )

    params = {
        "objective": "binary",
        "learning_rate": 0.05,
        "num_leaves": 31,
        "min_data_in_leaf": 20,
        "verbosity": -1,
        "seed": 42,
        **(lightgbm_params or {}),
    }
    train_set = lightgbm.Dataset(
        train_rows[feature_names].to_numpy(dtype=np.float64),
        label=train_rows["y_meta"].to_numpy(dtype=np.float64),
    )
    booster = lightgbm.train(params, train_set, num_boost_round=200)

    artifact = out_dir / f"meta_{model_id}.lgb.txt"
    artifact.write_text(booster.model_to_string(), encoding="utf-8")
    artifact_sha256 = hashlib.sha256(artifact.read_bytes()).hexdigest()
    train_scores = booster.predict(train_rows[feature_names].to_numpy(dtype=np.float64))
    eval_scores = booster.predict(eval_rows[feature_names].to_numpy(dtype=np.float64))
    receipt = {
        "schema_version": SCHEMA_VERSION,
        "model_id": model_id,
        "artifact": str(artifact),
        "artifact_sha256": artifact_sha256,
        "feature_names": feature_names,
        "train_events_through": split_date,
        "embargo_days": embargo_days,
        "eval_events_after": embargo_end,
        "train_rows": int(len(train_rows)),
        "eval_rows": int(len(eval_rows)),
        "train_auc": _auc(
            train_rows["y_meta"].to_numpy(dtype=np.float64), np.asarray(train_scores)
        ),
        "eval_auc": _auc(
            eval_rows["y_meta"].to_numpy(dtype=np.float64), np.asarray(eval_scores)
        ),
        "lightgbm_params": params,
    }
    sidecar = artifact.with_suffix(artifact.suffix + ".meta.json")
    sidecar.write_text(json.dumps(receipt, indent=1, sort_keys=True), encoding="utf-8")
    return receipt


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--training-receipt", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--eval-fraction", type=float, default=0.25)
    parser.add_argument("--embargo-days", type=int, default=2)
    args = parser.parse_args(argv)
    args.out_dir.mkdir(parents=True, exist_ok=True)
    training = json.loads(args.training_receipt.read_text(encoding="utf-8"))
    summary: dict[str, Any] = {}
    for model_id, entry in (training.get("models") or {}).items():
        try:
            receipt = train_model(
                Path(entry["table"]),
                [str(name) for name in entry["feature_names"]],
                args.out_dir,
                model_id,
                eval_fraction=args.eval_fraction,
                embargo_days=args.embargo_days,
            )
            summary[model_id] = {
                "eval_auc": receipt["eval_auc"],
                "artifact": receipt["artifact"],
            }
        except ValueError as exc:
            summary[model_id] = {"skipped": str(exc)}
    (args.out_dir / "meta_models_summary.json").write_text(
        json.dumps(summary, indent=1, sort_keys=True), encoding="utf-8"
    )
    print(json.dumps(summary, indent=1, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
