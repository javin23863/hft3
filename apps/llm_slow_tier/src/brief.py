"""F2 — Morning Brief generator.

Produces a one-page markdown brief for the upcoming trading day.

Architecture follows the AlphaGeometry pattern:
- The model writes prose ONLY for "## Overnight drivers".
- All other sections ("## Today's scheduled events", "## Capture health",
  "## Yesterday's session label") are rendered deterministically from data
  by code.  The model never restates numbers.

If ollama is unavailable the brief is still written, with a notice and raw
headline bullets substituted for the prose section.

Output artifact:
    artifacts/research_cards/slow_tier/morning_brief_{date}.md

Log record appended to:
    artifacts/research_cards/slow_tier/brief_log.jsonl
    {date, model, generated_utc, gdelt_n, calendar_n, model_used: bool}
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from .config import SlowTierConfig
from .llm_call import ollama_call

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Prompt templates — module-level constants so they are stable across runs.
# The model produces prose ONLY for Overnight Drivers.  The template explicitly
# tells it not to include numbers (those come from deterministic code).
# ---------------------------------------------------------------------------

_SYSTEM_BRIEF = """\
You are a senior quantitative analyst writing a morning brief for a US futures
trading operation.  Your role is ONLY to write the "Overnight Drivers" section.
Write 2-4 sentences of prose summarising the most significant geopolitical and
macroeconomic themes visible in the overnight headlines provided.  Do NOT
include raw numbers, percentages, or statistics — those are filled in by code.
Do NOT write any other section.  Output plain text only (no markdown headers).
"""

_USER_BRIEF = """\
Trade date: {trade_date}

=== OVERNIGHT GDELT HEADLINES ({gdelt_n} records) ===
{headline_lines}

Write 2-4 sentences summarising the dominant overnight themes for the
Overnight Drivers section of the morning brief.
"""


# ---------------------------------------------------------------------------
# Capture-health section (deterministic — code-rendered)
# ---------------------------------------------------------------------------

def _render_capture_health(manifest_dir: Path) -> str:
    """Render the ## Capture health section from manifest files.

    Reads the latest manifest directory for the given date.  Returns a
    formatted markdown block listing symbols present, total records, gap flags,
    and reconnect counts.  If the directory is missing or empty, returns a note.
    """
    if not manifest_dir.is_dir():
        return "_No manifests found for this date._\n"

    rows: List[str] = []
    total_records = 0
    total_gap_flags = 0
    total_reconnects = 0

    for path in sorted(manifest_dir.glob("*.json")):
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(raw, dict):
            continue
        sym = raw.get("symbol", path.stem)
        records = int(raw.get("records") or 0)
        gap_flags = int(raw.get("max_queue_gap_flag_count") or 0)
        reconnects = int(raw.get("reconnects") or 0)
        total_records += records
        total_gap_flags += gap_flags
        total_reconnects += reconnects
        flag_str = " **[GAP FLAGS]**" if gap_flags else ""
        recon_str = f", reconnects={reconnects}" if reconnects else ""
        rows.append(f"- **{sym}**: {records:,} records{recon_str}{flag_str}")

    if not rows:
        return "_No valid manifests found for this date._\n"

    header = (
        f"Total records: **{total_records:,}** | "
        f"Gap flags: **{total_gap_flags}** | "
        f"Reconnects: **{total_reconnects}**\n"
    )
    return header + "\n" + "\n".join(rows) + "\n"


# ---------------------------------------------------------------------------
# Yesterday's session label section (deterministic)
# ---------------------------------------------------------------------------

def _render_yesterday_label(labels_path: Path, trade_date: str) -> str:
    """Read the last record from session_labels.jsonl whose date < trade_date.

    Returns a formatted markdown string.
    """
    if not labels_path.is_file():
        return "_No session labels file found._\n"

    last_record: Optional[Dict[str, Any]] = None
    try:
        with labels_path.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                rec_date = rec.get("trade_date", "")
                if rec_date < trade_date:
                    last_record = rec
    except OSError:
        return "_Could not read session_labels.jsonl._\n"

    if last_record is None:
        return "_No prior session label available._\n"

    label = last_record.get("label", "unknown")
    confidence = last_record.get("confidence", 0.0)
    verdict = last_record.get("verifier_verdict", "unknown")
    drivers = last_record.get("drivers") or []
    rec_date = last_record.get("trade_date", "unknown")
    drivers_str = "; ".join(drivers) if drivers else "none recorded"

    return (
        f"**Date**: {rec_date} | "
        f"**Label**: `{label}` | "
        f"**Confidence**: {confidence:.2f} | "
        f"**Verifier**: {verdict}\n\n"
        f"**Drivers**: {drivers_str}\n"
    )


# ---------------------------------------------------------------------------
# Scheduled events section (deterministic)
# ---------------------------------------------------------------------------

def _render_scheduled_events(calendar_hits: List[str]) -> str:
    """Render the ## Today's scheduled events section."""
    if not calendar_hits:
        return "_No scheduled macro releases today._\n"
    return "\n".join(f"- {hit}" for hit in calendar_hits) + "\n"


# ---------------------------------------------------------------------------
# GDELT headline formatting for prompt
# ---------------------------------------------------------------------------

def _format_headlines(gdelt_records: List[Dict[str, Any]]) -> str:
    """Format GDELT article records as bullet lines for the model prompt."""
    if not gdelt_records:
        return "  (no headlines available)"
    lines = []
    for rec in gdelt_records:
        title = (rec.get("title") or "").strip() or "(no title)"
        domain = rec.get("domain") or ""
        domain_part = f" [{domain}]" if domain else ""
        lines.append(f"- {title}{domain_part}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main brief generator
# ---------------------------------------------------------------------------

def generate_brief(
    trade_date: str,
    gdelt_records: List[Dict[str, Any]],
    gdelt_error: Optional[str],
    calendar_hits: List[str],
    manifest_dir: Path,
    labels_path: Path,
    *,
    model: str,
    cfg: SlowTierConfig,
) -> str:
    """Generate the full morning brief markdown string.

    The model writes prose only for the Overnight Drivers section.
    The other three sections are rendered deterministically by code.

    Parameters
    ----------
    trade_date:
        The trade date for this brief (YYYY-MM-DD).
    gdelt_records:
        GDELT article records (may be empty on offline/error).
    gdelt_error:
        Error note from GDELT fetch, or None.
    calendar_hits:
        Scheduled release event types for today.
    manifest_dir:
        Directory containing *.manifest.json files for trade_date.
    labels_path:
        Path to session_labels.jsonl.
    model:
        Ollama model name.
    cfg:
        Slow-tier config.

    Returns
    -------
    str
        Full markdown string for the brief.
    """
    # --- Section: Overnight Drivers (model-written prose) ---
    headline_lines = _format_headlines(gdelt_records)
    gdelt_n = len(gdelt_records)

    model_used = False
    overnight_drivers_prose = ""

    if gdelt_error and not gdelt_records:
        # GDELT completely unavailable — skip model call, use raw notice.
        overnight_drivers_prose = (
            f"_(model unavailable — headlines listed raw)_\n\n"
            + headline_lines
        )
    else:
        system_text = _SYSTEM_BRIEF
        user_text = _USER_BRIEF.format(
            trade_date=trade_date,
            gdelt_n=gdelt_n,
            headline_lines=headline_lines,
        )
        text, error = ollama_call(
            system_text,
            user_text,
            model=model,
            cfg=cfg,
            format_json=False,
        )
        if error or not text:
            log.warning("ollama unavailable for morning brief: %s", error)
            overnight_drivers_prose = (
                "_(model unavailable — headlines listed raw)_\n\n"
                + headline_lines
            )
        else:
            overnight_drivers_prose = text.strip()
            model_used = True

    # --- Section: Today's Scheduled Events (deterministic) ---
    events_section = _render_scheduled_events(calendar_hits)

    # --- Section: Capture Health (deterministic) ---
    capture_health_section = _render_capture_health(manifest_dir)

    # --- Section: Yesterday's Session Label (deterministic) ---
    yesterday_label_section = _render_yesterday_label(labels_path, trade_date)

    # --- Assemble the brief ---
    brief = (
        f"# Morning Brief — {trade_date}\n\n"
        f"## Overnight drivers\n\n"
        f"{overnight_drivers_prose}\n\n"
        f"## Today's scheduled events\n\n"
        f"{events_section}\n"
        f"## Capture health\n\n"
        f"{capture_health_section}\n"
        f"## Yesterday's session label\n\n"
        f"{yesterday_label_section}"
    )

    return brief, model_used


# ---------------------------------------------------------------------------
# Artifact writer
# ---------------------------------------------------------------------------

def write_brief(
    brief_text: str,
    trade_date: str,
    *,
    artifact_root: str,
    model: str,
    gdelt_n: int,
    calendar_n: int,
    model_used: bool,
) -> tuple[Path, Path]:
    """Write the brief markdown and append a log record.

    Returns (brief_path, log_path).
    """
    out_dir = Path(artifact_root)
    out_dir.mkdir(parents=True, exist_ok=True)

    brief_path = out_dir / f"morning_brief_{trade_date}.md"
    brief_path.write_text(brief_text, encoding="utf-8")
    log.info("wrote morning brief: %s", brief_path)

    log_path = out_dir / "brief_log.jsonl"
    record = {
        "date": trade_date,
        "model": model,
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "gdelt_n": gdelt_n,
        "calendar_n": calendar_n,
        "model_used": model_used,
    }
    with open(log_path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")

    return brief_path, log_path


# ---------------------------------------------------------------------------
# Verifier — schema check only (§4.4)
# ---------------------------------------------------------------------------

_REQUIRED_HEADINGS = [
    "## Overnight drivers",
    "## Today's scheduled events",
    "## Capture health",
    "## Yesterday's session label",
]


def verify_brief(markdown: str) -> List[str]:
    """Return a list of missing section headings.  Empty list = valid."""
    missing = []
    for heading in _REQUIRED_HEADINGS:
        if heading.lower() not in markdown.lower():
            missing.append(heading)
    return missing
