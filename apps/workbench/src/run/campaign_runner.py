"""Walk-forward campaign orchestrator (B4 sequential gates)."""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from decision_engine.python.src.walk_forward import ValidationPeriod
from workbench.src.core.composition import ModelComposition
from workbench.src.core.params import DEFAULT_STRATEGY_PARAMS, param_hash_from_dict
from workbench.src.data.event_catalog import (
    catalog_years_available,
    list_campaign_events,
    load_model_binding,
    load_periods,
    load_sim_shadow_config,
    load_walk_forward_config,
    write_campaign_manifest,
)
from workbench.src.registry.model_catalog import phase_budget_summary
from workbench.src.registry.unified_registry import build_models_config, get_model_config, get_model_by_id
from workbench.src.optimization.matrix_runner import MatrixFoldDataError, run_full_matrix_oos, save_matrix_rows
from workbench.src.optimization.param_matrix import load_parameter_bounds
from workbench.src.optimization.plateau_selector import select_robust_plateau
from workbench.src.latency.operating_envelope import (
    aggregate_campaign_latency_envelopes,
    compact_envelope_fields,
    write_campaign_latency_operating_envelope,
)
from workbench.src.robustness.wfc import evaluate_wfc_gate, write_wfc_artifacts
from workbench.src.robustness.wfc.config import load_wfc_config
from workbench.src.robustness.wfc.gate import WfcResult
from hft3.validation.research_stamp import build_certification_stamp, format_stamp_footer
from workbench.src.artifacts.paths import artifact_root, campaign_dir_for, workbench_runs_dir_for


@dataclass
class PeriodResult:
    name: str
    gate_pass: bool
    evaluate_only: bool
    net_pnl: float
    num_trades: int
    expectancy: float
    events_run: int
    events_missing: int
    survives_cpp: bool
    event_results: List[Dict[str, Any]] = field(default_factory=list)
    error: Optional[str] = None


@dataclass
class CampaignResult:
    campaign_id: str
    model_id: str
    symbol: str
    status: str
    param_hash: str
    periods: List[PeriodResult] = field(default_factory=list)
    artifact_dir: str = ""


def _hot_memory_telemetry(repo_root: Path) -> Dict[str, Any]:
    """Read-only market-state residency snapshot (diagnostics only)."""
    from workbench.src.data.hot_memory_manager import hot_memory_telemetry_snapshot

    return hot_memory_telemetry_snapshot(repo_root)


def _campaign_id(model_id: str, symbol: str) -> str:
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    sym = symbol.replace(".", "_")
    return f"{model_id}_{sym}_{ts}"


def _param_hash(model_id: str, params: Optional[Dict[str, Any]] = None, *, seed: int = 42) -> str:
    if params:
        return param_hash_from_dict(model_id, params)
    return param_hash_from_dict(model_id, {"seed": seed})


def _read_control(job_dir: Path) -> str:
    path = job_dir / "control.json"
    if not path.is_file():
        return "run"
    try:
        return json.loads(path.read_text(encoding="utf-8")).get("command", "run")
    except json.JSONDecodeError:
        return "run"


def _write_control(job_dir: Path, command: str) -> None:
    job_dir.mkdir(parents=True, exist_ok=True)
    (job_dir / "control.json").write_text(json.dumps({"command": command}), encoding="utf-8")


def _write_status(job_dir: Path, payload: dict[str, Any]) -> None:
    job_dir.mkdir(parents=True, exist_ok=True)
    (job_dir / "status.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _wait_if_paused(job_dir: Path) -> bool:
    """Return False if stop requested."""
    while _read_control(job_dir) == "pause":
        _write_status(job_dir, {"state": "paused"})
        time.sleep(1.0)
        if _read_control(job_dir) == "stop":
            return False
    if _read_control(job_dir) == "stop":
        return False
    return True


def _period_evaluate_only(wf_cfg: dict[str, Any], period_name: str) -> bool:
    return period_name in (wf_cfg.get("holdout_evaluate_only") or [])


def _period_tune_allowed(wf_cfg: dict[str, Any], period_name: str) -> bool:
    allowed = wf_cfg.get("discovery_tune_allowed") or ["Discovery"]
    return period_name in allowed


def _plateau_matrix_rows(
    rows: List[Dict[str, Any]], wfc_cfg: Dict[str, Any]
) -> List[Dict[str, Any]]:
    exclude = {
        str(f.get("id"))
        for f in (wfc_cfg.get("folds") or [])
        if f.get("plateau_exclude") and f.get("id")
    }
    if not exclude:
        return rows
    return [r for r in rows if str(r.get("fold_id", "")) not in exclude]


def _holdout_used_for_tuning(
    artifact_dir: Path,
    period_results: List[PeriodResult],
    wf_cfg: dict[str, Any],
) -> bool:
    holdout_names = set(wf_cfg.get("holdout_evaluate_only") or [])
    default = dict(DEFAULT_STRATEGY_PARAMS)
    for p in period_results:
        if p.name not in holdout_names:
            continue
        summary_path = artifact_dir / "periods" / p.name.replace(" ", "_") / "period_summary.json"
        if not summary_path.is_file():
            continue
        try:
            used = json.loads(summary_path.read_text(encoding="utf-8")).get("params_used", default)
        except json.JSONDecodeError:
            continue
        if not isinstance(used, dict):
            continue
        if dict(used) != default:
            return True
    return False


def make_campaign_id(model_id: str, symbol: str) -> str:
    return _campaign_id(model_id, symbol)


def _sim_shadow_status(artifact_dir: Path) -> str:
    path = artifact_dir / "sim_shadow.json"
    if path.is_file():
        try:
            return str(json.loads(path.read_text(encoding="utf-8")).get("status", "pending_CHI404"))
        except json.JSONDecodeError:
            pass
    return "pending_CHI404"


def _append_blocking_gate(summary: Dict[str, Any], gate: Dict[str, Any]) -> None:
    gates = list(summary.get("blocking_gates") or [])
    gate_name = gate.get("gate") or gate.get("gate_name")
    if gate_name and any((row.get("gate") or row.get("gate_name")) == gate_name for row in gates if isinstance(row, dict)):
        summary["blocking_gates"] = gates
        return
    gates.append(gate)
    summary["blocking_gates"] = gates


def _institutional_metrics_gate(metrics_status: Any) -> tuple[bool, Dict[str, Any] | None]:
    if not isinstance(metrics_status, dict):
        return False, {
            "gate": "institutional_model_metrics",
            "status": "MISSING",
            "reason": "model scorecard and behavior envelope were not generated",
        }
    if metrics_status.get("status") != "ok":
        return False, {
            "gate": "institutional_model_metrics",
            "status": "FAIL",
            "reason": str(metrics_status.get("reason") or "model metric bundle failed"),
        }
    envelope = metrics_status.get("envelope") if isinstance(metrics_status.get("envelope"), dict) else {}
    if not bool(envelope.get("active")):
        return False, {
            "gate": "model_behavior_envelope",
            "status": "INACTIVE",
            "reason": "model behavior envelope is not active; grade/evidence is not eligible for promotion",
        }
    return True, None


def record_sim_shadow(repo_root: Path, campaign_id: str, status: str) -> Path:
    artifact_dir = campaign_dir_for(repo_root, campaign_id)
    artifact_dir.mkdir(parents=True, exist_ok=True)
    sim_cfg = load_sim_shadow_config(repo_root)
    payload = {
        "status": status,
        "anchor_date": sim_cfg.get("anchor_date"),
        "cme_days": sim_cfg.get("cme_days"),
        "host": sim_cfg.get("host"),
        "lane": sim_cfg.get("lane"),
    }
    (artifact_dir / "sim_shadow.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    summary_path = artifact_dir / "summary.json"
    if summary_path.is_file():
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        summary["sim_shadow_status"] = status
        wfc_ok = summary.get("wfc_status") == "PASS"
        robustness_ok = summary.get("robustness_passed") is True
        stamp = summary.get("certification_stamp") if isinstance(summary.get("certification_stamp"), dict) else {}
        cert_ok = bool(stamp.get("promotion_eligible", False))
        metrics_ok, metrics_gate = _institutional_metrics_gate(summary.get("institutional_metrics"))
        if metrics_gate:
            _append_blocking_gate(summary, metrics_gate)
        latency_ok = summary.get("latency_operating_envelope_status") == "PASS"
        if not latency_ok:
            _append_blocking_gate(
                summary,
                {
                    "gate": "campaign_latency_operating_envelope",
                    "status": summary.get("latency_operating_envelope_status", "MISSING"),
                    "reason": "campaign latency operating envelope is not promotion-eligible",
                },
            )
        summary["promote_candidate"] = (
            summary.get("status") == "PASS"
            and status == "PASS"
            and robustness_ok
            and wfc_ok
            and cert_ok
            and metrics_ok
            and latency_ok
        )
        summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return artifact_dir / "sim_shadow.json"


def _run_institutional_model_metrics(repo_root: Path, artifact_dir: Path) -> Dict[str, Any]:
    out_dir = artifact_dir / "model_metrics"
    try:
        from hft3.validation.model_metrics import generate_bundle_for_run_dir

        return generate_bundle_for_run_dir(artifact_dir, root=repo_root, output_dir=out_dir, force=True)
    except Exception as exc:
        out_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "status": "ERROR",
            "reason": str(exc),
            "blocking_gate": "institutional_model_metrics",
            "run_dir": str(artifact_dir),
        }
        (out_dir / "model_metric_calculation_logs.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return payload


def _run_options_campaign(
    repo_root: Path,
    model_id: str,
    symbol: str,
    artifact_dir: Path,
    param_hash: str,
    *,
    dry_run: bool = False,
) -> CampaignResult:
    """PDF_MODEL_5 fixture MVP — options_lane quarantine (B7)."""
    from workbench.src.registry.unified_registry import get_model_by_id
    from workbench.src.run.run_context import RunContext

    campaign_id = artifact_dir.name
    parity_root = artifact_root() / "parity"
    parity_root.mkdir(parents=True, exist_ok=True)

    if dry_run:
        preview = {
            "model_id": model_id,
            "symbol": symbol,
            "campaign_mode": "options_lane",
            "fixture": "options_lane/fixtures/fair_futures_quotes.ndjson",
            "artifact_root": str(parity_root),
        }
        write_campaign_manifest(artifact_dir / "dry_run_preview.json", preview)
        return CampaignResult(
            campaign_id=campaign_id,
            model_id=model_id,
            symbol=symbol,
            status="DRY_RUN",
            param_hash=param_hash,
            artifact_dir=str(artifact_dir),
        )

    adapter = get_model_by_id(model_id)
    import numpy as np

    ctx = RunContext(
        repo_root=repo_root,
        run_id=f"{model_id}_options_fixture",
        model_id=model_id,
        event_id="OPTIONS_FIXTURE",
        npz_path=repo_root / "options_lane" / "fixtures" / "fair_futures_quotes.ndjson",
        events=np.array([]),
        metadata={"promotion_eligible": False},
    )
    errs = adapter.validate_inputs(ctx)
    if errs:
        return CampaignResult(
            campaign_id=campaign_id,
            model_id=model_id,
            symbol=symbol,
            status="BLOCKED",
            param_hash=param_hash,
            artifact_dir=str(artifact_dir),
        )
    result = adapter.run_backtest(ctx)
    net = float(getattr(result, "net_pnl", 0.0))
    ntr = int(getattr(result, "num_trades", 0))
    exp = net / max(ntr, 1)
    gate_pass = net > 0 and ntr > 0
    pr = PeriodResult(
        name="Options fixture",
        gate_pass=gate_pass,
        evaluate_only=True,
        net_pnl=net,
        num_trades=ntr,
        expectancy=exp,
        events_run=1,
        events_missing=0,
        survives_cpp=True,
    )
    period_dir = artifact_dir / "periods" / "Options_fixture"
    period_dir.mkdir(parents=True, exist_ok=True)
    (period_dir / "period_summary.json").write_text(json.dumps(asdict(pr), indent=2), encoding="utf-8")
    status = "PASS" if gate_pass else "FAIL"
    sim_cfg = load_sim_shadow_config(repo_root)
    sim_status = _sim_shadow_status(artifact_dir)
    summary = {
        "campaign_id": campaign_id,
        "status": status,
        "model_id": model_id,
        "symbol": symbol,
        "param_hash": param_hash,
        "campaign_mode": "options_lane",
        "promotion_eligible": False,
        "periods": [asdict(pr)],
        "sim_shadow_anchor": str(sim_cfg.get("anchor_date")),
        "sim_shadow_cme_days": sim_cfg.get("cme_days"),
        "sim_shadow_status": sim_status,
        "sim_shadow_required": status == "PASS",
        "promote_candidate": status == "PASS" and sim_status == "PASS",
        "promote_note": "Options lane quarantined; sim shadow on CHI404 required for MBO promotion path",
    }
    (artifact_dir / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    metrics_status = _run_institutional_model_metrics(repo_root, artifact_dir)
    summary["institutional_metrics"] = metrics_status
    metrics_ok, metrics_gate = _institutional_metrics_gate(metrics_status)
    if not metrics_ok:
        summary["promote_candidate"] = False
        if metrics_gate:
            _append_blocking_gate(summary, metrics_gate)
    (artifact_dir / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    (artifact_dir / "diagnostics.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return CampaignResult(
        campaign_id=campaign_id,
        model_id=model_id,
        symbol=symbol,
        status=status,
        param_hash=param_hash,
        periods=[pr],
        artifact_dir=str(artifact_dir),
    )


def run_campaign(
    repo_root: Path,
    model_id: str,
    symbol: str,
    *,
    chi404_summary: Optional[Path] = None,
    seed: int = 42,
    audit_grade: bool = True,
    dry_run: bool = False,
    download_missing: bool = False,
    allow_partial: bool = False,
    trial_mode: bool = False,
    job_dir: Optional[Path] = None,
    campaign_id: Optional[str] = None,
    composition: Optional[ModelComposition] = None,
) -> CampaignResult:
    from features_engine.src.model_registry import resolve_model_id
    from workbench.src.registry.composition_orchestrator import CompositionOrchestrator
    from workbench.src.run.engine import WorkbenchEngine

    model_id = resolve_model_id(model_id)
    if trial_mode:
        audit_grade = False
        allow_partial = True

    effective = composition or CompositionOrchestrator.default_composition(model_id)
    primary_id = resolve_model_id(effective.primary_model_id)
    cfg = get_model_config(primary_id)
    binding = load_model_binding(repo_root, primary_id)
    wf_cfg = load_walk_forward_config(repo_root)
    periods = load_periods(repo_root)
    campaign_id = campaign_id or _campaign_id(primary_id, symbol)
    artifact_dir = campaign_dir_for(repo_root, campaign_id)
    job_dir = job_dir or artifact_dir
    param_hash = _param_hash(primary_id, DEFAULT_STRATEGY_PARAMS)

    if binding.get("campaign_mode") == "options_lane":
        artifact_dir.mkdir(parents=True, exist_ok=True)
        return _run_options_campaign(
            repo_root,
            model_id,
            symbol,
            artifact_dir,
            param_hash,
            dry_run=dry_run,
        )

    _write_control(job_dir, "run")
    years_avail = catalog_years_available(primary_id, symbol, repo_root)
    history_gate = audit_grade and years_avail < cfg.min_history_years

    campaign_meta = {
        "campaign_id": campaign_id,
        "model_id": primary_id,
        "symbol": symbol,
        "param_hash": param_hash,
        "audit_grade": audit_grade,
        "composition": effective.to_dict(),
        "authority_refs": [
            "BLUEPRINT.md §8",
            "decision_engine/python/src/walk_forward.py",
            "docs/REVIEWER_CHARTER.md B4",
        ],
        "catalog_years_available": years_avail,
        "min_history_years_required": cfg.min_history_years,
        "trial_mode": trial_mode,
    }
    artifact_dir.mkdir(parents=True, exist_ok=True)
    write_campaign_manifest(artifact_dir / "campaign.json", campaign_meta)

    if history_gate and not allow_partial:
        return CampaignResult(
            campaign_id=campaign_id,
            model_id=model_id,
            symbol=symbol,
            status="DATA_INSUFFICIENT",
            param_hash=param_hash,
            artifact_dir=str(artifact_dir),
        )

    if dry_run:
        from workbench.src.data.event_catalog import campaign_preview

        preview = campaign_preview(model_id, symbol, repo_root)
        preview.setdefault("diagnostics", {})
        preview["diagnostics"]["hot_memory_telemetry"] = _hot_memory_telemetry(repo_root)
        write_campaign_manifest(artifact_dir / "dry_run_preview.json", preview)
        return CampaignResult(
            campaign_id=campaign_id,
            model_id=model_id,
            symbol=symbol,
            status="DRY_RUN",
            param_hash=param_hash,
            artifact_dir=str(artifact_dir),
        )

    engine = WorkbenchEngine(repo_root)
    period_results: List[PeriodResult] = []
    status = "PASS"
    campaign_latency_event_envelopes: List[Dict[str, Any]] = []

    wfc_cfg = load_wfc_config(repo_root)
    wfc_result: Optional[WfcResult] = None
    wfc_dir = artifact_dir / "wfc"
    skip_periods = False
    matrix_rows: List[Dict[str, Any]] = []
    strategy_params: Dict[str, Any] = dict(DEFAULT_STRATEGY_PARAMS)
    bounds = load_parameter_bounds(primary_id)
    wfc_status_for_events = "SKIPPED"

    if not trial_mode and wfc_cfg.get("enabled") and bounds:
        try:
            matrix_rows = run_full_matrix_oos(
                repo_root,
                model_id=primary_id,
                symbol=symbol,
                campaign_id=campaign_id,
                composition=effective,
                chi404_summary=chi404_summary,
                seed=seed,
                audit_grade=audit_grade,
                years_avail=float(years_avail),
                wfc_cfg=wfc_cfg,
            )
        except MatrixFoldDataError as exc:
            wfc_result = WfcResult(
                run_id=campaign_id,
                model_id=primary_id,
                wfc_status="ERROR",
                rejection_reasons=[str(exc)],
            )
            wfc_dir.mkdir(parents=True, exist_ok=True)
            (wfc_dir / "wfc_summary.json").write_text(
                json.dumps(wfc_result.to_dict(), indent=2), encoding="utf-8"
            )
            status = "FAIL"
            skip_periods = True
            wfc_status_for_events = "ERROR"
        else:
            save_matrix_rows(matrix_rows, wfc_dir)
            wfc_result = evaluate_wfc_gate(
                matrix_rows,
                wfc_cfg,
                run_id=campaign_id,
                model_id=primary_id,
            )
            write_wfc_artifacts(
                wfc_dir,
                wfc_result,
                matrix_rows,
                primary_metric=str(wfc_cfg.get("primary_metric", "sharpe")),
            )
            wfc_status_for_events = wfc_result.wfc_status
            if wfc_result.wfc_status in ("FAIL", "ERROR"):
                status = "FAIL"
                skip_periods = True
            elif wfc_result.wfc_status == "PASS":
                plateau = select_robust_plateau(
                    _plateau_matrix_rows(matrix_rows, wfc_cfg),
                    primary_metric=str(wfc_cfg.get("primary_metric", "sharpe")),
                )
                if plateau:
                    plateau_hash = str(plateau.pop("__plateau_hash__", ""))
                    strategy_params = dict(plateau)
                    plateau_oos_metric = str(wfc_cfg.get("primary_metric", "sharpe"))
                    plateau_oos_pass = True
                    if plateau_hash:
                        ph_rows = [
                            r
                            for r in matrix_rows
                            if str(r.get("parameter_hash", "")) == plateau_hash
                        ]
                        if ph_rows:
                            oos_primaries = [
                                float(r.get("oos_metrics", {}).get(plateau_oos_metric, 0))
                                for r in ph_rows
                            ]
                            oos_nets = [
                                float(r.get("oos_metrics", {}).get("net_return", 0))
                                for r in ph_rows
                            ]
                            if oos_primaries and oos_nets:
                                mean_oos_primary = sum(oos_primaries) / len(oos_primaries)
                                mean_oos_net = sum(oos_nets) / len(oos_nets)
                                if mean_oos_primary <= 0 or mean_oos_net <= 0:
                                    wfc_result.rejection_reasons.append(
                                        f"Selected plateau OOS {plateau_oos_metric}={mean_oos_primary:.3f} net_return={mean_oos_net:.3f}"
                                    )
                                    plateau_oos_pass = False
                    if plateau_oos_pass:
                        param_hash = _param_hash(primary_id, strategy_params)
                        campaign_meta["param_hash"] = param_hash
                        campaign_meta["strategy_params"] = strategy_params
                        write_campaign_manifest(artifact_dir / "campaign.json", campaign_meta)
                    else:
                        status = "FAIL"
                        skip_periods = True
                else:
                    wfc_result.rejection_reasons.append("No robust plateau after WFC PASS")
                    status = "FAIL"
                    skip_periods = True
    elif wfc_cfg.get("enabled"):
        skip_reason = "Trial mode skips WFC" if trial_mode else "No parameter_bounds configured for model"
        wfc_result = WfcResult(
            run_id=campaign_id,
            model_id=primary_id,
            wfc_status="SKIPPED",
            rejection_reasons=[skip_reason],
        )
        wfc_dir.mkdir(parents=True, exist_ok=True)
        (wfc_dir / "wfc_summary.json").write_text(
            json.dumps(wfc_result.to_dict(), indent=2), encoding="utf-8"
        )
        wfc_status_for_events = "SKIPPED"

    if not skip_periods:
        for period in periods:
            if not _wait_if_paused(job_dir):
                status = "CANCELLED"
                break

            evaluate_only = _period_evaluate_only(wf_cfg, period.name)
            tune_allowed = _period_tune_allowed(wf_cfg, period.name)
            period_params = (
                strategy_params
                if tune_allowed and not evaluate_only
                else dict(DEFAULT_STRATEGY_PARAMS)
            )
            events = list_campaign_events(primary_id, period, symbol, repo_root)
            missing = [e for e in events if not e.npz_present]
            if download_missing and missing:
                from workbench.src.data.catalog_backfill import download_events

                download_events(repo_root, missing)
                events = list_campaign_events(primary_id, period, symbol, repo_root)
                missing = [e for e in events if not e.npz_present]
            runnable = [e for e in events if e.npz_present]

            period_dir = artifact_dir / "periods" / period.name.replace(" ", "_")
            period_dir.mkdir(parents=True, exist_ok=True)

            if not runnable:
                if allow_partial or trial_mode:
                    continue
                pr = PeriodResult(
                    name=period.name,
                    gate_pass=False,
                    evaluate_only=evaluate_only,
                    net_pnl=0.0,
                    num_trades=0,
                    expectancy=0.0,
                    events_run=0,
                    events_missing=len(events),
                    survives_cpp=False,
                    error="DATA_MISSING: no NPZ for period events",
                )
                period_results.append(pr)
                (period_dir / "period_summary.json").write_text(json.dumps(asdict(pr), indent=2), encoding="utf-8")
                status = "BLOCKED"
                if wf_cfg.get("sequential_gate", True):
                    break
                continue

            event_outcomes: List[Dict[str, Any]] = []
            total_pnl = 0.0
            total_trades = 0
            weighted_exp = 0.0
            all_survive = True

            for ev in runnable:
                if not _wait_if_paused(job_dir):
                    status = "CANCELLED"
                    break

                _write_status(
                    job_dir,
                    {
                        "state": "running",
                        "period": period.name,
                        "event_id": ev.event_id,
                        "evaluate_only": evaluate_only,
                    },
                )

                out = engine.run(
                    primary_id,
                    ev.event_id,
                    symbol=symbol,
                    npz_path=ev.npz_path,
                    chi404_summary=chi404_summary,
                    seed=seed,
                    history_years_available=float(years_avail),
                    skip_history_gate=not audit_grade,
                    fast_sweep=not audit_grade,
                    composition=effective,
                    strategy_params=period_params,
                    wfc_status=wfc_status_for_events,
                )
                if evaluate_only:
                    out.setdefault("metadata", {})["evaluate_only"] = True
                rep = out.get("report", {})
                pnl = float(rep.get("net_pnl", 0.0))
                ntr = int(rep.get("num_trades", 0))
                exp = pnl / ntr if ntr else 0.0
                surv = bool(rep.get("survives_cpp_execution_delay", False))
                total_pnl += pnl
                total_trades += ntr
                weighted_exp += exp * max(ntr, 1)
                all_survive = all_survive and surv

                src_run = Path(out["artifact_dir"])
                dest = period_dir / "events" / ev.event_id
                dest.mkdir(parents=True, exist_ok=True)
                for name in (
                    "diagnostics.json",
                    "manifest.json",
                    "report.md",
                    "config.yaml",
                    "research_card.json",
                    "composition_trace.json",
                    "latency_operating_envelope.json",
                    "latency_operating_envelope.md",
                    "after_action_packet.json",
                    "after_action_symbolic.json",
                    "after_action_response.json",
                    "after_action_report.md",
                    "after_action_annotations.json",
                    "after_action_meta.json",
                    "kg_slice.json",
                ):
                    src = src_run / name
                    if src.is_file():
                        dest.joinpath(name).write_bytes(src.read_bytes())
                diag_dest = dest / "diagnostics.json"
                if diag_dest.is_file():
                    diag_payload = json.loads(diag_dest.read_text(encoding="utf-8"))
                    diag_payload["wfc_status"] = wfc_status_for_events
                    diag_payload["hot_memory_telemetry"] = _hot_memory_telemetry(repo_root)
                    diag_dest.write_text(json.dumps(diag_payload, indent=2), encoding="utf-8")
                trades = src_run / "trades.parquet"
                if trades.is_file():
                    dest.joinpath("trades.parquet").write_bytes(trades.read_bytes())
                latency_envelope_path = src_run / "latency_operating_envelope.json"
                latency_envelope = {}
                if latency_envelope_path.is_file():
                    latency_envelope = json.loads(latency_envelope_path.read_text(encoding="utf-8"))
                    campaign_latency_event_envelopes.append(latency_envelope)
                latency_compact = (
                    compact_envelope_fields(latency_envelope)
                    if latency_envelope
                    else rep.get("latency_operating_envelope", {})
                )

                event_outcomes.append(
                    {
                        "event_id": ev.event_id,
                        "release_date": ev.release_date,
                        "net_pnl": pnl,
                        "num_trades": ntr,
                        "expectancy": exp,
                        "survives_cpp_execution_delay": surv,
                        "trades_vetoed_by_defense": rep.get("trades_vetoed_by_defense", 0),
                        "run_id": out.get("run_id"),
                        **latency_compact,
                    }
                )

            if status == "CANCELLED":
                break

            agg_exp = weighted_exp / max(total_trades, 1)
            gate_pass = agg_exp > 0 and all_survive and total_trades > 0
            pr = PeriodResult(
                name=period.name,
                gate_pass=gate_pass,
                evaluate_only=evaluate_only,
                net_pnl=total_pnl,
                num_trades=total_trades,
                expectancy=agg_exp,
                events_run=len(runnable),
                events_missing=len(events) - len(runnable),
                survives_cpp=all_survive,
                event_results=event_outcomes,
            )
            period_results.append(pr)
            summary = asdict(pr)
            summary["param_hash"] = _param_hash(primary_id, period_params)
            summary["tune_allowed"] = tune_allowed
            summary["params_used"] = period_params
            (period_dir / "period_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")

            if not gate_pass and wf_cfg.get("sequential_gate", True) and not trial_mode:
                status = "FAIL"
                break

    from workbench.src.robustness.pack import run_robustness_pack

    campaign_latency_envelope = aggregate_campaign_latency_envelopes(
        campaign_id=campaign_id,
        model_id=primary_id,
        symbol=symbol,
        period_results=period_results,
        event_envelopes=campaign_latency_event_envelopes,
    )
    campaign_latency_blocker_gates = {
        str(gate.get("gate"))
        for gate in (campaign_latency_envelope.get("promotion_blockers") or [])
        if isinstance(gate, dict)
    }

    def _campaign_latency_check_passed(name: str) -> bool:
        return campaign_latency_envelope.get("status") == "PASS" and name not in campaign_latency_blocker_gates

    trade_pnls = [float(e.get("net_pnl", 0.0)) for p in period_results for e in p.event_results]
    holdout_used_for_tuning = _holdout_used_for_tuning(artifact_dir, period_results, wf_cfg)
    sweep_count = len({r["parameter_hash"] for r in matrix_rows}) if matrix_rows else 1
    robustness = run_robustness_pack(
        lambda: {
            "expectancy": sum(trade_pnls) / max(len(trade_pnls), 1),
            "survives_cpp_execution_delay": bool(period_results and all(p.survives_cpp for p in period_results)),
            "operating_envelope_generated": _campaign_latency_check_passed("operating_envelope_generated"),
            "placement_speed_sensitivity_pass": _campaign_latency_check_passed("placement_speed_sensitivity"),
            "async_ack_state_risk_pass": _campaign_latency_check_passed("async_ack_state_risk"),
            "pending_exposure_guardrails_pass": _campaign_latency_check_passed("pending_exposure_guardrails"),
            "composition_latency_feasibility_pass": _campaign_latency_check_passed("composition_latency_feasibility"),
            "competitor_speed_sensitivity_pass": _campaign_latency_check_passed("competitor_speed_sensitivity"),
        },
        trade_pnls,
        sweep_count=sweep_count,
        holdout_used_for_tuning=holdout_used_for_tuning,
        campaign_periods=[asdict(p) for p in period_results],
    )
    robustness_pending_checks = [check.name for check in robustness.checks if check.status == "PENDING"]
    robustness_failed_checks = [check.name for check in robustness.checks if check.status == "FAIL"]
    robustness_input_status = (
        "PASS"
        if robustness.passed
        else (
            "PENDING_PHASE9_METRICS"
            if robustness_pending_checks
            else "FAILED_PHASE9_CHECKS"
        )
    )

    sim_cfg = load_sim_shadow_config(repo_root)
    sim_status = _sim_shadow_status(artifact_dir)
    trades_vetoed = sum(
        int(e.get("trades_vetoed_by_defense", 0)) for p in period_results for e in p.event_results
    )
    wfc_status = wfc_result.wfc_status if wfc_result else "SKIPPED"
    events_ran = sum(p.events_run for p in period_results)
    if trial_mode:
        if events_ran > 0 and status not in ("CANCELLED",):
            status = "PASS"
        elif events_ran == 0:
            status = "BLOCKED"
    if wfc_status == "CONDITIONAL" and status == "PASS":
        status = "CONDITIONAL"
    wfc_required = bool(wfc_cfg.get("enabled")) and bool(bounds) and wfc_status != "SKIPPED"
    cert_stamp = build_certification_stamp(
        event_id=primary_id,
        model_id=primary_id,
        instrument=symbol,
        execution_mode="REPLAY",
        execution_adapter_mode="workbench_campaign",
    )
    promote_ok = (
        status == "PASS"
        and sim_status == "PASS"
        and robustness.passed
        and (not wfc_required or wfc_status == "PASS")
        and cert_stamp.get("promotion_eligible", False)
    )
    write_campaign_latency_operating_envelope(artifact_dir, campaign_latency_envelope)
    campaign_latency_ok = campaign_latency_envelope.get("status") == "PASS"
    promote_ok = promote_ok and campaign_latency_ok
    summary = {
        "campaign_id": campaign_id,
        "status": status,
        "model_id": primary_id,
        "symbol": symbol,
        "param_hash": param_hash,
        "composition": effective.to_dict(),
        "phase_budgets_us": phase_budget_summary(effective, repo_root),
        "trades_vetoed_by_defense": trades_vetoed,
        "periods": [asdict(p) for p in period_results],
        "robustness_passed": robustness.passed,
        "robustness_input_status": robustness_input_status,
        "robustness_pending_checks": robustness_pending_checks,
        "robustness_failed_checks": robustness_failed_checks,
        "robustness_checks": robustness.checks_dict(),
        "overfit_risk": robustness.overfit_risk,
        "walk_forward": robustness.walk_forward,
        "wfc_status": wfc_status,
        "wfc": wfc_result.to_dict() if wfc_result else {},
        "sim_shadow_anchor": str(sim_cfg.get("anchor_date")),
        "sim_shadow_cme_days": sim_cfg.get("cme_days"),
        "sim_shadow_status": sim_status,
        "sim_shadow_required": status == "PASS",
        "certification_stamp": cert_stamp,
        "certification_footer": format_stamp_footer(cert_stamp),
        "latency_operating_envelope_status": campaign_latency_envelope.get("status"),
        "latency_operating_envelope": {
            "placement_speed_p99_us": campaign_latency_envelope.get("placement_speed_p99_us"),
            "events_observed": campaign_latency_envelope.get("events_observed"),
        },
        "latency_operating_envelope_blockers": campaign_latency_envelope.get("promotion_blockers", []),
        "promote_candidate": promote_ok,
        "promote_note": "WFC PASS + sim shadow (60 CME days on CHI404) + backtester certification stamp required before promotion — BLUEPRINT §8",
        "trial_mode": trial_mode,
        "events_ran": events_ran,
    }
    if not campaign_latency_ok:
        _append_blocking_gate(
            summary,
            {
                "gate": "campaign_latency_operating_envelope",
                "status": campaign_latency_envelope.get("status", "FAIL"),
                "reason": "campaign latency operating envelope is not promotion-eligible",
            },
        )
    (artifact_dir / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    metrics_status = _run_institutional_model_metrics(repo_root, artifact_dir)
    summary["institutional_metrics"] = metrics_status
    metrics_ok, metrics_gate = _institutional_metrics_gate(metrics_status)
    if not metrics_ok:
        summary["promote_candidate"] = False
        if metrics_gate:
            _append_blocking_gate(summary, metrics_gate)
    (artifact_dir / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    (artifact_dir / "diagnostics.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    _write_status(job_dir, {"state": status.lower(), "campaign_id": campaign_id})

    return CampaignResult(
        campaign_id=campaign_id,
        model_id=primary_id,
        symbol=symbol,
        status=status,
        param_hash=param_hash,
        periods=period_results,
        artifact_dir=str(artifact_dir),
    )
