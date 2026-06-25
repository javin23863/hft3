"""Bounded CUDA smoke for RL training readiness.

This script is deliberately tiny and non-promotional. It proves that the chosen
host can load validated RL rows, move tensors to CUDA, run a few optimizer
steps, and write a checkpoint plus receipt. It is not a production policy
trainer.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Sequence

_REPO_ROOT = Path(__file__).resolve().parents[1]
for _path in (_REPO_ROOT, _REPO_ROOT / "packages", _REPO_ROOT / "apps"):
    value = str(_path)
    if value not in sys.path:
        sys.path.insert(0, value)

from research_pipeline.rl_agents import (
    PROMOTION_BLOCKED_STATUS,
    load_training_rows,
    training_data_receipt,
    validate_rl_features,
)


def run_smoke(
    *,
    training_data_path: Path,
    feature_names: Sequence[str],
    output_dir: Path,
    steps: int,
    seed: int,
    max_rows: int,
) -> dict[str, Any]:
    started = time.perf_counter()
    features = validate_rl_features(feature_names)
    rows = load_training_rows(training_data_path)
    if len(rows) < 2:
        raise ValueError("rl gpu smoke requires at least two rows")
    rows = rows[: max(2, int(max_rows))]
    reward_key = _reward_key_for_rows(rows)
    timestamp_field = _chronology_field(rows)
    parsed = _parse_rows(rows, features, reward_key=reward_key)

    try:
        import torch  # type: ignore[import-not-found]
    except Exception as exc:  # pragma: no cover - depends on optional host runtime.
        raise RuntimeError("PyTorch is required for the CUDA RL smoke") from exc

    if not bool(torch.cuda.is_available()):
        raise RuntimeError("PyTorch CUDA is not available on this host")

    torch.manual_seed(int(seed))
    device = torch.device("cuda")
    x = torch.tensor([row["features"] for row in parsed], dtype=torch.float32, device=device)
    y = torch.tensor([row["action"] for row in parsed], dtype=torch.long, device=device)
    model = torch.nn.Linear(len(features), 3).to(device)
    optimizer = torch.optim.Adam(model.parameters(), lr=0.001)
    criterion = torch.nn.CrossEntropyLoss()
    losses: list[float] = []
    for _ in range(max(1, int(steps))):
        optimizer.zero_grad(set_to_none=True)
        loss = criterion(model(x), y)
        loss.backward()
        optimizer.step()
        losses.append(float(loss.detach().cpu().item()))

    output_dir.mkdir(parents=True, exist_ok=True)
    checkpoint_path = output_dir / "rl_gpu_smoke_checkpoint.pt"
    torch.save(
        {
            "schema_version": "hft3_rl_gpu_smoke_checkpoint_v1",
            "model_state_dict": model.state_dict(),
            "feature_names": features,
            "seed": int(seed),
            "steps": max(1, int(steps)),
        },
        checkpoint_path,
    )
    artifact = {
        "schema_version": "hft3_rl_gpu_smoke_v1",
        "status": "gpu_smoke_passed",
        "promotion_status": PROMOTION_BLOCKED_STATUS,
        "promotable": False,
        "device": "cuda",
        "runtime": "torch",
        "cuda_device_name": str(torch.cuda.get_device_name(0)),
        "training_data_receipt": training_data_receipt(training_data_path),
        "feature_names": features,
        "row_count": len(parsed),
        "reward_key": reward_key,
        "timestamp_field": timestamp_field,
        "steps": max(1, int(steps)),
        "seed": int(seed),
        "loss_start": losses[0],
        "loss_end": losses[-1],
        "checkpoint_path": str(checkpoint_path),
        "checkpoint_sha256": _sha256_file(checkpoint_path),
        "duration_seconds": round(time.perf_counter() - started, 6),
        "decision_time_boundary": (
            "CUDA smoke only; this artifact does not train a promotable policy "
            "or bypass VectorBT, robustness, or HftBacktest gates"
        ),
    }
    _write_json_atomic(output_dir / "rl_gpu_smoke_artifact.json", artifact)
    return artifact


def _parse_rows(
    rows: Sequence[dict[str, Any]],
    feature_names: Sequence[str],
    *,
    reward_key: str,
) -> list[dict[str, Any]]:
    parsed: list[dict[str, Any]] = []
    for row_idx, row in enumerate(rows):
        features: list[float] = []
        for feature in feature_names:
            features.append(_finite_float(row.get(feature), f"row {row_idx} feature {feature}"))
        reward = _finite_float(row.get(reward_key), f"row {row_idx} reward")
        parsed.append({"features": features, "action": _reward_to_action(reward)})
    return parsed


def _chronology_field(rows: Sequence[dict[str, Any]]) -> str:
    timestamp_fields = ("timestamp_ns", "ts_ns", "timestamp", "decision_time")
    for field in timestamp_fields:
        if all(field in row for row in rows):
            values = [_finite_float(row[field], f"row {idx} {field}") for idx, row in enumerate(rows)]
            for prev, cur in zip(values, values[1:]):
                if cur < prev:
                    raise ValueError(f"rl gpu smoke timestamp {field!r} must be non-decreasing")
            return field
    raise ValueError("rl gpu smoke requires timestamp_ns, ts_ns, timestamp, or decision_time")


def _reward_key_for_rows(rows: Sequence[dict[str, Any]]) -> str:
    selected: str | None = None
    for row_idx, row in enumerate(rows):
        row_key = next((key for key in ("reward", "next_return", "return") if key in row), None)
        if row_key is None:
            raise ValueError(f"row {row_idx} missing reward, next_return, or return")
        if selected is None:
            selected = row_key
            continue
        if row_key != selected:
            raise ValueError(f"mixed rl gpu smoke reward columns: expected {selected!r}, row {row_idx} uses {row_key!r}")
    if selected is None:
        raise ValueError("rl gpu smoke requires reward, next_return, or return")
    return selected


def _reward_to_action(reward: float) -> int:
    if abs(reward) <= 1e-12:
        return 0
    return 1 if reward > 0 else 2


def _finite_float(value: Any, label: str) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be finite numeric") from exc
    if not math.isfinite(parsed):
        raise ValueError(f"{label} must be finite numeric")
    return parsed


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            tmp_path = Path(handle.name)
            handle.write(json.dumps(payload, indent=2, sort_keys=True) + "\n")
        tmp_path.replace(path)
    finally:
        if tmp_path is not None and tmp_path.exists():
            tmp_path.unlink()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--training-data", type=Path, required=True)
    parser.add_argument("--feature", action="append", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--steps", type=int, default=1)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--max-rows", type=int, default=128)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    artifact = run_smoke(
        training_data_path=args.training_data,
        feature_names=args.feature,
        output_dir=args.output_dir,
        steps=args.steps,
        seed=args.seed,
        max_rows=args.max_rows,
    )
    print(json.dumps(artifact, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
