from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass
class TrialConfig:
    enabled: bool
    connector: str
    symbol: str
    exchange: str
    contract: str
    capture_environment: str
    source: str
    schema_version: str
    repo_root: Path
    raw_root: Path
    normalized_root: Path
    replay_root: Path
    reports_root: Path
    rtrader: dict[str, Any] = field(default_factory=dict)
    unattended: dict[str, Any] = field(default_factory=dict)
    rithmic: dict[str, Any] = field(default_factory=dict)
    rithmic_api_config: str = ""

    def raw_dir(self, date: str, symbol: str | None = None) -> Path:
        sym = symbol or self.symbol
        return self.raw_root / date / sym

    def normalized_dir(self, date: str, symbol: str | None = None) -> Path:
        sym = symbol or self.symbol
        return self.normalized_root / date / sym

    def replay_dir(self, date: str, symbol: str | None = None) -> Path:
        sym = symbol or self.symbol
        return self.replay_root / date / sym

    def reports_dir(self, date: str) -> Path:
        return self.reports_root / date


def _assert_quarantine(path: Path, repo_root: Path, label: str) -> None:
    """Trial lane paths must not overlap trusted production Databento NPZ."""
    prod_npz = (repo_root / "data" / "npz").resolve()
    resolved = path.resolve()
    try:
        resolved.relative_to(prod_npz)
        raise ValueError(
            f"Quarantine violation: {label}={path} must not be under production {prod_npz}"
        )
    except ValueError as exc:
        if "Quarantine violation" in str(exc):
            raise
        return


def load_config(path: str | Path) -> TrialConfig:
    cfg_path = Path(path)
    data = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
    repo_root = Path(data.get("paths", {}).get("repo_root", ".")).resolve()
    if not repo_root.is_absolute():
        repo_root = (cfg_path.parent.parent.parent / repo_root).resolve()

    def _p(key: str) -> Path:
        return repo_root / data["paths"][key]

    enabled = bool(data.get("enabled", False))
    if os.environ.get("RITHMIC_TRIAL_ENABLED", "").lower() in ("1", "true", "yes"):
        enabled = True

    connector = os.environ.get("RITHMIC_TRIAL_CONNECTOR", data.get("connector", "fixture"))
    symbol = os.environ.get("RITHMIC_SYMBOL", data.get("symbol", ""))
    exchange = os.environ.get("RITHMIC_EXCHANGE", data.get("exchange", "CME"))

    rtrader = dict(data.get("rtrader", {}))
    if os.environ.get("RTRADER_WINE_PREFIX"):
        rtrader["wine_prefix"] = os.environ["RTRADER_WINE_PREFIX"]
    if os.environ.get("RTRADER_INSTALLER_PATH"):
        rtrader["installer_path"] = os.environ["RTRADER_INSTALLER_PATH"]
    if os.environ.get("RTRADER_WATCH_DIRS"):
        rtrader["watch_dirs"] = [
            p.strip() for p in os.environ["RTRADER_WATCH_DIRS"].split(";") if p.strip()
        ]
    if os.environ.get("RITHMIC_GATEWAY"):
        rtrader["gateway"] = os.environ["RITHMIC_GATEWAY"]

    unattended = dict(data.get("unattended", {}))
    rithmic = dict(data.get("rithmic", {}))
    if os.environ.get("RITHMIC_ENVIRONMENT"):
        rithmic["environment"] = os.environ["RITHMIC_ENVIRONMENT"]
    if os.environ.get("RITHMIC_GATEWAY"):
        rithmic["gateway"] = os.environ["RITHMIC_GATEWAY"]

    raw_root = _p("raw_root")
    normalized_root = _p("normalized_root")
    replay_root = _p("replay_root")
    reports_root = _p("reports_root")
    for label, p in (
        ("raw_root", raw_root),
        ("normalized_root", normalized_root),
        ("replay_root", replay_root),
        ("reports_root", reports_root),
    ):
        _assert_quarantine(p, repo_root, label)

    return TrialConfig(
        enabled=enabled,
        connector=connector,
        symbol=symbol,
        exchange=exchange,
        contract=data.get("contract", ""),
        capture_environment=data.get("capture_environment", "rithmic_test"),
        source=data.get("source", "rithmic_trial"),
        schema_version=data.get("schema_version", "normalized_v1"),
        repo_root=repo_root,
        raw_root=raw_root,
        normalized_root=normalized_root,
        replay_root=replay_root,
        reports_root=reports_root,
        rtrader=rtrader,
        unattended=unattended,
        rithmic=rithmic,
        rithmic_api_config=data.get("rithmic_api_config", ""),
    )
