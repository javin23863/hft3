"""Strategy parameter hashing and canonical serialization."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Dict

DEFAULT_STRATEGY_PARAMS: Dict[str, float] = {"signal_threshold": 0.15}


def canonical_params_json(params: Dict[str, Any]) -> str:
    return json.dumps(params, sort_keys=True, separators=(",", ":"))


def param_hash_from_dict(model_id: str, params: Dict[str, Any]) -> str:
    payload = f"{model_id}:{canonical_params_json(params)}"
    return hashlib.sha256(payload.encode()).hexdigest()[:16]
