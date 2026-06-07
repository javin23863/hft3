"""Disk cache for full B2 synthetic bookticker probes (expensive)."""
from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from crypto_lane.src.types import repo_root_from_lane

CACHE_MAX_AGE_HOURS = 24


def _cache_path() -> Path:
    return repo_root_from_lane() / "runtime/data_audits/b2_synthetic_probe_cache.json"


def _fingerprint(synthetic_days: list[str]) -> str:
    return hashlib.sha256(",".join(sorted(synthetic_days)).encode()).hexdigest()


def load_cached_b2_synthetic_probe(
    synthetic_days: list[str],
    *,
    max_age_hours: float = CACHE_MAX_AGE_HOURS,
) -> dict[str, Any] | None:
    if not synthetic_days:
        return None
    path = _cache_path()
    if not path.is_file():
        return None
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    if doc.get("fingerprint") != _fingerprint(synthetic_days):
        return None
    probed_at = doc.get("probed_at")
    if not probed_at:
        return None
    try:
        ts = datetime.fromisoformat(str(probed_at).replace("Z", "+00:00"))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=UTC)
        age_h = (datetime.now(UTC) - ts).total_seconds() / 3600.0
        if age_h > max_age_hours:
            return None
    except (TypeError, ValueError):
        return None
    probe = doc.get("probe")
    if not isinstance(probe, dict):
        return None
    return {**probe, "from_cache": True, "cache_age_hours": round(age_h, 2)}


def clear_b2_synthetic_probe_cache() -> None:
    """Invalidate cached full B2 synthetic probes (call after bookticker ingest/purge)."""
    path = _cache_path()
    if path.is_file():
        path.unlink(missing_ok=True)


def save_cached_b2_synthetic_probe(synthetic_days: list[str], probe: dict[str, Any]) -> None:
    if not synthetic_days:
        return
    path = _cache_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "fingerprint": _fingerprint(synthetic_days),
        "synthetic_days": len(synthetic_days),
        "probed_at": datetime.now(UTC).isoformat(),
        "probe": {k: v for k, v in probe.items() if k not in ("from_cache", "cache_age_hours")},
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
