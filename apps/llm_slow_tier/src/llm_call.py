"""Shared ollama call helper for the slow-tier LLM lane.

Wraps ``data_layer.llm.ollama_client.generate`` with the CUDA->CPU retry
pattern required by both F1 (labeler) and F2 (brief).  Both flows call
``ollama_call()``; neither duplicates the retry logic.

Design notes:
- Returns ``(text: str | None, error: str | None)``.  ``text`` is None only
  when ollama is unreachable or returns an empty response after retries.
- The GPU->CPU retry is triggered by "CUDA" appearing in the error string,
  matching the pattern originally inlined in src/labeler.py.
- CPU retry uses ``max(timeout_s, 900.0)`` to allow for slower inference.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional, Tuple

from .config import SlowTierConfig

log = logging.getLogger(__name__)


def ollama_call(
    system: str,
    user: str,
    *,
    model: str,
    cfg: SlowTierConfig,
    format_json: bool = False,
    options: Optional[Dict[str, Any]] = None,
) -> Tuple[Optional[str], Optional[str]]:
    """Call ollama; retry on CUDA failure with CPU fallback.

    Parameters
    ----------
    system:
        System-prompt text.
    user:
        User-prompt text.
    model:
        Fully-qualified ollama model name.
    cfg:
        Slow-tier config (provides host, timeout_s, num_predict).
    format_json:
        Whether to request JSON-forced output (``format_json=True``).
    options:
        Additional ollama options dict merged on top of defaults.

    Returns
    -------
    (text, error)
        ``text`` is the model's response string on success, None on failure.
        ``error`` is a short diagnostic string on failure, None on success.
    """
    from data_layer.llm.ollama_client import generate as ollama_generate

    def _call(extra_options: Optional[Dict[str, Any]] = None):
        merged = options.copy() if options else {}
        if extra_options:
            merged.update(extra_options)
        return ollama_generate(
            system,
            user,
            model=model,
            host=cfg.host,
            timeout_s=cfg.timeout_s,
            num_predict=cfg.num_predict,
            format_json=format_json,
            options=merged if merged else None,
        )

    result = _call()

    if result.error and "CUDA" in result.error:
        # GPU runner wedged under VRAM pressure; retry once on CPU.
        # Increase timeout to accommodate slower CPU inference.
        log.warning(
            "ollama GPU runner failed (%s); retrying on CPU", result.error[:120]
        )
        result = _call({"num_gpu": 0})
        # Use extended timeout for CPU retry by temporarily overriding timeout_s
        # via a fresh generate call with the higher value.
        from data_layer.llm.ollama_client import generate as _gen
        cpu_opts = {"num_gpu": 0}
        if options:
            cpu_opts.update(options)
        result = _gen(
            system,
            user,
            model=model,
            host=cfg.host,
            timeout_s=max(cfg.timeout_s, 900.0),
            num_predict=cfg.num_predict,
            format_json=format_json,
            options=cpu_opts,
        )

    if result.error:
        log.error("ollama error: %s", result.error)
        return None, result.error

    if not result.text:
        log.error("ollama returned empty text")
        return None, "empty_response"

    return result.text, None
