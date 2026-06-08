"""All-models autonomous campaign orchestrator.

Iterates the model registry and runs the existing campaign_runner for each
(model, symbol) pair, persisting a per-campaign set of artifacts:

    campaign.json          -- high-level metadata (matches campaign_runner schema)
    planned_jobs.json      -- the planned queue BEFORE any work runs
    status.json            -- live state machine (matches campaign_runner schema)
    control.json           -- cooperative pause/stop (read by campaign_runner)
    summary.json           -- aggregate counts after each job
    evidence_snapshot.json -- atomic, UI-friendly snapshot for the active campaign
    errors.jsonl           -- one error per line (skip/block/fail, never crashes the run)
    backend.log            -- append-only runner log
    per-job <model>_<sym>_<ts>/  -- delegated to campaign_runner.run_campaign

This module is intentionally thin: it reuses the existing campaign_runner
end-to-end. No mock runner, no parallel framework, no synthetic data.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from hft3_bootstrap import setup_repo_paths

setup_repo_paths()

from workbench.src.artifacts.paths import (  # noqa: E402  (after setup_repo_paths)
    campaign_dir,
    workbench_runs_dir,
)
from workbench.src.run.campaign_runner import run_campaign  # noqa: E402


# ---------------------------------------------------------------------------
# Plan model
# ---------------------------------------------------------------------------


@dataclass
class PlannedJob:
    job_id: str
    model_id: str
    symbol: str
    campaign_id: str
    phase: str = "planned"
    block_reason: str = ""
    file_path: str = ""
    file_size: int = 0


# ---------------------------------------------------------------------------
# Cooperative control reader (compatible with campaign_runner._read_control)
# ---------------------------------------------------------------------------


def _read_control(control_path: Path) -> str:
    if not control_path.is_file():
        return "run"
    try:
        return json.loads(control_path.read_text(encoding="utf-8")).get("command", "run")
    except (OSError, json.JSONDecodeError):
        return "run"


def _wait_for_resume(control_path: Path, status_path: Path, log: logging.Logger) -> bool:
    """Block while control is 'pause' or 'stop'. Return False iff stop."""
    while True:
        cmd = _read_control(control_path)
        if cmd == "stop":
            try:
                status_path.write_text(
                    json.dumps({"state": "stopped", "command": "stop"}, indent=2),
                    encoding="utf-8",
                )
            except OSError:
                pass
            log.warning("control=stop -> exiting")
            return False
        if cmd != "pause":
            return True
        try:
            status_path.write_text(
                json.dumps({"state": "paused", "command": "pause"}, indent=2),
                encoding="utf-8",
            )
        except OSError:
            pass
        log.info("control=pause -> sleeping 1s")
        time.sleep(1.0)


# ---------------------------------------------------------------------------
# Plan building
# ---------------------------------------------------------------------------


def discover_jobs(
    repo_root: Path,
    symbols: Iterable[str],
    *,
    include_kinds: Optional[tuple[str, ...]] = None,
) -> List[PlannedJob]:
    """Walk the unified model registry and produce one PlannedJob per (model, symbol).

    include_kinds: optional tuple to filter on cfg.kind ('hypothesis', 'structural', 'hybrid').
    """
    from workbench.src.registry.unified_registry import list_models
    from workbench.src.run.campaign_runner import make_campaign_id

    slugs = list_models()
    jobs: List[PlannedJob] = []
    for model_id in slugs:
        try:
            from workbench.src.registry.unified_registry import get_model_config

            cfg = get_model_config(model_id)
        except Exception:
            cfg = None
        if include_kinds is not None and cfg is not None and cfg.kind not in include_kinds:
            continue
        for symbol in symbols:
            cid = make_campaign_id(model_id, symbol)
            job_id = f"{len(jobs):04d}_{model_id}_{symbol}"
            jobs.append(
                PlannedJob(
                    job_id=job_id,
                    model_id=model_id,
                    symbol=symbol,
                    campaign_id=cid,
                )
            )
    return jobs


# ---------------------------------------------------------------------------
# Evidence + errors writers
# ---------------------------------------------------------------------------


def write_planned_jobs(plan_path: Path, jobs: List[PlannedJob], meta: Dict[str, Any]) -> None:
    plan_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "meta": meta,
        "jobs": [job.__dict__ for job in jobs],
    }
    plan_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def write_evidence_snapshot(
    snapshot_path: Path,
    *,
    campaign_id: str,
    repo_root: Path,
    git_sha: str,
    state: str,
    current_job: Optional[PlannedJob],
    counts: Dict[str, int],
    last_artifact: str,
    backend_pid: Optional[int],
    last_heartbeat: float,
    control_command: str,
    blocked_reasons: Dict[str, int],
    skipped_reasons: Dict[str, int],
) -> None:
    snapshot_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": 1,
        "campaign_id": campaign_id,
        "repo_root": str(repo_root),
        "git_sha": git_sha,
        "state": state,
        "current_job": current_job.__dict__ if current_job else None,
        "counts": counts,
        "last_artifact": last_artifact,
        "backend_pid": backend_pid,
        "last_heartbeat": last_heartbeat,
        "control_command": control_command,
        "blocked_reasons": blocked_reasons,
        "skipped_reasons": skipped_reasons,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    tmp = snapshot_path.with_suffix(snapshot_path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    tmp.replace(snapshot_path)  # atomic


def append_error(errors_path: Path, *, job_id: str, kind: str, reason: str, **extra: Any) -> None:
    errors_path.parent.mkdir(parents=True, exist_ok=True)
    rec = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "job_id": job_id,
        "kind": kind,
        "reason": reason,
        **extra,
    }
    with errors_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec) + "\n")


# ---------------------------------------------------------------------------
# Main runner
# ---------------------------------------------------------------------------


def _setup_logging(artifact_dir: Path) -> logging.Logger:
    artifact_dir.mkdir(parents=True, exist_ok=True)
    log_path = artifact_dir / "backend.log"
    logger = logging.getLogger(f"all_lanes_{artifact_dir.name}")
    logger.setLevel(logging.INFO)
    # Clear any previous handlers (idempotent across re-runs of this process)
    for h in list(logger.handlers):
        logger.removeHandler(h)
    fh = logging.FileHandler(log_path, encoding="utf-8")
    fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(fh)
    sh = logging.StreamHandler()
    sh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(sh)
    return logger


def _git_sha(repo_root: Path) -> str:
    import subprocess

    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=str(repo_root),
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return "unknown"


@dataclass
class AutonomousCounts:
    completed: int = 0
    failed: int = 0
    blocked: int = 0
    skipped: int = 0
    pending: int = 0
    cancelled: int = 0

    def as_dict(self) -> Dict[str, int]:
        return {
            "completed": self.completed,
            "failed": self.failed,
            "blocked": self.blocked,
            "skipped": self.skipped,
            "pending": self.pending,
            "cancelled": self.cancelled,
        }


def run_all_lanes(
    repo_root: Path,
    *,
    symbols: Iterable[str],
    campaign_id: Optional[str] = None,
    include_kinds: Optional[tuple[str, ...]] = None,
    audit_grade: bool = True,
    chi404_summary: Optional[Path] = None,
    seed: int = 42,
    allow_partial: bool = False,
    trial_mode: bool = False,
    download_missing: bool = False,
    job_filter: Optional[List[str]] = None,
) -> Path:
    """Top-level orchestrator. Returns the campaign artifact dir.

    job_filter: optional list of model_ids to run; if None, all discovered.
    """
    repo_root = repo_root.resolve()
    symbols = list(symbols)
    if not symbols:
        raise ValueError("symbols list must not be empty")

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    campaign_id = campaign_id or f"autonomous_{timestamp}"
    artifact_dir = campaign_dir(campaign_id)
    artifact_dir.mkdir(parents=True, exist_ok=True)

    log = _setup_logging(artifact_dir)
    git_sha = _git_sha(repo_root)

    log.info("autonomous run starting: campaign_id=%s repo=%s sha=%s", campaign_id, repo_root, git_sha)

    plan_jobs = discover_jobs(repo_root, symbols, include_kinds=include_kinds)
    if job_filter is not None:
        job_filter_set = set(job_filter)
        plan_jobs = [j for j in plan_jobs if j.model_id in job_filter_set]
    plan_jobs = [j for j in plan_jobs if j.model_id not in {"WORKBENCH", "PLACEHOLDER"}]

    for idx, job in enumerate(plan_jobs):
        job.job_id = f"{idx:04d}_{job.model_id}_{job.symbol}"

    log.info("plan built: %d jobs", len(plan_jobs))
    plan_path = artifact_dir / "planned_jobs.json"
    status_path = artifact_dir / "status.json"
    control_path = artifact_dir / "control.json"
    summary_path = artifact_dir / "summary.json"
    errors_path = artifact_dir / "errors.jsonl"
    snapshot_path = artifact_dir / "evidence_snapshot.json"
    coverage_path = artifact_dir / "coverage_report.json"
    pit_path = artifact_dir / "pit_report.json"
    metrics_path = artifact_dir / "metrics.json"
    campaign_path = artifact_dir / "campaign.json"

    write_planned_jobs(
        plan_path,
        plan_jobs,
        {
            "campaign_id": campaign_id,
            "repo_root": str(repo_root),
            "git_sha": git_sha,
            "symbols": symbols,
            "audit_grade": audit_grade,
            "trial_mode": trial_mode,
            "include_kinds": list(include_kinds) if include_kinds else None,
        },
    )

    # Coverage + PIT reports (read-only, derived from the event catalog).
    try:
        from workbench.src.data.coverage_check import (
            build_coverage_report,
            write_coverage_report,
            write_pit_report,
        )

        cov_rows = build_coverage_report(repo_root, symbols)
        write_coverage_report(
            artifact_dir,
            cov_rows,
            meta={"campaign_id": campaign_id, "git_sha": git_sha},
        )
        write_pit_report(
            artifact_dir,
            cov_rows,
            meta={"campaign_id": campaign_id, "git_sha": git_sha},
        )
        log.info("coverage_report + pit_report written (%d rows)", len(cov_rows))
    except Exception as exc:  # noqa: BLE001
        log.warning("coverage/PIT report generation failed: %s", exc)
        append_error(
            errors_path,
            job_id="__coverage__",
            kind="coverage_check_failed",
            reason=type(exc).__name__,
            detail=str(exc),
        )

    # Top-level campaign.json (mirrors campaign_runner schema so consumers
    # like the audit tools and the UI work uniformly).
    campaign_path.write_text(
        json.dumps(
            {
                "campaign_id": campaign_id,
                "schema": "autonomous_all_lanes_v1",
                "repo_root": str(repo_root),
                "git_sha": git_sha,
                "symbols": symbols,
                "audit_grade": audit_grade,
                "trial_mode": trial_mode,
                "include_kinds": list(include_kinds) if include_kinds else None,
                "total_jobs": len(plan_jobs),
                "command": "python -m workbench.src.run.all_lanes",
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    # Initial control: if a prior run wrote control=stop or control=pause,
    # respect it (do not clobber). Otherwise default to "run".
    if not control_path.is_file():
        control_path.write_text(json.dumps({"command": "run"}), encoding="utf-8")
    status_path.write_text(
        json.dumps({"state": "running", "campaign_id": campaign_id, "total_jobs": len(plan_jobs)}, indent=2),
        encoding="utf-8",
    )

    counts = AutonomousCounts(pending=len(plan_jobs))
    blocked_reasons: Dict[str, int] = {}
    skipped_reasons: Dict[str, int] = {}

    summary: Dict[str, Any] = {
        "campaign_id": campaign_id,
        "repo_root": str(repo_root),
        "git_sha": git_sha,
        "symbols": symbols,
        "audit_grade": audit_grade,
        "trial_mode": trial_mode,
        "total_jobs": len(plan_jobs),
        "job_outcomes": [],
    }

    for job in plan_jobs:
        if not _wait_for_resume(control_path, status_path, log):
            counts.cancelled += 1
            counts.pending -= 1
            job.phase = "cancelled"
            summary["job_outcomes"].append(
                {
                    "job_id": job.job_id,
                    "model_id": job.model_id,
                    "symbol": job.symbol,
                    "status": "CANCELLED",
                    "block_reason": "user_stop",
                }
            )
            append_error(
                errors_path,
                job_id=job.job_id,
                kind="cancelled",
                reason="user_stop",
                model_id=job.model_id,
                symbol=job.symbol,
            )
            break

        # Heartbeat before each job
        write_evidence_snapshot(
            snapshot_path,
            campaign_id=campaign_id,
            repo_root=repo_root,
            git_sha=git_sha,
            state="running",
            current_job=job,
            counts=counts.as_dict(),
            last_artifact=str(plan_path),
            backend_pid=None,
            last_heartbeat=time.time(),
            control_command=_read_control(control_path),
            blocked_reasons=blocked_reasons,
            skipped_reasons=skipped_reasons,
        )
        status_path.write_text(
            json.dumps(
                {
                    "state": "running",
                    "campaign_id": campaign_id,
                    "current_job": job.__dict__,
                    "counts": counts.as_dict(),
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                },
                indent=2,
            ),
            encoding="utf-8",
        )

        log.info("starting job %s model=%s symbol=%s", job.job_id, job.model_id, job.symbol)
        try:
            result = run_campaign(
                repo_root,
                model_id=job.model_id,
                symbol=job.symbol,
                chi404_summary=chi404_summary,
                seed=seed,
                audit_grade=audit_grade,
                allow_partial=allow_partial,
                trial_mode=trial_mode,
                download_missing=download_missing,
                campaign_id=job.campaign_id,
            )
            outcome_status = str(result.status).upper()
            counts.pending -= 1
            if outcome_status == "PASS":
                counts.completed += 1
                job.phase = "completed"
            elif outcome_status == "BLOCKED":
                counts.blocked += 1
                job.phase = "blocked"
                blocked_reasons["DATA_MISSING"] = blocked_reasons.get("DATA_MISSING", 0) + 1
            elif outcome_status in {"FAIL", "DATA_INSUFFICIENT"}:
                counts.failed += 1
                job.phase = "failed"
            else:
                counts.skipped += 1
                job.phase = "skipped"
                skipped_reasons[outcome_status] = skipped_reasons.get(outcome_status, 0) + 1
            # Aggregate net_pnl / num_trades from the result's period list so
            # the autonomous-level summary.json carries real numbers (and the
            # metrics module can read them without parsing per-job dirs).
            agg_pnl = 0.0
            agg_trades = 0
            agg_exp = 0.0
            for p in getattr(result, "periods", []) or []:
                agg_pnl += float(getattr(p, "net_pnl", 0.0) or 0.0)
                agg_trades += int(getattr(p, "num_trades", 0) or 0)
                agg_exp += float(getattr(p, "expectancy", 0.0) or 0.0)
            summary["job_outcomes"].append(
                {
                    "job_id": job.job_id,
                    "model_id": job.model_id,
                    "symbol": job.symbol,
                    "status": outcome_status,
                    "net_pnl": agg_pnl,
                    "num_trades": agg_trades,
                    "expectancy": (agg_exp / max(len(getattr(result, "periods", []) or []), 1)) if getattr(result, "periods", None) else 0.0,
                    "artifact_dir": result.artifact_dir,
                }
            )
            log.info(
                "job done %s status=%s artifact=%s",
                job.job_id,
                outcome_status,
                result.artifact_dir,
            )
        except Exception as exc:  # noqa: BLE001  (we want to log + continue)
            counts.pending -= 1
            counts.failed += 1
            job.phase = "failed"
            log.exception("job failed %s", job.job_id)
            append_error(
                errors_path,
                job_id=job.job_id,
                kind="exception",
                reason=type(exc).__name__,
                detail=str(exc),
                model_id=job.model_id,
                symbol=job.symbol,
            )
            summary["job_outcomes"].append(
                {
                    "job_id": job.job_id,
                    "model_id": job.model_id,
                    "symbol": job.symbol,
                    "status": "FAIL",
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )

    # Final state
    state = "complete" if counts.pending == 0 and counts.cancelled == 0 else "stopped"
    final_status = {
        "state": state,
        "campaign_id": campaign_id,
        "counts": counts.as_dict(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    status_path.write_text(json.dumps(final_status, indent=2), encoding="utf-8")
    summary["counts"] = counts.as_dict()
    summary["final_state"] = state
    summary["blocked_reasons"] = blocked_reasons
    summary["skipped_reasons"] = skipped_reasons
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    write_evidence_snapshot(
        snapshot_path,
        campaign_id=campaign_id,
        repo_root=repo_root,
        git_sha=git_sha,
        state=state,
        current_job=None,
        counts=counts.as_dict(),
        last_artifact=str(summary_path),
        backend_pid=None,
        last_heartbeat=time.time(),
        control_command=_read_control(control_path),
        blocked_reasons=blocked_reasons,
        skipped_reasons=skipped_reasons,
    )
    # Trader-grade metrics (honest MISSING_REQUIRED_LEDGER if backtester
    # did not emit the required ledger / equity curve).
    try:
        from workbench.src.run.metrics import write_metrics

        write_metrics(artifact_dir)
        log.info("metrics.json written")
    except Exception as exc:  # noqa: BLE001
        log.warning("metrics generation failed: %s", exc)
        append_error(
            errors_path,
            job_id="__metrics__",
            kind="metrics_failed",
            reason=type(exc).__name__,
            detail=str(exc),
        )
    log.info("autonomous run ended: %s counts=%s", state, counts.as_dict())
    return artifact_dir


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(prog="all_lanes", description="Autonomous all-models orchestrator")
    parser.add_argument("--repo", default=".", help="Repo root")
    parser.add_argument("--symbols", nargs="+", default=["MES.v.0"], help="Symbols to run")
    parser.add_argument("--campaign-id", default=None)
    parser.add_argument("--trial", action="store_true", help="Smoke mode: faster, allows partial data")
    parser.add_argument(
        "--include-kinds", nargs="*", default=None, help="Filter on model kind (hypothesis/structural/hybrid)"
    )
    parser.add_argument(
        "--job-filter", nargs="*", default=None, help="Limit to specific model_ids"
    )
    parser.add_argument("--download-missing", action="store_true")
    args = parser.parse_args()
    out = run_all_lanes(
        Path(args.repo).resolve(),
        symbols=args.symbols,
        campaign_id=args.campaign_id,
        include_kinds=tuple(args.include_kinds) if args.include_kinds else None,
        audit_grade=not args.trial,
        trial_mode=args.trial,
        download_missing=args.download_missing,
        job_filter=args.job_filter,
    )
    print(json.dumps({"artifact_dir": str(out)}, indent=2))
