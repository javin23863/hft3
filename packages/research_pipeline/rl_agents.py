"""Fail-closed RL policy artifacts for the research pipeline.

This module is intentionally advisory. It can train a tiny CPU tabular policy
for unit-scale smoke tests, or produce a blocked CUDA artifact that names the
GPU handoff requirement. It never promotes a candidate.
"""

from __future__ import annotations

import hashlib
import json
import math
import time
from pathlib import Path
from typing import Any, Mapping, Sequence

from features_engine.feature_sets import MICROSTRUCTURE_FEATURE_RECEIPTS

PROMOTION_BLOCKED_STATUS = "blocked_downstream_validation_required"
SUPPORTED_RL_DEVICES = {"cpu", "cuda"}


def available_rl_feature_names() -> set[str]:
    features = MICROSTRUCTURE_FEATURE_RECEIPTS.get("features", {})
    return set(features) if isinstance(features, Mapping) else set()


def validate_rl_features(feature_names: Sequence[str]) -> list[str]:
    if isinstance(feature_names, str) or not isinstance(feature_names, Sequence):
        raise ValueError("rl feature names must be a non-empty sequence")
    clean = [str(name).strip() for name in feature_names if str(name).strip()]
    if not clean:
        raise ValueError("rl feature names must not be empty")
    if len(set(clean)) != len(clean):
        raise ValueError("rl feature names must be unique")
    allowed = available_rl_feature_names()
    unknown = sorted(name for name in clean if name not in allowed)
    if unknown:
        raise ValueError("unknown rl feature names: " + ", ".join(unknown))
    return clean


def load_training_rows(path: Path) -> list[dict[str, Any]]:
    path = Path(path)
    if not path.is_file():
        raise ValueError(f"rl training data does not exist: {path}")
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        raise ValueError("rl training data is empty")
    if path.suffix.lower() == ".jsonl":
        rows = [json.loads(line) for line in text.splitlines() if line.strip()]
    else:
        body = json.loads(text)
        rows = body.get("rows") if isinstance(body, Mapping) else body
    if not isinstance(rows, list) or not all(isinstance(row, Mapping) for row in rows):
        raise ValueError("rl training data must contain row objects")
    return [dict(row) for row in rows]


def training_data_receipt(path: Path) -> dict[str, Any]:
    path = Path(path)
    if not path.is_file():
        raise ValueError(f"rl training data does not exist: {path}")
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    stat = path.stat()
    return {
        "path": str(path),
        "sha256": digest.hexdigest(),
        "size_bytes": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
    }


def train_rl_policy_artifact(
    *,
    training_data_path: Path,
    feature_names: Sequence[str],
    device: str = "cpu",
    seed: int = 42,
    max_rows: int = 4096,
) -> dict[str, Any]:
    """Build an RL policy artifact or a CUDA handoff block."""
    started = time.perf_counter()
    features = validate_rl_features(feature_names)
    device = _validate_device(device)
    receipt = training_data_receipt(training_data_path)
    if device == "cuda":
        return blocked_rl_artifact(
            reason="cuda_training_requires_gpu_subagent",
            feature_names=features,
            device=device,
            training_data_receipt=receipt,
            seed=seed,
            duration_seconds=_elapsed(started),
            gpu_training_required=True,
        )

    rows = load_training_rows(training_data_path)
    if len(rows) < 2:
        raise ValueError("rl training data requires at least two rows")
    if len(rows) > max_rows:
        rows = rows[:max_rows]
    parsed_rows = _validate_rows(rows, features)
    policy = _train_tabular_policy(parsed_rows, features)
    return {
        "schema_version": "hft3_rl_policy_artifact_v1",
        "process": "tabular_q_learning_research_cpu_smoke",
        "status": "trained_research_only",
        "promotion_status": PROMOTION_BLOCKED_STATUS,
        "promotable": False,
        "failure_reasons": [],
        "device": device,
        "gpu_training_required": False,
        "seed": int(seed),
        "feature_names": features,
        "training_data_receipt": receipt,
        "duration_seconds": _elapsed(started),
        "decision_time_boundary": "features are read from each row at decision time t; reward is an explicit row field",
        "receipts": {
            "rl_execution": "https://www.cis.upenn.edu/~mkearns/papers/rlexec.pdf",
            "gpu_economics": "https://www.quantstart.com/articles/should-you-buy-or-rent-a-gpu-based-deep-learning-machine-for-quant-trading-research/",
            "feature_registry": "features_engine.feature_sets.MICROSTRUCTURE_FEATURE_RECEIPTS",
        },
        "training_summary": {
            "row_count": len(parsed_rows),
            "max_rows": max_rows,
            "state_count": len(policy["q_table"]),
            "action_space": ["hold", "enter_long", "enter_short"],
        },
        "q_table": policy["q_table"],
        "policy": policy["policy"],
    }


def blocked_rl_artifact(
    *,
    reason: str,
    feature_names: Sequence[str] | None = None,
    device: str = "cpu",
    training_data_receipt: Mapping[str, Any] | None = None,
    seed: int = 42,
    duration_seconds: float = 0.0,
    gpu_training_required: bool = False,
) -> dict[str, Any]:
    device = _validate_device(device)
    return {
        "schema_version": "hft3_rl_policy_artifact_v1",
        "process": "rl_training_research",
        "status": "blocked",
        "promotion_status": PROMOTION_BLOCKED_STATUS,
        "promotable": False,
        "failure_reasons": [str(reason)],
        "device": device,
        "gpu_training_required": bool(gpu_training_required),
        "seed": int(seed),
        "feature_names": list(feature_names or []),
        "training_data_receipt": dict(training_data_receipt or {}),
        "duration_seconds": float(duration_seconds),
        "decision_time_boundary": "no RL policy trained; required training handoff is blocked",
        "q_table": [],
        "policy": {},
    }


def validate_rl_policy_artifact(artifact: Mapping[str, Any]) -> None:
    if not isinstance(artifact, Mapping):
        raise ValueError("rl policy artifact must be an object")
    _require_equal(artifact, "schema_version", "hft3_rl_policy_artifact_v1")
    status = _require_str(artifact, "status")
    if status not in {"trained_research_only", "blocked"}:
        raise ValueError("rl policy artifact status must be trained_research_only or blocked")
    _require_equal(artifact, "promotion_status", PROMOTION_BLOCKED_STATUS)
    if artifact.get("promotable") is not False:
        raise ValueError("rl policy artifact must be non-promotable")
    _validate_device(_require_str(artifact, "device"))
    _require_str_list(artifact, "feature_names")
    if not isinstance(artifact.get("failure_reasons"), list):
        raise ValueError("rl policy artifact failure_reasons must be a list")
    if not isinstance(artifact.get("training_data_receipt"), Mapping):
        raise ValueError("rl policy artifact training_data_receipt must be an object")
    if not isinstance(artifact.get("duration_seconds"), (int, float)):
        raise ValueError("rl policy artifact duration_seconds must be numeric")
    if artifact.get("promotable") is not False:
        raise ValueError("rl policy artifact must not be promotable")
    if status == "trained_research_only":
        if artifact.get("failure_reasons"):
            raise ValueError("trained rl policy artifact must not include failure reasons")
        if not artifact.get("q_table"):
            raise ValueError("trained rl policy artifact requires q_table")
        if not artifact.get("policy"):
            raise ValueError("trained rl policy artifact requires policy")
    else:
        if not artifact.get("failure_reasons"):
            raise ValueError("blocked rl policy artifact requires failure reasons")


def write_rl_policy_artifact(path: Path, artifact: Mapping[str, Any]) -> Path:
    validate_rl_policy_artifact(artifact)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(artifact, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def _validate_device(device: str) -> str:
    value = str(device or "cpu").strip().lower()
    if value not in SUPPORTED_RL_DEVICES:
        raise ValueError("rl device must be cpu or cuda")
    return value


def _validate_rows(rows: Sequence[Mapping[str, Any]], feature_names: Sequence[str]) -> list[dict[str, float]]:
    parsed: list[dict[str, float]] = []
    for row_idx, row in enumerate(rows):
        out: dict[str, float] = {}
        for feature in feature_names:
            if feature not in row:
                raise ValueError(f"row {row_idx} missing rl feature {feature!r}")
            out[feature] = _finite_float(row[feature], f"row {row_idx} feature {feature}")
        reward_key = "reward" if "reward" in row else "next_return" if "next_return" in row else "return"
        if reward_key not in row:
            raise ValueError(f"row {row_idx} missing reward, next_return, or return")
        out["reward"] = _finite_float(row[reward_key], f"row {row_idx} reward")
        parsed.append(out)
    return parsed


def _train_tabular_policy(rows: Sequence[Mapping[str, float]], feature_names: Sequence[str]) -> dict[str, Any]:
    states: dict[str, dict[str, float]] = {}
    for row in rows[:-1]:
        state = _state_key(row, feature_names)
        reward = float(row["reward"])
        actions = states.setdefault(state, {"hold": 0.0, "enter_long": 0.0, "enter_short": 0.0})
        actions["enter_long"] += reward
        actions["enter_short"] -= reward
    q_table = []
    policy = {}
    for state in sorted(states):
        actions = {action: round(value, 10) for action, value in states[state].items()}
        best_action = max(actions, key=lambda action: (actions[action], -["hold", "enter_long", "enter_short"].index(action)))
        policy[state] = best_action
        q_table.append({"state_key": state, "action_values": actions, "best_action": best_action})
    return {"q_table": q_table, "policy": policy}


def _state_key(row: Mapping[str, float], feature_names: Sequence[str]) -> str:
    return "|".join(f"{feature}={_bucket(float(row[feature]))}" for feature in feature_names)


def _bucket(value: float) -> str:
    if abs(value) <= 1e-12:
        return "zero"
    return "pos" if value > 0 else "neg"


def _finite_float(value: Any, label: str) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be finite numeric") from exc
    if not math.isfinite(parsed):
        raise ValueError(f"{label} must be finite numeric")
    return parsed


def _elapsed(started: float) -> float:
    return round(time.perf_counter() - started, 6)


def _require_equal(artifact: Mapping[str, Any], key: str, expected: Any) -> None:
    if artifact.get(key) != expected:
        raise ValueError(f"rl policy artifact {key} must be {expected!r}")


def _require_str(artifact: Mapping[str, Any], key: str) -> str:
    value = artifact.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"rl policy artifact {key} must be a non-empty string")
    return value


def _require_str_list(artifact: Mapping[str, Any], key: str) -> list[str]:
    value = artifact.get(key)
    if not isinstance(value, list) or not all(isinstance(item, str) and item for item in value):
        raise ValueError(f"rl policy artifact {key} must be a list of strings")
    return value


__all__ = [
    "PROMOTION_BLOCKED_STATUS",
    "SUPPORTED_RL_DEVICES",
    "available_rl_feature_names",
    "blocked_rl_artifact",
    "load_training_rows",
    "train_rl_policy_artifact",
    "training_data_receipt",
    "validate_rl_features",
    "validate_rl_policy_artifact",
    "write_rl_policy_artifact",
]
