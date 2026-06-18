"""Non-certifying accelerated replay mode."""

from __future__ import annotations

from typing import Any

ACCELERATED_STATUSES = frozenset(
    {
        "accelerated_reject",
        "accelerated_survivor_requires_full_replay",
        "accelerated_not_certifying",
    }
)


def classify_accelerated_result(replay_result: dict[str, Any]) -> str:
    if replay_result.get("error"):
        return "accelerated_reject"
    if replay_result.get("book_not_live"):
        return "accelerated_reject"
    return "accelerated_survivor_requires_full_replay"


def annotate_accelerated_replay(replay_result: dict[str, Any]) -> dict[str, Any]:
    status = classify_accelerated_result(replay_result)
    payload = dict(replay_result)
    payload["accelerated_status"] = status
    payload["certification_status"] = "accelerated_not_certifying"
    payload["full_fidelity_replay_required"] = status != "accelerated_reject"
    return payload
