"""Imbalance feature catalog registry."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from hft3_bootstrap import features_engine_root


@dataclass
class ImbalanceFeatureSpec:
    feature_name: str
    feature_family: str
    feature_set_id: str
    source_dataset_id: str = ""
    source_schema: str = ""
    source_data_class: str = ""
    asset_class: str = ""
    instrument_coverage: str = ""
    date_coverage: str = ""
    computation_method: str = ""
    parameters: Dict[str, Any] = field(default_factory=dict)
    window_length: Optional[str] = None
    event_time_basis: bool = True
    missing_data_behavior: str = "nan"
    degraded_status: bool = False
    latency_estimate_ns: int = 0
    lineage: Dict[str, Any] = field(default_factory=dict)
    config_hash: str = ""

    @classmethod
    def from_dict(cls, raw: dict, config_hash: str = "") -> "ImbalanceFeatureSpec":
        return cls(
            feature_name=str(raw["feature_name"]),
            feature_family=str(raw["feature_family"]),
            feature_set_id=str(raw["feature_set_id"]),
            source_dataset_id=str(raw.get("source_dataset_id", "")),
            source_schema=str(raw.get("source_schema", "")),
            source_data_class=str(raw.get("source_data_class", "")),
            asset_class=str(raw.get("asset_class", "")),
            instrument_coverage=str(raw.get("instrument_coverage", "")),
            date_coverage=str(raw.get("date_coverage", "")),
            computation_method=str(raw.get("computation_method", "")),
            parameters=dict(raw.get("parameters") or {}),
            window_length=raw.get("window_length"),
            event_time_basis=bool(raw.get("event_time_basis", True)),
            missing_data_behavior=str(raw.get("missing_data_behavior", "nan")),
            degraded_status=bool(raw.get("degraded_status", False)),
            latency_estimate_ns=int(raw.get("latency_estimate_ns", 0)),
            lineage=dict(raw.get("lineage") or {}),
            config_hash=config_hash,
        )


def _config_path(repo: Optional[Path] = None) -> Path:
    root = features_engine_root(repo)
    return root / "config" / "imbalance_features.yaml"


def load_imbalance_registry(repo: Optional[Path] = None) -> Dict[str, Any]:
    path = _config_path(repo)
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) if path.is_file() else {}
    from features_engine.src.imbalance.normalize import config_hash as ch

    cfg_hash = ch(raw)
    specs = [
        ImbalanceFeatureSpec.from_dict(f, config_hash=cfg_hash)
        for f in raw.get("features") or []
    ]
    return {
        "feature_version": raw.get("feature_version", "1.0.0"),
        "default_windows_ms": raw.get("default_windows_ms", []),
        "features": specs,
        "promotion": raw.get("promotion") or {},
        "config_hash": cfg_hash,
    }


def features_for_set(feature_set_id: str, repo: Optional[Path] = None) -> List[ImbalanceFeatureSpec]:
    reg = load_imbalance_registry(repo)
    return [f for f in reg["features"] if f.feature_set_id == feature_set_id]
