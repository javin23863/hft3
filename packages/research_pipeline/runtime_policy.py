"""Lightweight runtime policy helpers for pipeline orchestration."""

from __future__ import annotations

import platform
from typing import Any, Mapping


def is_msi_local_host(host: str | None = None) -> bool:
    return str(host if host is not None else platform.node()).strip().lower() == "msi"


def effective_evaluation_workers(requested: int, config: Mapping[str, Any]) -> tuple[int, dict[str, Any]]:
    evaluation_config = _section(config, "evaluation")
    requested = _positive_int(requested, name="evaluation.workers")
    max_workers = _positive_int(evaluation_config.get("max_workers", 8), name="evaluation.max_workers")
    msi_max_workers = _positive_int(
        evaluation_config.get("msi_max_workers", 1),
        name="evaluation.msi_max_workers",
    )
    host = platform.node() or ""
    host_is_msi = is_msi_local_host(host)
    host_cap = msi_max_workers if host_is_msi else max_workers
    effective = min(requested, host_cap)
    return effective, {
        "requested_workers": requested,
        "effective_workers": effective,
        "host": host,
        "host_is_msi": host_is_msi,
        "max_workers": max_workers,
        "msi_max_workers": msi_max_workers,
        "cap_reason": "msi_local_cap"
        if requested != effective and host_is_msi
        else "max_workers_cap"
        if requested != effective
        else "none",
    }


def _section(config: Mapping[str, Any], key: str) -> dict[str, Any]:
    value = config.get(key)
    return dict(value) if isinstance(value, Mapping) else {}


def _positive_int(value: Any, *, name: str) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a positive integer") from exc
    if parsed <= 0:
        raise ValueError(f"{name} must be a positive integer")
    return parsed


__all__ = ["effective_evaluation_workers", "is_msi_local_host"]
