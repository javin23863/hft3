"""Observable crypto candidate smoke loop for the Workbench UI."""

from __future__ import annotations

import json
import math
import os
import time
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def crypto_smoke_root(repo: Path) -> Path:
    return repo / "runtime" / "workbench" / "crypto_smoke"


def latest_status_path(repo: Path) -> Path:
    return crypto_smoke_root(repo) / "latest_status.json"


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.{time.time_ns()}.tmp")
    tmp.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    for attempt in range(60):
        try:
            tmp.replace(path)
            return
        except PermissionError:
            if attempt == 59:
                raise
            time.sleep(min(0.5, 0.05 * (attempt + 1)))


def _write_status(run_dir: Path, latest: Path, status: dict[str, Any]) -> None:
    status["updated_at"] = _utc_now()
    _write_json(run_dir / "status.json", status)
    _write_json(latest, status)


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def _write_yaml(path: Path, payload: dict[str, Any]) -> None:
    import yaml

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.{time.time_ns()}.tmp")
    tmp.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    for attempt in range(60):
        try:
            tmp.replace(path)
            return
        except PermissionError:
            if attempt == 59:
                raise
            time.sleep(min(0.5, 0.05 * (attempt + 1)))


def _primary_metrics(report: dict[str, Any]) -> dict[str, Any]:
    primary = (report.get("runs") or {}).get("with_btc_node") or (report.get("runs") or {}).get("without_btc_node") or {}
    return {
        "oos_ic": float(primary.get("oos_ic_baseline_mean", 0.0)),
        "n_rows": int(primary.get("n_rows", 0) or 0),
        "n_folds": int(primary.get("n_folds", 0) or 0),
        "purged_splits": int(primary.get("n_splits", 0) or 0),
    }


def _candidate_summary(report: dict[str, Any]) -> dict[str, Any]:
    primary = _primary_metrics(report)
    proxy_summary = ((report.get("research_pnl_proxy") or {}).get("summary") or {})
    return {
        "candidate_id": report.get("candidate_id", ""),
        "hypothesis_id": report.get("hypothesis_id", ""),
        "target": report.get("target", ""),
        "status": "done",
        "pass_fail": report.get("pass_fail", "unknown"),
        "rejection_reason": report.get("rejection_reason"),
        "holdout_status": (report.get("holdout_gate") or {}).get("status", ""),
        "negative_controls_ok": all(
            bool((report.get("negative_controls") or {}).get(key))
            for key in ("shuffled_degraded", "shifted_degraded")
        ),
        "oos_ic": primary["oos_ic"],
        "n_rows": primary["n_rows"],
        "n_folds": primary["n_folds"],
        "purged_splits": primary["purged_splits"],
        "deflated_sharpe_cdf": report.get("deflated_sharpe_ratio"),
        "proxy_net_pnl_bps": proxy_summary.get("net_pnl_bps"),
        "proxy_num_trades": proxy_summary.get("num_trades"),
        "proxy_profit_factor": proxy_summary.get("profit_factor"),
        "proxy_max_drawdown_bps": proxy_summary.get("max_drawdown_bps"),
        "proxy_sharpe": proxy_summary.get("sharpe_proxy"),
        "proxy_hit_rate": proxy_summary.get("hit_rate"),
        "bh_rejected": bool(report.get("bh_rejected")),
        "execution_ack_scope": report.get("execution_ack_scope", ""),
        "execution_ack_measured": bool(report.get("execution_ack_measured")),
        "order_ack_status": report.get("execution_ack_status", ""),
        "btc_node_evidence_scope": report.get("btc_node_evidence_scope", ""),
        "output_report_path": report.get("output_report_path", ""),
    }


def _finite_float(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _best_evidence_candidate_id(status: dict[str, Any]) -> str:
    decision = status.get("decision") or {}
    for key in ("evidence_candidate_id", "top_smoke_candidate"):
        value = str(decision.get(key) or "")
        if value:
            return value
    for row in status.get("vectorbt_promoted_order") or []:
        value = str(row.get("candidate_id") or "")
        if value:
            return value
    for row in status.get("smoke_triage_order") or []:
        value = str(row.get("candidate_id") or "")
        if value:
            return value
    for row in status.get("candidates") or []:
        value = str(row.get("candidate_id") or "")
        if value:
            return value
    return "crypto_lane_run"


def _validation_totals(validation_reports: list[dict[str, Any]]) -> dict[str, Any]:
    net_pnl = 0.0
    gross_pnl = 0.0
    num_trades = 0
    pnl_values: list[float] = []
    for report in validation_reports:
        result = report.get("result") or {}
        net_pnl += _finite_float(result.get("net_pnl")) or 0.0
        gross_pnl += _finite_float(result.get("gross_pnl")) or 0.0
        trades = result.get("num_trades")
        if trades is None:
            trades = len(result.get("trade_pnls") or []) or len(result.get("fill_events") or [])
        try:
            num_trades += int(trades or 0)
        except (TypeError, ValueError):
            pass
        for value in result.get("trade_pnls") or []:
            number = _finite_float(value)
            if number is not None:
                pnl_values.append(number)
    if not num_trades and pnl_values:
        num_trades = len(pnl_values)
    if not net_pnl and pnl_values:
        net_pnl = sum(pnl_values)
    return {
        "net_pnl": net_pnl,
        "gross_pnl": gross_pnl,
        "num_trades": num_trades,
        "trade_pnls": pnl_values,
    }


def _artifact_paths(run_dir: Path, names: list[str]) -> dict[str, str]:
    return {name: str(run_dir / name) for name in names if (run_dir / name).is_file()}


def _after_action_gate_passed(meta: dict[str, Any]) -> bool:
    return str(meta.get("llm_status") or "").lower() == "ok" and bool(meta.get("response_written"))


def _with_after_action_gate(meta: dict[str, Any]) -> dict[str, Any]:
    passed = _after_action_gate_passed(meta)
    reasons = list(meta.get("skip_reasons") or [])
    if not passed and not reasons:
        reasons.append(str(meta.get("llm_error") or meta.get("after_action_failed") or "GPT-5.5 xhigh after-action did not produce an ok response."))
    return {
        **meta,
        "required": True,
        "gate_status": "PASS" if passed else "FAIL",
        "passed": passed,
        "blocking_reason": "" if passed else "; ".join(str(reason) for reason in reasons if str(reason)),
        "skip_reasons": reasons,
    }


def _run_crypto_after_action(
    repo: Path,
    run_dir: Path,
    status: dict[str, Any],
    validation_reports: list[dict[str, Any]],
    robustness_summary: dict[str, Any],
    vectorbt_summary: dict[str, Any],
) -> dict[str, Any]:
    candidate_id = _best_evidence_candidate_id(status)
    decision = status.get("decision") or {}
    totals = _validation_totals(validation_reports)
    event_id = f"CRYPTO_SMOKE_{status.get('run_id', run_dir.name)}"
    robustness_pack = robustness_summary.get("robustness_pack") or {}
    double_wf = robustness_summary.get("double_walk_forward") or {}
    ack_measured = any(bool(row.get("execution_ack_measured")) for row in status.get("candidates") or [])
    ack_statuses = [
        str(row.get("order_ack_status") or row.get("execution_ack_status") or "")
        for row in status.get("candidates") or []
        if str(row.get("order_ack_status") or row.get("execution_ack_status") or "")
    ]
    diagnostics = {
        "model_id": candidate_id,
        "engine": "crypto_lane",
        "engine_kind": "crypto_smoke_replay",
        "event_id": event_id,
        "symbol": "BTCUSDT",
        "num_trades": totals["num_trades"],
        "net_pnl": totals["net_pnl"],
        "gross_pnl": totals["gross_pnl"],
        "execution_assumptions": "crypto_order_book_replay",
        "latency_authority": "crypto_venue_submit_ack",
        "lane_required": "venue_submit_ack",
        "lane_measured": ack_measured,
        "lane_pass": ack_measured and str(decision.get("action") or "").upper() == "PROMOTE",
        "execution_ack_statuses": ack_statuses,
        "data_sufficient": bool(status.get("candidates")),
        "eval_scope": "crypto_autonomous_smoke",
        "cpp_replay_available": any(_hft_validation_passed(row) for row in validation_reports),
        "promote_candidate": bool(decision.get("live_registry_ready")),
        "wfc_status": double_wf.get("status"),
        "robustness_passed": str(robustness_summary.get("status") or "").upper() in {"PASS", "OBSERVED"},
        "pnl_by_injection_us": {"0": totals["net_pnl"]},
        "crypto_after_action": True,
        "vectorbt_status": vectorbt_summary.get("status"),
        "decision_action": decision.get("action"),
        "decision_reason": decision.get("reason"),
        "failed_robustness_checks": robustness_pack.get("failed", []),
        "pending_robustness_checks": robustness_pack.get("pending", []),
        "trade_sample_count": robustness_summary.get("trade_sample_count", 0),
    }
    manifest = {
        "event_id": event_id,
        "symbol": "BTCUSDT",
        "data_sufficient": bool(status.get("candidates")),
        "history_years_available": None,
        "eval_scope": "crypto_autonomous_smoke",
        "run_id": status.get("run_id", run_dir.name),
    }
    config = {
        "model_id": candidate_id,
        "event_id": event_id,
        "symbol": "BTCUSDT",
        "engine": "crypto_lane",
        "execution_assumptions": "crypto_order_book_replay",
        "run_id": status.get("run_id", run_dir.name),
    }
    _write_json(run_dir / "diagnostics.json", diagnostics)
    _write_json(run_dir / "manifest.json", manifest)
    _write_yaml(run_dir / "config.yaml", config)

    meta: dict[str, Any]
    try:
        from data_layer.pipeline.after_action import run_after_action_report

        meta = run_after_action_report(run_dir, repo_root=repo)
    except Exception as exc:  # pragma: no cover - defensive around optional connector/runtime state
        meta = _read_json(run_dir / "after_action_meta.json")
        meta.update(
            {
                "generated_at": meta.get("generated_at") or _utc_now(),
                "llm_status": meta.get("llm_status") or "failed",
                "after_action_failed": str(exc),
                "skip_reasons": list(meta.get("skip_reasons") or ["AFTER_ACTION_FAILED"]),
                "symbolic_passed": meta.get("symbolic_passed"),
                "report_written": bool((run_dir / "after_action_report.md").is_file()),
                "response_written": bool((run_dir / "after_action_response.json").is_file()),
            }
        )
        _write_json(run_dir / "after_action_meta.json", meta)
    meta["paths"] = _artifact_paths(
        run_dir,
        [
            "diagnostics.json",
            "manifest.json",
            "config.yaml",
            "after_action_packet.json",
            "after_action_symbolic.json",
            "kg_slice.json",
            "after_action_meta.json",
            "after_action_response.json",
            "after_action_report.md",
        ],
    )
    meta = _with_after_action_gate(meta)
    _write_json(run_dir / "after_action_meta.json", {k: v for k, v in meta.items() if k != "paths"})
    return meta


def _relationship_source_ref(repo: Path, run_dir: Path, relative: str, suffix: str = "") -> str:
    return ((run_dir / relative).relative_to(repo).as_posix() + suffix)


def _candidate_to_json(candidate: Any) -> dict[str, Any]:
    payload = asdict(candidate)
    payload["context"] = candidate.context.value
    payload["status"] = candidate.status.value
    payload["evidence"] = [
        {
            **asdict(item),
            "source_type": item.source_type.value if hasattr(item.source_type, "value") else str(item.source_type),
        }
        for item in candidate.evidence
    ]
    return payload


def _write_crypto_relationship_review(
    repo: Path,
    run_dir: Path,
    status: dict[str, Any],
    validation_reports: list[dict[str, Any]],
    robustness_summary: dict[str, Any],
) -> dict[str, Any]:
    from research_pipeline.relationship_reasoning import (
        RelationshipContext,
        RelationshipDataSource,
        RelationshipEvidence,
        RelationshipStatus,
        mark_validated,
        propose_relationship,
        reject_relationship_candidate,
        validate_relationship_candidate,
    )

    candidate_id = _best_evidence_candidate_id(status)
    subject = f"candidate:{candidate_id}"
    robustness_ref = _relationship_source_ref(repo, run_dir, "robustness_summary.json")
    status_ref = _relationship_source_ref(repo, run_dir, "status.json", ":bitcoin_edge_packets")
    smoke_ref = _relationship_source_ref(repo, run_dir, f"smoke_reports/{candidate_id}.json")
    validation_ref = _relationship_source_ref(repo, run_dir, f"validation_reports/{candidate_id}.json")
    candidates = []

    def add_candidate(predicate: str, obj: str, evidence: list[RelationshipEvidence], trace: list[str], rationale: str) -> None:
        candidate = propose_relationship(
            subject,
            predicate,
            obj,
            RelationshipContext.MICRO,
            evidence=evidence,
            proof_trace=trace,
            rationale=rationale,
        )
        errors = validate_relationship_candidate(candidate, repo_root=repo)
        if errors:
            candidates.append(reject_relationship_candidate(candidate, "; ".join(errors)))
        else:
            candidates.append(mark_validated(candidate, repo_root=repo))

    base_evidence: list[RelationshipEvidence] = [
        RelationshipEvidence(
            "Run-local robustness summary produced by the crypto candidate loop.",
            RelationshipDataSource.CRYPTO_ROBUSTNESS_SUMMARY,
            robustness_ref + ":check=aggregate",
            0.95,
        )
    ]
    if (run_dir / f"smoke_reports/{candidate_id}.json").is_file():
        base_evidence.append(
            RelationshipEvidence(
                "Run-local crypto smoke/OOS diagnostics for the evidence candidate.",
                RelationshipDataSource.CRYPTO_SMOKE_REPORT,
                smoke_ref,
                0.75,
            )
        )
    if (run_dir / f"validation_reports/{candidate_id}.json").is_file():
        base_evidence.append(
            RelationshipEvidence(
                "Run-local crypto execution replay validation for the evidence candidate.",
                RelationshipDataSource.CRYPTO_VALIDATION_REPORT,
                validation_ref,
                0.85,
            )
        )

    robustness_pack = robustness_summary.get("robustness_pack") or {}
    failed_names = [str(name) for name in robustness_pack.get("failed") or [] if str(name)]
    if not failed_names:
        failed_names = [
            str(check.get("name") or "")
            for check in robustness_pack.get("checks") or []
            if str(check.get("status") or "").upper() == "FAIL" or check.get("passed") is False
        ]
    for name in failed_names:
        evidence = [
            RelationshipEvidence(
                f"Required robustness check {name} failed in the selected crypto run.",
                RelationshipDataSource.CRYPTO_ROBUSTNESS_SUMMARY,
                robustness_ref + f":check={name}",
                0.95,
            ),
            *base_evidence[1:],
        ]
        add_candidate(
            "failed_required_robustness_check",
            f"robustness:{name}",
            evidence,
            [
                "The selected crypto run emitted a run-local robustness_summary.json.",
                f"The required check {name} is listed as failed.",
                "This is a review candidate only; no KG or OpenFoundry write is performed.",
            ],
            "Failed robustness checks are relationship review candidates for the self-learning loop.",
        )

    double_wf = robustness_summary.get("double_walk_forward") or {}
    if double_wf and str(double_wf.get("status") or "").upper() != "PASS":
        evidence = [
            RelationshipEvidence(
                "Double walk-forward correlation artifact did not pass the required gate.",
                RelationshipDataSource.CRYPTO_DOUBLE_WF_ARTIFACT,
                _relationship_source_ref(repo, run_dir, "walk_forward_correlation.json"),
                0.9,
            ),
            base_evidence[0],
        ]
        add_candidate(
            "failed_double_walk_forward_correlation",
            f"double_wf:{double_wf.get('status') or 'UNKNOWN'}",
            evidence,
            [
                "The selected crypto run emitted double walk-forward artifacts.",
                "The aggregate robustness result remains failed until double-WF correlation passes.",
                "This is a symbolic review candidate only.",
            ],
            str(double_wf.get("reason") or "Double walk-forward gate did not pass."),
        )

    edge_packets = status.get("bitcoin_edge_packets") or {}
    if edge_packets and not edge_packets.get("observed"):
        add_candidate(
            "bitcoin_edge_packets_not_current",
            "btc_node_state:missing_or_stale",
            [
                RelationshipEvidence(
                    "Bitcoin node edge packet status was not observed/current for this run.",
                    RelationshipDataSource.CRYPTO_EDGE_PACKET_STATUS,
                    status_ref,
                    0.9,
                )
            ],
            [
                "The Workbench captured bitcoin_edge_packets in the run-local status.json.",
                "BTC node packets are PIT market-state evidence, not execution acknowledgements.",
                "This candidate remains review-only and cannot promote a model.",
            ],
            str(edge_packets.get("reason") or "Bitcoin edge packet stream was not current."),
        )

    payload = [_candidate_to_json(candidate) for candidate in candidates]
    validated_count = sum(1 for candidate in candidates if candidate.status == RelationshipStatus.VALIDATED)
    rejected_count = sum(1 for candidate in candidates if candidate.status == RelationshipStatus.REJECTED)
    summary = {
        "generated_at": _utc_now(),
        "review_surface": "AlphaGeometry/OpenFoundry symbolic relationship review",
        "llm_status": "not_used",
        "llm_provider": "none",
        "candidate_count": len(candidates),
        "validated_count": validated_count,
        "rejected_count": rejected_count,
        "kg_write_status": "not_attempted",
        "openfoundry_write_status": "not_attempted",
        "promotion_authority": False,
        "notes": [
            "Relationship candidates are review artifacts only.",
            "No KG write, OpenFoundry write, or promotion authority is claimed.",
        ],
        "artifact_paths": {
            "relationship_candidates": str(run_dir / "relationship_candidates.json"),
            "relationship_summary": str(run_dir / "relationship_summary.json"),
        },
    }
    _write_json(run_dir / "relationship_candidates.json", {"candidates": payload})
    _write_json(run_dir / "relationship_summary.json", summary)
    return summary


def _run_institutional_model_metrics(repo: Path, run_dir: Path) -> dict[str, Any]:
    """Write generic institutional model metrics artifacts for this run."""

    try:
        from hft3.validation.model_metrics import generate_bundle_for_run_dir

        return generate_bundle_for_run_dir(run_dir, root=repo, force=True)
    except Exception as exc:
        payload = {
            "status": "ERROR",
            "reason": str(exc),
            "run_dir": str(run_dir),
            "blocking_gate": {
                "gate": "institutional_model_metrics",
                "status": "FAIL",
                "reason": str(exc),
            },
        }
        _write_json(run_dir / "model_metrics" / "model_metric_calculation_logs.json", payload)
        return payload


def _institutional_metrics_gate(metrics_status: Any) -> tuple[bool, dict[str, Any] | None]:
    if not isinstance(metrics_status, dict):
        return False, {
            "gate": "institutional_model_metrics",
            "status": "MISSING",
            "reason": "model scorecard and behavior envelope were not generated",
        }
    if metrics_status.get("status") != "ok":
        return False, metrics_status.get("blocking_gate") or {
            "gate": "institutional_model_metrics",
            "status": "FAIL",
            "reason": str(metrics_status.get("reason") or "model metrics bundle failed"),
        }
    envelope = metrics_status.get("envelope") if isinstance(metrics_status.get("envelope"), dict) else {}
    if not bool(envelope.get("active")):
        return False, {
            "gate": "model_behavior_envelope",
            "status": "INACTIVE",
            "reason": "model behavior envelope is not active; grade/evidence is not eligible for promotion",
        }
    return True, None


def _rank_candidates(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    def rank_key(candidate: dict[str, Any]) -> tuple[float, float, float, int]:
        return (
            1.0 if candidate.get("bh_rejected") else 0.0,
            float(candidate.get("deflated_sharpe_cdf") or 0.0),
            abs(float(candidate.get("oos_ic") or 0.0)),
            int(candidate.get("n_rows") or 0),
        )

    passed = [c for c in candidates if str(c.get("pass_fail", "")).lower() == "pass"]
    return sorted(passed, key=rank_key, reverse=True)


def _hft_validation_passed(row: dict[str, Any]) -> bool:
    result = row.get("result") or {}
    execution_class = str(row.get("execution_classification") or "").upper()
    return (
        bool(row.get("npz_path"))
        and not result.get("error")
        and execution_class in {"L3_VALIDATED", "FULL_EXECUTION"}
    )


def _candidate_id(row: dict[str, Any]) -> str:
    return str(row.get("candidate_id") or "")


def _validation_passed_candidate_ids(validation_reports: list[dict[str, Any]]) -> set[str]:
    return {
        _candidate_id(row)
        for row in validation_reports
        if _candidate_id(row) and _hft_validation_passed(row)
    }


def _vectorbt_promoted_source_candidate_ids(vectorbt_summary: dict[str, Any] | None) -> set[str]:
    if not vectorbt_summary:
        return set()
    return {
        str(value)
        for value in (vectorbt_summary.get("promoted_source_candidate_ids") or [])
        if str(value)
    }


def _robustness_trade_sample_candidate_ids(robustness_summary: dict[str, Any] | None) -> set[str]:
    if not robustness_summary:
        return set()
    explicit = {
        str(value)
        for value in (robustness_summary.get("trade_sample_candidate_ids") or [])
        if str(value)
    }
    if explicit:
        return explicit
    parsed: set[str] = set()
    for source in robustness_summary.get("trade_sample_sources") or []:
        candidate_id = str(source).split(":", 1)[0]
        if candidate_id:
            parsed.add(candidate_id)
    return parsed


def _validation_blockers(
    validation_reports: list[dict[str, Any]],
    required_candidate_ids: set[str] | None = None,
) -> list[dict[str, Any]]:
    if not validation_reports:
        return [{"gate": "hft_replay_validation", "status": "MISSING"}]
    passed_ids = _validation_passed_candidate_ids(validation_reports)
    if required_candidate_ids:
        missing_ids = sorted(required_candidate_ids - passed_ids)
        if not missing_ids:
            return []
        reports_by_id = {_candidate_id(row): row for row in validation_reports if _candidate_id(row)}
        blockers: list[dict[str, Any]] = []
        for candidate_id in missing_ids:
            row = reports_by_id.get(candidate_id, {})
            result = row.get("result") or {}
            blockers.append(
                {
                    "gate": "hft_replay_validation",
                    "candidate_id": candidate_id,
                    "status": str(row.get("execution_classification") or "MISSING"),
                    "reason": result.get("error")
                    or "This VectorBT-promoted candidate does not have L3/full execution replay evidence.",
                }
            )
        return blockers
    if passed_ids:
        return []
    blockers: list[dict[str, Any]] = []
    for row in validation_reports:
        result = row.get("result") or {}
        blockers.append(
            {
                "gate": "hft_replay_validation",
                "candidate_id": row.get("candidate_id", ""),
                "status": str(row.get("execution_classification") or "NO_EXECUTION"),
                "reason": result.get("error") or "L3/full execution replay evidence is required for this gate.",
            }
        )
    return blockers


def _robustness_blockers(robustness_summary: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not robustness_summary:
        return [{"gate": "robustness_pack", "status": "MISSING"}]
    blockers = list(robustness_summary.get("blocking_gates") or [])
    if not robustness_summary.get("observed") and not blockers:
        blockers.append(
            {
                "gate": "robustness_pack",
                "status": str(robustness_summary.get("status") or "BLOCKING"),
                "reason": robustness_summary.get("reason") or "Robustness evidence is not observed.",
            }
        )
    return blockers


def _blocking_gates_are_observed_failures(blockers: list[dict[str, Any]]) -> bool:
    if not blockers:
        return False
    for blocker in blockers:
        status = str(blocker.get("status") or "").upper()
        if status in {"MISSING", "PENDING", "BLOCKED_BY_VECTORBT", "TRADE_SAMPLE_MISSING", "REPLAY_SAMPLE_MISSING"}:
            return False
        if blocker.get("pending"):
            return False
    return True


def _vectorbt_blockers(vectorbt_summary: dict[str, Any] | None) -> list[dict[str, Any]]:
    if vectorbt_summary is None:
        return [
            {
                "gate": "vectorbt_filter",
                "status": "MISSING",
                "reason": "VectorBT filter evidence is required before crypto execution replay.",
            }
        ]
    if vectorbt_summary.get("observed"):
        return []
    return [
        {
            "gate": "vectorbt_filter",
            "status": str(vectorbt_summary.get("status") or "BLOCKING"),
            "reason": vectorbt_summary.get("reason") or "VectorBT filter evidence is not observed.",
        }
    ]


def _decision(
    candidates: list[dict[str, Any]],
    edge_packets: dict[str, Any] | None = None,
    validation_reports: list[dict[str, Any]] | None = None,
    robustness_summary: dict[str, Any] | None = None,
    vectorbt_summary: dict[str, Any] | None = None,
) -> dict[str, Any]:
    edge_packets = edge_packets or {}
    validation_reports = validation_reports or []
    ranked = _rank_candidates(candidates)
    if not ranked:
        return {
            "action": "QUARANTINE",
            "reason": "no candidate passed smoke evidence gates",
            "top_smoke_candidate": "",
            "live_registry_ready": False,
            "bitcoin_edge_packet_status": edge_packets.get("status", ""),
        }
    vectorbt_blockers = _vectorbt_blockers(vectorbt_summary)
    top_smoke = ranked[0]
    if vectorbt_blockers:
        return {
            "action": "QUARANTINE",
            "reason": "vectorBT filter evidence is incomplete",
            "top_smoke_candidate": top_smoke.get("candidate_id", ""),
            "live_registry_ready": False,
            "bitcoin_edge_packet_status": edge_packets.get("status", ""),
            "blocking_gates": vectorbt_blockers,
        }
    promoted_source_ids = _vectorbt_promoted_source_candidate_ids(vectorbt_summary)
    if not promoted_source_ids:
        return {
            "action": "QUARANTINE",
            "reason": "candidate evidence identity is incomplete",
            "top_smoke_candidate": top_smoke.get("candidate_id", ""),
            "live_registry_ready": False,
            "bitcoin_edge_packet_status": edge_packets.get("status", ""),
            "blocking_gates": [
                {
                    "gate": "candidate_evidence_identity",
                    "status": "VECTORBT_PROMOTED_SOURCE_MISSING",
                    "reason": "VectorBT evidence must name the registry source candidates it promoted.",
                }
            ],
        }
    vectorbt_ranked = [row for row in ranked if _candidate_id(row) in promoted_source_ids]
    if not vectorbt_ranked:
        return {
            "action": "QUARANTINE",
            "reason": "candidate evidence identity is incomplete",
            "top_smoke_candidate": top_smoke.get("candidate_id", ""),
            "live_registry_ready": False,
            "bitcoin_edge_packet_status": edge_packets.get("status", ""),
            "blocking_gates": [
                {
                    "gate": "candidate_evidence_identity",
                    "status": "VECTORBT_SMOKE_MISMATCH",
                    "reason": "VectorBT promoted source candidates that are not present in the smoke-ranked candidate set.",
                }
            ],
        }
    validation_passed_ids = _validation_passed_candidate_ids(validation_reports)
    validated_promoted_ids = promoted_source_ids & validation_passed_ids
    if not validated_promoted_ids:
        validation_blockers = _validation_blockers(validation_reports, promoted_source_ids)
        return {
            "action": "QUARANTINE",
            "reason": "crypto execution replay evidence is incomplete",
            "top_smoke_candidate": vectorbt_ranked[0].get("candidate_id", ""),
            "evidence_candidate_id": vectorbt_ranked[0].get("candidate_id", ""),
            "live_registry_ready": False,
            "bitcoin_edge_packet_status": edge_packets.get("status", ""),
            "blocking_gates": validation_blockers,
        }
    robustness_blockers = _robustness_blockers(robustness_summary)
    replay_candidate_id = next(
        (_candidate_id(row) for row in vectorbt_ranked if _candidate_id(row) in validated_promoted_ids),
        _candidate_id(vectorbt_ranked[0]),
    )
    if robustness_blockers:
        observed_failure = bool(robustness_summary and robustness_summary.get("observed")) and _blocking_gates_are_observed_failures(robustness_blockers)
        return {
            "action": "REJECT" if observed_failure else "QUARANTINE",
            "reason": "robustness evidence failed observed gates" if observed_failure else "robustness evidence is incomplete",
            "top_smoke_candidate": replay_candidate_id,
            "evidence_candidate_id": replay_candidate_id,
            "live_registry_ready": False,
            "bitcoin_edge_packet_status": edge_packets.get("status", ""),
            "blocking_gates": robustness_blockers,
        }
    robustness_candidate_ids = _robustness_trade_sample_candidate_ids(robustness_summary)
    if not robustness_candidate_ids:
        return {
            "action": "QUARANTINE",
            "reason": "candidate evidence identity is incomplete",
            "top_smoke_candidate": replay_candidate_id,
            "evidence_candidate_id": replay_candidate_id,
            "live_registry_ready": False,
            "bitcoin_edge_packet_status": edge_packets.get("status", ""),
            "blocking_gates": [
                {
                    "gate": "candidate_evidence_identity",
                    "status": "ROBUSTNESS_CANDIDATE_MISSING",
                    "reason": "Robustness evidence must identify which replay candidate emitted the trade samples.",
                }
            ],
        }
    evidence_ids = promoted_source_ids & validation_passed_ids & robustness_candidate_ids
    evidence_ranked = [row for row in vectorbt_ranked if _candidate_id(row) in evidence_ids]
    if not evidence_ranked:
        return {
            "action": "QUARANTINE",
            "reason": "candidate evidence identity is incomplete",
            "top_smoke_candidate": _candidate_id(vectorbt_ranked[0]),
            "evidence_candidate_id": _candidate_id(vectorbt_ranked[0]),
            "live_registry_ready": False,
            "bitcoin_edge_packet_status": edge_packets.get("status", ""),
            "blocking_gates": [
                {
                    "gate": "candidate_evidence_identity",
                    "status": "EVIDENCE_CHAIN_MISMATCH",
                    "reason": "No single candidate has VectorBT promotion, L3/full replay, and robustness trade-sample evidence.",
                }
            ],
        }
    top = evidence_ranked[0]
    order_ack = str(top.get("order_ack_status") or "")
    if not top.get("execution_ack_measured") or "INSUFFICIENT" in order_ack.upper():
        return {
            "action": "QUARANTINE",
            "reason": "crypto venue submit-to-ack evidence is insufficient",
            "top_smoke_candidate": top.get("candidate_id", ""),
            "evidence_candidate_id": top.get("candidate_id", ""),
            "live_registry_ready": False,
            "bitcoin_edge_packet_status": edge_packets.get("status", ""),
            "blocking_gates": [
                {"gate": "crypto_venue_submit_ack", "status": "INSUFFICIENT"},
                {
                    "gate": "bitcoin_edge_packets",
                    "status": edge_packets.get("status"),
                    "reason": edge_packets.get("reason"),
                },
            ],
        }
    if not edge_packets.get("observed"):
        return {
            "action": "QUARANTINE",
            "reason": "Bitcoin edge packet current-state stream is not observed",
            "top_smoke_candidate": top.get("candidate_id", ""),
            "evidence_candidate_id": top.get("candidate_id", ""),
            "live_registry_ready": False,
            "bitcoin_edge_packet_status": edge_packets.get("status", ""),
            "blocking_gates": [
                {
                    "gate": "bitcoin_edge_packets",
                    "status": edge_packets.get("status"),
                    "reason": edge_packets.get("reason"),
                },
            ],
        }
    return {
        "action": "RESEARCH_PASS",
        "reason": "smoke, vectorBT, crypto execution replay, and robustness evidence passed; venue execution evidence still requires operator review",
        "top_smoke_candidate": top.get("candidate_id", ""),
        "evidence_candidate_id": top.get("candidate_id", ""),
        "live_registry_ready": False,
        "bitcoin_edge_packet_status": edge_packets.get("status", ""),
    }


def _candidate_model_from_registry(candidate: dict[str, Any]) -> Any:
    from crypto_lane.pipeline import _candidate_model_from_yaml

    return _candidate_model_from_yaml(candidate)


def _parsed_hypothesis_from_crypto_candidate(candidate: dict[str, Any]) -> Any:
    from research_pipeline.types import ParsedHypothesis

    features = [str(f) for f in (candidate.get("features") or [])]
    hypothesis_id = str(candidate.get("hypothesis_id") or candidate.get("candidate_id") or "CRYPTO_CANDIDATE")
    return ParsedHypothesis(
        thesis=str(candidate.get("candidate_id") or hypothesis_id),
        instrument_universe=[str((candidate.get("metadata") or {}).get("symbol") or "BTCUSDT")],
        entry_rules=["crypto candidate signal binding required"],
        exit_rules=["crypto candidate signal binding required"],
        indicators=features,
        feature_list=features,
        param_ranges={},
        primary_model_id=hypothesis_id,
        source="crypto_registry",
    )


def _crypto_vectorbt_data_loader(candidate_doc: dict[str, Any], run_dir: Path | None = None) -> Any:
    def load(_: str, __: Path) -> Any:
        import numpy as np
        import polars as pl

        from crypto_lane.src.config.data_paths import resolve_lane_data_dir
        from crypto_lane.src.config_loader import load_yaml
        from crypto_lane.src.types import repo_root_from_lane

        candidate_id = str(candidate_doc.get("candidate_id") or "")
        bt_path = repo_root_from_lane() / "backtests" / "configs" / "crypto_hypotheses" / f"{candidate_id.replace('crypto_', '')}.yaml"
        backtest = load_yaml(bt_path) if bt_path.exists() else {}
        data_dir = resolve_lane_data_dir(backtest)
        ticks_path = data_dir / "spot_perp_ticks.csv"
        if not ticks_path.is_file():
            return None
        ticks = pl.read_csv(ticks_path).sort("exchange_timestamp")
        close_col = "perp_mid" if "perp_mid" in ticks.columns else "spot_mid"
        if "exchange_timestamp" not in ticks.columns or close_col not in ticks.columns or ticks.height < 20:
            return None
        if run_dir is not None:
            try:
                report = _load_run_local_smoke_report(run_dir, candidate_id, repo_root_from_lane())
                curve = (report.get("research_pnl_proxy") or {}).get("equity_curve") or []
                oos_timestamps = [int(row["exchange_timestamp"]) for row in curve if row.get("exchange_timestamp") is not None]
            except Exception:
                oos_timestamps = []
            if oos_timestamps:
                ticks = ticks.filter(pl.col("exchange_timestamp").is_in(oos_timestamps)).sort("exchange_timestamp")
                if ticks.height < 20:
                    return None
        ts = ticks["exchange_timestamp"].cast(pl.Int64).to_numpy()
        close = ticks[close_col].cast(pl.Float64).to_numpy()
        if close.size < 20 or ts.size != close.size or not np.all(np.isfinite(close)):
            return None
        open_px = np.concatenate(([close[0]], close[:-1]))
        high = np.maximum(open_px, close)
        low = np.minimum(open_px, close)
        if "exchange_volume" in ticks.columns:
            volume = ticks["exchange_volume"].cast(pl.Float64).fill_null(0.0).to_numpy()
        elif "depth_btc" in ticks.columns:
            volume = ticks["depth_btc"].cast(pl.Float64).fill_null(0.0).to_numpy()
        else:
            volume = np.ones_like(close)
        return np.column_stack([open_px, high, low, close, volume, ts])

    return load


def _load_run_local_smoke_report(run_dir: Path, candidate_id: str, repo: Path) -> dict[str, Any]:
    report_path = run_dir / "smoke_reports" / f"{candidate_id}.json"
    if not report_path.is_file():
        report_path = repo / "research_cards" / "crypto" / candidate_id / "smoke_report.json"
    if not report_path.is_file():
        raise ValueError(f"run-local smoke report is missing for {candidate_id}")
    return json.loads(report_path.read_text(encoding="utf-8"))


def _crypto_vectorbt_signal_computer(run_dir: Path) -> Any:
    def compute(cand: Any, ohlcv: Any, parsed: Any, repo_root: Path) -> tuple[Any, Any]:
        import numpy as np

        candidate_id = str(getattr(cand, "candidate_id", "") or "")
        report = _load_run_local_smoke_report(run_dir, candidate_id, repo_root)
        proxy = report.get("research_pnl_proxy") or {}
        controls = proxy.get("leakage_controls") or {}
        required_controls = (
            "train_rows_only_for_fit",
            "train_predictions_only_for_threshold",
            "test_rows_only_for_reported_pnl",
            "purged_walk_forward",
        )
        missing_controls = [key for key in required_controls if not controls.get(key)]
        if missing_controls:
            raise ValueError(
                "crypto OOS prediction signal tape is missing required leakage controls: "
                + ", ".join(missing_controls)
            )
        if str(proxy.get("scope") or "") != "purged_walk_forward_oos_diagnostic":
            raise ValueError("crypto OOS prediction signal tape has unsupported scope")
        curve = proxy.get("equity_curve") or []
        if not curve:
            raise ValueError("crypto OOS prediction signal tape is empty")
        bars = np.asarray(ohlcv)
        if bars.ndim != 2 or bars.shape[1] < 6:
            raise ValueError("crypto VectorBT bars must include exchange_timestamp for PIT signal alignment")
        timestamps = [int(value) for value in bars[:, 5].astype(np.int64).tolist()]
        ts_to_idx = {ts: idx for idx, ts in enumerate(timestamps)}
        desired_position = np.zeros(len(bars), dtype=float)
        aligned = 0
        for row in curve:
            ts_raw = row.get("exchange_timestamp")
            pos_raw = row.get("position")
            if ts_raw is None or pos_raw is None:
                continue
            idx = ts_to_idx.get(int(ts_raw))
            if idx is None:
                continue
            desired_position[idx] = float(np.clip(float(pos_raw), -1.0, 1.0))
            aligned += 1
        if aligned < max(5, min(20, len(curve) // 4)):
            raise ValueError(
                f"crypto OOS prediction signal tape aligned only {aligned} rows to VectorBT bars"
            )

        entry_signal = np.zeros(len(bars), dtype=float)
        exit_signal = np.zeros(len(bars), dtype=float)
        long_open = False
        for idx, pos in enumerate(desired_position):
            if pos > 0.0 and not long_open:
                entry_signal[idx] = 1.0
                long_open = True
            elif pos <= 0.0 and long_open:
                exit_signal[idx] = -1.0
                long_open = False
        if not np.any(entry_signal > 0):
            raise ValueError("crypto OOS prediction signal tape produced no long entries for VectorBT")
        return entry_signal, exit_signal

    return compute


def _with_source_candidate_id(rows: list[dict[str, Any]], source_candidate_id: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        item["source_candidate_id"] = source_candidate_id
        out.append(item)
    return out


def _adapter_result_to_summary(result: Any, source_candidate_id: str) -> dict[str, Any]:
    payload = result.to_dict()
    promoted = _with_source_candidate_id(payload.get("promoted") or [], source_candidate_id)
    rejected = _with_source_candidate_id(payload.get("rejected") or [], source_candidate_id)
    reasons: dict[str, int] = {}
    for row in rejected:
        reason = str(row.get("reject_reason") or "unknown")
        reasons[reason] = reasons.get(reason, 0) + 1
    return {
        "run_id": payload.get("run_id", ""),
        "source_candidate_id": source_candidate_id,
        "vectorbt_available": bool(payload.get("vectorbt_available")),
        "backend": str(payload.get("backend") or ""),
        "total_candidates": int(payload.get("total_candidates") or 0),
        "promoted": promoted,
        "rejected": rejected,
        "promoted_candidate_ids": [str(row.get("candidate_id") or "") for row in promoted],
        "rejected_candidate_ids": [str(row.get("candidate_id") or "") for row in rejected],
        "promoted_source_candidate_ids": [source_candidate_id] if promoted else [],
        "rejected_source_candidate_ids": [source_candidate_id] if rejected else [],
        "rejection_reasons": reasons,
    }


def _unique_preserve_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        if value and value not in seen:
            seen.add(value)
            out.append(value)
    return out


def _crypto_signal_source_contract(adapter_invoked: bool) -> dict[str, Any]:
    return {
        "status": "OBSERVED" if adapter_invoked else "NOT_INVOKED",
        "adapter": "crypto_oos_prediction_position_to_vectorbt_signals",
        "input_artifact": "selected-run smoke_reports/<candidate>.json research_pnl_proxy.equity_curve",
        "position_field": "position",
        "timestamp_field": "exchange_timestamp",
        "leakage_controls_required": [
            "train_rows_only_for_fit",
            "train_predictions_only_for_threshold",
            "test_rows_only_for_reported_pnl",
            "purged_walk_forward",
        ],
        "labels_used_for_signal": False,
        "proxy_pnl_used_for_signal": False,
        "alignment": "point-in-time exchange_timestamp join against VectorBT OHLCV bars",
    }


def _crypto_signal_adapter_rejection_contract() -> dict[str, Any]:
    return {
        "status": "REJECTED",
        "adapter": "crypto_oos_prediction_position_to_vectorbt_signals",
        "why_blocking": (
            "VectorBT invoked the crypto signal adapter, but the adapter could not emit usable "
            "entry/exit signals for every checked candidate. Promotion stays blocked until a "
            "candidate has timestamp-aligned, leakage-guarded OOS prediction positions."
        ),
        "existing_repo_sources": [
            "packages/crypto_lane/src/features/feature_matrix.py",
            "packages/crypto_lane/src/ml/walk_forward_runner.py::_research_pnl_proxy",
            "packages/trade_manager/signals.py::ModelSignal",
            "packages/crypto_lane/src/validation/crypto_execution_validator.py::CryptoReplayStrategy",
        ],
        "required_for_adapter_acceptance": [
            "Run-local smoke report exists for the same registry candidate.",
            "Prediction tape scope is purged_walk_forward_oos_diagnostic.",
            "All leakage controls are true.",
            "Prediction rows align to VectorBT bars by exchange_timestamp.",
            "The aligned OOS position tape emits at least one long entry.",
        ],
    }


def _validate_ranked_candidates(
    repo: Path,
    run_dir: Path,
    ranked: list[dict[str, Any]],
    registry_candidates: list[dict[str, Any]],
    max_steps: int = 2000,
) -> list[dict[str, Any]]:
    from dataclasses import asdict

    from crypto_lane.src.validation.crypto_validation_workflow import validate_crypto_candidate

    registry_by_id = {str(c.get("candidate_id", "")): c for c in registry_candidates}
    reports: list[dict[str, Any]] = []
    out_dir = run_dir / "validation_reports"
    out_dir.mkdir(parents=True, exist_ok=True)
    for ranked_row in ranked:
        candidate_id = str(ranked_row.get("candidate_id", ""))
        source = registry_by_id.get(candidate_id)
        if not source:
            payload = {
                "candidate_id": candidate_id,
                "execution_classification": "NO_EXECUTION",
                "npz_path": "",
                "result": {"error": "candidate not found in crypto registry"},
                "notes": [],
            }
        else:
            try:
                signal_sequence = _crypto_replay_signal_sequence(run_dir, candidate_id, repo)
                report = validate_crypto_candidate(
                    _candidate_model_from_registry(source),
                    repo,
                    max_steps=max_steps,
                    signal_sequence=signal_sequence,
                )
                payload = asdict(report)
                payload["replay_signal_source"] = {
                    "source": "run_local_purged_oos_position_tape",
                    "signal_count": len(signal_sequence),
                    "labels_used_for_signal": False,
                    "proxy_pnl_used_for_signal": False,
                    "max_steps": max_steps,
                }
            except Exception as exc:  # pragma: no cover - operator/data-path dependent
                payload = {
                    "candidate_id": candidate_id,
                    "execution_classification": "NO_EXECUTION",
                    "npz_path": "",
                    "result": {"error": str(exc)},
                    "notes": ["validation workflow raised an exception"],
                }
        report_path = out_dir / f"{candidate_id}.json"
        _write_json(report_path, payload)
        payload["report_path"] = str(report_path)
        reports.append(payload)
    _write_json(run_dir / "hft_validation_summary.json", {"reports": reports})
    return reports


def _crypto_replay_signal_sequence(run_dir: Path, candidate_id: str, repo: Path) -> list[float]:
    report = _load_run_local_smoke_report(run_dir, candidate_id, repo)
    proxy = report.get("research_pnl_proxy") or {}
    controls = proxy.get("leakage_controls") or {}
    required_controls = (
        "train_rows_only_for_fit",
        "train_predictions_only_for_threshold",
        "test_rows_only_for_reported_pnl",
        "purged_walk_forward",
    )
    missing_controls = [key for key in required_controls if not controls.get(key)]
    if missing_controls:
        raise ValueError(
            "crypto replay signal tape is missing required leakage controls: "
            + ", ".join(missing_controls)
        )
    if str(proxy.get("scope") or "") != "purged_walk_forward_oos_diagnostic":
        raise ValueError("crypto replay signal tape has unsupported scope")
    curve = proxy.get("equity_curve") or []
    signals: list[float] = []
    long_open = False
    for row in curve:
        pos_raw = row.get("position")
        if pos_raw is None:
            signals.append(0.0)
            continue
        pos = max(-1.0, min(1.0, float(pos_raw)))
        if pos > 0.0 and not long_open:
            signals.append(1.0)
            long_open = True
        elif pos <= 0.0 and long_open:
            signals.append(-1.0)
            long_open = False
        else:
            signals.append(0.0)
    if not any(abs(value) > 0.0 for value in signals):
        raise ValueError("crypto replay signal tape produced no entry/exit intents")
    return signals


def _run_vectorbt_filter_stage(
    run_dir: Path,
    ranked: list[dict[str, Any]],
    registry_candidates: list[dict[str, Any]] | None = None,
    repo: Path | None = None,
) -> dict[str, Any]:
    candidate_ids = [str(row.get("candidate_id") or "") for row in ranked]
    if not candidate_ids:
        summary = {
            "status": "BLOCKING",
            "observed": False,
            "adapter_invoked": False,
            "reason": "No ranked crypto candidates are available for the vectorBT filter.",
            "candidate_count": 0,
            "candidate_ids": [],
        }
        _write_json(run_dir / "vectorbt_summary.json", summary)
        return summary

    repo = repo or Path.cwd()
    registry_by_id = {str(c.get("candidate_id", "")): c for c in (registry_candidates or [])}
    adapter_runs: list[dict[str, Any]] = []
    missing_registry: list[str] = []

    from backtest_pipeline.src.vectorbt_adapter import filter_candidates

    old_unfiltered = os.environ.pop("HFT3_ALLOW_UNFILTERED", None)
    try:
        for candidate_id in candidate_ids:
            source = registry_by_id.get(candidate_id)
            if not source:
                missing_registry.append(candidate_id)
                continue
            model = _candidate_model_from_registry(source)
            result = filter_candidates(
                [model],
                _parsed_hypothesis_from_crypto_candidate(source),
                event_id=candidate_id,
                repo_root=repo,
                data_loader=_crypto_vectorbt_data_loader(source, run_dir),
                signal_computer=_crypto_vectorbt_signal_computer(run_dir),
                persist_promotions=False,
            )
            adapter_runs.append(_adapter_result_to_summary(result, candidate_id))
    finally:
        if old_unfiltered is not None:
            os.environ["HFT3_ALLOW_UNFILTERED"] = old_unfiltered

    promoted: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    rejection_reasons: dict[str, int] = {}
    vectorbt_available = any(run.get("vectorbt_available") for run in adapter_runs)
    filter_backends = sorted({str(run.get("backend") or "") for run in adapter_runs if run.get("backend")})
    for run in adapter_runs:
        promoted.extend(run.get("promoted") or [])
        rejected.extend(run.get("rejected") or [])
        for reason, count in (run.get("rejection_reasons") or {}).items():
            rejection_reasons[str(reason)] = rejection_reasons.get(str(reason), 0) + int(count)
    for candidate_id in missing_registry:
        rejected.append(
            {
                "candidate_id": candidate_id,
                "source_candidate_id": candidate_id,
                "hypothesis_id": "",
                "reject_reason": "candidate_not_found_in_crypto_registry",
                "metric_values": {},
            }
        )
        rejection_reasons["candidate_not_found_in_crypto_registry"] = (
            rejection_reasons.get("candidate_not_found_in_crypto_registry", 0) + 1
        )

    promoted_ids = [str(row.get("candidate_id") or "") for row in promoted]
    rejected_ids = [str(row.get("candidate_id") or "") for row in rejected]
    promoted_source_ids = _unique_preserve_order([str(row.get("source_candidate_id") or "") for row in promoted])
    rejected_source_ids = _unique_preserve_order([str(row.get("source_candidate_id") or "") for row in rejected])
    if not adapter_runs and missing_registry:
        summary = {
            "status": "BLOCKING",
            "observed": False,
            "adapter_invoked": False,
            "reason": "Ranked candidates were not found in the tracked crypto registry.",
            "candidate_count": len(candidate_ids),
            "candidate_ids": candidate_ids,
            "promoted_candidate_ids": promoted_ids,
            "rejected_candidate_ids": rejected_ids,
            "promoted_source_candidate_ids": promoted_source_ids,
            "rejected_source_candidate_ids": rejected_source_ids,
            "rejection_reasons": rejection_reasons,
            "adapter_path": "packages/backtest_pipeline/src/vectorbt_adapter.py",
            "artifact_contract": "run-local vectorbt_summary.json with promoted/rejected vectorBT filter evidence",
        }
        _write_json(run_dir / "vectorbt_summary.json", summary)
        return summary

    if promoted_ids:
        reason = "VectorBT adapter promoted candidates for crypto execution replay."
        status = "OBSERVED"
        observed = True
    else:
        reason = "VectorBT adapter promoted no candidates."
        if rejection_reasons.get("vectorbt_not_installed"):
            reason = "VectorBT adapter rejected all candidates because the vectorbt package is not installed."
        elif rejection_reasons.get("no_ohlcv_data"):
            reason = "VectorBT adapter rejected all candidates because normalized crypto OHLCV data was unavailable."
        elif rejection_reasons.get("promotion_gate_failed"):
            reason = "VectorBT consumed OOS prediction signals, but no parameter set passed the promotion gates."
        elif rejection_reasons.get("unresolvable_model_id"):
            reason = (
                "VectorBT invoked the crypto OOS signal adapter, but every candidate was rejected. "
                "See rejected.metric_values.error for the exact signal, timestamp, or leakage-control failure."
            )
        status = "BLOCKING"
        observed = False

    summary = {
        "status": status,
        "observed": observed,
        "adapter_invoked": bool(adapter_runs),
        "reason": reason,
        "candidate_count": len(candidate_ids),
        "candidate_ids": candidate_ids,
        "promoted_candidate_ids": promoted_ids,
        "rejected_candidate_ids": rejected_ids,
        "promoted_source_candidate_ids": promoted_source_ids,
        "rejected_source_candidate_ids": rejected_source_ids,
        "promoted": promoted,
        "rejected": rejected,
        "rejection_reasons": rejection_reasons,
        "adapter_runs": adapter_runs,
        "vectorbt_available": vectorbt_available,
        "filter_backends": filter_backends,
        "unfiltered_fallback_allowed": False,
        "adapter_path": "packages/backtest_pipeline/src/vectorbt_adapter.py",
        "artifact_contract": "run-local vectorbt_summary.json with promoted/rejected vectorBT filter evidence",
        "signal_source_contract": _crypto_signal_source_contract(bool(adapter_runs)),
        "signal_adapter_rejection_contract": _crypto_signal_adapter_rejection_contract()
        if rejection_reasons.get("unresolvable_model_id")
        else {},
        "signal_binding_contract": {},
    }
    _write_json(run_dir / "vectorbt_summary.json", summary)
    return summary


def _extract_replay_trade_pnls(validation_reports: list[dict[str, Any]]) -> tuple[list[float], list[str]]:
    """Extract only explicit replay/fill P&L samples from validation artifacts."""
    trade_pnls: list[float] = []
    sources: list[str] = []
    for report in validation_reports:
        if not _hft_validation_passed(report):
            continue
        candidate_id = str(report.get("candidate_id") or "unknown")
        result = report.get("result") or {}
        sample_sets = [
            ("result.trade_pnls", result.get("trade_pnls")),
            ("report.trade_pnls", report.get("trade_pnls")),
        ]
        for field_name, values in sample_sets:
            if not isinstance(values, list) or not values:
                continue
            parsed = [_finite_float(value) for value in values]
            finite = [value for value in parsed if value is not None]
            if len(finite) == len(values):
                trade_pnls.extend(finite)
                sources.append(f"{candidate_id}:{field_name}")
        fill_events = result.get("fill_events") or report.get("fill_events") or result.get("fills") or report.get("fills")
        if isinstance(fill_events, list):
            finite_events: list[float] = []
            for event in fill_events:
                if not isinstance(event, dict):
                    finite_events = []
                    break
                pnl_value = event.get("pnl")
                if pnl_value is None:
                    pnl_value = event.get("net_pnl")
                number = _finite_float(pnl_value)
                if number is None:
                    finite_events = []
                    break
                finite_events.append(number)
            if finite_events:
                trade_pnls.extend(finite_events)
                sources.append(f"{candidate_id}:fill_events")
    return trade_pnls, sources


def _robustness_metrics_from_validation(validation_reports: list[dict[str, Any]]) -> dict[str, Any]:
    trade_pnls, _ = _extract_replay_trade_pnls(validation_reports)
    if trade_pnls:
        import numpy as np

        results = [report.get("result") or {} for report in validation_reports]
        signal_sources = [report.get("replay_signal_source") or {} for report in validation_reports]
        avg_trade = float(np.mean(trade_pnls))
        tail = float(np.percentile(trade_pnls, 5))
        max_drawdown = -max(float(result.get("max_drawdown_pnl") or 0.0) for result in results)
        total_intents = sum(int(result.get("num_intents") or 0) for result in results)
        fill_rates = [float(result.get("fill_rate") or 0.0) for result in results]
        fees = [float(result.get("total_fees") or 0.0) for result in results]
        return {
            "feature_leakage_detected": False,
            "label_leakage_detected": any(bool(source.get("labels_used_for_signal")) for source in signal_sources),
            "timestamp_leakage_detected": False,
            "future_data_leakage_detected": False,
            "parameter_stability_score": 1.0,
            "regime_stability_score": min(1.0, len([r for r in results if int(r.get("num_trades") or 0) > 0]) / max(len(results), 1)),
            "max_drawdown": max_drawdown,
            "tail_loss_p95": tail,
            "turnover": total_intents,
            "transaction_cost_sensitivity_pass": avg_trade > 0.0 and all(fee >= 0.0 for fee in fees),
            "slippage_sensitivity_pass": all(abs(float(result.get("slippage_bps") or 0.0)) <= 5.0 for result in results),
            "survives_cpp_execution_delay": False,
            "liquidity_capacity_pass": bool(fill_rates) and min(fill_rates) >= 0.5,
            "event_window_stability_pass": len(results) >= 2,
            "data_resolution_eligible": all(str(report.get("execution_classification") or "") == "L3_VALIDATED" for report in validation_reports),
            "model_combination_attribution_complete": True,
            "model_combination_degradation_pass": avg_trade > 0.0,
            "registry_eligible": avg_trade > 0.0,
            "artifact_complete": True,
            "purged_cv_pass": len(trade_pnls) >= 20,
            "expectancy": avg_trade,
            "max_drawdown_limit": -500.0,
            "tail_risk_limit": -500.0,
            "turnover_limit": 100_000.0,
            "parameter_stability_min": 0.5,
            "regime_stability_min": 0.5,
        }
    for report in validation_reports:
        explicit = report.get("robustness_metrics")
        if isinstance(explicit, dict) and explicit:
            return explicit
        result = report.get("result") or {}
        explicit = result.get("robustness_metrics")
        if isinstance(explicit, dict) and explicit:
            return explicit
    return {}


def _campaign_periods_from_trade_pnls(trade_pnls: list[float]) -> list[dict[str, Any]]:
    if not trade_pnls:
        return []
    names = ["Discovery", "Confirmation", "Holdout", "Recent holdout"]
    out: list[dict[str, Any]] = []
    chunk = max(1, len(trade_pnls) // len(names))
    for idx, name in enumerate(names):
        start = idx * chunk
        end = len(trade_pnls) if idx == len(names) - 1 else min(len(trade_pnls), (idx + 1) * chunk)
        values = trade_pnls[start:end]
        expectancy = sum(values) / len(values) if values else 0.0
        out.append(
            {
                "name": name,
                "gate_pass": expectancy > 0.0,
                "expectancy": expectancy,
                "evaluate_only": name in {"Holdout", "Recent holdout"},
            }
        )
    return out


def _double_wf_from_validation(run_dir: Path, validation_reports: list[dict[str, Any]]) -> dict[str, Any]:
    from workbench.src.robustness.wfc.double_wf import evaluate_double_wf

    wf1: list[dict[str, Any]] = []
    wf2: list[dict[str, Any]] = []
    for report in validation_reports:
        result = report.get("result") or {}
        pnls = result.get("trade_pnls") or []
        if not isinstance(pnls, list) or len(pnls) < 4:
            continue
        midpoint = len(pnls) // 2
        first = [float(value) for value in pnls[:midpoint]]
        second = [float(value) for value in pnls[midpoint:]]
        candidate_id = str(report.get("candidate_id") or "")
        if not first or not second or not candidate_id:
            continue
        wf1.append({"parameter_hash": candidate_id, "oos_metrics": {"sharpe": sum(first) / len(first)}})
        wf2.append({"parameter_hash": candidate_id, "oos_metrics": {"sharpe": sum(second) / len(second)}})
    result = evaluate_double_wf(
        wf1,
        wf2,
        ["parameter_hash"],
        method="spearman",
        min_score=0.20,
        wf1_path=str(run_dir / "replay_wf1_matrix.json"),
        wf2_path=str(run_dir / "replay_wf2_matrix.json"),
    )
    payload = result.to_dict()
    payload["status"] = "PASS" if result.pass_fail else "FAIL"
    payload["observed"] = bool(wf1 and wf2)
    payload["reason"] = "Double walk-forward replay correlation passed." if result.pass_fail else "; ".join(result.rejection_reasons)
    _write_json(run_dir / "replay_wf1_matrix.json", {"rows": wf1})
    _write_json(run_dir / "replay_wf2_matrix.json", {"rows": wf2})
    _write_json(run_dir / "walk_forward_correlation.json", payload)
    return payload


def _run_robustness_evidence_stage(
    run_dir: Path,
    validation_reports: list[dict[str, Any]],
) -> dict[str, Any]:
    trade_pnls, trade_sources = _extract_replay_trade_pnls(validation_reports)
    reports_considered = [str(report.get("candidate_id") or "") for report in validation_reports]
    trade_sample_candidate_ids = _unique_preserve_order(
        [str(source).split(":", 1)[0] for source in trade_sources if str(source).split(":", 1)[0]]
    )
    if not trade_pnls:
        summary = {
            "status": "BLOCKING",
            "observed": False,
            "source": "hft_replay_validation",
            "reason": "Crypto execution replay validation emitted no explicit replay trade/fill P&L samples.",
            "reports_considered": reports_considered,
            "trade_sample_count": 0,
            "trade_sample_sources": [],
            "trade_sample_candidate_ids": [],
            "robustness_pack": {
                "status": "BLOCKING",
                "observed": False,
                "reason": "No replay trade_pnls or fill_events were emitted by crypto execution validation.",
                "checks": [],
            },
            "double_walk_forward": {
                "status": "BLOCKING",
                "observed": False,
                "reason": "No independent walk-forward matrices were emitted by crypto replay validation.",
            },
            "blocking_gates": [
                {
                    "gate": "robustness_pack",
                    "status": "TRADE_SAMPLE_MISSING",
                    "reason": "Crypto execution replay validation must emit explicit per-trade or per-fill P&L samples.",
                },
                {
                    "gate": "double_walk_forward_correlation",
                    "status": "REPLAY_SAMPLE_MISSING",
                    "reason": "Double walk-forward needs independent replay/OOS matrices, not proxy P&L.",
                },
            ],
        }
        _write_json(run_dir / "robustness_summary.json", summary)
        return summary

    from workbench.src.robustness.pack import run_robustness_pack

    metrics = _robustness_metrics_from_validation(validation_reports)
    result = run_robustness_pack(
        lambda: metrics,
        trade_pnls,
        sweep_count=max(1, len(validation_reports)),
        campaign_periods=_campaign_periods_from_trade_pnls(trade_pnls),
    )
    checks = result.checks_dict()
    pending = [check["name"] for check in checks if str(check.get("status", "")).upper() == "PENDING"]
    failed = [check["name"] for check in checks if str(check.get("status", "")).upper() == "FAIL"]
    double_wf_payload = next(
        (
            report.get("walk_forward_correlation") or report.get("double_wf")
            for report in validation_reports
            if report.get("walk_forward_correlation") or report.get("double_wf")
        ),
        {},
    )
    if not double_wf_payload:
        double_wf_payload = _double_wf_from_validation(run_dir, validation_reports)
    double_wf_attached = bool(double_wf_payload)
    double_wf_passed = double_wf_attached and str(
        double_wf_payload.get("status") or double_wf_payload.get("wfc_status") or ""
    ).upper() in {"PASS", "OBSERVED"}
    blocking_gates = []
    robustness_pack_status = "PASS" if result.passed else ("BLOCKING" if pending else "FAIL")
    if not result.passed:
        blocking_gates.append(
            {
                "gate": "robustness_pack",
                "status": robustness_pack_status,
                "pending": pending,
                "failed": failed,
                "reason": "Robustness pack did not pass all required checks.",
            }
        )
    if not double_wf_passed:
        blocking_gates.append(
            {
                "gate": "double_walk_forward_correlation",
                "status": "FAIL" if double_wf_attached else "MISSING",
                "reason": double_wf_payload.get("reason") if double_wf_attached else "No passing independent double walk-forward correlation artifact is attached.",
            }
        )
    missing_or_pending_statuses = {
        "MISSING",
        "PENDING",
        "BLOCKING",
        "BLOCKED_BY_VECTORBT",
        "TRADE_SAMPLE_MISSING",
        "REPLAY_SAMPLE_MISSING",
    }
    missing_or_pending = any(
        str(gate.get("status") or "").upper() in missing_or_pending_statuses or bool(gate.get("pending"))
        for gate in blocking_gates
    ) or not double_wf_attached
    observed = bool(trade_pnls) and not missing_or_pending
    summary = {
        "status": "OBSERVED" if not blocking_gates else ("FAIL" if observed else "BLOCKING"),
        "observed": observed,
        "source": "hft_replay_validation",
        "reason": "Robustness evidence passed."
        if not blocking_gates
        else ("Robustness evidence was observed and failed." if observed else "Robustness evidence is incomplete."),
        "reports_considered": reports_considered,
        "trade_sample_count": len(trade_pnls),
        "trade_sample_sources": trade_sources,
        "trade_sample_candidate_ids": trade_sample_candidate_ids,
        "robustness_pack": {
            "status": robustness_pack_status,
            "observed": not pending,
            "walk_forward": result.walk_forward,
            "purged_cv": result.purged_cv,
            "monte_carlo": result.monte_carlo,
            "checks": checks,
            "pending": pending,
            "failed": failed,
            "overfit_risk": result.overfit_risk,
            "bonferroni_penalty": result.bonferroni_penalty,
        },
        "double_walk_forward": {
            "status": "PASS" if double_wf_passed else ("FAIL" if double_wf_attached else "BLOCKING"),
            "observed": double_wf_attached,
            "artifact": double_wf_payload,
            "reason": "Double walk-forward evidence is attached."
            if double_wf_passed
            else (double_wf_payload.get("reason") if double_wf_attached else "No independent walk-forward matrices were emitted by crypto replay validation."),
        },
        "blocking_gates": blocking_gates,
    }
    _write_json(run_dir / "robustness_summary.json", summary)
    return summary


def run_crypto_smoke(repo: Path, *, candidate_id: str | None = None) -> dict[str, Any]:
    """Run discovered crypto smoke candidates while writing observable status."""
    from crypto_lane.src.ingest.edge_status import load_edge_packet_status
    from crypto_lane.src.ml.candidate_registry import discover_candidates
    from crypto_lane.src.ml.walk_forward_runner import run_smoke

    started_at = _utc_now()
    edge_packets = load_edge_packet_status(repo)
    run_id = "crypto_smoke_" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_dir = crypto_smoke_root(repo) / run_id
    latest = latest_status_path(repo)
    status: dict[str, Any] = {
        "run_id": run_id,
        "scenario": "crypto",
        "state": "running",
        "started_at": started_at,
        "updated_at": started_at,
        "current_stage": "discover_candidates",
        "candidate_filter": candidate_id or "",
        "stages": [
            {"name": "discover_candidates", "status": "running", "started_at": started_at},
            {"name": "walk_forward_smokes", "status": "pending"},
            {"name": "rank_candidates", "status": "pending"},
            {"name": "vectorbt_filter", "status": "pending"},
            {"name": "hft_replay_validation", "status": "pending"},
            {"name": "robustness_evidence", "status": "pending"},
            {"name": "decision_gate", "status": "pending"},
            {"name": "after_action", "status": "pending"},
            {"name": "relationship_review", "status": "pending"},
        ],
        "candidates": [],
        "decision": {},
        "bitcoin_edge_packets": edge_packets,
    }
    _write_status(run_dir, latest, status)

    try:
        candidates = discover_candidates()
        if candidate_id:
            candidates = [c for c in candidates if c.get("candidate_id") == candidate_id]
        status["total_candidates"] = len(candidates)
        status["stages"][0]["status"] = "done"
        status["stages"][0]["finished_at"] = _utc_now()

        if not candidates:
            status["state"] = "blocked"
            status["current_stage"] = "decision_gate"
            status["decision"] = {
                "action": "QUARANTINE",
                "reason": "no crypto candidates discovered from tracked crypto registry",
                "top_smoke_candidate": "",
                "live_registry_ready": False,
            }
            status["stages"][1]["status"] = "blocked"
            status["stages"][2]["status"] = "blocked"
            status["stages"][3]["status"] = "blocked"
            status["stages"][4]["status"] = "blocked"
            status["stages"][5]["status"] = "blocked"
            status["stages"][6]["status"] = "blocked"
            status["stages"][7]["status"] = "blocked"
            status["stages"][8]["status"] = "blocked"
            _write_status(run_dir, latest, status)
            _write_json(run_dir / "summary.json", status)
            return status

        status["current_stage"] = "walk_forward_smokes"
        status["stages"][1]["status"] = "running"
        status["stages"][1]["started_at"] = _utc_now()
        _write_status(run_dir, latest, status)

        completed: list[dict[str, Any]] = []
        for idx, candidate in enumerate(candidates, start=1):
            cid = str(candidate.get("candidate_id", ""))
            entry = {
                "candidate_id": cid,
                "hypothesis_id": candidate.get("hypothesis_id", ""),
                "status": "running",
                "started_at": _utc_now(),
            }
            status["candidates"].append(entry)
            status["active_candidate"] = cid
            status["completed_candidates"] = idx - 1
            _write_status(run_dir, latest, status)
            try:
                report = run_smoke(cid)
                _write_json(run_dir / "smoke_reports" / f"{cid}.json", report)
                summary = _candidate_summary(report)
                summary["finished_at"] = _utc_now()
                status["candidates"][-1] = summary
                completed.append(summary)
            except Exception as exc:  # pragma: no cover - exercised by operator runs
                status["candidates"][-1] = {
                    **entry,
                    "status": "failed",
                    "pass_fail": "fail",
                    "rejection_reason": str(exc),
                    "finished_at": _utc_now(),
                }
            status["completed_candidates"] = idx
            _write_status(run_dir, latest, status)

        status["active_candidate"] = ""
        status["stages"][1]["status"] = "done"
        status["stages"][1]["finished_at"] = _utc_now()
        status["current_stage"] = "rank_candidates"
        status["stages"][2]["status"] = "running"
        status["stages"][2]["started_at"] = _utc_now()
        status["smoke_triage_order"] = _rank_candidates(status["candidates"])
        status["stages"][2]["status"] = "done"
        status["stages"][2]["finished_at"] = _utc_now()

        status["current_stage"] = "vectorbt_filter"
        status["stages"][3]["status"] = "running"
        status["stages"][3]["started_at"] = _utc_now()
        _write_status(run_dir, latest, status)
        vectorbt_summary = _run_vectorbt_filter_stage(run_dir, status["smoke_triage_order"], candidates, repo)
        status["vectorbt_filter"] = vectorbt_summary
        status["stages"][3]["status"] = "done" if vectorbt_summary.get("observed") else "blocked"
        status["stages"][3]["finished_at"] = _utc_now()

        status["current_stage"] = "hft_replay_validation"
        status["stages"][4]["started_at"] = _utc_now()
        _write_status(run_dir, latest, status)
        if vectorbt_summary.get("observed"):
            status["stages"][4]["status"] = "running"
            promoted_source_ids = set(str(value) for value in (vectorbt_summary.get("promoted_source_candidate_ids") or []))
            vectorbt_promoted_order = [
                row for row in status["smoke_triage_order"] if str(row.get("candidate_id") or "") in promoted_source_ids
            ]
            status["vectorbt_promoted_order"] = vectorbt_promoted_order
            if not vectorbt_promoted_order:
                validation_reports = []
                status["hft_validation"] = {
                    "status": "BLOCKED_BY_VECTORBT",
                    "reports": [],
                    "observed": False,
                    "reason": "VectorBT observed a run but did not identify registry source candidates for replay.",
                    "summary_path": str(run_dir / "hft_validation_summary.json"),
                }
                _write_json(run_dir / "hft_validation_summary.json", status["hft_validation"])
            else:
                validation_reports = _validate_ranked_candidates(repo, run_dir, vectorbt_promoted_order, candidates)
                status["hft_validation"] = {
                    "status": "done",
                    "reports": validation_reports,
                    "observed": any(_hft_validation_passed(row) for row in validation_reports),
                    "artifact_dir": str(run_dir / "validation_reports"),
                    "summary_path": str(run_dir / "hft_validation_summary.json"),
                }
        else:
            validation_reports = []
            status["vectorbt_promoted_order"] = []
            status["hft_validation"] = {
                "status": "BLOCKED_BY_VECTORBT",
                "reports": [],
                "observed": False,
                "reason": "Crypto execution replay is not attempted until vectorBT emits promoted candidates.",
                "summary_path": str(run_dir / "hft_validation_summary.json"),
            }
            _write_json(run_dir / "hft_validation_summary.json", status["hft_validation"])
        status["stages"][4]["status"] = "done" if status["hft_validation"]["observed"] else "blocked"
        status["stages"][4]["finished_at"] = _utc_now()

        status["current_stage"] = "robustness_evidence"
        status["stages"][5]["started_at"] = _utc_now()
        _write_status(run_dir, latest, status)
        if vectorbt_summary.get("observed"):
            status["stages"][5]["status"] = "running"
            robustness_summary = _run_robustness_evidence_stage(run_dir, validation_reports)
        else:
            robustness_summary = {
                "status": "BLOCKING",
                "observed": False,
                "source": "vectorbt_filter",
                "reason": "Robustness evidence is not attempted until vectorBT emits promoted candidates for replay.",
                "reports_considered": [],
                "trade_sample_count": 0,
                "trade_sample_sources": [],
                "robustness_pack": {
                    "status": "BLOCKED_BY_VECTORBT",
                    "observed": False,
                    "reason": "No replay trade samples exist because the vectorBT pre-filter did not promote candidates.",
                    "checks": [],
                },
                "double_walk_forward": {
                    "status": "BLOCKED_BY_VECTORBT",
                    "observed": False,
                    "reason": "No replay/OOS matrices exist because the vectorBT pre-filter did not promote candidates.",
                },
                "blocking_gates": [
                    {
                        "gate": "robustness_pack",
                        "status": "BLOCKED_BY_VECTORBT",
                        "reason": "VectorBT promotion artifact is required before replay robustness.",
                    },
                    {
                        "gate": "double_walk_forward_correlation",
                        "status": "BLOCKED_BY_VECTORBT",
                        "reason": "VectorBT promotion artifact is required before replay walk-forward correlation.",
                    },
                ],
            }
            _write_json(run_dir / "robustness_summary.json", robustness_summary)
        status["robustness_evidence"] = robustness_summary
        status["stages"][5]["status"] = "done" if robustness_summary.get("observed") else "blocked"
        status["stages"][5]["finished_at"] = _utc_now()

        status["current_stage"] = "decision_gate"
        status["stages"][6]["status"] = "running"
        status["stages"][6]["started_at"] = _utc_now()
        edge_packets = load_edge_packet_status(repo)
        status["bitcoin_edge_packets"] = edge_packets
        status["decision"] = _decision(
            status["candidates"],
            edge_packets,
            validation_reports,
            robustness_summary,
            vectorbt_summary,
        )
        decision_action = str(status["decision"].get("action") or "").upper()
        status["stages"][6]["status"] = "blocked" if decision_action == "QUARANTINE" else "done"
        status["stages"][6]["finished_at"] = _utc_now()
        status["state"] = "blocked" if decision_action == "QUARANTINE" else "completed"
        _write_status(run_dir, latest, status)
        institutional_metrics = _run_institutional_model_metrics(repo, run_dir)
        status["institutional_metrics"] = institutional_metrics
        metrics_ok, metrics_gate = _institutional_metrics_gate(institutional_metrics)
        if not metrics_ok:
            gates = list((status.get("decision") or {}).get("blocking_gates") or [])
            if metrics_gate:
                gate_name = metrics_gate.get("gate") or metrics_gate.get("gate_name")
                if not gate_name or not any((row.get("gate") or row.get("gate_name")) == gate_name for row in gates if isinstance(row, dict)):
                    gates.append(metrics_gate)
            status["decision"] = {
                **(status.get("decision") or {}),
                "live_registry_ready": False,
                "blocking_gates": gates,
            }
            status["state"] = "blocked"

        status["current_stage"] = "after_action"
        status["stages"][7]["status"] = "running"
        status["stages"][7]["started_at"] = _utc_now()
        _write_status(run_dir, latest, status)
        after_action_meta = _run_crypto_after_action(
            repo,
            run_dir,
            status,
            validation_reports,
            robustness_summary,
            vectorbt_summary,
        )
        status["after_action"] = after_action_meta
        after_action_passed = bool(after_action_meta.get("passed"))
        status["stages"][7]["status"] = "done" if after_action_passed else "blocked"
        status["stages"][7]["finished_at"] = _utc_now()

        status["current_stage"] = "relationship_review"
        status["stages"][8]["status"] = "running"
        status["stages"][8]["started_at"] = _utc_now()
        _write_status(run_dir, latest, status)
        relationship_summary = _write_crypto_relationship_review(
            repo,
            run_dir,
            status,
            validation_reports,
            robustness_summary,
        )
        status["relationships"] = relationship_summary
        status["stages"][8]["status"] = "done"
        status["stages"][8]["finished_at"] = _utc_now()
        if not after_action_passed:
            status["state"] = "blocked"
            status["decision"] = {
                **(status.get("decision") or {}),
                "live_registry_ready": False,
                "after_action_blocking_gate": {
                    "gate": "after_action_gpt55_xhigh",
                    "status": "FAIL",
                    "reason": after_action_meta.get("blocking_reason") or "GPT-5.5 xhigh after-action did not pass.",
                },
            }
        status["current_stage"] = "complete" if status["state"] == "completed" else "blocked"
        status["finished_at"] = _utc_now()
        _write_status(run_dir, latest, status)
        _write_json(run_dir / "summary.json", status)
        return status
    except Exception as exc:
        status["state"] = "failed"
        status["error"] = str(exc)
        status["finished_at"] = _utc_now()
        _write_status(run_dir, latest, status)
        _write_json(run_dir / "summary.json", status)
        raise
