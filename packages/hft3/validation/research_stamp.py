"""Build certification stamp metadata for research runs (T1)."""
from __future__ import annotations

from typing import Any

from hft3.validation.certification_registry import backtester_version, load_registry, repo_root
from hft3.validation.certification_staleness import assess_staleness


def _resolve_stamp_status(registry_status: str, stale: bool) -> str:
    if registry_status == "MISSING":
        return "MISSING"
    if stale:
        return "STALE"
    return registry_status


def _resolve_promotion_label(
    registry_status: str,
    stale: bool,
) -> tuple[str, bool, str]:
    if registry_status == "MISSING":
        return "UNCERTIFIED", False, "no_full_certification_on_record"
    if registry_status == "RED":
        return "NOT_TRUSTED", False, "latest_certification_status=RED"
    if registry_status == "YELLOW":
        return "RESEARCH_ONLY", False, "latest_certification_status=YELLOW"
    if stale:
        return "STALE_CERTIFICATION", False, "core_backtester_changed_since_green_certification"
    if registry_status == "GREEN":
        return "PROMOTION_ELIGIBLE_FROM_BACKTESTER_SIDE", True, ""
    return "UNCERTIFIED", False, f"unknown_registry_status={registry_status}"


def build_certification_stamp(**run_context: Any) -> dict[str, Any]:
    """Return certification_stamp dict for embedding in result JSON / reports."""
    root = repo_root()
    registry = load_registry(root)
    staleness = assess_staleness(root, registry=registry)
    registry_status = registry.latest_certification_status
    stale = not staleness.certification_is_current and registry_status == "GREEN"
    status = _resolve_stamp_status(registry_status, stale)
    promotion_label, promotion_eligible, reason = _resolve_promotion_label(registry_status, stale)

    stamp: dict[str, Any] = {
        "status": status,
        "certification_run_id": registry.latest_certification_run_id,
        "certification_commit": registry.latest_certification_commit,
        "current_commit": staleness.current_commit,
        "git_sha": staleness.current_commit,
        "git_commit": staleness.current_commit,
        "stale": stale,
        "changed_core_files": staleness.changed_core_files,
        "promotion_eligible": promotion_eligible,
        "promotion_label": promotion_label,
        "reason_if_not_promotion_eligible": reason,
        "backtester_version": registry.backtester_version or backtester_version(root),
        "staleness_reason": staleness.stale_reason,
    }
    for key in (
        "data_version",
        "schema",
        "instrument",
        "event_type",
        "latency_band",
        "queue_model",
        "fee_model",
        "slippage_model",
        "fill_model_version",
        "mbo_parser_version",
        "book_reconstruction_version",
        "feature_version",
        "execution_adapter_mode",
        "execution_mode",
        "event_id",
        "model_id",
    ):
        if key in run_context and run_context[key] is not None:
            stamp[key] = run_context[key]
    return stamp


def format_stamp_footer(stamp: dict[str, Any]) -> str:
    return (
        f"Backtester certification used: {stamp.get('status', 'MISSING')}, "
        f"run_id={stamp.get('certification_run_id', '')}, "
        f"commit={stamp.get('certification_commit', '')}"
    )
