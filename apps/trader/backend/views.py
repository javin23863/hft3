"""View builders for the trader dashboard.

Fail-closed rendering contract: a view either returns verified numbers with
evidence attached, or `{"blocked": true, "reason": ...}` — never unverified
numbers. Every model figure traces to run-index rows whose own receipts
carry the stats-file sha256.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from apps.trader.backend.data import (
    LoadedDocument,
    find_campaign_monitor_documents,
    load_lifecycle,
    load_run_index,
)

# Money-path chain stages, in order. A run advances as far as its receipts prove.
CHAIN_STAGES = (
    "runs",
    "orders_submitted",
    "realized_pnl",
    "economic_pass",
    "gate3_pass",
    "gate4_pass",
    "promotion_allowed",
)


def _blocked(evidence: Any, extra: str = "") -> dict[str, Any]:
    reason = getattr(evidence, "reason", "") or "evidence_unavailable"
    return {
        "blocked": True,
        "reason": f"{reason}{(': ' + extra) if extra else ''}",
        "evidence": evidence.to_dict() if hasattr(evidence, "to_dict") else {},
    }


def _stage_flags(row: dict[str, Any]) -> dict[str, bool]:
    realized = row.get("realized_closed_trade_pnl")
    return {
        "runs": True,
        "orders_submitted": bool(row.get("orders_submitted")),
        "realized_pnl": isinstance(realized, (int, float)),
        "economic_pass": row.get("economic_result_status") == "pass",
        "gate3_pass": row.get("gate3_status") == "pass",
        "gate4_pass": row.get("gate4_status") == "pass",
        "promotion_allowed": bool(row.get("promotion_allowed")),
    }


def build_funnel() -> dict[str, Any]:
    index = load_run_index()
    if index.payload is None:
        return _blocked(index.evidence)
    totals = {stage: 0 for stage in CHAIN_STAGES}
    per_model: dict[str, dict[str, int]] = defaultdict(lambda: {s: 0 for s in CHAIN_STAGES})
    for row in index.rows:
        flags = _stage_flags(row)
        model = str(row.get("canonical_model_id") or "unknown")
        for stage in CHAIN_STAGES:
            if flags[stage]:
                totals[stage] += 1
                per_model[model][stage] += 1
    return {
        "blocked": False,
        "stages": list(CHAIN_STAGES),
        "totals": totals,
        "per_model": dict(sorted(per_model.items())),
        "evidence": index.evidence.to_dict(),
    }


def build_models() -> dict[str, Any]:
    index = load_run_index()
    if index.payload is None:
        return _blocked(index.evidence)
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in index.rows:
        grouped[str(row.get("canonical_model_id") or "unknown")].append(row)
    models = []
    for model_id, rows in sorted(grouped.items()):
        realized = [
            r["realized_closed_trade_pnl"]
            for r in rows
            if isinstance(r.get("realized_closed_trade_pnl"), (int, float))
        ]
        events = sorted({str(r.get("event_id") or "") for r in rows if r.get("event_id")})
        models.append(
            {
                "canonical_model_id": model_id,
                "runs": len(rows),
                "events": events,
                "event_count": len(events),
                "realized_run_count": len(realized),
                "realized_total": round(sum(realized), 6) if realized else None,
                "realized_mean": round(sum(realized) / len(realized), 6) if realized else None,
                "realized_min": min(realized) if realized else None,
                "realized_max": max(realized) if realized else None,
                "gate3_any_pass": any(r.get("gate3_status") == "pass" for r in rows),
                "gate4_any_pass": any(r.get("gate4_status") == "pass" for r in rows),
                "promotion_any_allowed": any(r.get("promotion_allowed") for r in rows),
            }
        )
    return {
        "blocked": False,
        "models": models,
        "evidence": index.evidence.to_dict(),
    }


def build_model_detail(model_id: str) -> dict[str, Any]:
    index = load_run_index()
    if index.payload is None:
        return _blocked(index.evidence)
    rows = [
        r for r in index.rows if str(r.get("canonical_model_id") or "") == model_id
    ]
    if not rows:
        return {
            "blocked": True,
            "reason": f"no_runs_for_model:{model_id}",
            "evidence": index.evidence.to_dict(),
        }
    runs = []
    for row in sorted(rows, key=lambda r: str(r.get("event_id") or "")):
        runs.append(
            {
                "run_id": row.get("run_id"),
                "event_id": row.get("event_id"),
                "symbol": row.get("symbol"),
                "strategy_params": row.get("strategy_params") or {},
                "realized_closed_trade_pnl": row.get("realized_closed_trade_pnl"),
                "unrealized_pnl_marked_to_mid": row.get("unrealized_pnl_marked_to_mid"),
                "end_position": row.get("end_position"),
                "exit_reason": row.get("exit_reason"),
                "fill_rate": row.get("fill_rate"),
                "gate3_status": row.get("gate3_status"),
                "gate3_min_realized": row.get("gate3_min_realized"),
                "gate3_axes_unavailable_upstream": row.get("gate3_axes_unavailable_upstream") or [],
                "gate4_status": row.get("gate4_status"),
                "gate4_psr": row.get("gate4_psr"),
                "gate4_dsr": row.get("gate4_dsr"),
                "gate4_pbo": row.get("gate4_pbo"),
                "promotion_decision": row.get("promotion_decision"),
                "promotion_allowed": row.get("promotion_allowed"),
                "fail_closed_reasons": row.get("fail_closed_reasons") or [],
                "receipt": {
                    "artifact_dir": row.get("artifact_dir"),
                    "stats_summary_sha256": row.get("stats_summary_sha256"),
                },
            }
        )
    return {
        "blocked": False,
        "canonical_model_id": model_id,
        "runs": runs,
        "evidence": index.evidence.to_dict(),
    }


def build_campaign() -> dict[str, Any]:
    docs = find_campaign_monitor_documents()
    receipts = []
    for doc in docs:
        if doc.payload is None:
            continue
        payload = doc.payload if isinstance(doc.payload, dict) else {}
        receipts.append(
            {
                "kind": payload.get("schema_version") or payload.get("phase") or "receipt",
                "summary": {
                    k: payload.get(k)
                    for k in (
                        "schema_version",
                        "campaign_id",
                        "instance_id",
                        "status",
                        "phase",
                        "rows",
                        "reason",
                        "destroyed_at_utc",
                        "credit_after_destroy_usd",
                    )
                    if k in payload
                },
                "evidence": doc.evidence.to_dict(),
            }
        )
    return {
        "blocked": False,
        "active_campaign": None,  # populated when the fresh Vast canary launches
        "receipts": receipts,
        "note": "No active Vast campaign. Old instance 42609000 destroyed 2026-07-02; fresh canary awaits owner budget gate.",
    }


def build_lifecycle() -> dict[str, Any]:
    registry, transitions = load_lifecycle()
    if registry.payload is None:
        return {
            "blocked": False,
            "empty_state": True,
            "models": [],
            "note": (
                "Lifecycle registry not created yet — no model has been "
                "promoted/enrolled. It appears after the first certified "
                "promotion (money-path link 7)."
            ),
            "evidence": registry.evidence.to_dict(),
        }
    models = []
    payload = registry.payload if isinstance(registry.payload, dict) else {}
    for model_id, entry in sorted((payload.get("models") or {}).items()):
        if not isinstance(entry, dict):
            continue
        models.append(
            {
                "model_id": model_id,
                "state": entry.get("current_state"),
                "hypothesis_id": entry.get("hypothesis_id"),
                "symbol": entry.get("symbol"),
                "last_revalidation": entry.get("last_revalidation"),
                "envelope_id": entry.get("envelope_id"),
            }
        )
    return {
        "blocked": False,
        "empty_state": not models,
        "models": models,
        "transitions_count": len(transitions.rows),
        "evidence": registry.evidence.to_dict(),
    }
