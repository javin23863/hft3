"""Build MicrostructureAARPacket from workbench run artifacts."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
import yaml

from data_layer.stack_check_contract import REQUIRED_STACK_CHECKS
from data_layer.openfoundry_bridge import read_vendor_lock, validate_connector

_REQUIRED_STACK_CHECKS = REQUIRED_STACK_CHECKS


def _cpp_stack_verified_from_diagnostics(diagnostics: Dict[str, Any]) -> bool:
    checks = diagnostics.get("cpp_stack_checks") or {}
    if not isinstance(checks, dict) or not checks:
        return False
    return all(checks.get(k) for k in _REQUIRED_STACK_CHECKS)

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
_PHASE5_AUDIT_NS_FIELDS = (
    "market_data_exchange_ts",
    "market_data_wire_ts",
    "market_data_receive_start_ts",
    "market_data_receive_ts",
    "market_data_decode_start_ts",
    "market_data_decode_end_ts",
    "book_snapshot_start_ts",
    "book_snapshot_end_ts",
    "feature_build_start_ts",
    "feature_build_end_ts",
    "signal_start_ts",
    "signal_end_ts",
    "decision_start_ts",
    "decision_end_ts",
    "risk_check_start_ts",
    "risk_check_end_ts",
    "sizing_start_ts",
    "sizing_end_ts",
    "order_intent_create_ts",
    "order_queue_enter_ts",
    "order_queue_exit_ts",
    "order_send_ts",
    "gateway_send_ts",
    "gateway_ack_ts",
    "exchange_ack_ts",
    "queue_position_ts",
    "fill_model_start_ts",
    "fill_model_end_ts",
    "fill_ts",
    "pnl_mark_start_ts",
    "pnl_mark_end_ts",
    "audit_record_start_ts",
    "audit_record_end_ts",
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
    for field in row.index:
        if isinstance(field, str) and field.endswith("_ts") and field not in d and pd.notna(row[field]):
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


def _audit_complete(rec: Dict[str, Any], *, require_phase5: bool = False) -> bool:
    ns_fields = _PHASE5_AUDIT_NS_FIELDS if require_phase5 else _AUDIT_NS_FIELDS
    previous = None
    for f in ns_fields:
        if f not in rec:
            return False
        if require_phase5:
            try:
                current = int(rec[f])
            except (TypeError, ValueError):
                return False
            if previous is not None and current < previous:
                return False
            previous = current
    for f in ("feed_delay_us", "decision_compute_us", "decision_to_send_us", "send_to_ack_us", "tick_to_ack_us"):
        if f not in rec:
            return False
    return True


def _load_per_trade_audit(
    artifact_dir: Path,
    expected_audit_count: int,
    *,
    require_phase5: bool = False,
) -> Tuple[List[Dict[str, Any]], bool]:
    if expected_audit_count <= 0:
        return [], True
    path = artifact_dir / "trades.parquet"
    if not path.is_file():
        return [], False
    df = pd.read_parquet(path)
    audits = [_row_to_audit(row) for _, row in df.iterrows()]
    complete = len(audits) == expected_audit_count and all(
        _audit_complete(a, require_phase5=require_phase5) for a in audits
    )
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
    phase5_schema = diagnostics.get("phase5_timestamp_schema") or {}
    require_phase5_audit = phase5_schema.get("schema_version") == "phase5_33_timestamp_v1"
    expected_audit_count = int(phase5_schema.get("expected_trade_count", num_trades))
    per_trade_audit, audit_complete = _load_per_trade_audit(
        artifact_dir,
        expected_audit_count,
        require_phase5=require_phase5_audit,
    )
    execution_assumptions = str(
        config.get("execution_assumptions") or diagnostics.get("execution_assumptions") or ""
    )
    audit_waiver_reason: Optional[str] = None
    if expected_audit_count > 0 and not audit_complete:
        if execution_assumptions == "quote_engine":
            audit_waiver_reason = "quote_engine_aggregate_only"
        else:
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
            "wfc_status": diagnostics.get("wfc_status"),
            "robustness_passed": diagnostics.get("robustness_passed"),
            "cpp_hot_path_runtime_us": diagnostics.get("cpp_hot_path_runtime_us"),
            "python_research_runtime_us": diagnostics.get("python_research_runtime_us"),
            "python_research_runtime_authoritative": False,
        },
        "injection_sweep": injection_sweep,
        "per_trade_audit": per_trade_audit,
        "simulation_fidelity": {
            "cpp_replay_available": diagnostics.get("cpp_replay_available", False),
            "cpp_stack_verified": _cpp_stack_verified_from_diagnostics(diagnostics),
            "matching_config": str(repo_root / "workbench" / "src" / "sim" / "matching_config.yaml"),
            "quote_engine_replay": execution_assumptions == "quote_engine",
            "queue_tracker_status": (
                "available"
                if diagnostics.get("cpp_replay_available")
                else (
                    "link_only"
                    if _cpp_stack_verified_from_diagnostics(diagnostics)
                    else "stub_or_unverified"
                )
            ),
        },
        "predictions_vs_outcomes": _predictions_vs_outcomes(diagnostics, per_trade_audit),
        "skip_reasons": skip_reasons,
    }
    if audit_waiver_reason:
        packet["audit_waiver_reason"] = audit_waiver_reason
    if diagnostics.get("phase_budgets_us"):
        packet["composition_trace"] = {
            "phase_budgets_us": diagnostics.get("phase_budgets_us"),
            "trades_vetoed_by_defense": diagnostics.get("trades_vetoed_by_defense"),
        }
    if diagnostics.get("ablation_modes"):
        packet["ablation_modes"] = diagnostics.get("ablation_modes")
    return packet, skip_reasons


def validate_packet_schema(packet: Dict[str, Any]) -> List[str]:
    """Validate against schema_v1.json via jsonschema."""
    from data_layer.packet.validate import validate_aar_packet_in

    return validate_aar_packet_in(packet)
