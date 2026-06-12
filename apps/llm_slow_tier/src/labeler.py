"""LLM labeler for the nightly session classification flow (F1).

Builds the prompt (system + user), calls ollama_client.generate(format_json=True),
parses the JSON response into a LabelResult.  The system prompt template is a
module-level constant so its SHA-256 hash is stable across runs.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from .config import SlowTierConfig
from .digest import Digest

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Prompt templates (module-level constants for stable hash computation)
# ---------------------------------------------------------------------------

SYSTEM_TEMPLATE = """\
You are a market-regime classifier for a US futures trading operation.
Your job is to read a structured summary of a single trade date and classify it
into exactly one regime label.

LABEL DEFINITIONS:
- war_escalation  : Active military escalation or geopolitical shock that visibly
                    influenced futures trading on this date.
- macro_event     : A scheduled macroeconomic release (FOMC, NFP, CPI, PPI, etc.)
                    was the dominant driver of tape activity.
- calm            : Low-volatility session with no notable macro or geopolitical driver.
                    Trade counts near baseline, no gap flags, no reconnects.
- mixed           : Multiple conflicting drivers present; no single regime dominated.
- conflict_review : You are uncertain, the evidence is contradictory, or you cannot
                    assign one of the above four labels with reasonable confidence.

OUTPUT FORMAT (JSON only, no prose):
{
  "label": "<one of the five labels above>",
  "drivers": ["<brief driver string>", ...],
  "confidence": <float 0.0-1.0>
}

CONSTRAINTS:
- drivers: list of strings, maximum 5 elements.
- confidence: float in [0.0, 1.0].
- Do not output anything outside the JSON object.
- Tape statistics outrank interpretive text. If the tape says calm (low z-scores,
  no gaps, no reconnects), do NOT label it war_escalation or macro_event.
"""

USER_TEMPLATE = """\
Trade date: {trade_date}

=== TAPE DIGEST ===
Symbols captured: {n_symbols}
Total trades: {total_trades}
Total records: {total_records}
Total quotes: {total_quotes}
Gap flag count: {total_gap_flags}
MD drops: {total_drops}
Reconnects: {total_reconnects}

Per-symbol trade-count z-scores (vs trailing 20-session baseline):
{zscore_lines}
Symbols with insufficient baseline (<5 prior sessions): {insufficient_baseline}

=== CALENDAR HITS ===
{calendar_lines}

=== GDELT TOP EVENTS ===
{gdelt_lines}

Classify this trade date. Respond with JSON only.
"""


# Stable 12-char hex hash of the system template — included in every output
# record so consumers can detect when the prompt was changed.
PROMPT_TEMPLATE_HASH: str = hashlib.sha256(SYSTEM_TEMPLATE.encode("utf-8")).hexdigest()[:12]


# ---------------------------------------------------------------------------
# Data class for labeler output
# ---------------------------------------------------------------------------

@dataclass
class LabelResult:
    label: str
    drivers: List[str]
    confidence: float
    raw_json: str          # The raw text returned by the model (for logging)
    parse_error: Optional[str] = None  # Non-None if JSON parsing failed


# ---------------------------------------------------------------------------
# Prompt construction helpers
# ---------------------------------------------------------------------------

def _zscore_lines(digest: Digest) -> str:
    if not digest.trade_count_zscores:
        return "  (no symbols)"
    lines = []
    for sym in sorted(digest.trade_count_zscores):
        z = digest.trade_count_zscores[sym]
        insuff = " [insufficient_baseline]" if sym in digest.insufficient_baseline else ""
        lines.append(f"  {sym}: z={z:+.2f}{insuff}")
    return "\n".join(lines)


def _calendar_lines(calendar_hits: List[str]) -> str:
    if not calendar_hits:
        return "  (no scheduled releases)"
    return "\n".join(f"  - {hit}" for hit in calendar_hits)


def _gdelt_lines(gdelt_records: List[Dict[str, Any]]) -> str:
    """Format GDELT article records for the prompt.

    Handles the DOC 2.0 article shape {title, domain, seendate}.
    """
    if not gdelt_records:
        return "  (no GDELT records)"
    lines = []
    for rec in gdelt_records:
        title = rec.get("title") or "(no title)"
        domain = rec.get("domain") or ""
        seendate = rec.get("seendate") or ""
        domain_part = f" [{domain}]" if domain else ""
        date_part = f" ({seendate})" if seendate else ""
        lines.append(f"  {title}{domain_part}{date_part}")
    return "\n".join(lines)


def build_prompt(
    digest: Digest,
    sources_summary: Dict[str, Any],
) -> tuple[str, str]:
    """Return (system_text, user_text) ready for ollama_client.generate()."""
    calendar_hits: List[str] = sources_summary.get("calendar_hits") or []
    gdelt_records: List[Dict[str, Any]] = sources_summary.get("gdelt_top_events") or []

    user = USER_TEMPLATE.format(
        trade_date=digest.trade_date,
        n_symbols=digest.n_symbols,
        total_trades=digest.total_trades,
        total_records=digest.total_records,
        total_quotes=digest.total_quotes,
        total_gap_flags=digest.total_gap_flags,
        total_drops=digest.total_drops,
        total_reconnects=digest.total_reconnects,
        zscore_lines=_zscore_lines(digest),
        insufficient_baseline=(
            ", ".join(digest.insufficient_baseline) if digest.insufficient_baseline else "none"
        ),
        calendar_lines=_calendar_lines(calendar_hits),
        gdelt_lines=_gdelt_lines(gdelt_records),
    )

    return SYSTEM_TEMPLATE, user


# ---------------------------------------------------------------------------
# JSON parsing
# ---------------------------------------------------------------------------

_VALID_LABELS = frozenset(
    {"war_escalation", "macro_event", "calm", "mixed", "conflict_review"}
)


def _parse_label_json(text: str) -> LabelResult:
    """Parse the model's JSON output into a LabelResult.

    On any parse error, returns a LabelResult with label='conflict_review',
    confidence=0.0, and parse_error set.
    """
    raw = text.strip()
    try:
        obj = json.loads(raw)
    except json.JSONDecodeError as exc:
        return LabelResult(
            label="conflict_review",
            drivers=[],
            confidence=0.0,
            raw_json=raw,
            parse_error=f"json_decode_error: {exc}",
        )

    if not isinstance(obj, dict):
        return LabelResult(
            label="conflict_review",
            drivers=[],
            confidence=0.0,
            raw_json=raw,
            parse_error="response_not_a_json_object",
        )

    label = obj.get("label", "")
    drivers = obj.get("drivers", [])
    confidence = obj.get("confidence", 0.0)

    # Coerce types tolerantly but record parse errors for the verifier
    errors = []

    if not isinstance(label, str) or label not in _VALID_LABELS:
        errors.append(f"invalid_label: {label!r}")
        label = "conflict_review"

    if not isinstance(drivers, list):
        errors.append("drivers_not_a_list")
        drivers = []
    else:
        drivers = [str(d) for d in drivers]

    try:
        confidence = float(confidence)
        if not (0.0 <= confidence <= 1.0):
            errors.append(f"confidence_out_of_range: {confidence}")
            confidence = max(0.0, min(1.0, confidence))
    except (TypeError, ValueError):
        errors.append(f"confidence_not_float: {obj.get('confidence')!r}")
        confidence = 0.0

    return LabelResult(
        label=label,
        drivers=drivers[:5],
        confidence=confidence,
        raw_json=raw,
        parse_error=("; ".join(errors) if errors else None),
    )


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def run_labeler(
    digest: Digest,
    sources_summary: Dict[str, Any],
    *,
    model: str,
    cfg: SlowTierConfig,
) -> Optional[LabelResult]:
    """Build the prompt, call ollama, parse the result.

    Returns None only if ollama itself is unreachable (not on parse errors —
    those produce a conflict_review LabelResult with parse_error set).

    The GPU->CPU retry is handled by the shared ``ollama_call`` helper in
    ``src/llm_call.py``; this function delegates to it rather than repeating
    the retry logic inline.
    """
    from .llm_call import ollama_call

    system_text, user_text = build_prompt(digest, sources_summary)

    log.info("calling ollama model=%s", model)
    text, error = ollama_call(
        system_text,
        user_text,
        model=model,
        cfg=cfg,
        format_json=True,
    )

    if error:
        log.error("ollama generate error: %s", error)
        return None

    if not text:
        log.error("ollama returned empty text")
        return None

    label_result = _parse_label_json(text)
    if label_result.parse_error:
        log.warning("label parse error: %s", label_result.parse_error)

    return label_result
