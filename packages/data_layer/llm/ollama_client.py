"""Legacy local Ollama client; HFT3 default LLM uses openai_compatible_client."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any, Dict, List, Optional
from urllib import error, request

DEFAULT_AAR_MODEL = os.environ.get("HFT3_LEGACY_OLLAMA_MODEL", "gemma4:31b-cloud")
DEFAULT_MODEL = DEFAULT_AAR_MODEL
DEFAULT_HOST = "http://127.0.0.1:11434"
DEFAULT_TIMEOUT_S = float(os.environ.get("HFT3_LEGACY_OLLAMA_TIMEOUT_S", "600"))


@dataclass
class GenerateResult:
    text: Optional[str]
    error: Optional[str] = None
    model: Optional[str] = None
    elapsed_s: Optional[float] = None


def _fetch_tags(host: str) -> List[str]:
    req = request.Request(f"{host.rstrip('/')}/api/tags", method="GET")
    with request.urlopen(req, timeout=5.0) as resp:
        body = json.loads(resp.read().decode("utf-8"))
    return [str(m.get("name", "")) for m in body.get("models", []) if m.get("name")]


def resolve_model(host: str = DEFAULT_HOST, preferred: str = DEFAULT_AAR_MODEL) -> Optional[str]:
    try:
        names = _fetch_tags(host)
    except (error.URLError, error.HTTPError, TimeoutError, OSError, json.JSONDecodeError):
        return None
    if preferred in names:
        return preferred
    base = preferred.split(":")[0]
    for name in names:
        if name == base or name.startswith(base + ":"):
            return name
    return None


def ollama_available(host: str = DEFAULT_HOST, model: str = DEFAULT_AAR_MODEL) -> bool:
    return resolve_model(host, model) is not None


def generate(
    system: str,
    user: str,
    *,
    model: str = DEFAULT_AAR_MODEL,
    host: str = DEFAULT_HOST,
    timeout_s: float = DEFAULT_TIMEOUT_S,
    num_predict: int = 2048,
    format_json: bool = False,
) -> GenerateResult:
    import time

    resolved = resolve_model(host, model)
    if resolved is None:
        return GenerateResult(None, error="model not found in ollama list", model=model, elapsed_s=0.0)
    payload: Dict[str, Any] = {
        "model": resolved,
        "stream": False,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "options": {"num_predict": num_predict, "temperature": 0.3},
    }
    if format_json:
        payload["format"] = "json"
    data = json.dumps(payload).encode("utf-8")
    req = request.Request(
        f"{host.rstrip('/')}/api/chat",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    t0 = time.time()
    try:
        with request.urlopen(req, timeout=timeout_s) as resp:
            body = json.loads(resp.read().decode("utf-8"))
        text = str(body.get("message", {}).get("content", "")).strip()
        if not text:
            return GenerateResult(None, error="empty response", model=resolved, elapsed_s=time.time() - t0)
        return GenerateResult(text, model=resolved, elapsed_s=time.time() - t0)
    except error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:500]
        return GenerateResult(None, error=f"HTTP {exc.code}: {detail}", model=resolved, elapsed_s=time.time() - t0)
    except (error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        return GenerateResult(None, error=str(exc), model=resolved, elapsed_s=time.time() - t0)
