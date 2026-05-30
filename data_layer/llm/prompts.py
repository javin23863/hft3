"""After-action LLM prompts — Hawkish-8B constraints."""

from __future__ import annotations

import json
from typing import Any, Dict, List


SYSTEM_PROMPT = """You are an HFT microstructure after-action analyst for CME MBO backtests.

Rules:
- Cite µs fields for execution latency diagnosis; cite ns fields for event ordering only.
- Treat symbolic invariant violations as ground truth — do not override them.
- python_research_runtime_us is NON-AUTHORITATIVE; never use it for promotion narrative.
- Macro/Fed language only when tied to event_context (CPI/NFP release windows).
- Never override promote_candidate or claim production-ready if symbolic checks failed.
- Suggestions must use scope: discovery_only | infra | latency_probe only.
- When cpp_replay_available is false, state simulation stub limitations explicitly.
- cpp_stack_verified true with queue_tracker_status link_only means CMake/runtime self-test only — not MBO queue replay; never claim full C++ replay fidelity.
- queue_tracker_status available requires cpp_replay_available true (historical NPZ through C++ engine).
- Walk-forward retuning suggestions must be tagged discovery_only.
- Keep the report under 800 words.
"""


def _audit_summary(trades: List[Dict[str, Any]], limit: int = 5) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for t in trades[:limit]:
        out.append(
            {
                "side": t.get("side"),
                "tick_to_ack_us": t.get("tick_to_ack_us"),
                "feed_delay_us": t.get("feed_delay_us"),
                "decision_compute_us": t.get("decision_compute_us"),
                "decision_to_send_us": t.get("decision_to_send_us"),
                "send_to_ack_us": t.get("send_to_ack_us"),
                "net_pnl_contribution": t.get("net_pnl_contribution"),
                "signal": t.get("signal"),
            }
        )
    if len(trades) > limit:
        out.append({"truncated_trades": len(trades) - limit})
    return out


def build_packet_summary(packet: Dict[str, Any]) -> Dict[str, Any]:
    lat = packet.get("latency_authority") or {}
    evt = packet.get("event_context") or {}
    sim = packet.get("simulation_fidelity") or {}
    pred = packet.get("predictions_vs_outcomes") or {}
    trades = packet.get("per_trade_audit") or []
    return {
        "run_id": packet.get("run_id"),
        "event_context": evt,
        "latency_authority": lat,
        "injection_sweep": packet.get("injection_sweep"),
        "simulation_fidelity": sim,
        "predictions_vs_outcomes": pred,
        "per_trade_audit_summary": _audit_summary(trades),
        "composition_trace": packet.get("composition_trace"),
        "skip_reasons": packet.get("skip_reasons"),
        "pdf_citations_complete": packet.get("pdf_citations_complete"),
    }


def build_user_prompt(
    packet: Dict[str, Any],
    symbolic: Dict[str, Any],
    pdf_citations: List[Dict[str, Any]],
    *,
    similar_runs: List[Dict[str, Any]] | None = None,
) -> str:
    summary = build_packet_summary(packet)
    similar_block = ""
    if similar_runs:
        similar_block = f"## Similar prior runs\n{json.dumps(similar_runs, indent=2)}\n\n"
    return (
        "Produce a plain-English after-action report (markdown) and a JSON block of KG annotations.\n\n"
        f"## Symbolic result\n{json.dumps(symbolic, indent=2)}\n\n"
        f"{similar_block}"
        f"## PDF citation index\n{json.dumps(pdf_citations, indent=2)}\n\n"
        f"## Packet summary (full audit in artifact JSON)\n{json.dumps(summary, indent=2)}\n\n"
        "End with a fenced ```json block containing "
        "OpenFoundry-typed edge proposals: [{\"from\",\"to\",\"relation\",\"scope\"}]."
    )


def parse_annotations(llm_text: str) -> List[Dict[str, Any]]:
    if "```json" not in llm_text:
        return []
    chunk = llm_text.split("```json", 1)[1].split("```", 1)[0].strip()
    try:
        data = json.loads(chunk)
    except json.JSONDecodeError:
        return []
    if isinstance(data, dict) and "annotations" in data:
        data = data["annotations"]
    return data if isinstance(data, list) else []
