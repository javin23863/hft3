"""Ollama wrapper for pipeline LLM calls.

Runtime model only — not a vendored repo. OpenFoundry (ontology) and AlphaGeometry
(Google DeepMind symbolic reference) live under vendor/; see docs/research/VENDOR_BOUNDARIES.md.
Do not default to Gemma; pipeline uses glm-5.1:cloud unless HFT3_PIPELINE_LLM_MODEL is set.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any, Dict, Optional

from data_layer.llm.ollama_client import generate

DEFAULT_PIPELINE_MODEL = os.environ.get("HFT3_PIPELINE_LLM_MODEL", "glm-5.1:cloud")


def generate_json(
    system: str,
    user: str,
    *,
    model: str = DEFAULT_PIPELINE_MODEL,
) -> tuple[Optional[Dict[str, Any]], Optional[str]]:
    """Return parsed JSON object or (None, error)."""
    result = generate(system, user, model=model, num_predict=4096, format_json=True)
    if result.error or not result.text:
        return None, result.error or "empty response"
    text = result.text.strip()
    fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    if fence:
        text = fence.group(1).strip()
    try:
        return json.loads(text), None
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            try:
                return json.loads(text[start : end + 1]), None
            except json.JSONDecodeError as exc:
                return None, str(exc)
        return None, "response is not valid JSON"
