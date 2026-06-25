"""Fail-closed RL policy artifacts for the research pipeline.

This module is intentionally advisory. It can train a tiny CPU tabular policy
for unit-scale smoke tests, or produce a blocked CUDA artifact that names the
GPU handoff requirement. It never promotes a candidate.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import math
import re
import shlex
import tempfile
import time
from pathlib import Path
from typing import Any, Mapping, Sequence

from features_engine.feature_sets import MICROSTRUCTURE_FEATURE_RECEIPTS

PROMOTION_BLOCKED_STATUS = "blocked_downstream_validation_required"
SUPPORTED_RL_DEVICES = {"cpu", "cuda"}
RL_POLICY_CACHE_SCHEMA_VERSION = "hft3_rl_policy_cache_v1"
RL_POLICY_CACHE_INPUT_VERSION = "hft3_rl_policy_cache_inputs_v1"
RL_POLICY_ALGORITHM = "tabular_q_learning_research_cpu_smoke"
RL_GPU_READINESS_SCHEMA_VERSION = "hft3_rl_gpu_readiness_v1"
DEEP_RL_POLICY_SCHEMA_VERSION = "hft3_deep_rl_policy_artifact_v1"
_TIMESTAMP_FIELDS = ("timestamp_ns", "ts_ns", "timestamp", "decision_time")
_LEAKY_FEATURE_RE = re.compile(
    r"(^|_)(future|lead|next|target|label|outcome|reward)(_|$)|"
    r"^(return|pnl|profit|realized|post|after)$|"
    r"(^|_)(pnl|profit)_(net|target|label|outcome)(_|$)|"
    r"(^|_)(net|gross|realized|daily|cumulative)_(pnl|profit|return)(_|$)",
    re.IGNORECASE,
)


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
    invalid_delimiters = [name for name in clean if "|" in name or "=" in name]
    if invalid_delimiters:
        raise ValueError(
            "rl feature names must not contain state-key delimiters: "
            + ", ".join(invalid_delimiters)
        )
    leaky = [
        name
        for name in clean
        if _LEAKY_FEATURE_RE.search(_normalise_feature_name(name))
    ]
    if leaky:
        raise ValueError("rl feature names include non-PIT or label-like fields: " + ", ".join(leaky))
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


def rl_policy_cache_receipt(
    *,
    training_data_path: Path,
    feature_names: Sequence[str],
    device: str = "cpu",
    seed: int = 42,
    max_rows: int = 4096,
    allow_missing_timestamps: bool = False,
) -> dict[str, Any]:
    features = validate_rl_features(feature_names)
    device = _validate_device(device)
    max_rows = _validate_max_rows(max_rows)
    receipt = training_data_receipt(training_data_path)
    invalidation_inputs = {
        "schema_version": RL_POLICY_CACHE_INPUT_VERSION,
        "artifact_schema_version": "hft3_rl_policy_artifact_v1",
        "algorithm": RL_POLICY_ALGORITHM,
        "implementation": {
            "module": "research_pipeline.rl_agents",
            "sha256": _file_sha256(Path(__file__)),
        },
        "training_data": {
            "path": str(Path(training_data_path).resolve()),
            "sha256": receipt["sha256"],
            "size_bytes": receipt["size_bytes"],
        },
        "feature_names": features,
        "device": device,
        "seed": int(seed),
        "max_rows": max_rows,
        "allow_missing_timestamps": bool(allow_missing_timestamps),
    }
    cache_key = hashlib.sha256(
        json.dumps(invalidation_inputs, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return {
        "schema_version": RL_POLICY_CACHE_SCHEMA_VERSION,
        "cache_key": cache_key,
        "invalidation_inputs": invalidation_inputs,
        "training_data_receipt": receipt,
    }


def train_rl_policy_artifact(
    *,
    training_data_path: Path,
    feature_names: Sequence[str],
    device: str = "cpu",
    seed: int = 42,
    max_rows: int = 4096,
    allow_missing_timestamps: bool = False,
) -> dict[str, Any]:
    """Build an RL policy artifact or a CUDA handoff block."""
    started = time.perf_counter()
    features = validate_rl_features(feature_names)
    device = _validate_device(device)
    max_rows = _validate_max_rows(max_rows)
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
    source_row_count = len(rows)
    budget_exhausted = source_row_count > max_rows
    if len(rows) > max_rows:
        rows = rows[:max_rows]
    chronology = _chronology_audit(rows, allow_missing_timestamps=allow_missing_timestamps)
    reward_key = _reward_key_for_rows(rows)
    reward_metadata = _reward_metadata_for_rows(rows)
    parsed_rows = _validate_rows(rows, features, reward_key=reward_key)
    policy = _train_tabular_policy(parsed_rows, features)
    # CPU smoke keeps the final chronological row as a fixed one-row holdout.
    train_rows = max(0, len(parsed_rows) - 1)
    eval_rows = len(parsed_rows) - train_rows
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
            "source_row_count": source_row_count,
            "max_rows": max_rows,
            "reward_key": reward_key,
            "reward_metadata": reward_metadata,
            "train_eval_split": {
                "train_rows": train_rows,
                "eval_rows": eval_rows,
                "chronology_status": chronology["status"],
                "timestamp_field": chronology.get("timestamp_field"),
                "missing_timestamps_allowed": bool(allow_missing_timestamps),
            },
            "training_budget": {
                "max_rows": max_rows,
                "updates_used": train_rows,
                "budget_exhausted": budget_exhausted,
            },
            "state_count": len(policy["q_table"]),
            "action_space": ["hold", "enter_long", "enter_short"],
        },
        "q_table": policy["q_table"],
        "policy": policy["policy"],
    }


def train_or_load_rl_policy_artifact(
    *,
    training_data_path: Path,
    feature_names: Sequence[str],
    device: str = "cpu",
    seed: int = 42,
    max_rows: int = 4096,
    cache_root: Path | None = None,
    cache_enabled: bool = True,
    allow_missing_timestamps: bool = False,
) -> dict[str, Any]:
    """Load a matching CPU policy from cache, otherwise train and cache it."""
    if not cache_enabled or cache_root is None:
        artifact = train_rl_policy_artifact(
            training_data_path=training_data_path,
            feature_names=feature_names,
            device=device,
            seed=seed,
            max_rows=max_rows,
            allow_missing_timestamps=allow_missing_timestamps,
        )
        return _with_cache_receipt(artifact, status="disabled", receipt=None, cache_path=None)

    receipt = rl_policy_cache_receipt(
        training_data_path=training_data_path,
        feature_names=feature_names,
        device=device,
        seed=seed,
        max_rows=max_rows,
        allow_missing_timestamps=allow_missing_timestamps,
    )
    cache_path = Path(cache_root) / f"{receipt['cache_key']}.json"
    if receipt["invalidation_inputs"]["device"] == "cuda":
        artifact = train_rl_policy_artifact(
            training_data_path=training_data_path,
            feature_names=feature_names,
            device=device,
            seed=seed,
            max_rows=max_rows,
            allow_missing_timestamps=allow_missing_timestamps,
        )
        return _with_cache_receipt(artifact, status="blocked", receipt=receipt, cache_path=cache_path)

    if cache_path.is_file():
        try:
            cached = json.loads(cache_path.read_text(encoding="utf-8"))
            validate_rl_policy_artifact(cached)
            if _cache_artifact_matches(cached, receipt):
                return _with_cache_receipt(cached, status="hit", receipt=receipt, cache_path=cache_path)
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            pass

    artifact = train_rl_policy_artifact(
        training_data_path=training_data_path,
        feature_names=feature_names,
        device=device,
        seed=seed,
        max_rows=max_rows,
        allow_missing_timestamps=allow_missing_timestamps,
    )
    artifact = _with_cache_receipt(artifact, status="miss", receipt=receipt, cache_path=cache_path)
    write_rl_policy_artifact(cache_path, artifact)
    return artifact


def rl_gpu_training_readiness_artifact(
    *,
    training_data_path: Path,
    feature_names: Sequence[str],
    gpu_host: str,
    command: str,
    output_dir: Path | str,
    expected_duration_minutes: float,
    stop_rule: str,
    device: str = "cuda",
    require_runtime_smoke: bool = True,
    runtime_probe: Any | None = None,
) -> dict[str, Any]:
    """Build a fail-closed preflight artifact for GPU RL training."""
    started = time.perf_counter()
    blockers: list[str] = []
    features: list[str] = []
    receipt: dict[str, Any] = {}

    try:
        if _validate_device(device) != "cuda":
            blockers.append("rl_gpu_readiness_requires_cuda_device")
    except ValueError as exc:
        blockers.append(str(exc))
    try:
        features = validate_rl_features(feature_names)
    except ValueError as exc:
        blockers.append(f"invalid_rl_features:{exc}")
    try:
        receipt = training_data_receipt(training_data_path)
        rows = load_training_rows(training_data_path)
        if len(rows) < 2:
            raise ValueError("rl gpu readiness requires at least two training rows")
        chronology = _chronology_audit(rows, allow_missing_timestamps=False)
        reward_key = _reward_key_for_rows(rows)
        reward_metadata = _reward_metadata_for_rows(rows)
        if features:
            _validate_rows(rows, features, reward_key=reward_key)
        training_summary = {
            "row_count": len(rows),
            "reward_key": reward_key,
            "reward_metadata": reward_metadata,
            "chronology_status": chronology["status"],
            "timestamp_field": chronology.get("timestamp_field"),
        }
    except ValueError as exc:
        blockers.append(f"invalid_training_data:{exc}")
        training_summary = {}

    _require_non_empty_preflight("gpu_host", gpu_host, blockers)
    _require_non_empty_preflight("command", command, blockers)
    _require_resumable_or_smoke_command(command, output_dir, blockers)
    _require_non_empty_preflight("stop_rule", stop_rule, blockers)
    output_dir_value = _bounded_output_dir(output_dir, blockers)
    try:
        duration = float(expected_duration_minutes)
        if not math.isfinite(duration) or duration <= 0:
            blockers.append("expected_duration_minutes_must_be_positive")
    except (TypeError, ValueError):
        duration = 0.0
        blockers.append("expected_duration_minutes_must_be_positive")

    if require_runtime_smoke:
        try:
            runtime = (runtime_probe or probe_cuda_runtime_smoke)()
        except Exception as exc:  # pragma: no cover - defensive for injected probes.
            runtime = {"cuda_runtime_ok": False, "selected_runtime": None, "error": str(exc), "probes": []}
        if not bool(runtime.get("cuda_runtime_ok")) or runtime.get("selected_runtime") != "torch":
            blockers.append("cuda_runtime_smoke_failed")
    else:
        runtime = {"cuda_runtime_ok": None, "selected_runtime": None, "skipped": True, "probes": []}
        blockers.append("cuda_runtime_smoke_required_for_ready")

    ready = not blockers
    return {
        "schema_version": RL_GPU_READINESS_SCHEMA_VERSION,
        "status": "ready_for_gpu_training" if ready else "blocked_gpu_training_readiness",
        "ready_for_gpu_training": ready,
        "promotion_status": PROMOTION_BLOCKED_STATUS,
        "promotable": False,
        "gpu_training_required": True,
        "failure_reasons": list(dict.fromkeys(blockers)),
        "device": "cuda",
        "gpu_host": str(gpu_host or ""),
        "command": str(command or ""),
        "output_dir": output_dir_value,
        "expected_duration_minutes": duration,
        "stop_rule": str(stop_rule or ""),
        "feature_names": features,
        "training_data_receipt": receipt,
        "training_summary": training_summary,
        "runtime_smoke_required": bool(require_runtime_smoke),
        "runtime_smoke": runtime,
        "duration_seconds": _elapsed(started),
        "decision_time_boundary": (
            "readiness only; this artifact does not train, promote, or bypass "
            "VectorBT, robustness, or HftBacktest gates"
        ),
    }


def train_deep_rl_policy_artifact(
    *,
    training_data_path: Path,
    feature_names: Sequence[str],
    output_dir: Path,
    device: str = "cuda",
    seed: int = 42,
    max_rows: int = 1_000_000,
    steps: int = 1_000,
    batch_size: int = 4096,
    hidden_dim: int = 64,
    learning_rate: float = 1e-3,
    eval_fraction: float = 0.2,
    resume_checkpoint: Path | None = None,
) -> dict[str, Any]:
    """Train a research-only replay Q-network artifact with PyTorch."""
    started = time.perf_counter()
    features = validate_rl_features(feature_names)
    device = _validate_device(device)
    max_rows = _validate_max_rows(max_rows)
    steps = _positive_int(steps, "rl deep steps")
    batch_size = _positive_int(batch_size, "rl deep batch_size")
    hidden_dim = _positive_int(hidden_dim, "rl deep hidden_dim")
    learning_rate = _positive_float(learning_rate, "rl deep learning_rate")
    eval_fraction = _eval_fraction(eval_fraction)
    output_dir = Path(output_dir)
    receipt = training_data_receipt(training_data_path)
    rows = load_training_rows(training_data_path)
    if len(rows) < 3:
        raise ValueError("deep rl training data requires at least three rows")
    source_row_count = len(rows)
    if len(rows) > max_rows:
        rows = rows[:max_rows]
    chronology = _chronology_audit(rows, allow_missing_timestamps=False)
    reward_key = _reward_key_for_rows(rows)
    reward_metadata = _reward_metadata_for_rows(rows)
    parsed_rows = _validate_rows(rows, features, reward_key=reward_key)
    if resume_checkpoint is not None and not Path(resume_checkpoint).is_file():
        return _blocked_deep_rl_artifact(
            reason="resume_checkpoint_missing",
            feature_names=features,
            device=device,
            training_data_receipt=receipt,
            seed=seed,
            duration_seconds=_elapsed(started),
        )

    try:
        import torch  # type: ignore[import-not-found]
    except Exception as exc:  # pragma: no cover - depends on optional runtime.
        return _blocked_deep_rl_artifact(
            reason=f"torch_runtime_unavailable:{exc}",
            feature_names=features,
            device=device,
            training_data_receipt=receipt,
            seed=seed,
            duration_seconds=_elapsed(started),
        )
    if device == "cuda" and not bool(torch.cuda.is_available()):
        return _blocked_deep_rl_artifact(
            reason="cuda_runtime_unavailable",
            feature_names=features,
            device=device,
            training_data_receipt=receipt,
            seed=seed,
            duration_seconds=_elapsed(started),
        )

    torch.manual_seed(int(seed))
    selected_device = torch.device(device)
    x_raw = torch.tensor(
        [[float(row[feature]) for feature in features] for row in parsed_rows],
        dtype=torch.float32,
        device=selected_device,
    )
    rewards = torch.tensor(
        [float(row["reward"]) for row in parsed_rows],
        dtype=torch.float32,
        device=selected_device,
    )
    target_q = torch.stack(
        [torch.zeros_like(rewards), rewards, -rewards],
        dim=1,
    )
    train_rows, eval_rows = _train_eval_counts(len(parsed_rows), eval_fraction)
    normalizer = _fit_train_prefix_normalizer(x_raw[:train_rows], features)
    x_all = _apply_normalizer(x_raw, normalizer, selected_device)
    x_train = x_all[:train_rows]
    target_train = target_q[:train_rows]
    x_eval = x_all[train_rows:]
    target_eval = target_q[train_rows:]
    model = torch.nn.Sequential(
        torch.nn.Linear(len(features), hidden_dim),
        torch.nn.ReLU(),
        torch.nn.Linear(hidden_dim, 3),
    ).to(selected_device)
    optimizer = torch.optim.Adam(model.parameters(), lr=learning_rate)
    criterion = torch.nn.MSELoss()
    losses: list[float] = []
    if resume_checkpoint is not None:
        checkpoint = _load_deep_rl_resume_checkpoint(
            torch,
            Path(resume_checkpoint),
            selected_device,
            feature_names=features,
            normalizer=normalizer,
        )
        state = checkpoint["model_state_dict"]
        model.load_state_dict(state)
        opt_state = checkpoint.get("optimizer_state_dict") if isinstance(checkpoint, Mapping) else None
        if isinstance(opt_state, Mapping):
            optimizer.load_state_dict(opt_state)
    generator = torch.Generator(device=selected_device) if selected_device.type == "cuda" else torch.Generator()
    generator.manual_seed(int(seed))
    for _ in range(steps):
        if train_rows > batch_size:
            idx = torch.randint(0, train_rows, (batch_size,), generator=generator, device=selected_device)
            x_batch = x_train[idx]
            y_batch = target_train[idx]
        else:
            x_batch = x_train
            y_batch = target_train
        optimizer.zero_grad(set_to_none=True)
        loss = criterion(model(x_batch), y_batch)
        loss.backward()
        optimizer.step()
        losses.append(float(loss.detach().cpu().item()))

    output_dir.mkdir(parents=True, exist_ok=True)
    checkpoint_path = output_dir / "deep_rl_policy_checkpoint.pt"
    torch.save(
        {
            "schema_version": "hft3_deep_rl_policy_checkpoint_v1",
            "model_state_dict": model.state_dict(),
            "optimizer_state_dict": optimizer.state_dict(),
            "feature_names": features,
            "normalizer": normalizer,
            "seed": int(seed),
            "steps": steps,
            "action_space": ["hold", "enter_long", "enter_short"],
        },
        checkpoint_path,
    )
    with torch.no_grad():
        eval_pred = model(x_eval)
        eval_loss = float(criterion(eval_pred, target_eval).detach().cpu().item())
        action_ids = torch.argmax(eval_pred, dim=1).detach().cpu().tolist()
    action_counts = {
        "hold": int(action_ids.count(0)),
        "enter_long": int(action_ids.count(1)),
        "enter_short": int(action_ids.count(2)),
    }
    return {
        "schema_version": DEEP_RL_POLICY_SCHEMA_VERSION,
        "process": "deep_q_replay_research_gpu",
        "status": "trained_research_only",
        "promotion_status": PROMOTION_BLOCKED_STATUS,
        "promotable": False,
        "failure_reasons": [],
        "device": device,
        "runtime": "torch",
        "torch_version": str(getattr(torch, "__version__", "")),
        "cuda_device_name": str(torch.cuda.get_device_name(0)) if device == "cuda" else "",
        "implementation": {
            "module": "research_pipeline.rl_agents",
            "sha256": _file_sha256(Path(__file__)),
        },
        "gpu_training_required": device == "cuda",
        "seed": int(seed),
        "feature_names": features,
        "training_data_receipt": receipt,
        "duration_seconds": _elapsed(started),
        "decision_time_boundary": (
            "features are PIT inputs; reward is an offline replay label; "
            "artifact is research-only and non-promotable"
        ),
        "checkpoint": {
            "path": str(checkpoint_path),
            "sha256": _file_sha256(checkpoint_path),
            "resume_checkpoint": str(resume_checkpoint) if resume_checkpoint else "",
        },
        "training_summary": {
            "row_count": len(parsed_rows),
            "source_row_count": source_row_count,
            "max_rows": max_rows,
            "reward_key": reward_key,
            "reward_metadata": reward_metadata,
            "algorithm": "offline_deep_q_proxy_mse",
            "chronology_status": chronology["status"],
            "timestamp_field": chronology.get("timestamp_field"),
            "train_eval_split": {
                "method": "chronological_prefix_train_suffix_eval",
                "train_rows": train_rows,
                "eval_rows": eval_rows,
                "random_split": False,
            },
            "normalizer": normalizer,
            "training_budget": {
                "steps": steps,
                "batch_size": batch_size,
                "hidden_dim": hidden_dim,
                "learning_rate": learning_rate,
                "budget_exhausted": source_row_count > max_rows,
            },
            "loss_start": losses[0],
            "loss_end": losses[-1],
            "eval_mse": eval_loss,
            "action_space": ["hold", "enter_long", "enter_short"],
            "eval_action_counts": action_counts,
        },
        "receipts": {
            "rl_execution": "https://www.cis.upenn.edu/~mkearns/KN.html",
            "feature_registry": "features_engine.feature_sets.MICROSTRUCTURE_FEATURE_RECEIPTS",
        },
    }


def validate_rl_deep_policy_artifact(artifact: Mapping[str, Any]) -> None:
    if not isinstance(artifact, Mapping):
        raise ValueError("deep rl policy artifact must be an object")
    _require_equal(artifact, "schema_version", DEEP_RL_POLICY_SCHEMA_VERSION)
    status = _require_str(artifact, "status")
    if status not in {"trained_research_only", "blocked"}:
        raise ValueError("deep rl policy artifact status must be trained_research_only or blocked")
    _require_equal(artifact, "promotion_status", PROMOTION_BLOCKED_STATUS)
    if artifact.get("promotable") is not False:
        raise ValueError("deep rl policy artifact must be non-promotable")
    _validate_device(_require_str(artifact, "device"))
    _require_str_list(artifact, "feature_names")
    if not isinstance(artifact.get("failure_reasons"), list):
        raise ValueError("deep rl policy artifact failure_reasons must be a list")
    if not isinstance(artifact.get("training_data_receipt"), Mapping):
        raise ValueError("deep rl policy artifact training_data_receipt must be an object")
    if not isinstance(artifact.get("duration_seconds"), (int, float)):
        raise ValueError("deep rl policy artifact duration_seconds must be numeric")
    if status == "trained_research_only":
        if artifact.get("failure_reasons"):
            raise ValueError("trained deep rl artifact must not include failure reasons")
        checkpoint = artifact.get("checkpoint")
        if not isinstance(checkpoint, Mapping) or not checkpoint.get("sha256"):
            raise ValueError("trained deep rl artifact requires checkpoint sha256")
        summary = artifact.get("training_summary")
        if not isinstance(summary, Mapping) or not summary.get("action_space"):
            raise ValueError("trained deep rl artifact requires training_summary action_space")
    elif not artifact.get("failure_reasons"):
        raise ValueError("blocked deep rl artifact requires failure reasons")


def write_rl_deep_policy_artifact(path: Path, artifact: Mapping[str, Any]) -> Path:
    validate_rl_deep_policy_artifact(artifact)
    path = Path(path)
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
            handle.write(json.dumps(artifact, indent=2, sort_keys=True) + "\n")
        tmp_path.replace(path)
    finally:
        if tmp_path is not None and tmp_path.exists():
            tmp_path.unlink()
    return path


def probe_cuda_runtime_smoke() -> dict[str, Any]:
    """Check optional CUDA runtimes without making them project dependencies."""
    probes: list[dict[str, Any]] = []
    torch_probe = _probe_torch_cuda()
    probes.append(torch_probe)
    return {
        "cuda_runtime_ok": bool(torch_probe.get("cuda_runtime_ok")),
        "selected_runtime": "torch" if torch_probe.get("cuda_runtime_ok") else None,
        "probes": probes,
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
    cache_receipt = artifact.get("cache_receipt")
    if cache_receipt is not None:
        _validate_cache_receipt(cache_receipt)


def write_rl_policy_artifact(path: Path, artifact: Mapping[str, Any]) -> Path:
    validate_rl_policy_artifact(artifact)
    path = Path(path)
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
            handle.write(json.dumps(artifact, indent=2, sort_keys=True) + "\n")
        tmp_path.replace(path)
    finally:
        if tmp_path is not None and tmp_path.exists():
            tmp_path.unlink()
    return path


def _validate_device(device: str) -> str:
    value = str(device or "cpu").strip().lower()
    if value not in SUPPORTED_RL_DEVICES:
        raise ValueError("rl device must be cpu or cuda")
    return value


def _positive_int(value: int, label: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise ValueError(f"{label} must be positive")
    return parsed


def _positive_float(value: float, label: str) -> float:
    parsed = float(value)
    if not math.isfinite(parsed) or parsed <= 0.0:
        raise ValueError(f"{label} must be positive finite")
    return parsed


def _eval_fraction(value: float) -> float:
    parsed = float(value)
    if not math.isfinite(parsed) or parsed <= 0.0 or parsed >= 0.5:
        raise ValueError("rl deep eval_fraction must be in (0, 0.5)")
    return parsed


def _train_eval_counts(row_count: int, eval_fraction: float) -> tuple[int, int]:
    eval_rows = max(1, int(round(row_count * eval_fraction)))
    train_rows = row_count - eval_rows
    if train_rows < 1:
        raise ValueError("deep rl training split requires at least one train row")
    return train_rows, eval_rows


def _fit_train_prefix_normalizer(x_train: Any, feature_names: Sequence[str]) -> dict[str, Any]:
    mean = x_train.mean(dim=0)
    std = x_train.std(dim=0, unbiased=False)
    scale = std.clamp_min(1e-12)
    return {
        "method": "train_prefix_zscore",
        "feature_names": list(feature_names),
        "mean": [float(value) for value in mean.detach().cpu().tolist()],
        "scale": [float(value) for value in scale.detach().cpu().tolist()],
        "fit_rows": int(x_train.shape[0]),
        "leakage_boundary": "normalizer statistics are fit on chronological train prefix only",
    }


def _apply_normalizer(x: Any, normalizer: Mapping[str, Any], device: Any) -> Any:
    import torch  # type: ignore[import-not-found]

    mean = torch.tensor(normalizer["mean"], dtype=x.dtype, device=device)
    scale = torch.tensor(normalizer["scale"], dtype=x.dtype, device=device)
    return (x - mean) / scale


def _load_deep_rl_resume_checkpoint(
    torch_module: Any,
    path: Path,
    device: Any,
    *,
    feature_names: Sequence[str],
    normalizer: Mapping[str, Any],
) -> Mapping[str, Any]:
    try:
        checkpoint = torch_module.load(path, map_location=device, weights_only=True)
    except TypeError as exc:  # pragma: no cover - depends on optional torch version.
        raise ValueError("deep rl resume requires torch.load(weights_only=True)") from exc
    if not isinstance(checkpoint, Mapping):
        raise ValueError("deep rl resume checkpoint must be a mapping")
    if checkpoint.get("schema_version") != "hft3_deep_rl_policy_checkpoint_v1":
        raise ValueError("deep rl resume checkpoint schema mismatch")
    if list(checkpoint.get("feature_names") or []) != list(feature_names):
        raise ValueError("deep rl resume checkpoint feature_names mismatch")
    if list(checkpoint.get("action_space") or []) != ["hold", "enter_long", "enter_short"]:
        raise ValueError("deep rl resume checkpoint action_space mismatch")
    if not _normalizer_matches(checkpoint.get("normalizer"), normalizer):
        raise ValueError("deep rl resume checkpoint normalizer mismatch")
    if not isinstance(checkpoint.get("model_state_dict"), Mapping):
        raise ValueError("deep rl resume checkpoint missing model_state_dict")
    opt_state = checkpoint.get("optimizer_state_dict")
    if opt_state is not None and not isinstance(opt_state, Mapping):
        raise ValueError("deep rl resume checkpoint optimizer_state_dict mismatch")
    return checkpoint


def _normalizer_matches(candidate: Any, expected: Mapping[str, Any]) -> bool:
    if not isinstance(candidate, Mapping):
        return False
    for key in ("method", "feature_names", "mean", "scale", "fit_rows"):
        if candidate.get(key) != expected.get(key):
            return False
    return True


def _blocked_deep_rl_artifact(
    *,
    reason: str,
    feature_names: Sequence[str],
    device: str,
    training_data_receipt: Mapping[str, Any],
    seed: int,
    duration_seconds: float,
) -> dict[str, Any]:
    artifact = {
        "schema_version": DEEP_RL_POLICY_SCHEMA_VERSION,
        "process": "deep_q_replay_research_gpu",
        "status": "blocked",
        "promotion_status": PROMOTION_BLOCKED_STATUS,
        "promotable": False,
        "failure_reasons": [str(reason)],
        "device": device,
        "runtime": "torch",
        "implementation": {
            "module": "research_pipeline.rl_agents",
            "sha256": _file_sha256(Path(__file__)),
        },
        "gpu_training_required": device == "cuda",
        "seed": int(seed),
        "feature_names": list(feature_names),
        "training_data_receipt": dict(training_data_receipt),
        "duration_seconds": float(duration_seconds),
        "decision_time_boundary": "no deep RL policy trained; runtime precondition failed",
        "checkpoint": {},
        "training_summary": {},
    }
    validate_rl_deep_policy_artifact(artifact)
    return artifact


def _require_non_empty_preflight(key: str, value: Any, blockers: list[str]) -> None:
    if not isinstance(value, str) or not value.strip():
        blockers.append(f"{key}_missing")


def _require_resumable_or_smoke_command(
    command: Any,
    output_dir: Path | str,
    blockers: list[str],
) -> None:
    if not isinstance(command, str) or not command.strip():
        return
    parsed = _parse_allowed_command(command)
    if parsed is None:
        blockers.append("command_must_be_resumable_or_bounded_smoke")
        return
    entrypoint, tokens = parsed
    if entrypoint == "run_rl_gpu_smoke.py":
        if _smoke_command_output_matches(tokens, output_dir):
            return
        blockers.append("command_output_dir_mismatch")
        return
    if entrypoint == "train_deep_rl_policy.py":
        if _trainer_command_output_matches(tokens, output_dir):
            return
        blockers.append("command_output_dir_mismatch")
        return
    blockers.append("command_must_be_resumable_or_bounded_smoke")


def _parse_allowed_command(command: str) -> tuple[str, list[str]] | None:
    if any(separator in command for separator in ("&&", "||", ";", "\n", "\r", "|")):
        return None
    try:
        tokens = shlex.split(command, posix=False)
    except ValueError:
        return None
    if not tokens:
        return None
    matches = [
        (idx, entrypoint)
        for idx, token in enumerate(tokens)
        for entrypoint in [_allowed_entrypoint(token)]
        if entrypoint is not None
    ]
    if len(matches) != 1:
        return None
    idx, entrypoint = matches[0]
    if not _allowed_entrypoint_position(tokens, idx):
        return None
    return entrypoint, tokens


def _allowed_entrypoint_position(tokens: Sequence[str], idx: int) -> bool:
    if idx == 0:
        return True
    executable = Path(tokens[0].strip("\"'")).name.lower()
    if executable in {"python", "python.exe", "py", "py.exe"}:
        return idx == 1 or (idx == 2 and tokens[1].strip("\"'") == "-u")
    return False


def _allowed_entrypoint(token: str) -> str | None:
    normalized = _normalise_path_text(token.strip("\"'")).lower()
    for entrypoint in ("run_rl_gpu_smoke.py", "train_deep_rl_policy.py"):
        if normalized == entrypoint or normalized == f"scripts/{entrypoint}":
            return entrypoint
    return None


def _smoke_command_output_matches(tokens: Sequence[str], output_dir: Path | str) -> bool:
    return _command_output_dir_matches(tokens, output_dir)


def _trainer_command_output_matches(tokens: Sequence[str], output_dir: Path | str) -> bool:
    if not any(token.strip("\"'") == "--resume-checkpoint" for token in tokens):
        return False
    return _command_output_dir_matches(tokens, output_dir)


def _command_output_dir_matches(tokens: Sequence[str], output_dir: Path | str) -> bool:
    expected = _normalise_path_text(output_dir)
    for idx, token in enumerate(tokens):
        stripped = token.strip("\"'")
        if stripped == "--output-dir" and idx + 1 < len(tokens):
            return _normalise_path_text(tokens[idx + 1].strip("\"'")) == expected
        if stripped.startswith("--output-dir="):
            return _normalise_path_text(stripped.split("=", 1)[1].strip("\"'")) == expected
    return False


def _normalise_path_text(path: Path | str) -> str:
    return str(path).replace("\\", "/").strip().rstrip("/")


def _bounded_output_dir(output_dir: Path | str, blockers: list[str]) -> str:
    raw = str(output_dir or "").strip()
    if not raw:
        blockers.append("output_dir_missing")
        return raw
    path = Path(raw)
    parts = set(path.parts)
    if path.is_absolute() or ".." in parts or raw in {".", "/", "\\"} or not path.name:
        blockers.append("output_dir_must_be_bounded")
    return raw


def _probe_torch_cuda() -> dict[str, Any]:
    if importlib.util.find_spec("torch") is None:
        return {"runtime": "torch", "installed": False, "cuda_runtime_ok": False}
    try:
        import torch  # type: ignore[import-not-found]

        available = bool(torch.cuda.is_available())
        payload: dict[str, Any] = {
            "runtime": "torch",
            "installed": True,
            "cuda_available": available,
            "device_count": int(torch.cuda.device_count()) if available else 0,
            "cuda_runtime_ok": False,
        }
        if available:
            tensor = torch.ones((1,), device="cuda") + torch.ones((1,), device="cuda")
            payload["device_name"] = str(torch.cuda.get_device_name(0))
            payload["smoke_value"] = float(tensor.item())
            payload["cuda_runtime_ok"] = payload["smoke_value"] == 2.0
        return payload
    except Exception as exc:  # pragma: no cover - depends on optional local GPU libs.
        return {
            "runtime": "torch",
            "installed": True,
            "cuda_runtime_ok": False,
            "error": str(exc),
        }


def _validate_max_rows(max_rows: int) -> int:
    parsed = int(max_rows)
    if parsed < 2:
        raise ValueError("rl max_rows must be at least 2")
    return parsed


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _with_cache_receipt(
    artifact: Mapping[str, Any],
    *,
    status: str,
    receipt: Mapping[str, Any] | None,
    cache_path: Path | None,
) -> dict[str, Any]:
    out = dict(artifact)
    cache_payload: dict[str, Any] = {
        "schema_version": RL_POLICY_CACHE_SCHEMA_VERSION,
        "status": status,
        "enabled": receipt is not None,
        "cache_path": str(cache_path) if cache_path is not None else None,
    }
    if receipt is not None:
        cache_payload["cache_key"] = str(receipt["cache_key"])
        cache_payload["invalidation_inputs"] = dict(receipt["invalidation_inputs"])
    out["cache_receipt"] = cache_payload
    validate_rl_policy_artifact(out)
    return out


def _cache_receipt_matches(candidate: Any, receipt: Mapping[str, Any]) -> bool:
    if not isinstance(candidate, Mapping):
        return False
    return (
        candidate.get("schema_version") == RL_POLICY_CACHE_SCHEMA_VERSION
        and candidate.get("cache_key") == receipt.get("cache_key")
        and candidate.get("invalidation_inputs") == receipt.get("invalidation_inputs")
    )


def _cache_artifact_matches(artifact: Mapping[str, Any], receipt: Mapping[str, Any]) -> bool:
    if not _cache_receipt_matches(artifact.get("cache_receipt"), receipt):
        return False
    inputs = receipt.get("invalidation_inputs")
    if not isinstance(inputs, Mapping):
        return False
    training_data = inputs.get("training_data")
    if not isinstance(training_data, Mapping):
        return False
    artifact_training_data = artifact.get("training_data_receipt")
    if not isinstance(artifact_training_data, Mapping):
        return False
    training_summary = artifact.get("training_summary")
    if not isinstance(training_summary, Mapping):
        return False
    if artifact.get("status") != "trained_research_only":
        return False
    if artifact.get("feature_names") != inputs.get("feature_names"):
        return False
    if artifact.get("device") != inputs.get("device"):
        return False
    if int(artifact.get("seed", -1)) != int(inputs.get("seed", -2)):
        return False
    if int(training_summary.get("max_rows", -1)) != int(inputs.get("max_rows", -2)):
        return False
    return (
        artifact_training_data.get("sha256") == training_data.get("sha256")
        and artifact_training_data.get("size_bytes") == training_data.get("size_bytes")
    )


def _validate_cache_receipt(receipt: Any) -> None:
    if not isinstance(receipt, Mapping):
        raise ValueError("rl policy artifact cache_receipt must be an object")
    if receipt.get("schema_version") != RL_POLICY_CACHE_SCHEMA_VERSION:
        raise ValueError("rl policy artifact cache_receipt schema_version is invalid")
    status = receipt.get("status")
    if status not in {"disabled", "miss", "hit", "blocked"}:
        raise ValueError("rl policy artifact cache_receipt status is invalid")
    if not isinstance(receipt.get("enabled"), bool):
        raise ValueError("rl policy artifact cache_receipt enabled must be boolean")
    if receipt.get("enabled"):
        if not isinstance(receipt.get("cache_key"), str) or not receipt.get("cache_key"):
            raise ValueError("rl policy artifact cache_receipt cache_key must be a string")
        if not isinstance(receipt.get("invalidation_inputs"), Mapping):
            raise ValueError("rl policy artifact cache_receipt invalidation_inputs must be an object")


def _normalise_feature_name(name: str) -> str:
    with_pnl_boundaries = re.sub(r"pnl", "_pnl_", name, flags=re.IGNORECASE)
    with_acronym_boundaries = re.sub(r"(?<=[A-Z])(?=[A-Z][a-z])", "_", with_pnl_boundaries)
    with_boundaries = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", with_acronym_boundaries)
    return re.sub(r"[^A-Za-z0-9]+", "_", with_boundaries).lower().strip("_")


def _chronology_audit(
    rows: Sequence[Mapping[str, Any]],
    *,
    allow_missing_timestamps: bool,
) -> dict[str, str]:
    present = [
        field
        for field in _TIMESTAMP_FIELDS
        if all(isinstance(row, Mapping) and field in row for row in rows)
    ]
    if not present:
        if not allow_missing_timestamps:
            raise ValueError(
                "rl training data requires a timestamp_ns, ts_ns, timestamp, or decision_time column; "
                "set allow_missing_timestamps=True only for synthetic smoke fixtures"
            )
        return {"status": "missing_timestamp"}
    field = present[0]
    timestamps = [
        _finite_float(row[field], f"row {idx} {field}")
        for idx, row in enumerate(rows)
    ]
    for prev, cur in zip(timestamps, timestamps[1:]):
        if cur < prev:
            raise ValueError(f"rl training data timestamp {field!r} must be non-decreasing")
    return {"status": "non_decreasing_timestamp", "timestamp_field": field}


def _reward_key_for_rows(rows: Sequence[Mapping[str, Any]]) -> str:
    selected: str | None = None
    for row_idx, row in enumerate(rows):
        row_key = next(
            (key for key in ("reward", "next_return", "return") if key in row),
            None,
        )
        if row_key is None:
            raise ValueError(f"row {row_idx} missing reward, next_return, or return")
        if selected is None:
            selected = row_key
            continue
        if row_key != selected:
            raise ValueError(
                f"mixed rl reward columns: expected {selected!r}, row {row_idx} uses {row_key!r}"
            )
    if selected is None:
        raise ValueError("rl training data requires reward, next_return, or return")
    return selected


def _reward_metadata_for_rows(rows: Sequence[Mapping[str, Any]]) -> dict[str, str]:
    units = {
        str(row.get("reward_units", "unknown")).strip() or "unknown"
        for row in rows
    }
    cost_models = {
        str(row.get("reward_cost_model", "unknown")).strip() or "unknown"
        for row in rows
    }
    if len(units) != 1:
        raise ValueError("mixed rl reward_units columns")
    if len(cost_models) != 1:
        raise ValueError("mixed rl reward_cost_model columns")
    return {
        "reward_units": next(iter(units)),
        "reward_cost_model": next(iter(cost_models)),
    }


def _validate_rows(
    rows: Sequence[Mapping[str, Any]],
    feature_names: Sequence[str],
    *,
    reward_key: str,
) -> list[dict[str, float]]:
    parsed: list[dict[str, float]] = []
    for row_idx, row in enumerate(rows):
        out: dict[str, float] = {}
        for feature in feature_names:
            if feature not in row:
                raise ValueError(f"row {row_idx} missing rl feature {feature!r}")
            out[feature] = _finite_float(row[feature], f"row {row_idx} feature {feature}")
        if reward_key not in row:
            raise ValueError(f"row {row_idx} missing reward column {reward_key!r}")
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
    "DEEP_RL_POLICY_SCHEMA_VERSION",
    "PROMOTION_BLOCKED_STATUS",
    "RL_GPU_READINESS_SCHEMA_VERSION",
    "SUPPORTED_RL_DEVICES",
    "available_rl_feature_names",
    "blocked_rl_artifact",
    "load_training_rows",
    "probe_cuda_runtime_smoke",
    "rl_gpu_training_readiness_artifact",
    "rl_policy_cache_receipt",
    "train_deep_rl_policy_artifact",
    "train_rl_policy_artifact",
    "train_or_load_rl_policy_artifact",
    "training_data_receipt",
    "validate_rl_deep_policy_artifact",
    "validate_rl_features",
    "validate_rl_policy_artifact",
    "write_rl_deep_policy_artifact",
    "write_rl_policy_artifact",
]
