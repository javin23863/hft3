"""OpenAI-compatible GPT-5.5 client for HFT3 packet-strict LLM calls."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any, Dict, List, Optional
from urllib import error, request


DEFAULT_MODEL = os.environ.get("HFT3_LLM_MODEL", "gpt-5.5")
DEFAULT_AAR_MODEL = os.environ.get("HFT3_AAR_LLM_MODEL", DEFAULT_MODEL)
DEFAULT_RESEARCH_MODEL = os.environ.get(
    "HFT3_RESEARCH_LLM_MODEL",
    os.environ.get("HFT3_PIPELINE_LLM_MODEL", DEFAULT_MODEL),
)
DEFAULT_PIPELINE_MODEL = DEFAULT_RESEARCH_MODEL
DEFAULT_MODEL_DEVELOPMENT_MODEL = os.environ.get(
    "HFT3_MODEL_DEVELOPMENT_LLM_MODEL",
    os.environ.get("HFT3_MODEL_DEV_LLM_MODEL", DEFAULT_MODEL),
)
DEFAULT_BASE_URL = os.environ.get("HFT3_LLM_BASE_URL", "https://api.openai.com/v1")
DEFAULT_TIMEOUT_S = float(os.environ.get("HFT3_LLM_TIMEOUT_S", "600"))
DEFAULT_REASONING_EFFORT = os.environ.get("HFT3_LLM_REASONING_EFFORT", "xhigh").lower()


@dataclass
class GenerateResult:
    text: Optional[str]
    error: Optional[str] = None
    model: Optional[str] = None
    elapsed_s: Optional[float] = None


def _api_key(api_key: str | None = None) -> str | None:
    return api_key or os.environ.get("HFT3_LLM_API_KEY") or os.environ.get("OPENAI_API_KEY")


def _chat_completions_url(base_url: str) -> str:
    url = base_url.rstrip("/")
    if url.endswith("/chat/completions"):
        return url
    return f"{url}/chat/completions"


def llm_available(*, api_key: str | None = None) -> bool:
    """Return whether the OpenAI-compatible endpoint is configured for calls."""

    return bool(_api_key(api_key))


def _extract_content(body: Dict[str, Any]) -> str:
    choices = body.get("choices")
    if not isinstance(choices, list) or not choices:
        return ""
    first = choices[0]
    if not isinstance(first, dict):
        return ""
    message = first.get("message")
    if not isinstance(message, dict):
        return ""
    content = message.get("content")
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts: List[str] = []
        for part in content:
            if isinstance(part, dict) and isinstance(part.get("text"), str):
                parts.append(part["text"])
        return "".join(parts).strip()
    return ""


def generate(
    system: str,
    user: str,
    *,
    model: str = DEFAULT_MODEL,
    base_url: str = DEFAULT_BASE_URL,
    api_key: str | None = None,
    timeout_s: float = DEFAULT_TIMEOUT_S,
    num_predict: int = 2048,
    format_json: bool = False,
    reasoning_effort: str = DEFAULT_REASONING_EFFORT,
    temperature: float | None = None,
    top_p: float | None = None,
) -> GenerateResult:
    import time

    key = _api_key(api_key)
    if not key:
        return GenerateResult(
            None,
            error="HFT3_LLM_API_KEY or OPENAI_API_KEY is not set",
            model=model,
            elapsed_s=0.0,
        )

    payload: Dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "stream": False,
        "max_completion_tokens": num_predict,
        "reasoning_effort": reasoning_effort,
    }
    if format_json:
        payload["response_format"] = {"type": "json_object"}
    if temperature is not None:
        payload["temperature"] = temperature
    if top_p is not None:
        payload["top_p"] = top_p

    data = json.dumps(payload).encode("utf-8")
    req = request.Request(
        _chat_completions_url(base_url),
        data=data,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"},
        method="POST",
    )
    t0 = time.time()
    try:
        with request.urlopen(req, timeout=timeout_s) as resp:
            body = json.loads(resp.read().decode("utf-8"))
        text = _extract_content(body)
        if not text:
            return GenerateResult(None, error="empty response", model=model, elapsed_s=time.time() - t0)
        return GenerateResult(text, model=model, elapsed_s=time.time() - t0)
    except error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:500]
        return GenerateResult(None, error=f"HTTP {exc.code}: {detail}", model=model, elapsed_s=time.time() - t0)
    except (error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        return GenerateResult(None, error=str(exc), model=model, elapsed_s=time.time() - t0)
