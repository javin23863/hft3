"""Build MicrostructureAARPacket from workbench run artifacts."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
import yaml

from data_layer.openfoundry_bridge import read_vendor_lock, validate_connector

_AUDIT_NS_FIELDS = (
    "market_data_exchange_ts",
    "market_data_receive_ts",
    "decision_start_ts",
    "decision_end_ts",
    "order_send_ts",
    "gateway_ack_ts",
    "fill_ts",
)
_AUDIT_US_FIELDS = (
    "feed_delay_us",
    "decision_compute_us",
    "decision_to_send_us",
    "send_to_ack_us",
    "tick_to_ack_us",
    "python_research_compute_us",
    "latency_injection_us",
)

_MANIFEST_TABLE_ROW = re.compile(
    r"^\|\s*`([^`]+)`.*\|\s*`([^`]+)`.*\|\s*([^|]+)\|\s*\*\*(present|absent)\*\*\s*\|",
    re.MULTILINE,
)


def _manifest_path(repo_root: Path) -> Path:
    return repo_root / "docs" / "references" / "MANIFEST.md"


def load_pdf_citations(repo_root: Path) -> Tuple[List[Dict[str, Any]], bool]:
    path = _manifest_path(repo_root)
    citations: List[Dict[str, Any]] = []
    all_present = True
    if not path.is_file():
        return citations, False
    text = path.read_text(encoding="utf-8")
    refs_dir = repo_root / "docs" / "references"
    for m in _MANIFEST_TABLE_ROW.finditer(text):
        field, pdf_name, section, status = m.groups()
        present = status.strip().lower() == "present" and (refs_dir / pdf_name.strip()).is_file()
        if status.strip().lower() == "present" and not present:
            all_present = False
        elif status.strip().lower() == "absent":
            all_present = False
        citations.append(
            {
                "field": field.strip(),
                "pdf": pdf_name.strip(),
                "section": section.strip(),
                "present_on_disk": present,
            }
        )
    return citations, all_present and bool(citations)


def _event_context_from_id(event_id: str) -> str:
    """Heuristic label from event_id string — not posterior P(Z_t|F_t)."""
    if "CPI" in event_id:
        return "CPI_TIGHT"
    if "NFP" in event_id:
        return "NFP_TIGHT"
    return event_id.split("_")[0] if event_id else "unknown"


def _read_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _normalize_injection_sweep(diagnostics: Dict[str, Any]) -> Dict[str, float]:
    raw = diagnostics.get("pnl_by_injection_us") or {}
    if raw:
        return {str(int(k) if isinstance(k, str) and k.isdigit() else k): float(v) for k, v in raw.items()}
    legacy = diagnostics.get("pnl_by_latency") or {}
    out: Dict[str, float] = {}
    for k, v in legacy.items():
        us_key = str(int(float(k) * 1000))
        out[us_key] = float(v)
    return out


def _row_to_audit(row: pd.Series) -> Dict[str, Any]:
    d: Dict[str, Any] = {}
    col_map = {
        "gateway_ack_ts": "order_ack_ts",
        "market_data_exchange_ts": "market_data_exchange_ts",
    }
    for field in _AUDIT_NS_FIELDS:
        src = col_map.get(field, field)
        if src in row.index and pd.notna(row[src]):
            d[field] = int(row[src])
        elif field in row.index and pd.notna(row[field]):
            d[field] = int(row[field])
    for field in _AUDIT_US_FIELDS:
        if field in row.index and pd.notna(row[field]):
            d[field] = float(row[field])
        elif field == "tick_to_ack_us" and "tick_to_ack_ms" in row.index:
            d[field] = float(row["tick_to_ack_ms"]) * 1000.0
        elif field == "feed_delay_us" and "tick_to_decision_ms" in row.index:
            d[field] = float(row["tick_to_decision_ms"]) * 1000.0
        elif field == "decision_compute_us" and "decision_compute_ms" in row.index:
            d[field] = float(row["decision_compute_ms"]) * 1000.0
        elif field == "decision_to_send_us" and "decision_to_send_ms" in row.index:
            d[field] = float(row["decision_to_send_ms"]) * 1000.0
        elif field == "send_to_ack_us" and "send_to_ack_ms" in row.index:
            d[field] = float(row["send_to_ack_ms"]) * 1000.0
    if "tick_to_ack_us" not in d and all(k in d for k in ("feed_delay_us", "decision_compute_us", "decision_to_send_us", "send_to_ack_us")):
        d["tick_to_ack_us"] = (
            d["feed_delay_us"] + d["decision_compute_us"] + d["decision_to_send_us"] + d["send_to_ack_us"]
        )
    for k in ("model_id", "side", "exec_price", "qty", "signal", "mid_at_signal", "net_pnl_contribution"):
        if k in row.index:
            d[k] = row[k]
    return d


def _audit_complete(rec: Dict[str, Any]) -> bool:
    for f in _AUDIT_NS_FIELDS:
        if f not in rec:
            return False
    for f in ("feed_delay_us", "decision_compute_us", "decision_to_send_us", "send_to_ack_us", "tick_to_ack_us"):
        if f not in rec:
            return False
    return True


def _load_per_trade_audit(artifact_dir: Path, num_trades: int) -> Tuple[List[Dict[str, Any]], bool]:
    if num_trades <= 0:
        return [], True
    path = artifact_dir / "trades.parquet"
    if not path.is_file():
        return [], False
    df = pd.read_parquet(path)
    audits = [_row_to_audit(row) for _, row in df.iterrows()]
    complete = len(audits) == num_trades and all(_audit_complete(a) for a in audits)
    return audits, complete


def _predictions_vs_outcomes(
    diagnostics: Dict[str, Any],
    audits: List[Dict[str, Any]],
) -> Dict[str, Any]:
    signal_raw = diagnostics.get("signal_raw")
    signal_adj = diagnostics.get("signal_adjusted")
    vetoed = diagnostics.get("trades_vetoed_by_defense", 0)
    adverse = diagnostics.get("adverse_selection_ticks")
    aligned = 0
    for a in audits:
        sig = float(a.get("signal", 0.0))
        side = str(a.get("side", "")).upper()
        if (sig > 0 and side == "BUY") or (sig < 0 and side == "SELL"):
            aligned += 1
    return {
        "signal_raw": signal_raw,
        "signal_adjusted": signal_adj,
        "trades_vetoed_by_defense": vetoed,
        "adverse_selection_ticks": adverse,
        "signal_fill_direction_aligned_count": aligned,
        "signal_fill_direction_aligned_ratio": aligned / max(len(audits), 1),
    }


def build_microstructure_aar_packet(
    artifact_dir: Path,
    repo_root: Path,
) -> Tuple[Dict[str, Any], List[str]]:
    """Return (packet, skip_reasons)."""
    skip_reasons: List[str] = []
    bridge = validate_connector(repo_root)
    diagnostics_path = artifact_dir / "diagnostics.json"
    manifest_path = artifact_dir / "manifest.json"
    config_path = artifact_dir / "config.yaml"

    diagnostics = _read_json(diagnostics_path) if diagnostics_path.is_file() else {}
    manifest = _read_json(manifest_path) if manifest_path.is_file() else {}
    config = yaml.safe_load(config_path.read_text(encoding="utf-8")) if config_path.is_file() else {}

    event_id = str(diagnostics.get("event_id") or manifest.get("event_id") or "")
    data_sufficient = bool(manifest.get("data_sufficient", diagnostics.get("data_sufficient", True)))
    if not data_sufficient:
        skip_reasons.append("HISTORY_GATE")

    num_trades = int(diagnostics.get("num_trades", 0))
    per_trade_audit, audit_complete = _load_per_trade_audit(artifact_dir, num_trades)
    if num_trades > 0 and not audit_complete:
        skip_reasons.append("AUDIT_INCOMPLETE")

    pdf_citations, pdf_complete = load_pdf_citations(repo_root)
    injection_sweep = _normalize_injection_sweep(diagnostics)

    packet: Dict[str, Any] = {
        "schema_version": "1",
        "run_id": artifact_dir.name,
        "openfoundry_meta": {
            "connector_id": bridge["connector"]["connector_id"],
            "asset_class": bridge["connector"]["asset_class"],
            "vendor_shas": bridge["vendor_shas"],
            "schema_version": bridge["connector"]["schema_version"],
        },
        "pdf_citations": pdf_citations,
        "pdf_citations_complete": pdf_complete,
        "event_context": {
            "event_id": event_id,
            "event_state": _event_context_from_id(event_id),
            "event_state_heuristic": True,
            "data_sufficient": data_sufficient,
            "catalog_years": manifest.get("history_years_available"),
            "symbol": config.get("symbol"),
        },
        "latency_authority": {
            "authority": diagnostics.get("latency_authority", "cpp_measured"),
            "measured_production_p99_us": diagnostics.get("measured_production_p99_us"),
            "breakeven_us": diagnostics.get("breakeven_us"),
            "latency_profitability_buffer_us": diagnostics.get("latency_profitability_buffer_us"),
            "lane_required": diagnostics.get("lane_required"),
            "lane_measured": diagnostics.get("lane_measured"),
            "lane_pass": diagnostics.get("lane_pass"),
            "survives_cpp_execution_delay": diagnostics.get("survives_cpp_execution_delay"),
            "promote_candidate": diagnostics.get("promote_candidate"),
            "robustness_passed": diagnostics.get("robustness_passed"),
            "cpp_hot_path_runtime_us": diagnostics.get("cpp_hot_path_runtime_us"),
            "python_research_runtime_us": diagnostics.get("python_research_runtime_us"),
            "python_research_runtime_authoritative": False,
        },
        "injection_sweep": injection_sweep,
        "per_trade_audit": per_trade_audit,
        "simulation_fidelity": {
            "cpp_replay_available": diagnostics.get("cpp_replay_available", False),
            "matching_config": str(repo_root / "workbench" / "src" / "sim" / "matching_config.yaml"),
            "queue_tracker_status": (
                "available" if diagnostics.get("cpp_replay_available") else "stub_or_unverified"
            ),
        },
        "predictions_vs_outcomes": _predictions_vs_outcomes(diagnostics, per_trade_audit),
        "skip_reasons": skip_reasons,
    }
    if diagnostics.get("phase_budgets_us"):
        packet["composition_trace"] = {
            "phase_budgets_us": diagnostics.get("phase_budgets_us"),
            "trades_vetoed_by_defense": diagnostics.get("trades_vetoed_by_defense"),
        }
    return packet, skip_reasons


def validate_packet_schema(packet: Dict[str, Any]) -> List[str]:
    """Lightweight required-field check (schema_v1.json mirror)."""
    errors: List[str] = []
    for key in (
        "schema_version",
        "run_id",
        "openfoundry_meta",
        "pdf_citations",
        "pdf_citations_complete",
        "event_context",
        "latency_authority",
        "injection_sweep",
        "per_trade_audit",
        "simulation_fidelity",
        "predictions_vs_outcomes",
    ):
        if key not in packet:
            errors.append(f"missing {key}")
    lat = packet.get("latency_authority") or {}
    if lat.get("python_research_runtime_authoritative") is not False:
        errors.append("python_research_runtime_authoritative must be false")
    return errors
