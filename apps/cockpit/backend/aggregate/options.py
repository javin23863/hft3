"""Options zone - first-class read-only CME options lane status.

The lane spec allows CME options research/backtest in Phases 0-1; only
shadow/live execution is blocked while the lane-scoped defect ledger is open.
This zone reuses the System options readiness primitives so the cockpit has one
source of truth and no new pipeline surface.
"""
from __future__ import annotations

import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .. import paths, schemas
from . import system

_BLOCKED_STATUSES = {schemas.FAIL, schemas.MISSING, schemas.STALE, schemas.UNKNOWN}
_CME_OPTIONS_CAMPAIGN_MODES = {"cme_options"}
_LEGACY_OPTIONS_CAMPAIGN_MODES = {"options_lane"}
_LEGACY_OPTIONS_MODEL_IDS = {"DEALER_HEDGING", "PDF_MODEL_5"}
_LEGACY_OPTIONS_PREFIXES = ("OPTIONS_", "PARITY_")
_SUMMARY_TIME_FIELDS = (
    "generated_utc",
    "run_utc",
    "created_utc",
    "completed_utc",
    "as_of_utc",
)
_CAMPAIGN_TS_RE = re.compile(r"(\d{8}T\d{6}Z)")
_ROBUSTNESS_PASS_STATUSES = {"clear", "green", "ok", "pass", "passed"}


def _rel(path: Path) -> str:
    try:
        return path.relative_to(paths.REPO).as_posix()
    except ValueError:
        return str(path)


def _workbench_roots() -> list[Path]:
    candidates = [
        paths.REPO / "artifacts" / "research_cards" / "workbench_runs",
        paths.REPO / "artifacts" / "workbench_runs",
        paths.REPO / "research_cards" / "workbench_runs",
    ]
    env = os.environ.get("HFT3_ARTIFACTS_ROOT")
    if env:
        candidates.append(Path(env).resolve() / "workbench_runs")
    seen: set[Path] = set()
    roots: list[Path] = []
    for root in candidates:
        try:
            resolved = root.resolve()
        except OSError:
            resolved = root
        if resolved not in seen and root.is_dir():
            seen.add(resolved)
            roots.append(root)
    return roots


def _period_names(summary: dict[str, Any]) -> list[str]:
    periods = summary.get("periods")
    if not isinstance(periods, list):
        return []
    names: list[str] = []
    for period in periods:
        if isinstance(period, dict) and period.get("name") is not None:
            names.append(str(period["name"]))
    return names


def _summary_campaign_mode(summary: dict[str, Any]) -> str:
    return str(summary.get("campaign_mode") or "").lower()


def _summary_lane(summary: dict[str, Any]) -> str:
    return str(summary.get("lane") or "").lower()


def _is_cme_options_summary(summary: dict[str, Any]) -> bool:
    model_id = str(summary.get("model_id") or "").upper()
    return (
        model_id.startswith("FOPT_")
        or _summary_lane(summary) in _CME_OPTIONS_CAMPAIGN_MODES
        or _summary_campaign_mode(summary) in _CME_OPTIONS_CAMPAIGN_MODES
    )


def _is_legacy_options_summary(summary: dict[str, Any]) -> bool:
    if _is_cme_options_summary(summary):
        return False
    model_id = str(summary.get("model_id") or "").upper()
    if (
        model_id.startswith(_LEGACY_OPTIONS_PREFIXES)
        or model_id in _LEGACY_OPTIONS_MODEL_IDS
    ):
        return True
    if _summary_campaign_mode(summary) in _LEGACY_OPTIONS_CAMPAIGN_MODES:
        return True
    return any("options fixture" in name.lower() for name in _period_names(summary))


def _has_fixture_evidence(summary: dict[str, Any]) -> bool:
    if summary.get("fixture") or summary.get("fixture_backed") is True:
        return True
    if str(summary.get("campaign_mode") or "").lower() == "options_lane":
        return True
    return any("options fixture" in name.lower() for name in _period_names(summary))


def _parse_utc_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    raw = str(value).strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _campaign_timestamp(summary: dict[str, Any]) -> datetime | None:
    match = _CAMPAIGN_TS_RE.search(str(summary.get("campaign_id") or ""))
    if match is None:
        return None
    return datetime.strptime(match.group(1), "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)


def _artifact_time(summary_path: Path, summary: dict[str, Any]) -> tuple[float, str | None, str, bool]:
    for field in _SUMMARY_TIME_FIELDS:
        parsed = _parse_utc_datetime(summary.get(field))
        if parsed is not None:
            return parsed.timestamp(), parsed.isoformat(), field, True
    parsed = _campaign_timestamp(summary)
    if parsed is not None:
        return parsed.timestamp(), parsed.isoformat(), "campaign_id", True
    try:
        mtime = summary_path.stat().st_mtime
    except OSError:
        mtime = 0.0
    return mtime, paths.mtime_iso(summary_path), "mtime", False


def _float_value(value: Any) -> float:
    if isinstance(value, bool):
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _summary_trade_count(summary: dict[str, Any]) -> float:
    top_level = _float_value(summary.get("num_trades"))
    periods = summary.get("periods")
    period_total = 0.0
    if isinstance(periods, list):
        for period in periods:
            if isinstance(period, dict):
                period_total += _float_value(period.get("num_trades"))
    return top_level if top_level > 0 else period_total


def _extra(summary: dict[str, Any]) -> dict[str, Any]:
    extra = summary.get("extra")
    return extra if isinstance(extra, dict) else {}


def _nonempty_list(value: Any) -> bool:
    return isinstance(value, list) and bool(value)


def _nonempty_container(value: Any) -> bool:
    return isinstance(value, (dict, list, tuple, set)) and bool(value)


def _coverage_summary(summary: dict[str, Any]) -> dict[str, Any]:
    coverage = summary.get("coverage_summary")
    return coverage if isinstance(coverage, dict) else {}


def _has_source_ids(summary: dict[str, Any]) -> bool:
    coverage = _coverage_summary(summary)
    return (
        _nonempty_list(summary.get("source_ids"))
        or _nonempty_list(summary.get("data_source_ids"))
        or _nonempty_list(coverage.get("source_ids"))
    )


def _has_timestamp_ids(summary: dict[str, Any]) -> bool:
    coverage = _coverage_summary(summary)
    return _nonempty_container(summary.get("timestamp_ids")) or _nonempty_container(
        coverage.get("timestamp_ids")
    )


def _uses_2026_options_data(summary: dict[str, Any]) -> bool:
    return summary.get("uses_2026_options_data") is True


def _has_2026_usage_class(summary: dict[str, Any]) -> bool:
    return bool(summary.get("options_2026_usage_class") or summary.get("usage_class"))


def _robustness_status(robustness: Any) -> tuple[str, str | None]:
    if not isinstance(robustness, dict):
        return "not_observed", None
    status = str(
        robustness.get("status")
        or robustness.get("gate_status")
        or robustness.get("result")
        or "observed"
    )
    return status, status.lower()


def _real_data_proof_missing(summary: dict[str, Any], robustness: Any) -> list[str]:
    missing: list[str] = []
    if not _has_source_ids(summary):
        missing.append("source_ids")
    if not _has_timestamp_ids(summary):
        missing.append("timestamp_ids")
    if _summary_trade_count(summary) <= 0:
        missing.append("nonzero_num_trades")
    _, normalized_robustness = _robustness_status(robustness)
    if normalized_robustness not in _ROBUSTNESS_PASS_STATUSES:
        missing.append("robustness_pass")
    if _uses_2026_options_data(summary) and not _has_2026_usage_class(summary):
        missing.append("options_2026_usage_class")
    return missing


def _failure_notes(summary: dict[str, Any]) -> list[str]:
    notes = summary.get("failure_notes")
    if isinstance(notes, list):
        return [str(note) for note in notes]
    return []


def _is_structural(summary: dict[str, Any]) -> bool:
    notes = " ".join(_failure_notes(summary)).lower()
    return (
        summary.get("structural_only") is True
        or _extra(summary).get("structural_only") is True
        or "structural-only" in notes
        or "structural only" in notes
    )


def _is_degraded(summary: dict[str, Any]) -> bool:
    return (
        summary.get("degraded") is True
        or bool(_failure_notes(summary))
        or _promotable(summary) is False
    )


def _promotable(summary: dict[str, Any]) -> Any:
    if "promotable" in summary:
        return summary.get("promotable")
    extra = _extra(summary)
    if "promotable" in extra:
        return extra.get("promotable")
    return None


def _latest_options_summary(predicate) -> tuple[Path, dict[str, Any]] | None:
    latest: tuple[tuple[int, float, float], Path, dict[str, Any]] | None = None
    for root in _workbench_roots():
        try:
            summaries = root.glob("*/summary.json")
        except OSError:
            continue
        for path in summaries:
            data = paths.read_json(path)
            if not isinstance(data, dict) or not predicate(data):
                continue
            try:
                mtime = path.stat().st_mtime
            except OSError:
                mtime = 0.0
            artifact_epoch, _, _, has_semantic_time = _artifact_time(path, data)
            key = (1 if has_semantic_time else 0, artifact_epoch, mtime)
            if latest is None or key > latest[0]:
                latest = (key, path, data)
    if latest is None:
        return None
    return latest[1], latest[2]


def _summary_evidence_update(summary_path: Path, summary: dict[str, Any]) -> dict:
    robustness_path = summary_path.parent / "robustness_summary.json"
    robustness = paths.read_json(robustness_path)
    claimed_real_data_backed = summary.get("real_data_backed") is True
    missing_real_data_proof = _real_data_proof_missing(summary, robustness)
    real_data_proof_passed = claimed_real_data_backed and not missing_real_data_proof
    fixture_backed = _has_fixture_evidence(summary)
    structural_only = _is_structural(summary)
    degraded = _is_degraded(summary)
    if structural_only:
        status = "structural_only"
    elif degraded:
        status = "artifact_degraded"
    elif real_data_proof_passed:
        status = "real_data_backed"
    elif claimed_real_data_backed:
        status = "real_data_claim_unverified"
    elif fixture_backed:
        status = "fixture_only"
    else:
        status = "artifact_present_unclassified"
    real_data_backed = status == "real_data_backed"
    robustness_status, _ = _robustness_status(robustness)
    robustness_artifact = None
    if isinstance(robustness, dict):
        robustness_artifact = _rel(robustness_path)
    artifact_epoch, artifact_time_utc, artifact_time_source, _ = _artifact_time(summary_path, summary)

    return {
        "status": status,
        "latest_artifact": _rel(summary_path),
        "latest_artifact_status": "present",
        "latest_artifact_mtime_utc": paths.mtime_iso(summary_path),
        "latest_artifact_time_utc": artifact_time_utc,
        "latest_artifact_time_source": artifact_time_source,
        "latest_artifact_time_epoch": artifact_epoch,
        "latest_campaign_id": summary.get("campaign_id") or summary_path.parent.name,
        "latest_model_id": summary.get("model_id"),
        "latest_symbol": summary.get("symbol"),
        "latest_summary_status": summary.get("status"),
        "latest_campaign_mode": summary.get("campaign_mode"),
        "latest_lane": summary.get("lane"),
        "real_data_backed": real_data_backed,
        "claimed_real_data_backed": claimed_real_data_backed,
        "missing_real_data_proof": missing_real_data_proof,
        "fixture_backed": fixture_backed,
        "structural_only": structural_only,
        "degraded": degraded,
        "failure_notes": _failure_notes(summary),
        "promotable": _promotable(summary),
        "trade_count": _summary_trade_count(summary),
        "robustness_status": robustness_status,
        "robustness_artifact": robustness_artifact,
    }


def _standalone_model_evidence() -> dict:
    base = {
        "status": "structural_only",
        "lane": "cme_options",
        "model_id_prefix": "FOPT_",
        "latest_artifact": None,
        "latest_artifact_status": "missing",
        "real_data_backed": False,
        "fixture_backed": False,
        "structural_only": True,
        "robustness_status": "not_observed",
        "robustness_detail": (
            "CMEOptionsBacktester returns structural evidence only; no real-data "
            "FOPT robustness artifact was observed."
        ),
        "next_required_artifact": "artifacts/research_cards/workbench_runs/<run_id>/summary.json",
        "fixture_contract_path": "tests/test_workbench/test_options_lane_campaign.py",
        "authority_sources": [
            "packages/hft3/validation/lanes/adapters/cme_options_adapter.py",
            "packages/hft3/validation/lanes/registration.py",
            "tests/test_workbench/test_options_lane_campaign.py",
        ],
    }
    latest = _latest_options_summary(_is_cme_options_summary)
    if latest is None:
        return base

    summary_path, summary = latest
    update = _summary_evidence_update(summary_path, summary)
    if update["real_data_backed"]:
        update["robustness_detail"] = (
            "Latest FOPT workbench summary is real-data-backed with source IDs, "
            "timestamp IDs, nonzero trades, and passing robustness evidence."
        )
    elif update["structural_only"]:
        update["robustness_detail"] = (
            "Latest FOPT workbench summary is structural-only or degraded; it is not "
            "evidence for a tradable standalone options model."
        )
    elif update["status"] == "artifact_degraded":
        update["robustness_detail"] = (
            "Latest FOPT workbench summary is degraded or non-promotable and cannot "
            "be promoted as real-data evidence."
        )
    elif update["status"] == "real_data_claim_unverified":
        missing = ", ".join(update["missing_real_data_proof"])
        update["robustness_detail"] = (
            "Latest FOPT workbench summary claims real-data backing but is missing "
            f"required proof: {missing}."
        )
    elif update["fixture_backed"]:
        update["robustness_detail"] = (
            "Latest FOPT workbench summary is fixture-backed; no real-data FOPT "
            "robustness artifact was observed."
        )
    else:
        update["robustness_detail"] = (
            "Latest FOPT summary was observed but does not identify fixture or "
            "real-data backing."
        )
    base.update(update)
    return base


def _legacy_options_fixture_evidence() -> dict:
    base = {
        "status": "missing",
        "lane": "legacy_options_parity",
        "model_id_prefix": "OPTIONS_/PARITY_",
        "latest_artifact": None,
        "latest_artifact_status": "missing",
        "real_data_backed": False,
        "fixture_backed": False,
        "structural_only": False,
        "robustness_status": "not_observed",
        "robustness_detail": "No legacy options/parity workbench summary was observed.",
        "authority_sources": [
            "packages/hft3/validation/lanes/registration.py",
            "apps/workbench/src/run/campaign_runner.py",
            "tests/test_workbench/test_options_lane_campaign.py",
        ],
    }
    latest = _latest_options_summary(_is_legacy_options_summary)
    if latest is None:
        return base

    summary_path, summary = latest
    update = _summary_evidence_update(summary_path, summary)
    if update["real_data_backed"]:
        update["robustness_detail"] = (
            "Latest legacy options/parity summary is real-data-backed, but it is "
            "still not FOPT CME_OPTIONS evidence."
        )
    elif update["structural_only"] or update["status"] == "artifact_degraded":
        update["robustness_detail"] = (
            "Latest legacy options/parity summary is structural-only, degraded, or "
            "non-promotable; it is not FOPT CME_OPTIONS evidence."
        )
    elif update["status"] == "real_data_claim_unverified":
        missing = ", ".join(update["missing_real_data_proof"])
        update["robustness_detail"] = (
            "Latest legacy options/parity summary claims real-data backing but is "
            f"missing required proof: {missing}. It is still not FOPT CME_OPTIONS evidence."
        )
    elif update["fixture_backed"]:
        update["robustness_detail"] = (
            "Latest legacy options/parity summary is fixture-backed; it is not FOPT "
            "CME_OPTIONS evidence."
        )
    else:
        update["robustness_detail"] = (
            "Latest legacy options/parity summary was observed but does not identify "
            "fixture or real-data backing."
        )
    base.update(update)
    return base


def _health(data_status: str, defect_status: str, *, shadow_live_blocked: bool) -> str:
    if data_status == schemas.FAIL:
        return schemas.RED
    if (
        data_status in {schemas.MISSING, schemas.STALE, schemas.UNKNOWN}
        or defect_status == schemas.FAIL
        or shadow_live_blocked
    ):
        return schemas.AMBER
    return schemas.GREEN


def build() -> dict:
    data = system._options_data_readiness()
    defects = system._options_defect_ledger()
    data_status = str(data.get("status", schemas.UNKNOWN))
    defect_status = str(defects.get("status", schemas.UNKNOWN))
    research_only = True
    shadow_live_blocked = True
    blocked_reasons = ["shadow_live_phase_gate"]
    if data_status in _BLOCKED_STATUSES:
        blocked_reasons.append(f"data_readiness:{data_status}")
    if defect_status == schemas.FAIL:
        blocked_reasons.append("defect_ledger_open")
    return {
        "zone": "options",
        "generated_utc": paths.now_iso(),
        "health": _health(data_status, defect_status, shadow_live_blocked=shadow_live_blocked),
        "lane": "cme_options",
        "model_id_prefix": "FOPT_",
        "phase": "research_backtest_only",
        "research_backtest_status": "allowed",
        "research_backtest_detail": (
            "OPTIONS_LANE.md Phases 0-1 allow research/backtest; shadow/live "
            "execution remains blocked until the options gates clear."
        ),
        "execution_status": "shadow_live_blocked",
        "research_only": research_only,
        "data_readiness": data,
        "defect_ledger": defects,
        "context_feature_coverage": {
            "status": "not_measured",
            "options_as_clue": "not_measured",
            "options_standalone_strategy": "not_measured",
            "note": "No artifact-level options context-feature coverage is present yet.",
        },
        "standalone_model_evidence": _standalone_model_evidence(),
        "legacy_options_fixture_evidence": _legacy_options_fixture_evidence(),
        "shadow_live_status": "blocked",
        "shadow_live_blockers": blocked_reasons,
        "controls": {
            "live_order_controls": False,
            "paper_order_controls": False,
            "reason": (
                "Options research/backtest is allowed; live/paper order controls "
                "remain disabled until Phase 2/3 gates clear."
            ),
        },
        "authority_sources": [
            "specs/OPTIONS_LANE.md",
            "vault:decisions/2026-06-12 Options-lane build decisions (slices 1-7).md",
            "vault:sessions/2026-06-13 Options backfill, study verdicts, cockpit integration.md",
        ],
    }
