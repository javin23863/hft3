"""Deterministic narrative renderer for AAR responses.

The LLM no longer writes `narrative_md`. Instead, the AAR response renders
a markdown summary from the packet + symbolic gate result + closed-claim
kg_annotations. This is the only narrative path; the LLM cannot inject
free-form claims. See `docs/research/PACKET_LLM_CONTRACT.md` (planned
phase 8) for the closed-claim contract.
"""

from __future__ import annotations

from typing import Any, Dict, List


def _fmt_float(v: Any, digits: int = 2) -> str:
    try:
        return f"{float(v):.{digits}f}"
    except (TypeError, ValueError):
        return "n/a"


def _section_latency_authority(lat: Dict[str, Any]) -> List[str]:
    lines = ["## Latency authority", ""]
    rows = [
        ("breakeven_us", "Breakeven (µs)"),
        ("latency_profitability_buffer_us", "Profitability buffer (µs)"),
        ("feed_to_ack_us_p50", "Feed→Ack p50 (µs)"),
        ("feed_to_ack_us_p99", "Feed→Ack p99 (µs)"),
        ("python_research_runtime_us", "Python research runtime (µs, non-authoritative)"),
    ]
    for key, label in rows:
        if key in lat:
            lines.append(f"- **{label}**: `{lat[key]}`")
    if lat.get("promote_candidate") is True:
        lines.append("- **promote_candidate**: true")
    if lat.get("robustness_passed") is True:
        lines.append("- **robustness_passed**: true")
    wfc = lat.get("wfc_status")
    if wfc is not None:
        lines.append(f"- **wfc_status**: `{wfc}`")
    return lines


def _section_symbolic(symbolic: Dict[str, Any]) -> List[str]:
    passed = bool(symbolic.get("passed"))
    lines = [
        "## Symbolic gate (AlphaGeometry pattern)",
        "",
        f"**Result:** {'PASSED' if passed else 'FAILED'}",
        "",
    ]
    if not passed:
        lines.append("**Violations** (every violation carries a cite):")
        lines.append("")
        for cite in symbolic.get("violation_cites") or []:
            msg = cite.get("message", "<unknown>")
            c = cite.get("cite") or {}
            lines.append(
                f"- `{msg}` — cite: `{c.get('pdf', '?')} §{c.get('section', '?')} p.{c.get('page', '?')}`"
            )
        lines.append("")
    return lines


def _section_annotations(annotations: List[Dict[str, Any]]) -> List[str]:
    lines = ["## Closed-claim kg_annotations", ""]
    if not annotations:
        lines.append("_No closed-claim annotations emitted._")
        lines.append("")
        return lines
    for i, ann in enumerate(annotations, start=1):
        source_type = ann.get("source_type", "?")
        source_id = ann.get("source_id", "?")
        field = ann.get("field", "?")
        value = ann.get("value")
        lines.append(f"{i}. **{source_type}** `{source_id}.{field}` = `{value}`")
        cite = ann.get("cite")
        if cite:
            lines.append(
                f"   cite: `{cite.get('pdf', '?')} §{cite.get('section', '?')} p.{cite.get('page', '?')}`"
            )
    lines.append("")
    return lines


def _section_decision(packet: Dict[str, Any], symbolic: Dict[str, Any]) -> List[str]:
    lat = packet.get("latency_authority") or {}
    promote = bool(lat.get("promote_candidate"))
    symbolic_ok = bool(symbolic.get("passed"))
    if not symbolic_ok:
        verdict = "FAIL_CLOSED (symbolic gate failed)"
    elif promote:
        verdict = "PROMOTE_CANDIDATE"
    else:
        verdict = "NO_PROMOTE"
    return [
        "## Decision",
        "",
        f"**Verdict:** `{verdict}`",
        "",
        f"- symbolic_passed: `{symbolic_ok}`",
        f"- promote_candidate: `{promote}`",
        "",
    ]


def render_deterministic_narrative(
    packet: Dict[str, Any],
    symbolic: Dict[str, Any],
    annotations: List[Dict[str, Any]],
) -> str:
    """Render the AAR narrative as deterministic markdown.

    The output is fully derived from the packet, symbolic gate result, and
    closed-claim annotations. The LLM has no input into the prose. The
    decision and citations are explicit and machine-checkable.
    """
    run_id = str(packet.get("run_id", "unknown"))
    evt = packet.get("event_context") or {}
    lines = [
        f"# After-action report — `{run_id}`",
        "",
        f"**Event:** `{evt.get('event_state', '?')}` (id `{evt.get('event_id', '?')}`)",
        "",
    ]
    lat = packet.get("latency_authority") or {}
    if lat:
        lines.extend(_section_latency_authority(lat))
        lines.append("")
    lines.extend(_section_symbolic(symbolic))
    lines.extend(_section_decision(packet, symbolic))
    lines.extend(_section_annotations(annotations))
    return "\n".join(lines).rstrip() + "\n"
