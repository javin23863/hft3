"""Lane binding loader: reads lane_bindings.yaml and resolves model-to-lane mapping."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


def _wb_root(repo: Path) -> Path:
    return repo / "apps" / "workbench"


@dataclass
class LaneBinding:
    lane_id: str
    lane_name: str
    description: str = ""
    status: str = "unknown"
    allowed_symbols: list[str] = field(default_factory=list)
    event_catalog: str = ""
    session_config: str = ""
    universe_config: str = ""
    group_config: str = ""
    data_roots: dict[str, str] = field(default_factory=dict)
    artifact_root: str = ""
    config_root: str = ""
    pipeline_class: str = ""
    runner_module: str = ""
    runner_args: list[str] = field(default_factory=list)
    event_driven: bool = False
    walk_forward_config: str = ""
    wfc_config: str = ""
    cpp_latency_profile: str = ""
    l3_policy: str = ""
    l3_only: bool = False
    options_feature_phase: str = ""
    options_feature_enabled_for: list[str] = field(default_factory=list)
    validation_policies: list[str] = field(default_factory=list)

    @property
    def is_operational(self) -> bool:
        return self.status == "operational"

    @property
    def blockers(self) -> list[str]:
        b: list[str] = []
        if not self.is_operational:
            b.append(f"lane_status: {self.status}")
        return b


@dataclass
class LaneBindings:
    lanes: dict[str, LaneBinding]
    model_to_lanes: dict[str, list[str]]


def load_lane_bindings(repo: Path) -> LaneBindings:
    path = _wb_root(repo) / "config" / "lane_bindings.yaml"
    raw: dict[str, Any] = {}
    if path.is_file():
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}

    lanes: dict[str, LaneBinding] = {}
    for lane_id, ldata in raw.get("lanes", {}).items():
        if not isinstance(ldata, dict):
            continue
        lanes[lane_id] = LaneBinding(
            lane_id=str(lane_id),
            lane_name=str(ldata.get("lane_name", lane_id)),
            description=str(ldata.get("description", "")),
            status=str(ldata.get("status", "unknown")),
            allowed_symbols=[str(s) for s in ldata.get("allowed_symbols", [])],
            event_catalog=str(ldata.get("event_catalog", "")),
            session_config=str(ldata.get("session_config", "")),
            universe_config=str(ldata.get("universe_config", "")),
            group_config=str(ldata.get("group_config", "")),
            data_roots={str(k): str(v) for k, v in ldata.get("data_roots", {}).items()},
            artifact_root=str(ldata.get("artifact_root", "")),
            config_root=str(ldata.get("config_root", "")),
            pipeline_class=str(ldata.get("pipeline_class", "")),
            runner_module=str(ldata.get("runner_module", "")),
            runner_args=[str(a) for a in ldata.get("runner_args", [])],
            event_driven=bool(ldata.get("event_driven", False)),
            walk_forward_config=str(ldata.get("walk_forward_config", "")),
            wfc_config=str(ldata.get("wfc_config", "")),
            cpp_latency_profile=str(ldata.get("cpp_latency_profile", "")),
            l3_policy=str(ldata.get("l3_policy", "")),
            l3_only=bool(ldata.get("l3_only", False)),
            options_feature_phase=str(ldata.get("options_feature_phase", "")),
            options_feature_enabled_for=[str(s) for s in ldata.get("options_feature_enabled_for", [])],
            validation_policies=[str(p) for p in ldata.get("validation_policies", [])],
        )

    model_to_lanes: dict[str, list[str]] = {}
    for model_id, mdata in raw.get("model_lane_bindings", {}).items():
        if isinstance(mdata, dict):
            model_to_lanes[str(model_id)] = [str(l) for l in mdata.get("lanes", [])]

    return LaneBindings(lanes=lanes, model_to_lanes=model_to_lanes)


def get_lane_for_model(model_id: str, repo: Path) -> list[str]:
    bindings = load_lane_bindings(repo)
    return bindings.model_to_lanes.get(model_id, ["cme_futures"])


def get_lane_binding(lane_id: str, repo: Path) -> LaneBinding | None:
    bindings = load_lane_bindings(repo)
    return bindings.lanes.get(lane_id)
