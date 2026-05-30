"""Ingest a workbench run into the file-backed KG."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Tuple

from data_layer.kg.store import append_edges, append_nodes
from data_layer.packet.microstructure_aar_packet import _event_context_from_id


def build_kg_slice(packet: Dict[str, Any]) -> Dict[str, Any]:
    lat = packet.get("latency_authority") or {}
    evt = packet.get("event_context") or {}
    run_node = {
        "id": f"run:{packet['run_id']}",
        "type": "backtest-run",
        "run_id": packet["run_id"],
        "model_id": (packet.get("config") or {}).get("model_id"),
        "event_id": evt.get("event_id"),
        "event_context": evt.get("event_state") or _event_context_from_id(str(evt.get("event_id", ""))),
        "event_state_heuristic": evt.get("event_state_heuristic", True),
        "latency_lane": lat.get("lane_required"),
        "breakeven_us": lat.get("breakeven_us"),
        "lane_pass": lat.get("lane_pass"),
        "promote_candidate": lat.get("promote_candidate"),
        "net_pnl": lat.get("net_pnl"),
    }
    if run_node["model_id"] is None:
        run_node["model_id"] = packet.get("openfoundry_meta", {}).get("connector_id")
    edges: List[Dict[str, Any]] = []
    nodes: List[Dict[str, Any]] = [run_node]
    if evt.get("event_id"):
        event_id = f"event:{evt['event_id']}"
        event_node = {
            "id": event_id,
            "type": "macro-event",
            "event_id": evt["event_id"],
            "event_state_heuristic": evt.get("event_state_heuristic", True),
        }
        nodes.append(event_node)
        edges.append({"from": run_node["id"], "to": event_id, "relation": "executed_on"})
    return {"nodes": nodes, "edges": edges}


def ingest_run(
    artifact_dir: Path,
    repo_root: Path,
    packet: Dict[str, Any],
) -> Tuple[Dict[str, Any], int, int]:
    config_path = artifact_dir / "config.yaml"
    if config_path.is_file():
        import yaml

        packet = {**packet, "config": yaml.safe_load(config_path.read_text(encoding="utf-8"))}
    lat = packet.get("latency_authority") or {}
    lat = {**lat, "net_pnl": _load_net_pnl(artifact_dir)}
    packet = {**packet, "latency_authority": lat}

    kg_slice = build_kg_slice(packet)
    n_nodes = append_nodes(repo_root, kg_slice["nodes"])
    n_edges = append_edges(repo_root, kg_slice["edges"])
    return kg_slice, n_nodes, n_edges


def _load_net_pnl(artifact_dir: Path) -> float:
    diag = artifact_dir / "diagnostics.json"
    if not diag.is_file():
        return 0.0
    data = json.loads(diag.read_text(encoding="utf-8"))
    return float(data.get("net_pnl", 0.0))


def write_kg_slice(artifact_dir: Path, kg_slice: Dict[str, Any]) -> Path:
    out = artifact_dir / "kg_slice.json"
    out.write_text(json.dumps(kg_slice, indent=2), encoding="utf-8")
    return out
