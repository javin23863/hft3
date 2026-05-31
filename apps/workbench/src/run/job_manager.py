"""Background campaign jobs with cooperative pause/stop."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, Optional

from workbench.src.core.composition import ModelComposition


from features_engine.src.model_registry import resolve_model_id
from workbench.src.artifacts.paths import campaign_dir_for, workbench_runs_dir_for


def job_dir_for(repo_root: Path, campaign_id: str) -> Path:
    return campaign_dir_for(repo_root, campaign_id)


from workbench.src.run.campaign_runner import make_campaign_id


def start_campaign_subprocess(
    repo_root: Path,
    *,
    model_id: str,
    symbol: str,
    audit_grade: bool = True,
    download_missing: bool = False,
    allow_partial: bool = False,
    trial_mode: bool = False,
    chi404_summary: str = "runtime/latency_reports/latency_summary.json",
    composition: Optional[ModelComposition] = None,
) -> tuple[subprocess.Popen, str]:
    from hft3_bootstrap import pythonpath_entries, setup_repo_paths

    setup_repo_paths()
    campaign_id = make_campaign_id(resolve_model_id(model_id), symbol)
    composition_path: Optional[Path] = None
    if composition and composition.defensive_stubs:
        composition_path = job_dir_for(repo_root, campaign_id) / "composition.json"
        composition_path.parent.mkdir(parents=True, exist_ok=True)
        composition_path.write_text(json.dumps(composition.to_dict(), indent=2), encoding="utf-8")

    cmd = [
        sys.executable,
        "-m",
        "workbench",
        "campaign",
        "--model",
        model_id,
        "--symbol",
        symbol,
        "--campaign-id",
        campaign_id,
        "--chi404-summary",
        chi404_summary,
    ]
    if audit_grade:
        cmd.append("--enforce-history-gate")
        cmd.append("--full-sweep")
    if download_missing:
        cmd.append("--download-missing")
    if allow_partial:
        cmd.append("--allow-partial")
    if trial_mode:
        cmd.append("--trial")
    if composition_path is not None:
        cmd.extend(["--composition", str(composition_path)])

    if trial_mode:
        audit_grade = False
        allow_partial = True
        if "--allow-partial" not in cmd:
            cmd.append("--allow-partial")
        cmd = [c for c in cmd if c not in ("--enforce-history-gate", "--full-sweep")]

    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join(pythonpath_entries(repo_root))
    env.setdefault("HFT3_CPP_STACK_VERIFY", "off")
    proc = subprocess.Popen(
        cmd,
        cwd=str(repo_root),
        env=env,
    )
    return proc, campaign_id


def set_control(repo_root: Path, campaign_id: str, command: str) -> None:
    d = job_dir_for(repo_root, campaign_id)
    d.mkdir(parents=True, exist_ok=True)
    (d / "control.json").write_text(json.dumps({"command": command}), encoding="utf-8")


def get_job_status(repo_root: Path, campaign_id: str) -> Dict[str, Any]:
    d = job_dir_for(repo_root, campaign_id)
    status_path = d / "status.json"
    if status_path.is_file():
        return json.loads(status_path.read_text(encoding="utf-8"))
    summary = d / "summary.json"
    if summary.is_file():
        return json.loads(summary.read_text(encoding="utf-8"))
    return {"state": "unknown", "campaign_id": campaign_id}


def list_active_campaigns(repo_root: Path) -> list[str]:
    root = workbench_runs_dir_for(repo_root)
    if not root.is_dir():
        return []
    out = []
    for p in sorted(root.iterdir(), reverse=True):
        if (p / "status.json").is_file() or (p / "campaign.json").is_file():
            out.append(p.name)
    return out[:20]
