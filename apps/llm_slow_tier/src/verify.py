"""Deterministic verifier for F1 session labels.

The verifier applies binding code rules to the model's proposed label.  In
every conflict between model text and tape statistics, tape statistics win.

Rules (from LLM_SLOW_TIER.md §6):
1. Schema check — label in enum, drivers list[str] max 5, confidence float 0-1.
2. Calm override — if label=="calm" and (any z > z_stress OR gap_flags > 0 OR
   reconnects > 2) -> conflict_review with reason "tape_contradicts_calm".
3. Stress-but-quiet override — if label in {war_escalation, macro_event} and
   all z < z_quiet AND no calendar hits AND GDELT empty -> conflict_review with
   reason "tape_does_not_support_stress_label".
4. Insufficient-evidence override — if more than
   cfg.verify_insufficient_baseline_fraction of symbols have the
   insufficient_baseline flag AND no calendar hits AND (no GDELT records OR a
   gdelt_error note) -> conflict_review with reason
   "insufficient_evidence: no baseline, no news corroboration".
   Fires regardless of the model label.
5. Accept — if none of the above fires.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List

from .config import SlowTierConfig
from .digest import Digest
from .labeler import LabelResult, _VALID_LABELS


# ---------------------------------------------------------------------------
# Verifier result
# ---------------------------------------------------------------------------

@dataclass
class VerifierResult:
    verdict: str          # "accept" or "conflict_review"
    final_label: str      # equals model label on accept; "conflict_review" otherwise
    reasons: List[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _schema_errors(result: LabelResult) -> List[str]:
    """Return a list of schema violations in the LabelResult."""
    errors: List[str] = []

    if result.label not in _VALID_LABELS:
        errors.append(f"invalid_label: {result.label!r}")

    if not isinstance(result.drivers, list):
        errors.append("drivers_not_a_list")
    elif len(result.drivers) > 5:
        errors.append(f"drivers_too_many: {len(result.drivers)}")

    if not isinstance(result.confidence, float):
        errors.append(f"confidence_not_float: {result.confidence!r}")
    elif not (0.0 <= result.confidence <= 1.0):
        errors.append(f"confidence_out_of_range: {result.confidence}")

    if result.parse_error:
        errors.append(f"parse_error: {result.parse_error}")

    return errors


def _max_zscore(digest: Digest) -> float:
    """Return the maximum absolute z-score across all symbols (0.0 if none)."""
    if not digest.trade_count_zscores:
        return 0.0
    return max(abs(z) for z in digest.trade_count_zscores.values())


def _all_zscores_below(digest: Digest, threshold: float) -> bool:
    """Return True if all per-symbol z-scores (absolute value) are below threshold."""
    if not digest.trade_count_zscores:
        return True
    return all(abs(z) < threshold for z in digest.trade_count_zscores.values())


def _any_zscore_above(digest: Digest, threshold: float) -> bool:
    """Return True if any per-symbol z-score (absolute value) exceeds threshold."""
    if not digest.trade_count_zscores:
        return False
    return any(abs(z) > threshold for z in digest.trade_count_zscores.values())


# ---------------------------------------------------------------------------
# Main verifier
# ---------------------------------------------------------------------------

def verify_label_result(
    result: LabelResult,
    digest: Digest,
    sources_summary: Dict[str, Any],
    cfg: SlowTierConfig,
) -> VerifierResult:
    """Apply all verifier rules to a LabelResult.

    Returns a VerifierResult with verdict 'accept' or 'conflict_review'.
    Tape statistics always outrank model text.
    """
    reasons: List[str] = []

    # --- Rule 1: schema check ---
    schema_errors = _schema_errors(result)
    if schema_errors:
        reasons.extend(schema_errors)
        return VerifierResult(
            verdict="conflict_review",
            final_label="conflict_review",
            reasons=reasons,
        )

    label = result.label

    # --- Rule 2: calm override ---
    if label == "calm":
        calm_violations: List[str] = []
        if _any_zscore_above(digest, cfg.z_stress):
            calm_violations.append(
                f"z_score_exceeds_z_stress_{cfg.z_stress}"
            )
        if digest.total_gap_flags > 0:
            calm_violations.append(
                f"gap_flags_nonzero_{digest.total_gap_flags}"
            )
        if digest.total_reconnects > 2:
            calm_violations.append(
                f"reconnects_above_2_{digest.total_reconnects}"
            )
        if calm_violations:
            reasons.append("tape_contradicts_calm")
            reasons.extend(calm_violations)
            return VerifierResult(
                verdict="conflict_review",
                final_label="conflict_review",
                reasons=reasons,
            )

    # --- Rule 3: stress-but-quiet override ---
    _stress_labels = {"war_escalation", "macro_event"}
    if label in _stress_labels:
        calendar_hits: List[str] = sources_summary.get("calendar_hits") or []
        gdelt_records: List[Any] = sources_summary.get("gdelt_top_events") or []

        quiet = _all_zscores_below(digest, cfg.z_quiet)
        no_calendar = len(calendar_hits) == 0
        no_gdelt = len(gdelt_records) == 0

        if quiet and no_calendar and no_gdelt:
            reasons.append("tape_does_not_support_stress_label")
            reasons.append(
                f"all_z_below_z_quiet_{cfg.z_quiet}_no_calendar_no_gdelt"
            )
            return VerifierResult(
                verdict="conflict_review",
                final_label="conflict_review",
                reasons=reasons,
            )

    # --- Rule 4: insufficient-evidence override ---
    # Fires when the tape cannot be trusted (no baseline for most symbols) AND
    # there is no external corroboration (no calendar hits and no GDELT data).
    n_symbols = len(digest.symbols)
    if n_symbols > 0:
        insuff_count = len(digest.insufficient_baseline)
        insuff_fraction = insuff_count / n_symbols

        cal_hits_ie: List[str] = sources_summary.get("calendar_hits") or []
        gdelt_recs_ie: List[Any] = sources_summary.get("gdelt_top_events") or []
        gdelt_error_ie: Any = sources_summary.get("gdelt_error")

        no_calendar_ie = len(cal_hits_ie) == 0
        no_gdelt_ie = (len(gdelt_recs_ie) == 0) or bool(gdelt_error_ie)

        if (
            insuff_fraction > cfg.verify_insufficient_baseline_fraction
            and no_calendar_ie
            and no_gdelt_ie
        ):
            reasons.append(
                "insufficient_evidence: no baseline, no news corroboration"
            )
            return VerifierResult(
                verdict="conflict_review",
                final_label="conflict_review",
                reasons=reasons,
            )

    # --- Rule 5: accept ---
    return VerifierResult(
        verdict="accept",
        final_label=label,
        reasons=[],
    )
