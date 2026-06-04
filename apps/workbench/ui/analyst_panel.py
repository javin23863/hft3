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
            "OpenAI-compatible GPT-5.5 endpoint not configured. Set "
            "HFT3_LLM_API_KEY or OPENAI_API_KEY."
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
