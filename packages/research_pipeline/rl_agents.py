"""Deterministic RL research process for autoresearch candidates.

This module is intentionally research-pipeline scoped. It produces auditable
policy artifacts for screening and review, not deployable execution logic.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import math
from pathlib import Path
import random
import re
from typing import Any, Callable, Iterable, Mapping, Sequence


DEFAULT_ACTION_SPACE = ("hold", "enter_long", "enter_short", "exit")
PROMOTION_BLOCKED_STATUS = "blocked_downstream_validation_required"
_TIMESTAMP_FIELDS = ("timestamp_ns", "ts_ns", "timestamp", "decision_time")
_LEAKY_FEATURE_RE = re.compile(
    r"(^|_)(future|lead|next|target|label|outcome|reward)(_|$)|"
    r"^(return|pnl|profit|realized|post|after)$|"
    r"(^|_)(pnl|profit)_(net|target|label|outcome)(_|$)|"
    r"(^|_)(net|gross|realized|daily|cumulative)_(pnl|profit|return)(_|$)",
    re.IGNORECASE,
)

RewardFunction = Callable[[Mapping[str, float], str, Mapping[str, float] | None, int], float]


@dataclass(frozen=True)
class RLBudget:
    episodes: int = 4
    max_steps_per_episode: int = 128
    max_updates: int | None = None

    def validate(self) -> None:
        if self.episodes <= 0:
            raise ValueError("episodes must be positive")
        if self.max_steps_per_episode <= 0:
            raise ValueError("max_steps_per_episode must be positive")
        if self.max_updates is not None and self.max_updates <= 0:
            raise ValueError("max_updates must be positive when provided")

    def resolved_max_updates(self) -> int:
        self.validate()
        return self.max_updates or self.episodes * self.max_steps_per_episode


def load_training_rows(path: Path) -> list[dict[str, Any]]:
    """Load RL rows from JSON or JSONL.

    JSON input may be a list of row objects or an object with a ``rows`` list.
    JSONL input expects one row object per non-empty line.
    """
    path = Path(path)
    if not path.is_file():
        raise ValueError(f"training data does not exist: {path}")
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        raise ValueError("training data is empty")
    if path.suffix.lower() == ".jsonl":
        rows = [json.loads(line) for line in text.splitlines() if line.strip()]
    else:
        body = json.loads(text)
        rows = body.get("rows") if isinstance(body, dict) else body
    if not isinstance(rows, list):
        raise ValueError("training data must be a list of row objects")
    return rows


def train_rl_agent(
    data: Sequence[Mapping[str, Any]],
    feature_names: Sequence[str],
    reward_function: RewardFunction | None = None,
    *,
    action_space: Sequence[str] = DEFAULT_ACTION_SPACE,
    seed: int = 42,
    episodes: int = 4,
    max_steps_per_episode: int = 128,
    max_updates: int | None = None,
    learning_rate: float = 0.20,
    discount: float = 0.90,
    epsilon: float = 0.05,
    train_fraction: float = 0.70,
) -> dict[str, Any]:
    """Train a small tabular Q-learning policy and return an auditable artifact.

    The state at row ``t`` is built only from the declared feature values present
    in row ``t``. The default reward consumes an explicit row reward/return field;
    callers can provide a custom reward function for tests or later adapters.
    """
    feature_names = _validate_feature_names(feature_names)
    action_space = _validate_action_space(action_space)
    budget = RLBudget(
        episodes=episodes,
        max_steps_per_episode=max_steps_per_episode,
        max_updates=max_updates,
    )
    budget.validate()
    rows = _validate_rows(data, feature_names)
    if len(rows) < 2:
        raise ValueError("RL training requires at least two rows for train/eval split")
    chronology = _chronology_audit(rows)
    if not (0.0 < train_fraction < 1.0):
        raise ValueError("train_fraction must be between 0 and 1")
    if not (0.0 < learning_rate <= 1.0):
        raise ValueError("learning_rate must be in (0, 1]")
    if not (0.0 <= discount <= 1.0):
        raise ValueError("discount must be in [0, 1]")
    if not (0.0 <= epsilon <= 1.0):
        raise ValueError("epsilon must be in [0, 1]")

    split_index = int(round(len(rows) * train_fraction))
    split_index = min(max(split_index, 1), len(rows) - 1)
    train_rows = rows[:split_index]
    eval_rows = rows[split_index:]
    resolved_max_updates = budget.resolved_max_updates()

    rng = random.Random(seed)
    q_table: dict[str, dict[str, float]] = {}
    updates_used = 0

    for _episode in range(episodes):
        if updates_used >= resolved_max_updates:
            break
        offset = rng.randrange(len(train_rows))
        steps_this_episode = min(max_steps_per_episode, len(train_rows) - offset)
        for local_step in range(steps_this_episode):
            if updates_used >= resolved_max_updates:
                break
            row_index = offset + local_step
            row = train_rows[row_index]
            next_row = train_rows[row_index + 1] if row_index + 1 < len(train_rows) else None
            state_key = _state_key(row, feature_names)
            next_state_key = _state_key(next_row, feature_names) if next_row is not None else None
            state_values = q_table.setdefault(state_key, _zero_actions(action_space))
            action = (
                rng.choice(tuple(action_space))
                if rng.random() < epsilon
                else _best_action(state_values, action_space)
            )
            reward = _reward(
                row,
                action,
                next_row,
                row_index,
                reward_function=reward_function,
            )
            next_best = 0.0
            if next_state_key is not None:
                next_values = q_table.setdefault(next_state_key, _zero_actions(action_space))
                next_best = max(next_values.values())
            old_value = state_values[action]
            state_values[action] = round(
                old_value + learning_rate * (reward + discount * next_best - old_value),
                10,
            )
            updates_used += 1

    eval_rewards = _evaluate_policy(
        eval_rows,
        feature_names,
        action_space,
        q_table,
        reward_function=reward_function,
    )
    q_entries = _serialise_q_table(q_table, action_space)
    policy = {entry["state_key"]: entry["best_action"] for entry in q_entries}
    reward_definition = (
        f"callable:{getattr(reward_function, '__name__', 'anonymous')}"
        if reward_function is not None
        else "default row reward: enter_long=reward, enter_short=-reward, hold/exit=0"
    )

    return {
        "schema_version": "1",
        "process": "tabular_q_learning_research",
        "status": "trained_research_only",
        "promotion_status": PROMOTION_BLOCKED_STATUS,
        "promotable": False,
        "failure_reasons": [],
        "seed": seed,
        "feature_names": list(feature_names),
        "action_space": list(action_space),
        "reward_definition": reward_definition,
        "decision_time_boundary": (
            "state uses declared feature values from row t only; reward must be "
            "recorded or computed under the caller's train/eval split"
        ),
        "train_eval_split": {
            "total_rows": len(rows),
            "train_rows": len(train_rows),
            "eval_rows": len(eval_rows),
            "train_fraction": train_fraction,
            "split_index": split_index,
            "chronology_status": chronology["status"],
            "timestamp_field": chronology.get("timestamp_field"),
        },
        "training_budget": {
            "episodes": episodes,
            "max_steps_per_episode": max_steps_per_episode,
            "max_updates": resolved_max_updates,
            "updates_used": updates_used,
            "budget_exhausted": updates_used >= resolved_max_updates,
        },
        "hyperparameters": {
            "learning_rate": learning_rate,
            "discount": discount,
            "epsilon": epsilon,
        },
        "metrics": {
            "audit_status": (
                "chronology_audited"
                if chronology["status"] == "monotonic_timestamp"
                else "chronology_not_audited"
            ),
            "eval_steps": len(eval_rewards),
            "mean_eval_reward": round(sum(eval_rewards) / len(eval_rewards), 10)
            if eval_rewards
            else 0.0,
            "total_eval_reward": round(sum(eval_rewards), 10),
        },
        "q_table": q_entries,
        "policy": policy,
    }


def blocked_rl_artifact(
    *,
    reason: str,
    feature_names: Sequence[str] | None = None,
    action_space: Sequence[str] = DEFAULT_ACTION_SPACE,
    seed: int = 42,
    episodes: int = 4,
    max_steps_per_episode: int = 128,
    max_updates: int | None = None,
) -> dict[str, Any]:
    budget = RLBudget(
        episodes=episodes,
        max_steps_per_episode=max_steps_per_episode,
        max_updates=max_updates,
    )
    resolved_max_updates = budget.resolved_max_updates()
    return {
        "schema_version": "1",
        "process": "tabular_q_learning_research",
        "status": "blocked",
        "promotion_status": PROMOTION_BLOCKED_STATUS,
        "promotable": False,
        "failure_reasons": [reason],
        "seed": seed,
        "feature_names": list(feature_names or []),
        "action_space": list(action_space),
        "training_budget": {
            "episodes": episodes,
            "max_steps_per_episode": max_steps_per_episode,
            "max_updates": resolved_max_updates,
            "updates_used": 0,
            "budget_exhausted": False,
        },
        "decision_time_boundary": "no RL policy trained; required training data was not accepted",
        "q_table": [],
        "policy": {},
    }


def validate_rl_artifact(artifact: Mapping[str, Any]) -> None:
    """Validate the RL research artifact before it is written or embedded."""
    if not isinstance(artifact, Mapping):
        raise ValueError("RL artifact must be an object")
    _require_equal(artifact, "schema_version", "1")
    _require_equal(artifact, "process", "tabular_q_learning_research")
    status = _require_str(artifact, "status")
    if status not in {"trained_research_only", "blocked"}:
        raise ValueError("RL artifact status must be trained_research_only or blocked")
    _require_equal(artifact, "promotion_status", PROMOTION_BLOCKED_STATUS)
    if artifact.get("promotable") is not False:
        raise ValueError("RL artifact must be non-promotable")
    failure_reasons = artifact.get("failure_reasons")
    if not isinstance(failure_reasons, list) or not all(
        isinstance(reason, str) and reason for reason in failure_reasons
    ):
        raise ValueError("RL artifact failure_reasons must be a list of strings")
    _require_str_list(artifact, "feature_names")
    _require_str_list(artifact, "action_space")
    _require_training_budget(artifact.get("training_budget"))
    q_table = artifact.get("q_table")
    policy = artifact.get("policy")
    if not isinstance(q_table, list):
        raise ValueError("RL artifact q_table must be a list")
    if not isinstance(policy, Mapping):
        raise ValueError("RL artifact policy must be an object")
    if status == "trained_research_only":
        if failure_reasons:
            raise ValueError("trained RL artifact must not include failure_reasons")
        _require_int(artifact, "seed")
        _require_str(artifact, "reward_definition")
        _require_str(artifact, "decision_time_boundary")
        if not isinstance(artifact.get("train_eval_split"), Mapping):
            raise ValueError("trained RL artifact requires train_eval_split")
        if not isinstance(artifact.get("hyperparameters"), Mapping):
            raise ValueError("trained RL artifact requires hyperparameters")
        if not isinstance(artifact.get("metrics"), Mapping):
            raise ValueError("trained RL artifact requires metrics")
        if not q_table:
            raise ValueError("trained RL artifact requires q_table entries")
        if not policy:
            raise ValueError("trained RL artifact requires policy entries")
    else:
        if not failure_reasons:
            raise ValueError("blocked RL artifact must include failure_reasons")


def write_rl_artifact(path: Path, artifact: Mapping[str, Any]) -> Path:
    validate_rl_artifact(artifact)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(artifact, indent=2) + "\n", encoding="utf-8")
    return path


def _validate_feature_names(feature_names: Sequence[str]) -> tuple[str, ...]:
    if isinstance(feature_names, str) or not isinstance(feature_names, Sequence):
        raise ValueError("feature_names must be a non-empty sequence of strings")
    names = tuple(str(name).strip() for name in feature_names if str(name).strip())
    if not names:
        raise ValueError("feature_names must not be empty")
    if len(set(names)) != len(names):
        raise ValueError("feature_names must be unique")
    invalid_delimiters = [name for name in names if "|" in name or "=" in name]
    if invalid_delimiters:
        raise ValueError(
            "feature_names must not contain '|' or '=' (state-key delimiters): "
            + ", ".join(invalid_delimiters)
        )
    leaky = [
        name
        for name in names
        if _LEAKY_FEATURE_RE.search(_normalise_feature_name(name))
    ]
    if leaky:
        raise ValueError(
            "feature_names include non-PIT or label-like fields: " + ", ".join(leaky)
        )
    return names


def _normalise_feature_name(name: str) -> str:
    with_pnl_boundaries = re.sub(r"(?i)pnl", "_pnl_", name)
    with_acronym_boundaries = re.sub(r"(?<=[A-Z])(?=[A-Z][a-z])", "_", with_pnl_boundaries)
    with_boundaries = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", with_acronym_boundaries)
    return re.sub(r"[^A-Za-z0-9]+", "_", with_boundaries).lower().strip("_")


def _validate_action_space(action_space: Sequence[str]) -> tuple[str, ...]:
    if isinstance(action_space, str) or not isinstance(action_space, Sequence):
        raise ValueError("action_space must be a non-empty sequence of strings")
    actions = tuple(str(action).strip() for action in action_space if str(action).strip())
    if not actions:
        raise ValueError("action_space must not be empty")
    if len(set(actions)) != len(actions):
        raise ValueError("action_space must be unique")
    return actions


def _validate_rows(
    data: Sequence[Mapping[str, Any]],
    feature_names: Sequence[str],
) -> list[dict[str, float]]:
    if isinstance(data, (str, bytes)) or not isinstance(data, Sequence):
        raise ValueError("data must be a sequence of row objects")
    rows: list[dict[str, float]] = []
    for row_idx, row in enumerate(data):
        if not isinstance(row, Mapping):
            raise ValueError(f"row {row_idx} must be an object")
        clean: dict[str, float] = {}
        for feature in feature_names:
            if feature not in row:
                raise ValueError(f"row {row_idx} missing feature {feature!r}")
            clean[feature] = _number(row[feature], f"row {row_idx} feature {feature}")
        for reward_key in ("reward", "next_return", "return"):
            if reward_key in row:
                clean[reward_key] = _number(row[reward_key], f"row {row_idx} {reward_key}")
        for timestamp_key in _TIMESTAMP_FIELDS:
            if timestamp_key in row:
                clean[timestamp_key] = _number(row[timestamp_key], f"row {row_idx} {timestamp_key}")
        rows.append(clean)
    return rows


def _chronology_audit(data: Sequence[Mapping[str, Any]]) -> dict[str, str]:
    present = [
        field
        for field in _TIMESTAMP_FIELDS
        if all(isinstance(row, Mapping) and field in row for row in data)
    ]
    if not present:
        return {"status": "missing_timestamp"}
    field = present[0]
    timestamps = [_number(row[field], f"row {idx} {field}") for idx, row in enumerate(data)]
    for prev, cur in zip(timestamps, timestamps[1:]):
        if cur <= prev:
            return {"status": "non_monotonic_timestamp", "timestamp_field": field}
    return {"status": "monotonic_timestamp", "timestamp_field": field}


def _is_number(value: Any) -> bool:
    if isinstance(value, bool):
        return False
    if not isinstance(value, (int, float)):
        return False
    return math.isfinite(float(value))


def _number(value: Any, label: str) -> float:
    if not _is_number(value):
        raise ValueError(f"{label} must be finite numeric")
    return float(value)


def _require_equal(artifact: Mapping[str, Any], key: str, expected: Any) -> None:
    if artifact.get(key) != expected:
        raise ValueError(f"RL artifact {key} must be {expected!r}")


def _require_str(artifact: Mapping[str, Any], key: str) -> str:
    value = artifact.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"RL artifact {key} must be a non-empty string")
    return value


def _require_int(artifact: Mapping[str, Any], key: str) -> int:
    value = artifact.get(key)
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"RL artifact {key} must be an integer")
    return value


def _require_str_list(artifact: Mapping[str, Any], key: str) -> list[str]:
    value = artifact.get(key)
    if not isinstance(value, list) or not all(
        isinstance(item, str) and item for item in value
    ):
        raise ValueError(f"RL artifact {key} must be a list of strings")
    return value


def _require_training_budget(value: Any) -> None:
    if not isinstance(value, Mapping):
        raise ValueError("RL artifact training_budget must be an object")
    for key in ("episodes", "max_steps_per_episode", "max_updates", "updates_used"):
        item = value.get(key)
        if not isinstance(item, int) or isinstance(item, bool):
            raise ValueError(f"RL artifact training_budget.{key} must be an integer")
    if value["episodes"] <= 0:
        raise ValueError("RL artifact training_budget.episodes must be positive")
    if value["max_steps_per_episode"] <= 0:
        raise ValueError("RL artifact training_budget.max_steps_per_episode must be positive")
    if value["max_updates"] <= 0:
        raise ValueError("RL artifact training_budget.max_updates must be positive")
    if value["updates_used"] < 0:
        raise ValueError("RL artifact training_budget.updates_used must be non-negative")
    if not isinstance(value.get("budget_exhausted"), bool):
        raise ValueError("RL artifact training_budget.budget_exhausted must be boolean")


def _feature_bin(value: float) -> str:
    if value < 0:
        return "neg"
    if value > 0:
        return "pos"
    return "zero"


def _state_key(row: Mapping[str, float] | None, feature_names: Sequence[str]) -> str:
    if row is None:
        return "terminal"
    return "|".join(f"{name}={_feature_bin(row[name])}" for name in feature_names)


def _state_from_key(state_key: str) -> dict[str, str]:
    if state_key == "terminal":
        return {}
    state: dict[str, str] = {}
    for part in state_key.split("|"):
        name, bucket = part.split("=", 1)
        state[name] = bucket
    return state


def _zero_actions(action_space: Sequence[str]) -> dict[str, float]:
    return {action: 0.0 for action in action_space}


def _best_action(action_values: Mapping[str, float], action_space: Sequence[str]) -> str:
    best = max(float(action_values.get(action, 0.0)) for action in action_space)
    for action in action_space:
        if float(action_values.get(action, 0.0)) == best:
            return action
    return action_space[0]


def _row_base_reward(row: Mapping[str, float]) -> float:
    for key in ("reward", "next_return", "return"):
        if key in row:
            return float(row[key])
    raise ValueError("row reward is required when reward_function is not provided")


def _reward(
    row: Mapping[str, float],
    action: str,
    next_row: Mapping[str, float] | None,
    step_index: int,
    *,
    reward_function: RewardFunction | None,
) -> float:
    if reward_function is not None:
        return _number(reward_function(row, action, next_row, step_index), "reward_function result")
    base = _row_base_reward(row)
    if action == "enter_long":
        return base
    if action == "enter_short":
        return -base
    return 0.0


def _evaluate_policy(
    rows: Iterable[Mapping[str, float]],
    feature_names: Sequence[str],
    action_space: Sequence[str],
    q_table: Mapping[str, Mapping[str, float]],
    *,
    reward_function: RewardFunction | None,
) -> list[float]:
    rewards: list[float] = []
    row_list = list(rows)
    for idx, row in enumerate(row_list):
        state_key = _state_key(row, feature_names)
        action_values = q_table.get(state_key, _zero_actions(action_space))
        action = _best_action(action_values, action_space)
        next_row = row_list[idx + 1] if idx + 1 < len(row_list) else None
        rewards.append(
            _reward(row, action, next_row, idx, reward_function=reward_function)
        )
    return rewards


def _serialise_q_table(
    q_table: Mapping[str, Mapping[str, float]],
    action_space: Sequence[str],
) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for state_key in sorted(q_table):
        action_values = {
            action: round(float(q_table[state_key].get(action, 0.0)), 10)
            for action in action_space
        }
        entries.append(
            {
                "state_key": state_key,
                "state": _state_from_key(state_key),
                "action_values": action_values,
                "best_action": _best_action(action_values, action_space),
            }
        )
    return entries
