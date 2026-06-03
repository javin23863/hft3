"""After-action LLM prompts — packet-strict GPT-5.5 constraints."""

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

Output: respond with JSON only matching MicrostructureAARResponse schema.
Required keys: schema_version ("1"), run_id, input_schema_version, llm_model, llm_elapsed_s,
llm_status ("ok"), symbolic_passed, decision.promote_candidate_recommendation (bool),
kg_annotations (array), narrative_md (markdown string).
"""


def build_symbolic_narrative(symbolic: Dict[str, Any], packet: Dict[str, Any]) -> str:
    """Deterministic narrative when LLM is skipped due to symbolic failure."""
    run_id = packet.get("run_id", "unknown")
    violations = symbolic.get("violations") or []
    passed = symbolic.get("passed", False)
    lines = [
        f"# After-action report (symbolic-only) — `{run_id}`",
        "",
        f"**Symbolic check:** {'PASSED' if passed else 'FAILED'}",
        "",
    ]
    if violations:
        lines.append("## Invariant violations")
        for v in violations:
            lines.append(f"- {v}")
        lines.append("")
    lines.append(
        "LLM narrative skipped: symbolic invariant gate failed (AlphaGeometry pattern). "
        "Do not treat promote_candidate as validated."
    )
    return "\n".join(lines)


def build_aar_system_prompt(output_schema_json: str) -> str:
    return f"{SYSTEM_PROMPT}\n\nOutput JSON Schema:\n{output_schema_json}"


def build_aar_user_envelope(
    packet: Dict[str, Any],
    symbolic: Dict[str, Any],
    *,
    similar_prior_runs: List[Dict[str, Any]] | None = None,
) -> str:
    """Full Option B envelope: entire MicrostructureAARPacket + symbolic context."""
    envelope: Dict[str, Any] = {
        "microstructure_aar_packet": packet,
        "symbolic_result": symbolic,
    }
    if similar_prior_runs:
        envelope["similar_prior_runs"] = similar_prior_runs
    return json.dumps(envelope, indent=2)


def build_packet_summary(packet: Dict[str, Any]) -> Dict[str, Any]:
    """Deprecated — use full packet envelope. Kept for one release."""
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
        "per_trade_audit": trades,
        "composition_trace": packet.get("composition_trace"),
        "ablation_modes": packet.get("ablation_modes"),
        "audit_waiver_reason": packet.get("audit_waiver_reason"),
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
    """Deprecated — delegates to full envelope."""
    _ = pdf_citations
    return build_aar_user_envelope(packet, symbolic, similar_prior_runs=similar_runs)


def parse_annotations(llm_text: str) -> List[Dict[str, Any]]:
    """Deprecated — parse kg_annotations from response JSON if present."""
    try:
        data = json.loads(llm_text)
    except json.JSONDecodeError:
        if "```json" not in llm_text:
            return []
        chunk = llm_text.split("```json", 1)[1].split("```", 1)[0].strip()
        try:
            data = json.loads(chunk)
        except json.JSONDecodeError:
            return []
    if isinstance(data, dict):
        anns = data.get("kg_annotations")
        if isinstance(anns, list):
            return anns
        if "annotations" in data:
            anns = data["annotations"]
            return anns if isinstance(anns, list) else []
    return []
