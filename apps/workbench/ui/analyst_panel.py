"""Analyst tab: symbolic gate, KG slice, LLM narrative, Ollama chat."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

import streamlit as st

from data_layer.llm import ollama_client
from data_layer.llm.prompts import SYSTEM_PROMPT
from workbench.ui.flow_state import event_artifact_dir, load_json_artifact

CHAT_SYSTEM = (
    SYSTEM_PROMPT
    + "\n\nYou are now in follow-up Q&A mode. Answer only about the attached run artifacts. "
    "Do not suggest live trading actions."
)


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
    packet = load_json_artifact(art / "after_action_packet.json")
    if packet:
        slim = {
            "model_id": packet.get("model_id"),
            "event_context": packet.get("event_context"),
            "latency_authority": packet.get("latency_authority"),
            "promote_candidate": packet.get("promote_candidate"),
        }
        parts.append("packet: " + json.dumps(slim, indent=0)[:4000])
    meta = load_json_artifact(art / "after_action_meta.json")
    if meta:
        parts.append("meta: " + json.dumps(meta, indent=0)[:2000])
    return "\n\n".join(parts)


def _chat_reply(art: Path, question: str) -> str:
    ctx = _compact_context(art)
    user = f"Run artifacts:\n{ctx}\n\nQuestion: {question}"
    result = ollama_client.generate(CHAT_SYSTEM, user, num_predict=1024)
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
        st.info("Select a model in **Model Selector** to start a walk-forward campaign.")
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
    report_path = art / "after_action_report.md"

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
        c1, c2, c3 = st.columns(3)
        c1.metric("KG nodes", len(nodes))
        c2.metric("KG edges", len(edges))
        c3.metric("Model", str(packet.get("model_id", "—")))
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
    if report_path.is_file():
        st.markdown(report_path.read_text(encoding="utf-8"))
    elif meta.get("llm_status"):
        st.info(f"No LLM report ({meta.get('llm_status')}).")
    else:
        st.caption("Run full-sweep campaign with audit grade for Hawkish-8B narrative.")

    st.markdown("### Chat with local LM")
    ctx_key = f"{campaign_id}:{period}:{event_id}"
    if st.session_state.get("wb_chat_context_key") != ctx_key:
        st.session_state.wb_chat_context_key = ctx_key
        st.session_state.wb_chat_messages = []

    for msg in st.session_state.wb_chat_messages:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])

    if not ollama_client.ollama_available():
        st.warning("Ollama / Hawkish model not available. Start Ollama and pull Hawkish-8B.")
        return

    if not sym_path.is_file() and not report_path.is_file():
        st.caption("Chat unlocks after symbolic or LLM artifacts exist for this event.")
        return

    question = st.chat_input("Ask about this run…", key="wb__analyst_chat")
    if question:
        st.session_state.wb_chat_messages.append({"role": "user", "content": question})
        with st.spinner("Hawkish-8B thinking…"):
            answer = _chat_reply(art, question)
        st.session_state.wb_chat_messages.append({"role": "assistant", "content": answer})
        st.rerun()
