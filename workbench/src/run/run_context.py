"""Immutable run envelope for reproducible backtests."""

from __future__ import annotations

import hashlib
import json
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np

from workbench.src.sim.latency_simulator import LatencyPolicy
from workbench.src.sim.cpp_latency_profile import CppLatencyProfile
from workbench.src.sim.latency_injector import CppLatencyInjector


def _git_sha(repo_root: Path) -> str:
    try:
        out = subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=str(repo_root),
            stderr=subprocess.DEVNULL,
            text=True,
        )
        return out.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return "unknown"


def _npz_hash(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(1 << 20):
            h.update(chunk)
    return h.hexdigest()[:16]


@dataclass
class RunContext:
    repo_root: Path
    run_id: str
    model_id: str
    event_id: str
    npz_path: Path
    events: np.ndarray
    seed: int = 42
    git_sha: str = ""
    npz_hash: str = ""
    chi404_summary_path: Optional[Path] = None
    latency_policy: LatencyPolicy = field(default_factory=LatencyPolicy)
    cpp_profile: Optional[CppLatencyProfile] = None
    cpp_injector: Optional[CppLatencyInjector] = None
    measured_p99_ms: float = 0.0
    metadata: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def build(
        cls,
        repo_root: Path,
        model_id: str,
        event_id: str,
        npz_path: Path,
        events: np.ndarray,
        *,
        seed: int = 42,
        chi404_summary: Optional[Path] = None,
        latency_policy: Optional[LatencyPolicy] = None,
        measured_p99_ms: float = 0.0,
    ) -> "RunContext":
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        run_id = f"{model_id}_{event_id}_{ts}"
        policy = latency_policy or LatencyPolicy.fixed(measured_p99_ms or 1.0)
        cpp_prof = policy.cpp_profile
        if cpp_prof is None and chi404_summary and chi404_summary.is_file():
            cpp_prof = CppLatencyProfile.from_chi404_summary(chi404_summary)
            policy = LatencyPolicy.from_cpp_profile(cpp_prof)
        injector = CppLatencyInjector(cpp_prof, seed=seed) if cpp_prof else None
        if measured_p99_ms <= 0 and cpp_prof:
            measured_p99_ms = cpp_prof.measured_production_p99_ms
        return cls(
            repo_root=repo_root,
            run_id=run_id,
            model_id=model_id,
            event_id=event_id,
            npz_path=npz_path,
            events=events,
            seed=seed,
            git_sha=_git_sha(repo_root),
            npz_hash=_npz_hash(npz_path),
            chi404_summary_path=chi404_summary,
            latency_policy=policy,
            cpp_profile=cpp_prof,
            cpp_injector=injector,
            measured_p99_ms=measured_p99_ms,
        )

    @property
    def artifact_dir(self) -> Path:
        return self.repo_root / "research_cards" / "workbench_runs" / self.run_id

    def write_reproducibility_files(self) -> None:
        self.artifact_dir.mkdir(parents=True, exist_ok=True)
        (self.artifact_dir / "seed.txt").write_text(str(self.seed), encoding="utf-8")
        (self.artifact_dir / "git_sha.txt").write_text(self.git_sha, encoding="utf-8")
        meta = {
            "run_id": self.run_id,
            "model_id": self.model_id,
            "event_id": self.event_id,
            "npz_path": str(self.npz_path),
            "npz_hash": self.npz_hash,
            "seed": self.seed,
            "git_sha": self.git_sha,
            "measured_p99_ms": self.measured_p99_ms,
        }
        (self.artifact_dir / "run_meta.json").write_text(
            json.dumps(meta, indent=2), encoding="utf-8"
        )
