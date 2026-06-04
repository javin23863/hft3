"""Normalized run evidence for Workbench tabs."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from workbench.src.artifacts.paths import workbench_runs_dir_for


def read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def read_text(path: Path) -> str:
    if not path.is_file():
        return ""
    return path.read_text(encoding="utf-8")


@dataclass
class RunEvidenceSnapshot:
    source: str
    run_id: str = ""
    state: str = "idle"
    current_stage: str = ""
    started_at: str = ""
    finished_at: str = ""
    root: str = ""
    stages: list[dict[str, Any]] = field(default_factory=list)
    artifacts: dict[str, str] = field(default_factory=dict)
    registry: dict[str, Any] = field(default_factory=dict)
    data: dict[str, Any] = field(default_factory=dict)
    backtest: dict[str, Any] = field(default_factory=dict)
    latency: dict[str, Any] = field(default_factory=dict)
    diagnostics: dict[str, Any] = field(default_factory=dict)
    robustness: dict[str, Any] = field(default_factory=dict)
    decision: dict[str, Any] = field(default_factory=dict)
    reports: dict[str, Any] = field(default_factory=dict)
    system: dict[str, Any] = field(default_factory=dict)

    @property
    def has_run(self) -> bool:
        return bool(self.run_id)


def _mtime(path: Path) -> float:
    return path.stat().st_mtime if path.exists() else 0.0


def _latest_dir_with(path: Path, filename: str) -> Path | None:
    if not path.is_dir():
        return None
    candidates = [p for p in path.iterdir() if p.is_dir() and (p / filename).is_file()]
    return max(candidates, key=_mtime) if candidates else None


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def _crypto_reports(repo: Path) -> list[dict[str, Any]]:
    root = repo / "research_cards" / "crypto"
    reports: list[dict[str, Any]] = []
    if not root.is_dir():
        return reports
    for path in sorted(root.glob("*/smoke_report.json"), key=lambda p: _mtime(p), reverse=True):
        payload = read_json(path)
        if payload:
            payload["_path"] = str(path)
            reports.append(payload)
    return reports


def _primary_run(report: dict[str, Any]) -> dict[str, Any]:
    runs = report.get("runs") or {}
    return runs.get("with_btc_node") or runs.get("without_btc_node") or {}


def _crypto_snapshot(repo: Path) -> RunEvidenceSnapshot:
    from crypto_lane.src.align.latency_profile import load_venue_profiles, node_profile_path, resolve_node_latency
    from crypto_lane.src.config_loader import load_hypotheses, load_manifest, load_universe
    from crypto_lane.src.config_loader import list_candidate_paths, list_backtest_config_paths
    from crypto_lane.src.ingest.edge_status import load_edge_packet_status
    from crypto_lane.src.ml.candidate_registry import discover_backtest_configs, discover_candidates
    from workbench.src.run.crypto_smoke_runner import latest_status_path

    status_path = latest_status_path(repo)
    status = read_json(status_path)
    reports = _crypto_reports(repo)
    candidates = discover_candidates()
    backtests = discover_backtest_configs()
    candidate_rows = []
    source_candidates = status.get("candidates") or []
    if not source_candidates:
        source_candidates = [
            {
                "candidate_id": r.get("candidate_id", ""),
                "hypothesis_id": r.get("hypothesis_id", ""),
                "status": "done",
                "pass_fail": r.get("pass_fail", ""),
                "holdout_status": (r.get("holdout_gate") or {}).get("status", ""),
                "negative_controls_ok": all(
                    bool((r.get("negative_controls") or {}).get(k))
                    for k in ("shuffled_degraded", "shifted_degraded")
                ),
                "order_ack_status": r.get("execution_ack_status", ""),
                "execution_ack_scope": r.get("execution_ack_scope", ""),
                "btc_node_evidence_scope": r.get("btc_node_evidence_scope", ""),
                "oos_ic": _primary_run(r).get("oos_ic_baseline_mean"),
                "n_rows": _primary_run(r).get("n_rows"),
                "n_folds": _primary_run(r).get("n_folds"),
                "purged_splits": _primary_run(r).get("n_splits"),
            }
            for r in reports
        ]
    for row in source_candidates:
        candidate_rows.append(dict(row))

    universe = load_universe()
    manifest = load_manifest()
    fixture_dir = repo / "packages" / "crypto_lane" / "fixtures"
    data_files = [
        {"path": str(path), "exists": path.is_file(), "bytes": path.stat().st_size if path.is_file() else 0}
        for path in (
            fixture_dir / "spot_perp_ticks.csv",
            fixture_dir / "deribit_surface.csv",
            fixture_dir / "mempool_snapshots.csv",
        )
    ]
    venue_profiles = {k: v.__dict__ for k, v in load_venue_profiles().items()}
    node_profile = resolve_node_latency().__dict__
    node_path = node_profile_path()
    edge_packets = load_edge_packet_status(repo)

    backtest_rows = []
    proxy_leaderboard = []
    equity_curves: dict[str, list[dict[str, Any]]] = {}
    holdout_stage_rows = []
    negative_control_rows = []
    robustness_rows = []
    feature_rows = []
    for report in reports:
        primary = _primary_run(report)
        proxy = report.get("research_pnl_proxy") or {}
        proxy_summary = proxy.get("summary") or {}
        candidate_id = report.get("candidate_id", "")
        backtest_rows.append({
            "candidate_id": candidate_id,
            "hypothesis_id": report.get("hypothesis_id", ""),
            "target": report.get("target", ""),
            "pass_fail": report.get("pass_fail", ""),
            "smoke_mode": report.get("smoke_mode"),
            "oos_ic": primary.get("oos_ic_baseline_mean"),
            "rows": primary.get("n_rows"),
            "folds": primary.get("n_folds"),
            "holdout": (report.get("holdout_gate") or {}).get("status", ""),
            "proxy_net_pnl_bps": proxy_summary.get("net_pnl_bps"),
            "proxy_trades": proxy_summary.get("num_trades"),
            "proxy_hit_rate": proxy_summary.get("hit_rate"),
            "proxy_profit_factor": proxy_summary.get("profit_factor"),
            "proxy_max_drawdown_bps": proxy_summary.get("max_drawdown_bps"),
            "proxy_sharpe": proxy_summary.get("sharpe_proxy"),
        })
        proxy_leaderboard.append({
            "candidate_id": candidate_id,
            "target": report.get("target", ""),
            "oos_ic": primary.get("oos_ic_baseline_mean"),
            "proxy_net_pnl_bps": proxy_summary.get("net_pnl_bps"),
            "proxy_trades": proxy_summary.get("num_trades"),
            "proxy_hit_rate": proxy_summary.get("hit_rate"),
            "proxy_profit_factor": proxy_summary.get("profit_factor"),
            "proxy_max_drawdown_bps": proxy_summary.get("max_drawdown_bps"),
            "proxy_sharpe": proxy_summary.get("sharpe_proxy"),
            "proxy_status": proxy.get("status", ""),
            "promotion_gate": proxy.get("promotion_gate"),
        })
        equity_curves[candidate_id] = list(proxy.get("equity_curve") or [])
        for stage_name, stage in ((report.get("holdout_gate") or {}).get("stages") or {}).items():
            holdout_stage_rows.append({
                "candidate_id": candidate_id,
                "stage": stage_name,
                "mode": stage.get("mode"),
                "ic": stage.get("ic"),
                "n_rows": stage.get("n_rows"),
                "status": stage.get("status"),
            })
        controls = report.get("negative_controls") or {}
        negative_control_rows.append({
            "candidate_id": candidate_id,
            "real_oos_ic": controls.get("real_oos_ic"),
            "shuffled_labels_ic": controls.get("shuffled_labels_ic"),
            "shifted_features_ic": controls.get("shifted_features_ic"),
            "shuffled_degraded": controls.get("shuffled_degraded"),
            "shifted_degraded": controls.get("shifted_degraded"),
        })
        robustness_rows.append({
            "candidate_id": candidate_id,
            "purged_cv": report.get("purged_cv_implemented"),
            "purged_splits": primary.get("n_splits"),
            "holdout": (report.get("holdout_gate") or {}).get("status", ""),
            "shuffled_degraded": (report.get("negative_controls") or {}).get("shuffled_degraded"),
            "shifted_degraded": (report.get("negative_controls") or {}).get("shifted_degraded"),
            "randomized_degraded": (report.get("negative_controls") or {}).get("randomized_degraded"),
        })
    for candidate in candidates:
        feature_rows.append({
            "candidate_id": candidate.get("candidate_id", ""),
            "hypothesis_id": candidate.get("hypothesis_id", ""),
            "target": candidate.get("target", ""),
            "features": ", ".join(candidate.get("features") or []),
            "btc_node_required": candidate.get("btc_node_required"),
            "ablation": candidate.get("ablation", {}),
        })

    decision = status.get("decision") or {
        "action": "NO_RUN",
        "reason": "No crypto candidate loop status observed.",
        "top_research_candidate": "",
        "live_registry_ready": False,
    }
    blocking_gates = [
        gate
        for gate in (decision.get("blocking_gates") or [])
        if not (isinstance(gate, dict) and gate.get("gate") == "bitcoin_edge_packets")
    ]
    if not edge_packets.get("observed"):
        blocking_gates.append(
            {
                "gate": "bitcoin_edge_packets",
                "status": edge_packets.get("status"),
                "reason": edge_packets.get("reason"),
            }
        )
    return RunEvidenceSnapshot(
        source="crypto_lane",
        run_id=str(status.get("run_id", "crypto_lane")),
        state=str(status.get("state", "idle")),
        current_stage=str(status.get("current_stage", "")),
        started_at=str(status.get("started_at", "")),
        finished_at=str(status.get("finished_at", "")),
        root=str(status_path.parent),
        stages=list(status.get("stages") or []),
        artifacts={
            "latest_status": str(status_path),
            "candidate_registry": str((repo / "packages" / "crypto_lane" / "config" / "candidates").resolve()),
            "smoke_reports": str((repo / "research_cards" / "crypto").resolve()),
        },
        registry={
            "hypotheses": [h.get("hypothesis_id", "") for h in load_hypotheses()],
            "candidates": [c.get("candidate_id", "") for c in candidates],
            "candidate_paths": [str(p) for p in list_candidate_paths()],
            "backtests": [b.get("config_id", "") for b in backtests],
            "backtest_paths": [str(p) for p in list_backtest_config_paths()],
            "manifest": manifest,
        },
        data={
            "universe": universe,
            "data_files": data_files,
            "missing": [f for f in data_files if not f["exists"]],
            "btc_node": {"profile": node_profile, "profile_path": str(node_path), "profile_exists": node_path.is_file()},
            "bitcoin_edge_packets": edge_packets,
        },
        backtest={
            "rows": backtest_rows,
            "reports": reports,
            "proxy_leaderboard": sorted(
                proxy_leaderboard,
                key=lambda r: float(r.get("proxy_net_pnl_bps") or 0.0),
                reverse=True,
            ),
            "equity_curves": equity_curves,
            "holdout_stage_rows": holdout_stage_rows,
            "negative_control_rows": negative_control_rows,
        },
        latency={
            "venue_profiles": venue_profiles,
            "node_profile": node_profile,
            "bitcoin_edge_packets": edge_packets,
            "edge_packet_history": edge_packets.get("packet_history", []),
            "execution_ack_rows": [
                {
                    "candidate_id": c.get("candidate_id", ""),
                    "scope": c.get("execution_ack_scope") or "crypto_venue_submit_ack",
                    "measured": bool(c.get("execution_ack_measured")),
                    "status": c.get("order_ack_status") or c.get("execution_ack_status", ""),
                    "btc_node_scope": c.get("btc_node_evidence_scope", ""),
                }
                for c in candidate_rows
            ],
        },
        diagnostics={
            "feature_rows": feature_rows,
            "feature_builders": manifest.get("feature_builders", []),
            "align_modules": manifest.get("align_modules", []),
            "edge_packet_schema": edge_packets.get("schema", []),
        },
        robustness={"rows": robustness_rows},
        decision={
            **decision,
            "ranking": status.get("ranking", []),
            "research_pass_count": sum(1 for c in candidate_rows if str(c.get("pass_fail", "")).lower() == "pass"),
            "live_registry_ready": bool(decision.get("live_registry_ready")) and bool(edge_packets.get("observed")),
            "bitcoin_edge_packet_status": edge_packets.get("status"),
            "blocking_gates": blocking_gates,
        },
        reports={"smoke_reports": [r.get("_path", "") for r in reports]},
        system={
            "status": status,
            "manifest": manifest,
            "runtime_path": str(status_path),
            "bitcoin_edge_packets": edge_packets,
        },
    )


def _workbench_snapshot(repo: Path, campaign_id: str = "") -> RunEvidenceSnapshot:
    root = workbench_runs_dir_for(repo)
    run_dir = root / campaign_id if campaign_id else _latest_dir_with(root, "summary.json")
    if run_dir is None or not run_dir.is_dir():
        return RunEvidenceSnapshot(source="workbench_campaign", state="idle")
    summary = read_json(run_dir / "summary.json")
    status = read_json(run_dir / "status.json")
    campaign = read_json(run_dir / "campaign.json")
    periods = summary.get("periods") or []
    event_rows = [event for period in periods for event in (period.get("event_results") or [])]
    latest_event_dir = None
    for event_dir in sorted((run_dir / "periods").glob("*/events/*"), key=_mtime, reverse=True):
        if event_dir.is_dir():
            latest_event_dir = event_dir
            break
    event_diag = read_json(latest_event_dir / "diagnostics.json") if latest_event_dir else {}
    wfc = read_json(run_dir / "wfc" / "wfc_summary.json") or summary.get("wfc", {})
    return RunEvidenceSnapshot(
        source="workbench_campaign",
        run_id=run_dir.name,
        state=str(status.get("state") or summary.get("status") or "unknown"),
        current_stage=str(status.get("period") or summary.get("status") or ""),
        root=str(run_dir),
        stages=[
            {"name": "campaign_manifest", "status": "done" if campaign else "missing"},
            {"name": "walk_forward_correlation", "status": wfc.get("wfc_status", "SKIPPED")},
            {"name": "period_backtests", "status": summary.get("status", "unknown")},
            {"name": "decision_gate", "status": "done" if summary else "missing"},
        ],
        artifacts={
            "campaign": str(run_dir / "campaign.json"),
            "summary": str(run_dir / "summary.json"),
            "latest_event": str(latest_event_dir or ""),
        },
        registry={"model_id": summary.get("model_id") or campaign.get("model_id"), "composition": summary.get("composition") or campaign.get("composition")},
        data={"symbol": summary.get("symbol") or campaign.get("symbol"), "periods": periods},
        backtest={"rows": event_rows, "periods": periods, "summary": summary},
        latency={"latest_event_diagnostics": event_diag, "cpp_latency_profile": event_diag.get("cpp_latency_profile", {})},
        diagnostics={"composition": summary.get("composition", {}), "latest_event_diagnostics": event_diag},
        robustness={
            "wfc": wfc,
            "robustness_checks": summary.get("robustness_checks", []),
            "robustness_passed": summary.get("robustness_passed"),
            "pending": summary.get("robustness_pending_checks", []),
            "failed": summary.get("robustness_failed_checks", []),
        },
        decision={
            "action": "PROMOTE" if summary.get("promote_candidate") else "QUARANTINE",
            "reason": summary.get("promote_note", ""),
            "live_registry_ready": bool(summary.get("promote_candidate")),
            "ranking": event_rows,
        },
        reports={
            "summary": str(run_dir / "summary.json"),
            "latest_report": str(latest_event_dir / "report.md") if latest_event_dir else "",
            "after_action_report": str(latest_event_dir / "after_action_report.md") if latest_event_dir else "",
        },
        system={"summary": summary, "status": status, "campaign": campaign},
    )


def _autonomous_snapshot(repo: Path) -> RunEvidenceSnapshot:
    artifacts_root = repo / "artifacts" / "runs"
    state_root = repo / "runtime" / "research"
    run_dir = _latest_dir_with(artifacts_root, "manifest.json")
    if run_dir is None:
        return RunEvidenceSnapshot(source="autonomous", state="idle")
    run_id = run_dir.name
    state = read_json(state_root / run_id / "state.json")
    manifest = read_json(run_dir / "manifest.json")
    stages = [
        {"name": name, "status": "done", "artifact": path}
        for name, path in (manifest.get("artifacts") or {}).items()
    ]
    data_resolution = read_json(run_dir / "data_resolution.json")
    data_lineage = read_json(run_dir / "data_lineage.json")
    feature_lineage = read_json(run_dir / "feature_lineage.json")
    model_combo = read_json(run_dir / "model_combination.json")
    experiment_spec = read_json(run_dir / "experiment_spec.json")
    backtest = read_json(run_dir / "backtest_metrics.json")
    gates = read_json(run_dir / "robustness_gates.json")
    wf = read_json(run_dir / "walk_forward_results.json")
    wfc = read_json(run_dir / "walk_forward_correlation.json")
    scoring = read_json(run_dir / "scoring_summary.json")
    decision = read_json(run_dir / "promotion_decision.json")
    return RunEvidenceSnapshot(
        source="autonomous",
        run_id=run_id,
        state="completed" if manifest else "unknown",
        current_stage=str((state.get("completed_stages") or [""])[-1] if state else ""),
        started_at=str(manifest.get("started_at", "")),
        root=str(run_dir),
        stages=stages,
        artifacts={k: str(run_dir / Path(v).name) for k, v in (manifest.get("artifacts") or {}).items()},
        registry={"model_combination": model_combo, "experiment_spec": experiment_spec},
        data={"data_resolution": data_resolution, "data_lineage": data_lineage},
        backtest=backtest,
        latency={"feature_lineage": feature_lineage, "latency_profile": feature_lineage.get("latency_profile", {})},
        diagnostics={"feature_lineage": feature_lineage, "model_combination": model_combo},
        robustness={"gates": gates, "walk_forward": wf, "wfc": wfc},
        decision={**decision, "scoring_summary": scoring},
        reports={"report_md": str(run_dir / "report.md")},
        system={
            "manifest": manifest,
            "artifact_bundle_validation": read_json(run_dir / "artifact_bundle_validation.json"),
            "registry_update": read_json(run_dir / "registry_update.json"),
        },
    )


def load_run_evidence(repo: Path, source: str, *, campaign_id: str = "") -> RunEvidenceSnapshot:
    if source == "workbench_campaign":
        return _workbench_snapshot(repo, campaign_id)
    if source == "autonomous":
        return _autonomous_snapshot(repo)
    return _crypto_snapshot(repo)


def default_source(repo: Path) -> str:
    crypto_path = repo / "runtime" / "workbench" / "crypto_smoke" / "latest_status.json"
    crypto_status = read_json(crypto_path)
    if crypto_status.get("state") == "running":
        return "crypto_lane"
    runs = workbench_runs_dir_for(repo)
    for path in runs.glob("*/status.json") if runs.is_dir() else []:
        status = read_json(path)
        if str(status.get("state", "")).lower() == "running":
            return "workbench_campaign"
    choices: list[tuple[float, str]] = []
    if crypto_path.is_file():
        choices.append((_mtime(crypto_path), "crypto_lane"))
    latest_campaign = _latest_dir_with(runs, "summary.json")
    if latest_campaign is not None:
        choices.append((_mtime(latest_campaign / "summary.json"), "workbench_campaign"))
    latest_autonomous = _latest_dir_with(repo / "artifacts" / "runs", "manifest.json")
    if latest_autonomous is not None:
        choices.append((_mtime(latest_autonomous / "manifest.json"), "autonomous"))
    return max(choices, default=(0.0, "crypto_lane"))[1]
