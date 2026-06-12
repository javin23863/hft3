"""Configuration loader for the LLM slow-tier lane.

Loads ``config/slow_tier.yaml`` (relative to this file's package root) using
``yaml.safe_load`` and exposes the result as a typed dataclass.  All fields
have defaults so tests can instantiate ``SlowTierConfig()`` without a file.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


@dataclass
class GdeltConfig:
    max_records: int = 20
    timeout_s: float = 30.0
    query: str = (
        "(oil OR gold OR crude OR opec OR iran OR fed OR inflation OR tariff)"
        " sourcelang:eng"
    )


@dataclass
class IntakeConfig:
    """Configuration for the F3 hypothesis intake flow."""
    enabled: bool = False  # gate: must be explicitly true to enable F3
    weekly_quota: int = 5  # max candidates per trailing 7-day window


@dataclass
class SlowTierConfig:
    model: str = "hf.co/unsloth/gemma-4-12B-it-qat-GGUF:UD-Q4_K_XL"
    host: str = "http://127.0.0.1:11434"
    timeout_s: float = 300.0
    num_predict: int = 1024

    # Verifier thresholds
    z_stress: float = 3.0
    z_quiet: float = 1.0
    # Fraction of symbols that must have the insufficient_baseline flag before the
    # insufficient-evidence rule can fire (default: more than half).
    verify_insufficient_baseline_fraction: float = 0.5

    # Artifact paths (relative to repo root)
    artifact_root: str = "artifacts/research_cards/slow_tier"
    manifest_subdir: str = "manifests"
    golden_subdir: str = "golden"

    # GDELT sub-config
    gdelt: GdeltConfig = field(default_factory=GdeltConfig)

    # Intake (F3) sub-config
    intake: IntakeConfig = field(default_factory=IntakeConfig)

    # Offline mode
    offline: bool = False


def _config_file_path() -> Path:
    """Return the path to slow_tier.yaml bundled with this package."""
    # This file lives at apps/llm_slow_tier/src/config.py;
    # the yaml is at apps/llm_slow_tier/config/slow_tier.yaml.
    return Path(__file__).resolve().parent.parent / "config" / "slow_tier.yaml"


def load_config(path: Optional[Path] = None) -> SlowTierConfig:
    """Load SlowTierConfig from YAML.  Falls back to defaults if file is absent."""
    target = path or _config_file_path()

    if not target.is_file():
        return SlowTierConfig()

    try:
        import yaml  # PyYAML
    except ImportError:
        # yaml not available — return defaults; caller can still override
        return SlowTierConfig()

    raw: dict = yaml.safe_load(target.read_text(encoding="utf-8")) or {}

    gdelt_raw = raw.get("gdelt") or {}
    gdelt = GdeltConfig(
        max_records=int(gdelt_raw.get("max_records", 20)),
        timeout_s=float(gdelt_raw.get("timeout_s", 30.0)),
        query=str(gdelt_raw.get("query", GdeltConfig.query)),
    )

    verify_raw = raw.get("verify") or {}
    intake_raw = raw.get("intake") or {}
    intake = IntakeConfig(
        enabled=bool(intake_raw.get("enabled", False)),
        weekly_quota=int(intake_raw.get("weekly_quota", 5)),
    )

    return SlowTierConfig(
        model=str(raw.get("model", SlowTierConfig.model)),
        host=str(raw.get("host", SlowTierConfig.host)),
        timeout_s=float(raw.get("timeout_s", SlowTierConfig.timeout_s)),
        num_predict=int(raw.get("num_predict", SlowTierConfig.num_predict)),
        z_stress=float(raw.get("z_stress", SlowTierConfig.z_stress)),
        z_quiet=float(raw.get("z_quiet", SlowTierConfig.z_quiet)),
        verify_insufficient_baseline_fraction=float(
            verify_raw.get(
                "insufficient_baseline_fraction",
                SlowTierConfig.verify_insufficient_baseline_fraction,
            )
        ),
        artifact_root=str(raw.get("artifact_root", SlowTierConfig.artifact_root)),
        manifest_subdir=str(raw.get("manifest_subdir", SlowTierConfig.manifest_subdir)),
        golden_subdir=str(raw.get("golden_subdir", SlowTierConfig.golden_subdir)),
        gdelt=gdelt,
        intake=intake,
        offline=bool(raw.get("offline", False)),
    )
