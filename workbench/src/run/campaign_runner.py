"""Walk-forward campaign orchestrator (B4 sequential gates)."""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from decision_engine.python.src.walk_forward import ValidationPeriod
from workbench.src.core.protocol import ModelComposition
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
from workbench.src.registry.unified_registry import build_models_config


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


def _campaign_id(model_id: str, symbol: str) -> str:
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    sym = symbol.replace(".", "_")
    return f"{model_id}_{sym}_{ts}"


def _param_hash(model_id: str, seed: int) -> str:
    h = hashlib.sha256(f"{model_id}:{seed}".encode()).hexdigest()
    return h[:16]


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


def record_sim_shadow(repo_root: Path, campaign_id: str, status: str) -> Path:
    artifact_dir = repo_root / "research_cards" / "workbench_runs" / campaign_id
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
        summary["promote_candidate"] = summary.get("status") == "PASS" and status == "PASS"
        summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return artifact_dir / "sim_shadow.json"


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
    parity_root = repo_root / "research_cards" / "parity"
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
    job_dir: Optional[Path] = None,
    campaign_id: Optional[str] = None,
    composition: Optional[ModelComposition] = None,
) -> CampaignResult:
    from workbench.src.registry.composition_orchestrator import CompositionOrchestrator
    from workbench.src.run.engine import WorkbenchEngine

    effective = composition or CompositionOrchestrator.default_composition(model_id)
    primary_id = effective.primary_model_id
    cfg = build_models_config()[primary_id]
    binding = load_model_binding(repo_root, primary_id)
    wf_cfg = load_walk_forward_config(repo_root)
    periods = load_periods(repo_root)
    campaign_id = campaign_id or _campaign_id(primary_id, symbol)
    artifact_dir = repo_root / "research_cards" / "workbench_runs" / campaign_id
    job_dir = job_dir or artifact_dir
    param_hash = _param_hash(primary_id, seed)

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

    for period in periods:
        if not _wait_if_paused(job_dir):
            status = "CANCELLED"
            break

        evaluate_only = _period_evaluate_only(wf_cfg, period.name)
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

        if not runnable and not allow_partial:
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
                chi404_summary=chi404_summary,
                seed=seed,
                history_years_available=float(years_avail),
                skip_history_gate=not audit_grade,
                fast_sweep=not audit_grade,
                composition=effective,
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
                "after_action_packet.json",
                "after_action_symbolic.json",
                "after_action_report.md",
                "after_action_annotations.json",
                "after_action_meta.json",
                "kg_slice.json",
            ):
                src = src_run / name
                if src.is_file():
                    dest.joinpath(name).write_bytes(src.read_bytes())
            trades = src_run / "trades.parquet"
            if trades.is_file():
                dest.joinpath("trades.parquet").write_bytes(trades.read_bytes())

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
        summary["param_hash"] = param_hash if evaluate_only else param_hash
        (period_dir / "period_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")

        if not gate_pass and wf_cfg.get("sequential_gate", True):
            status = "FAIL"
            break

    from workbench.src.robustness.pack import run_robustness_pack

    trade_pnls = [float(e.get("net_pnl", 0.0)) for p in period_results for e in p.event_results]
    holdout_touched = any(p.evaluate_only for p in period_results)
    robustness = run_robustness_pack(
        lambda: {"expectancy": sum(trade_pnls) / max(len(trade_pnls), 1)},
        trade_pnls,
        sweep_count=1,
        holdout_touched=holdout_touched,
        campaign_periods=[asdict(p) for p in period_results],
    )

    sim_cfg = load_sim_shadow_config(repo_root)
    sim_status = _sim_shadow_status(artifact_dir)
    trades_vetoed = sum(
        int(e.get("trades_vetoed_by_defense", 0)) for p in period_results for e in p.event_results
    )
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
        "overfit_risk": robustness.overfit_risk,
        "walk_forward": robustness.walk_forward,
        "sim_shadow_anchor": str(sim_cfg.get("anchor_date")),
        "sim_shadow_cme_days": sim_cfg.get("cme_days"),
        "sim_shadow_status": sim_status,
        "sim_shadow_required": status == "PASS",
        "promote_candidate": status == "PASS" and sim_status == "PASS",
        "promote_note": "Sim shadow (60 CME days on CHI404 from 2026-03-01) required before promotion — BLUEPRINT §8",
    }
    (artifact_dir / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
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
