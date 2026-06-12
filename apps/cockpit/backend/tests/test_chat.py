"""V2 — vault RAG + chat prompt assembly + SSE stream (offline)."""
import asyncio
import json

from fastapi.testclient import TestClient

from apps.cockpit.backend import chat, vault_rag
from apps.cockpit.backend.main import app


def _make_vault(tmp_path, monkeypatch):
    v = tmp_path / "vault"
    v.mkdir()
    (v / "Ontology.md").write_text("# Ontology\nEntities: paper, book. Relations: extends, maps_to_hft3.", encoding="utf-8")
    (v / "queue.md").write_text("# Queue position\nQueue position predicts fill probability and adverse selection.", encoding="utf-8")
    (v / "hawkes.md").write_text("# Hawkes\nSelf-exciting point process for order arrivals.", encoding="utf-8")
    monkeypatch.setenv("HFT3_VAULT_DIR", str(v))
    vault_rag._index["sig"] = None  # bust cache
    return v


def test_rag_pins_ontology_and_ranks(tmp_path, monkeypatch):
    _make_vault(tmp_path, monkeypatch)
    out = vault_rag.retrieve("queue position fill probability", k=2)
    titles = [d["title"] for d in out]
    assert "Ontology" in titles  # always pinned
    assert any("Queue" in t for t in titles)  # query match ranked in
    queue = next(d for d in out if "Queue" in d["title"])
    assert queue["score"] > 0 and "fill" in queue["snippet"].lower()


def test_build_messages_persona_state_query(tmp_path, monkeypatch):
    _make_vault(tmp_path, monkeypatch)
    ctx = chat.build_context("explain queue position")
    msgs = chat.build_messages("explain queue position", ctx)
    assert msgs[0]["role"] == "system"
    assert "READ-ONLY" in msgs[0]["content"]
    user = msgs[1]["content"]
    assert "SYSTEM STATE" in user and "VAULT CONTEXT" in user
    assert "explain queue position" in user


def test_stream_chat_graceful_when_ollama_down(tmp_path, monkeypatch):
    _make_vault(tmp_path, monkeypatch)
    # force a dead host -> connection refused -> error event, never hits real ollama
    monkeypatch.setattr(chat, "_model_and_host", lambda: ("m", "http://127.0.0.1:9"))

    async def _collect():
        return [e async for e in chat.stream_chat("hi")]

    events = asyncio.run(_collect())
    parsed = [json.loads(e[len("data: "):]) for e in events if e.startswith("data: ")]
    types = [p["type"] for p in parsed]
    assert types[0] == "context"           # citations emitted first
    assert "error" in types                # ollama down -> graceful error event


# --- HTTP endpoint: read-only/view boundary + empty-query guard -------------

def test_chat_requires_view_token(monkeypatch):
    # The chat endpoint is view-scoped; a remote/un-tokened caller is rejected
    # BEFORE any ollama call (regression guard for the read-only boundary).
    monkeypatch.setenv("COCKPIT_VIEW_TOKEN", "secret-view")
    monkeypatch.delenv("COCKPIT_CONTROL_TOKEN", raising=False)
    client = TestClient(app)
    assert client.post("/api/chat", json={"query": "hi"}).status_code == 401


def test_chat_empty_query_short_circuits(monkeypatch):
    # Blank/whitespace/missing query returns a plain JSON error, NOT an SSE
    # stream, and never reaches stream_chat (so no ollama call fires).
    monkeypatch.setenv("COCKPIT_VIEW_TOKEN", "secret-view")
    monkeypatch.delenv("COCKPIT_CONTROL_TOKEN", raising=False)
    client = TestClient(app)
    hdr = {"Authorization": "Bearer secret-view"}
    for body in ({}, {"query": "   "}):
        r = client.post("/api/chat", json=body, headers=hdr)
        assert r.status_code == 200
        assert r.json() == {"error": "query required"}
        assert "text/event-stream" not in r.headers.get("content-type", "")
