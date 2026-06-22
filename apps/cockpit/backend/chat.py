"""Local-Gemma chat for the cockpit — system-state aware, vault-grounded.

READ-ONLY ADVISORY. The model explains state + theory; it has no path to place
orders, trigger jobs, or change config (detect-only doctrine; the control plane
stays local-gated and is not reachable from here). Context = a compact snapshot
of the live cockpit zones + vault RAG (ontology-grounded) + best-effort run
history. Streams tokens from ollama as SSE.
"""
from __future__ import annotations

import json
import os
from typing import AsyncIterator

from . import paths, vault_rag
from .aggregate import ZONES

_DEFAULT_MODEL = "hf.co/unsloth/gemma-4-12B-it-qat-GGUF:UD-Q4_K_XL"
_DEFAULT_HOST = "http://127.0.0.1:11434"

_PERSONA = (
    "You are the analyst assistant embedded in an HFT CME-futures research cockpit. "
    "You are versed in market microstructure, quantitative finance, high-frequency "
    "trading, and the mathematics behind them, and you speak in the system's ontology "
    "(see VAULT CONTEXT). You answer the trader's questions about the system's current "
    "state, its models, its backtests, and the theory behind them. "
    "You are STRICTLY READ-ONLY AND ADVISORY: you explain and reason; you NEVER instruct "
    "or imply placing orders, arming models, or changing configuration. Ground claims in "
    "the provided SYSTEM STATE and VAULT CONTEXT; if you don't have the data, say so."
)


def _model_and_host() -> tuple[str, str]:
    try:
        import yaml

        cfg = yaml.safe_load((paths.REPO / "apps" / "llm_slow_tier" / "config" / "slow_tier.yaml").read_text(encoding="utf-8")) or {}
        return str(cfg.get("model", _DEFAULT_MODEL)), str(cfg.get("host", _DEFAULT_HOST))
    except Exception:
        return _DEFAULT_MODEL, _DEFAULT_HOST


def _state_summary() -> str:
    """Compact, token-cheap snapshot of the live zones."""
    lines = []
    try:
        p = ZONES["pipeline"]()
        lines.append("Pipeline health=%s stages=%s" % (
            p.get("health"), ", ".join(f"{s['id']}:{s['status']}" for s in p.get("stages", []))))
        vbt = p.get("vectorbt_paid_screen_tracking") or {}
        if isinstance(vbt, dict):
            lines.append(
                "VectorBT paid screen: state=%s run_id=%s progress=%s/%s failed=%s skipped=%s units_per_hour=%s last_sync=%s host=%s" % (
                    vbt.get("state"),
                    vbt.get("run_id"),
                    vbt.get("completed_work_units"),
                    vbt.get("expected_work_units"),
                    vbt.get("failed_work_units"),
                    vbt.get("skipped_work_units"),
                    vbt.get("units_per_hour"),
                    vbt.get("last_sync_utc"),
                    vbt.get("host_label") or vbt.get("ssh_host"),
                )
            )
    except Exception:
        pass
    try:
        m = ZONES["models"]()
        f = m.get("funnel", {})
        lines.append("Models: %d registered, %d screened, %d survivors, %d structurally-dead" % (
            f.get("registry", 0), f.get("screened_stage_a", 0), f.get("survivors_stage_a", 0), f.get("structurally_dead", 0)))
    except Exception:
        pass
    try:
        s = ZONES["system"]()
        lat = s.get("latency", {})
        cert = s.get("certification", {})
        lines.append("System: latency lane=%s order_ack_p99=%sms; certification=%s; exec_mode=%s" % (
            lat.get("recommended_lane"), lat.get("order_ack_p99_ms"),
            cert.get("certification_status"), (s.get("execution", {}) or {}).get("execution_mode")))
    except Exception:
        pass
    try:
        a = ZONES["autonomy"]()
        lines.append("Autonomy: master_enabled=%s breaker_frozen=%s" % (
            a.get("master_enabled"), (a.get("breaker", {}) or {}).get("frozen")))
    except Exception:
        pass
    return "\n".join(lines) or "(state unavailable)"


def _kg_runs(query: str) -> list:
    """Best-effort backtest run-history. Degrades to [] if the KG/sig is unavailable."""
    try:
        from data_layer.kg.query import find_similar_runs  # type: ignore

        res = find_similar_runs(query)  # type: ignore[call-arg]
        return list(res)[:5] if res else []
    except Exception:
        return []


def build_context(query: str) -> dict:
    return {"state": _state_summary(), "rag": vault_rag.retrieve(query, k=4), "runs": _kg_runs(query)}


def build_messages(query: str, ctx: dict) -> list[dict]:
    rag = "\n\n".join(f"[{d['title']}]\n{d['snippet']}" for d in ctx.get("rag", []))
    runs = json.dumps(ctx.get("runs", []))[:1500]
    user = (
        f"SYSTEM STATE (live):\n{ctx['state']}\n\n"
        f"VAULT CONTEXT (ontology + retrieved):\n{rag or '(none)'}\n\n"
        f"RUN HISTORY:\n{runs}\n\n"
        f"QUESTION:\n{query}\n\n"
        "Answer as a read-only advisor. Cite the vault titles you used."
    )
    return [{"role": "system", "content": _PERSONA}, {"role": "user", "content": user}]


def _sse(obj: dict) -> str:
    return f"data: {json.dumps(obj)}\n\n"


async def stream_chat(query: str) -> AsyncIterator[str]:
    # Build context/messages inside the try so a vault/RAG/serialization failure
    # is framed as an SSE error event (matching the ollama-failure path below)
    # rather than escaping the generator mid-stream as a truncated, frameless
    # response — StreamingResponse has already committed HTTP 200 by now.
    try:
        ctx = build_context(query)
        yield _sse({"type": "context", "rag": [d["path"] for d in ctx["rag"]],
                    "rag_titles": [d["title"] for d in ctx["rag"]], "runs": len(ctx["runs"])})
        model, host = _model_and_host()
        messages = build_messages(query, ctx)
    except Exception as exc:
        yield _sse({"type": "error", "detail": f"context build failed: {exc}"})
        return
    payload = {"model": model, "messages": messages, "stream": True,
               "options": {"temperature": float(os.environ.get("HFT3_CHAT_TEMPERATURE", "0.4"))}}
    try:
        import httpx

        async with httpx.AsyncClient(timeout=httpx.Timeout(600.0, connect=10.0)) as client:
            async with client.stream("POST", f"{host}/api/chat", json=payload) as resp:
                if resp.status_code >= 400:
                    body = (await resp.aread()).decode("utf-8", "ignore")[:300]
                    yield _sse({"type": "error", "detail": f"ollama {resp.status_code}: {body}"})
                    return
                async for line in resp.aiter_lines():
                    if not line.strip():
                        continue
                    try:
                        obj = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    tok = (obj.get("message") or {}).get("content")
                    if tok:
                        yield _sse({"type": "token", "text": tok})
                    if obj.get("done"):
                        break
    except Exception as exc:
        yield _sse({"type": "error", "detail": f"chat failed: {exc} (is ollama running on {host}?)"})
        return
    yield _sse({"type": "done"})
