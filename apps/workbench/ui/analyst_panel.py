"""Analyst tab: symbolic gate, KG slice, LLM narrative, GPT-5.5 chat."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

import streamlit as st

from data_layer.llm import openai_compatible_client as llm_client
from data_layer.llm.prompts import SYSTEM_PROMPT
from workbench.src.artifacts.paths import AAR_RESPONSE_FILENAME, AAR_REPORT_FILENAME
from workbench.ui.flow_state import event_artifact_dir, load_json_artifact

CHAT_SYSTEM = (
    SYSTEM_PROMPT
    + "\n\nYou are now in follow-up Q&A mode. Answer only about the attached run artifacts. "
    "Do not suggest live trading actions."
)

WORKBENCH_CONSOLE_SYSTEM = (
    "You are the HFT3 Workbench research console. Answer the operator in concise "
    "plain text from the provided Workbench snapshot and packet status context. "
    "This lane is advisory only: you have no "
    "promotion, deployment, order, or live-routing authority. RDP is schema-only and "
    "inactive. The idea-set lane is queue-only. VectorBT-only evidence is "
    "PREFILTER_ONLY and is not fully validated. Idea param ranges are packet integrity "
    "fields and are clamped or ignored until tested params exist. Do not suggest "
    "live, CHI404, Rithmic, or order-submit hooks, and do not imply that chat output "
    "changes any gate state."
)


def _snapshot_field(snapshot: Any, field: str, default: Any = None) -> Any:
    if isinstance(snapshot, dict):
        return snapshot.get(field, default)
    return getattr(snapshot, field, default)


def _dict_field(payload: Any, field: str) -> Any:
    if isinstance(payload, dict):
        return payload.get(field)
    return None


def _compact_json(payload: Any, *, limit: int = 4000) -> str:
    text = json.dumps(payload, indent=0, sort_keys=True, default=str)
    return text[:limit]


def _compact_gates(gates: Any, *, limit: int = 8) -> list[dict[str, Any]]:
    if not isinstance(gates, list):
        return []
    return [dict(gate) for gate in gates[:limit] if isinstance(gate, dict)]


def _compact_edge_packets(snapshot: Any) -> dict[str, Any]:
    data = _snapshot_field(snapshot, "data", {}) or {}
    latency = _snapshot_field(snapshot, "latency", {}) or {}
    system = _snapshot_field(snapshot, "system", {}) or {}
    for payload in (
        _dict_field(data, "bitcoin_edge_packets"),
        _dict_field(latency, "bitcoin_edge_packets"),
        _dict_field(system, "bitcoin_edge_packets"),
    ):
        if isinstance(payload, dict) and payload:
            return {
                "status": payload.get("status"),
                "observed": payload.get("observed"),
                "reason": payload.get("reason"),
                "transport": payload.get("transport"),
                "packet_history_count": len(payload.get("packet_history") or []),
            }
    return {"status": "not_observed", "observed": False}


def _workbench_console_context(snapshot: Any) -> dict[str, Any]:
    after_action = _snapshot_field(snapshot, "after_action", {}) or {}
    backtest = _snapshot_field(snapshot, "backtest", {}) or {}
    diagnostics = _snapshot_field(snapshot, "diagnostics", {}) or {}
    decision = _snapshot_field(snapshot, "decision", {}) or {}
    system = _snapshot_field(snapshot, "system", {}) or {}
    vectorbt = _dict_field(backtest, "vectorbt_summary") or {}
    packet = _dict_field(after_action, "packet") or {}
    packet_paths = _dict_field(after_action, "paths") or {}
    feature_fabric = _dict_field(diagnostics, "feature_fabric") or _dict_field(system, "feature_fabric") or {}
    lane_registry = _dict_field(system, "lane_registry") or _dict_field(_snapshot_field(snapshot, "registry", {}) or {}, "lane_registry") or {}

    return {
        "snapshot": {
            "source": _snapshot_field(snapshot, "source", ""),
            "run_id": _snapshot_field(snapshot, "run_id", ""),
            "state": _snapshot_field(snapshot, "state", ""),
            "current_stage": _snapshot_field(snapshot, "current_stage", ""),
        },
        "packet_status": {
            "after_action_packet_present": bool(packet or packet_paths.get("packet")),
            "after_action_gate_status": after_action.get("gate_status"),
            "after_action_llm_status": after_action.get("llm_status"),
            "symbolic_passed": after_action.get("symbolic_passed"),
            "skip_reasons": after_action.get("skip_reasons") or packet.get("skip_reasons") or [],
            "bitcoin_edge_packets": _compact_edge_packets(snapshot),
            "rdp": {"status": "SCHEMA_ONLY_INACTIVE"},
            "idea_set": {
                "status": "QUEUE_ONLY",
                "authority": "cannot select, tune, promote, deploy, or override gates",
                "param_ranges": "clamped_or_ignored_until_tested_params_exist",
            },
            "vectorbt": {
                "status": vectorbt.get("status"),
                "observed": vectorbt.get("observed"),
                "authority": "PREFILTER_ONLY",
                "fully_validated": False,
                "reason": vectorbt.get("reason"),
            },
        },
        "decision_gate": {
            "action": decision.get("action") or decision.get("decision"),
            "live_registry_ready": bool(decision.get("live_registry_ready")),
            "reason": decision.get("reason"),
            "blocking_gates": _compact_gates(decision.get("blocking_gates")),
        },
        "feature_fabric": {
            "status": feature_fabric.get("status"),
            "gate_status": feature_fabric.get("gate_status"),
            "pit_validation_status": feature_fabric.get("pit_validation_status"),
            "blocking_gates": _compact_gates(feature_fabric.get("blocking_gates"), limit=5),
        },
        "lane_registry": {
            "status": lane_registry.get("status"),
            "blocking_gates": _compact_gates(lane_registry.get("blocking_gates"), limit=5),
        },
        "advisory_limits": {
            "llm_authority": "advisory_only",
            "no_authority": [
                "promotion",
                "deployment",
                "order_submit",
                "live_routing",
                "CHI404_or_Rithmic_hooks",
                "gate_override",
            ],
        },
    }


def _workbench_console_reply(snapshot: Any, question: str) -> str:
    ctx = _workbench_console_context(snapshot)
    user = (
        "Workbench snapshot and packet status context:\n"
        f"{_compact_json(ctx, limit=6000)}\n\n"
        f"Operator question: {question}"
    )
    result = llm_client.generate(
        WORKBENCH_CONSOLE_SYSTEM,
        user,
        model=llm_client.DEFAULT_RESEARCH_MODEL,
        num_predict=1024,
    )
    if result.error or not result.text:
        return f"LLM error: {result.error or 'empty response'}"
    return result.text


def _render_packet_status_summary(snapshot: Any) -> None:
    ctx = _workbench_console_context(snapshot)
    packet_status = ctx["packet_status"]
    vectorbt = packet_status["vectorbt"]
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("LLM authority", "ADVISORY")
    c2.metric("RDP", packet_status["rdp"]["status"])
    c3.metric("Idea set", packet_status["idea_set"]["status"])
    c4.metric("VectorBT-only", vectorbt["authority"])
    st.caption(
        "Current truth: RDP is schema-only, idea sets only expand the queue, "
        "VectorBT-only is not full validation, and chat cannot promote or deploy anything."
    )


def workbench_llm_console(snapshot: Any) -> None:
    st.markdown("### Workbench LLM console")
    _render_packet_status_summary(snapshot)
    context_key = ":".join(
        str(_snapshot_field(snapshot, field, ""))
        for field in ("source", "run_id", "state", "current_stage")
    )
    if st.session_state.get("wb_llm_console_context_key") != context_key:
        st.session_state.wb_llm_console_context_key = context_key
        st.session_state.wb_llm_console_messages = []
        st.session_state.wb__llm_console_prompt = ""

    for msg in st.session_state.wb_llm_console_messages:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])

    if not llm_client.llm_available():
        st.warning("Configured OpenAI-compatible runtime is not reachable from this Workbench process.")

    prompt = st.text_area("Ask the configured Workbench LLM", key="wb__llm_console_prompt", height=110)
    if st.button("Send", key="wb__llm_console_send") and prompt.strip():
        question = prompt.strip()
        st.session_state.wb_llm_console_messages.append({"role": "user", "content": question})
        with st.spinner("Workbench LLM thinking..."):
            answer = _workbench_console_reply(snapshot, question)
        st.session_state.wb_llm_console_messages.append({"role": "assistant", "content": answer})
        st.rerun()


def _load_aar_narrative(art: Path) -> tuple[str, Dict[str, Any]]:
    """Prefer canonical response packet, then legacy report.md."""
    response_path = art / AAR_RESPONSE_FILENAME
    if response_path.is_file():
        data = load_json_artifact(response_path)
        return str(data.get("narrative_md") or ""), data
    report_path = art / AAR_REPORT_FILENAME
    if report_path.is_file():
        return report_path.read_text(encoding="utf-8"), {}
    return "", {}


def _compact_context(art: Path) -> str:
    parts: List[str] = []
    sym = load_json_artifact(art / "after_action_symbolic.json")
    if sym:
        slim_sym = {
            "passed": sym.get("passed"),
            "violations": sym.get("violations"),
            "obligations": sym.get("obligations"),
        }
        parts.append("symbolic: " + json.dumps(slim_sym, indent=0))
    response = load_json_artifact(art / AAR_RESPONSE_FILENAME)
    if response:
        slim = {
            "llm_status": response.get("llm_status"),
            "decision": response.get("decision"),
            "symbolic_passed": response.get("symbolic_passed"),
        }
        parts.append("response_packet: " + json.dumps(slim, indent=0)[:4000])
    packet = load_json_artifact(art / "after_action_packet.json")
    if packet:
        slim = {
            "event_context": packet.get("event_context"),
            "latency_authority": packet.get("latency_authority"),
        }
        parts.append("packet: " + json.dumps(slim, indent=0)[:4000])
    meta = load_json_artifact(art / "after_action_meta.json")
    if meta:
        parts.append("meta: " + json.dumps(meta, indent=0)[:2000])
    return "\n\n".join(parts)


def _chat_reply(art: Path, question: str) -> str:
    ctx = _compact_context(art)
    user = f"Run artifacts:\n{ctx}\n\nQuestion: {question}"
    result = llm_client.generate(
        CHAT_SYSTEM,
        user,
        model=llm_client.DEFAULT_AAR_MODEL,
        num_predict=1024,
    )
    if result.error or not result.text:
        return f"LLM error: {result.error or 'empty response'}"
    return result.text


def analyst_panel(
    repo: Path,
    campaign_id: str,
    period: str,
    event_id: str,
) -> None:
    st.subheader("Analyst — symbolic, ontology, LLM")
    art = event_artifact_dir(repo, campaign_id, period, event_id)

    if not campaign_id:
        st.info("Select a model in **Registry & Data** to start a walk-forward campaign.")
        return
    if not art:
        st.info(
            "Campaign in progress or no event artifacts yet. "
            "When the first event completes, symbolic and LLM reports appear here."
        )
        if campaign_id:
            st.caption(f"Campaign: `{campaign_id}`")
        return

    sym_path = art / "after_action_symbolic.json"
    kg_path = art / "kg_slice.json"
    packet_path = art / "after_action_packet.json"
    meta_path = art / "after_action_meta.json"
    response_path = art / AAR_RESPONSE_FILENAME

    st.markdown("### Symbolic latency invariants")
    if sym_path.is_file():
        sym_data = load_json_artifact(sym_path)
        passed = sym_data.get("passed", False)
        st.metric("Symbolic gate", "PASS" if passed else "FAIL")
        if sym_data.get("violations"):
            st.json(sym_data.get("violations"))
        if sym_data.get("obligations"):
            with st.expander("Obligations"):
                st.json(sym_data.get("obligations"))
    else:
        st.caption("No symbolic report yet for this event.")

    st.markdown("### Ontology / knowledge graph slice")
    if kg_path.is_file() or packet_path.is_file():
        kg = load_json_artifact(kg_path)
        packet = load_json_artifact(packet_path)
        nodes = kg.get("nodes") or []
        edges = kg.get("edges") or []
        evt = (packet.get("event_context") or {}).get("event_id", "—")
        c1, c2, c3 = st.columns(3)
        c1.metric("KG nodes", len(nodes))
        c2.metric("KG edges", len(edges))
        c3.metric("Event", str(evt))
        st.caption(
            "OpenFoundry ingest slice for this run (portable subgraph appended to research_cards/kg/)."
        )
        with st.expander("KG slice preview"):
            preview = {"nodes": nodes[:5], "edges": edges[:5]}
            st.json(preview)
    else:
        st.caption("KG slice not written for this event (after-action may have been skipped).")

    st.markdown("### LLM after-action narrative")
    meta = load_json_artifact(meta_path)
    narrative, response_data = _load_aar_narrative(art)
    if narrative:
        if response_data.get("llm_model"):
            st.caption(f"Model: `{response_data.get('llm_model')}` · status: `{response_data.get('llm_status')}`")
        st.markdown(narrative)
    elif meta.get("llm_status"):
        st.info(f"No LLM report ({meta.get('llm_status')}).")
    elif response_path.is_file():
        st.info("Response packet present but narrative_md empty.")
    else:
        st.caption("Run full-sweep campaign with audit grade for GPT-5.5 after-action narrative.")

    st.markdown("### Chat with GPT-5.5")
    ctx_key = f"{campaign_id}:{period}:{event_id}"
    if st.session_state.get("wb_chat_context_key") != ctx_key:
        st.session_state.wb_chat_context_key = ctx_key
        st.session_state.wb_chat_messages = []

    for msg in st.session_state.wb_chat_messages:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])

    if not llm_client.llm_available():
        st.warning(
            "GPT-5.5 xhigh target is defined, but the Workbench process has no OpenAI-compatible "
            "runtime transport. ChatGPT Pro/browser access is operator access; this backend packet "
            "lane needs a reachable runtime transport to call the model."
        )
        return

    if not sym_path.is_file() and not narrative:
        st.caption("Chat unlocks after symbolic or LLM artifacts exist for this event.")
        return

    question = st.chat_input("Ask about this run…", key="wb__analyst_chat")
    if question:
        st.session_state.wb_chat_messages.append({"role": "user", "content": question})
        with st.spinner("GPT-5.5 thinking…"):
            answer = _chat_reply(art, question)
        st.session_state.wb_chat_messages.append({"role": "assistant", "content": answer})
        st.rerun()
