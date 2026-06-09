"""Stage 0 — Repo and lane inventory."""
from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


@dataclass
class LaneInventory:
    lane_id: str
    lane_name: str
    packages: List[str] = field(default_factory=list)
    symbols: List[str] = field(default_factory=list)
    data_location: str = ""
    data_status: str = "unknown"
    feature_status: str = "unknown"
    vectorbt_supported: bool = False
    hftbacktest_supported: bool = False
    models_bound: int = 0
    status: str = "unknown"
    blockers: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "lane_id": self.lane_id,
            "lane_name": self.lane_name,
            "packages": self.packages,
            "symbols": self.symbols,
            "data_location": self.data_location,
            "data_status": self.data_status,
            "feature_status": self.feature_status,
            "vectorbt_supported": self.vectorbt_supported,
            "hftbacktest_supported": self.hftbacktest_supported,
            "models_bound": self.models_bound,
            "status": self.status,
            "blockers": self.blockers,
        }


@dataclass
class RepoInventory:
    repo_root: Path
    repo_commit: str = ""
    repo_branch: str = ""
    generated_at: str = ""
    lanes: List[LaneInventory] = field(default_factory=list)
    model_catalog: List[str] = field(default_factory=list)
    vectorbt_available: bool = False
    hftbacktest_available: bool = False
    metrics_engine_available: bool = False
    certification_registry_available: bool = False
    trade_manager_available: bool = False
    workbench_available: bool = False
    data_availability: Dict[str, str] = field(default_factory=dict)
    feature_availability: Dict[str, str] = field(default_factory=dict)
    blockers: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "repo_root": str(self.repo_root),
            "repo_commit": self.repo_commit,
            "repo_branch": self.repo_branch,
            "generated_at": self.generated_at,
            "lanes": [l.to_dict() for l in self.lanes],
            "model_catalog": self.model_catalog,
            "capabilities": {
                "vectorbt": self.vectorbt_available,
                "hftbacktest": self.hftbacktest_available,
                "metrics_engine": self.metrics_engine_available,
                "certification_registry": self.certification_registry_available,
                "trade_manager": self.trade_manager_available,
                "workbench": self.workbench_available,
            },
            "data_availability": self.data_availability,
            "feature_availability": self.feature_availability,
            "blockers": self.blockers,
        }


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


def _git_branch(repo_root: Path) -> str:
    try:
        out = subprocess.check_output(
            ["git", "branch", "--show-current"],
            cwd=str(repo_root),
            stderr=subprocess.DEVNULL,
            text=True,
        )
        return out.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return "unknown"


def _check_import(module_name: str) -> bool:
    try:
        __import__(module_name)
        return True
    except ImportError:
        return False


def _count_npz_files(data_dir: Path) -> int:
    if not data_dir.is_dir():
        return 0
    return len(list(data_dir.glob("*.npz")))


def build_inventory(repo_root: Path) -> RepoInventory:
    inv = RepoInventory(
        repo_root=repo_root,
        repo_commit=_git_sha(repo_root),
        repo_branch=_git_branch(repo_root),
        generated_at=datetime.now(timezone.utc).isoformat(),
    )

    inv.vectorbt_available = _check_import("vectorbt")
    inv.hftbacktest_available = _check_import("hftbacktest")
    inv.metrics_engine_available = (repo_root / "packages" / "hft3" / "model_metrics").is_dir()
    inv.certification_registry_available = (repo_root / "packages" / "hft3" / "validation" / "certification_registry.py").is_file()
    inv.trade_manager_available = (repo_root / "packages" / "trade_manager" / "manager.py").is_file()
    inv.workbench_available = (repo_root / "apps" / "workbench" / "ui" / "app.py").is_file()

    try:
        import sys
        _packages = str(repo_root / "packages")
        _apps = str(repo_root / "apps")
        if _packages not in sys.path:
            sys.path.insert(0, _packages)
        if _apps not in sys.path:
            sys.path.insert(0, _apps)
        from workbench.src.registry.unified_registry import list_models
        inv.model_catalog = list_models()
    except Exception:
        inv.model_catalog = []

    npz_dir = repo_root / "data" / "npz"
    inv.data_availability["cme_mbo_npz"] = f"{_count_npz_files(npz_dir)} files" if npz_dir.is_dir() else "missing"

    equities_dir = repo_root / "data" / "equities"
    inv.data_availability["equities"] = "present" if equities_dir.is_dir() else "missing"

    options_dir = repo_root / "data" / "options"
    inv.data_availability["options"] = "present" if options_dir.is_dir() else "missing"

    crypto_dir = repo_root / "data" / "replay" / "hftbacktest" / "crypto"
    inv.data_availability["crypto"] = "present" if crypto_dir.is_dir() else "missing"

    cme_lane = LaneInventory(
        lane_id="cme_futures",
        lane_name="CME Futures",
        packages=["backtest_pipeline", "features_engine", "replay", "data_system"],
        symbols=["MES.v.0", "MNQ.v.0", "ES.v.0", "NQ.v.0"],
        data_location="data/npz/",
        data_status="ready" if npz_dir.is_dir() and _count_npz_files(npz_dir) > 0 else "missing",
        feature_status="ready",
        vectorbt_supported=True,
        hftbacktest_supported=True,
        status="operational",
    )
    inv.lanes.append(cme_lane)

    equities_lane = LaneInventory(
        lane_id="equities_low_float",
        lane_name="Equities Low-Float",
        packages=["equities_lane"],
        data_location="data/equities/",
        data_status="ready" if equities_dir.is_dir() else "missing",
        feature_status="ready",
        vectorbt_supported=True,
        hftbacktest_supported=True,
        status="operational" if equities_dir.is_dir() else "degraded",
    )
    inv.lanes.append(equities_lane)

    options_lane = LaneInventory(
        lane_id="options_parity",
        lane_name="Options / Parity",
        packages=["options_lane"],
        data_location="data/options/",
        data_status="ready" if options_dir.is_dir() else "missing",
        feature_status="ready",
        vectorbt_supported=False,
        hftbacktest_supported=True,
        status="operational" if options_dir.is_dir() else "degraded",
    )
    inv.lanes.append(options_lane)

    crypto_lane = LaneInventory(
        lane_id="crypto",
        lane_name="Crypto",
        packages=["crypto_lane"],
        data_location="data/replay/hftbacktest/crypto/",
        data_status="ready" if crypto_dir.is_dir() else "missing",
        feature_status="ready",
        vectorbt_supported=True,
        hftbacktest_supported=True,
        status="operational" if crypto_dir.is_dir() else "degraded",
    )
    inv.lanes.append(crypto_lane)

    if not inv.vectorbt_available:
        inv.blockers.append("vectorbt not installed — pip install vectorbt")
    if not inv.hftbacktest_available:
        inv.blockers.append("hftbacktest not installed — pip install hftbacktest")
    if not inv.trade_manager_available:
        inv.blockers.append("trade_manager package missing")
    if not inv.certification_registry_available:
        inv.blockers.append("certification_registry missing")

    return inv
