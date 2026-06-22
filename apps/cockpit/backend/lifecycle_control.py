"""Lifecycle operator action receipts — request-only, no registry mutation."""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from . import paths
from .auth import require_control

router = APIRouter(prefix="/api/lifecycle", tags=["lifecycle-control"])

_ALLOWED_ACTIONS = frozenset({
    "acknowledge_observation",
    "request_retest",
    "request_quarantine",
    "request_rearm",
    "request_retire",
})

_MODEL_ID_RE = re.compile(r"^[A-Za-z0-9_.-]+$")


def _receipts_path() -> Path:
    return paths.LIFECYCLE_OPERATOR_RECEIPTS


def _load_registry_models() -> dict:
    raw = paths.read_json(paths.MODEL_LIFECYCLE)
    if not isinstance(raw, dict):
        return {}
    models = raw.get("models")
    return models if isinstance(models, dict) else {}


def _write_receipt(receipt: dict) -> Path:
    p = _receipts_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(receipt, sort_keys=True) + "\n"
    with open(p, "a", encoding="utf-8") as fh:
        fh.write(line)
        fh.flush()
        os.fsync(fh.fileno())
    return p


class LifecycleActionRequest(BaseModel):
    model_id: str
    action: str
    reason: str = Field(min_length=1)


@router.post("/action")
def lifecycle_action(req: LifecycleActionRequest, scope: str = Depends(require_control)) -> dict:
    """Write an operator request receipt. Does not mutate model_lifecycle.json."""
    actor = scope or "cockpit-control"
    if req.action not in _ALLOWED_ACTIONS:
        raise HTTPException(400, f"unknown action '{req.action}'")
    if not _MODEL_ID_RE.fullmatch(req.model_id):
        raise HTTPException(400, "invalid model_id")
    reason = req.reason.strip()
    if not reason:
        raise HTTPException(422, "reason must be non-empty")

    models = _load_registry_models()
    rec = models.get(req.model_id)
    if rec is None:
        raise HTTPException(404, f"unknown model '{req.model_id}'")

    receipt = {
        "ts": paths.now_iso(),
        "model_id": req.model_id,
        "action": req.action,
        "reason": reason,
        "actor": actor,
        "source_state": rec.get("current_state"),
        "source_route": (rec.get("reentry_routing") or {}).get("route"),
        "last_revalidation": rec.get("last_revalidation"),
        "note": "receipt only — lifecycle state changes require authorized job path",
    }
    _write_receipt(receipt)
    return {"status": "accepted", "receipt": receipt}


@router.get("/receipts")
def lifecycle_receipts(tail: int = 50, _: str = Depends(require_control)) -> dict:
    p = _receipts_path()
    if not p.is_file():
        return {"receipts": []}
    try:
        lines = p.read_text(encoding="utf-8").splitlines()
    except OSError:
        return {"receipts": []}
    out = []
    for line in lines[-max(1, min(tail, 500)):]:
        if not line.strip():
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return {"receipts": out}
