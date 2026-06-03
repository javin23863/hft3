"""Data-class tagging for HFT3 (Phase 6).

The pipeline must clearly distinguish five data classes:

  L3_MBO                 true Level 3 event/message data
  L3_ORDERBOOK_SNAPSHOT   periodic book snapshots (no MBO delta stream)
  TRADES_ONLY             only trade prints (no book updates)
  AGGREGATED_BARS         OHLC / volume bars
  SYNTHETIC_OR_DEGRADED   mock / replay / fallback data

Every run must be tagged with:
  - requested_data_class  (what the experiment asked for)
  - resolved_data_class   (what the data source actually provides)
  - downgrade_reason      (None if classes match, else a stable reason code)
  - symbols_affected      (list[str])
  - time_windows_affected (list[(start_ns, end_ns)])
  - validity_impact       (INFO / WARN / BLOCKING)
  - promotion_eligibility_impact
      - "eligible"   (classes match)
      - "demoted"    (resolved < requested, but experiment still valid
                       with documented caveats)
      - "ineligible" (resolved < required, candidate cannot promote)

Downgrades are NEVER silent. A candidate that requires L3_MBO but
gets TRADES_ONLY is demoted; the gate schema reflects the demotion
and the registry's promotion decision records the caveat.

This module is the **contract** between the data layer and the rest
of the pipeline. It does not know how to read NPZ files — it only
classifies what was requested, what was resolved, and what the
implications are.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional, Tuple


# ---------- enums ----------


class DataClass(str, Enum):
    """Five data classes the HFT3 pipeline distinguishes."""

    L3_MBO = "L3_MBO"
    L3_ORDERBOOK_SNAPSHOT = "L3_ORDERBOOK_SNAPSHOT"
    TRADES_ONLY = "TRADES_ONLY"
    AGGREGATED_BARS = "AGGREGATED_BARS"
    SYNTHETIC_OR_DEGRADED = "SYNTHETIC_OR_DEGRADED"


# Numeric "rank" of each class — higher = more information.
# Used to detect downgrades: if requested rank > resolved rank, the
# run is demoted.
_DATA_CLASS_RANK: dict[DataClass, int] = {
    DataClass.L3_MBO: 5,
    DataClass.L3_ORDERBOOK_SNAPSHOT: 4,
    DataClass.TRADES_ONLY: 3,
    DataClass.AGGREGATED_BARS: 2,
    DataClass.SYNTHETIC_OR_DEGRADED: 1,
}


def data_class_rank(dc: DataClass) -> int:
    return _DATA_CLASS_RANK[dc]


class ValidityImpact(str, Enum):
    INFO = "info"           # no impact; just an FYI
    WARN = "warn"           # observed, blocking=false; surfaces in report
    BLOCKING = "blocking"   # observed, blocking=true; cannot promote


class PromotionEligibility(str, Enum):
    ELIGIBLE = "eligible"       # requested == resolved
    DEMOTED = "demoted"         # resolved < requested, experiment still valid
    INELIGIBLE = "ineligible"   # resolved << required, candidate cannot promote


# ---------- stable reason codes (per Phase 6 spec) ----------


# Stable identifiers for downgrade reasons. These are the only valid
# values for `DataResolutionTag.downgrade_reason`.
REASON_REQUESTED_UNAVAILABLE = "DATA_REQUESTED_UNAVAILABLE"
REASON_PARTIAL_COVERAGE = "DATA_PARTIAL_COVERAGE"
REASON_TIME_BOUNDED = "DATA_TIME_BOUNDED"  # snapshot or windowed
REASON_SYMBOL_BOUNDED = "DATA_SYMBOL_BOUNDED"
REASON_FALLBACK_TO_SYNTHETIC = "DATA_FALLBACK_TO_SYNTHETIC"
REASON_NO_DOWNGRADE = ""  # sentinel: no downgrade occurred


# ---------- main record ----------


@dataclass
class DataResolutionTag:
    """The 7 required fields from the Phase 6 spec.

    Written to `artifacts/runs/{run_id}/data_resolution.json` (per
    Phase 12) and consulted by the gate schema (Phase 8) when
    deciding REJECT / QUARANTINE / PROMOTE.
    """

    requested_data_class: DataClass
    resolved_data_class: DataClass
    symbols_affected: List[str] = field(default_factory=list)
    time_windows_affected: List[Tuple[int, int]] = field(default_factory=list)
    downgrade_reason: str = REASON_NO_DOWNGRADE
    validity_impact: ValidityImpact = ValidityImpact.INFO
    promotion_eligibility_impact: PromotionEligibility = PromotionEligibility.ELIGIBLE

    # Optional metadata
    source: str = ""          # e.g. "databento", "rithmic", "synthetic"
    notes: str = ""          # free-text for human review

    def to_dict(self) -> dict:
        d = {
            "requested_data_class": self.requested_data_class.value,
            "resolved_data_class": self.resolved_data_class.value,
            "symbols_affected": list(self.symbols_affected),
            "time_windows_affected": [
                [int(s), int(e)] for (s, e) in self.time_windows_affected
            ],
            "downgrade_reason": self.downgrade_reason,
            "validity_impact": self.validity_impact.value,
            "promotion_eligibility_impact": self.promotion_eligibility_impact.value,
            "source": self.source,
            "notes": self.notes,
        }
        return d

    @classmethod
    def from_dict(cls, raw: dict) -> "DataResolutionTag":
        if not isinstance(raw, dict):
            raise TypeError("data resolution tag must be a mapping")
        required = {
            "requested_data_class",
            "resolved_data_class",
            "symbols_affected",
            "time_windows_affected",
            "downgrade_reason",
            "validity_impact",
            "promotion_eligibility_impact",
        }
        missing = sorted(required - set(raw))
        if missing:
            raise ValueError(f"data resolution tag missing required fields: {missing}")
        tag = cls(
            requested_data_class=DataClass(raw["requested_data_class"]),
            resolved_data_class=DataClass(raw["resolved_data_class"]),
            symbols_affected=list(raw["symbols_affected"]),
            time_windows_affected=[
                (int(s), int(e)) for s, e in raw["time_windows_affected"]
            ],
            downgrade_reason=str(raw["downgrade_reason"]),
            validity_impact=ValidityImpact(raw["validity_impact"]),
            promotion_eligibility_impact=PromotionEligibility(
                raw["promotion_eligibility_impact"]
            ),
            source=str(raw.get("source", "")),
            notes=str(raw.get("notes", "")),
        )
        tag.validate()
        return tag

    @property
    def is_downgrade(self) -> bool:
        return data_class_rank(self.resolved_data_class) < data_class_rank(
            self.requested_data_class
        )

    @property
    def is_mismatch(self) -> bool:
        return self.requested_data_class != self.resolved_data_class

    def validate(self) -> None:
        if self.is_downgrade and self.downgrade_reason == REASON_NO_DOWNGRADE:
            raise ValueError(
                f"downgrade_reason must be set when requested "
                f"({self.requested_data_class.value}) != resolved "
                f"({self.resolved_data_class.value}). Use one of the "
                f"REASON_* constants from data_class.py."
            )
        valid_reasons = {
            REASON_NO_DOWNGRADE,
            REASON_REQUESTED_UNAVAILABLE,
            REASON_PARTIAL_COVERAGE,
            REASON_TIME_BOUNDED,
            REASON_SYMBOL_BOUNDED,
            REASON_FALLBACK_TO_SYNTHETIC,
        }
        if self.downgrade_reason not in valid_reasons:
            raise ValueError(
                f"downgrade_reason {self.downgrade_reason!r} not in {sorted(valid_reasons)}"
            )
        if not self.is_downgrade:
            if self.downgrade_reason != REASON_NO_DOWNGRADE:
                raise ValueError("downgrade_reason must be empty when resolved data is not downgraded")
            if self.validity_impact != ValidityImpact.INFO:
                raise ValueError("non-downgraded data must have info validity impact")
            if self.promotion_eligibility_impact != PromotionEligibility.ELIGIBLE:
                raise ValueError("non-downgraded data must be promotion eligible")
        elif self.resolved_data_class == DataClass.SYNTHETIC_OR_DEGRADED:
            if self.downgrade_reason != REASON_FALLBACK_TO_SYNTHETIC:
                raise ValueError("synthetic/degraded data must use DATA_FALLBACK_TO_SYNTHETIC")
            if self.validity_impact != ValidityImpact.BLOCKING:
                raise ValueError("synthetic/degraded data must have blocking validity impact")
            if self.promotion_eligibility_impact != PromotionEligibility.INELIGIBLE:
                raise ValueError("synthetic/degraded data must be ineligible for promotion")
        else:
            if self.validity_impact != ValidityImpact.WARN:
                raise ValueError("downgraded non-synthetic data must have warn validity impact")
            if self.promotion_eligibility_impact != PromotionEligibility.DEMOTED:
                raise ValueError("downgraded non-synthetic data must be demoted")


# ---------- factory helpers ----------


def make_tag(
    requested: str | DataClass,
    resolved: str | DataClass,
    *,
    source: str = "",
    symbols: Optional[List[str]] = None,
    time_windows: Optional[List[Tuple[int, int]]] = None,
    notes: str = "",
) -> DataResolutionTag:
    """Convenience constructor with auto-derived impact fields.

    Derivation rules:
      - If requested == resolved → eligible, no downgrade, info impact
      - If resolved is SYNTHETIC_OR_DEGRADED → ineligible, blocking
      - If resolved rank < requested rank (and not synthetic) → demoted,
        warn, reason = DATA_REQUESTED_UNAVAILABLE
      - Otherwise (only possible if classes match rank) → eligible
    """
    req = DataClass(requested) if isinstance(requested, str) else requested
    res = DataClass(resolved) if isinstance(resolved, str) else resolved
    if req == res:
        tag = DataResolutionTag(
            requested_data_class=req,
            resolved_data_class=res,
            symbols_affected=list(symbols or []),
            time_windows_affected=list(time_windows or []),
            downgrade_reason=REASON_NO_DOWNGRADE,
            validity_impact=ValidityImpact.INFO,
            promotion_eligibility_impact=PromotionEligibility.ELIGIBLE,
            source=source,
            notes=notes,
        )
    elif res == DataClass.SYNTHETIC_OR_DEGRADED:
        tag = DataResolutionTag(
            requested_data_class=req,
            resolved_data_class=res,
            symbols_affected=list(symbols or []),
            time_windows_affected=list(time_windows or []),
            downgrade_reason=REASON_FALLBACK_TO_SYNTHETIC,
            validity_impact=ValidityImpact.BLOCKING,
            promotion_eligibility_impact=PromotionEligibility.INELIGIBLE,
            source=source,
            notes=notes,
        )
    elif data_class_rank(res) < data_class_rank(req):
        tag = DataResolutionTag(
            requested_data_class=req,
            resolved_data_class=res,
            symbols_affected=list(symbols or []),
            time_windows_affected=list(time_windows or []),
            downgrade_reason=REASON_REQUESTED_UNAVAILABLE,
            validity_impact=ValidityImpact.WARN,
            promotion_eligibility_impact=PromotionEligibility.DEMOTED,
            source=source,
            notes=notes,
        )
    else:
        # Classes differ but same rank — treat as eligible
        tag = DataResolutionTag(
            requested_data_class=req,
            resolved_data_class=res,
            symbols_affected=list(symbols or []),
            time_windows_affected=list(time_windows or []),
            downgrade_reason=REASON_NO_DOWNGRADE,
            validity_impact=ValidityImpact.INFO,
            promotion_eligibility_impact=PromotionEligibility.ELIGIBLE,
            source=source,
            notes=notes,
        )
    return tag


# ---------- gate derivation ----------


def to_gate_result(tag: DataResolutionTag) -> "GateResult":  # type: ignore[name-defined]
    """Convert a data resolution tag into a `GateResult` (Phase 8).

    The gate is BLOCKING iff the tag's `validity_impact` is BLOCKING
    (i.e. the run is ineligible to promote). It is INFO otherwise.
    """
    from hft3.validation.gate_result import (
        GateCategory, GateResult, Severity,
    )
    blocking = tag.validity_impact == ValidityImpact.BLOCKING
    severity = Severity.BLOCKING if blocking else (
        Severity.WARN if tag.validity_impact == ValidityImpact.WARN else Severity.INFO
    )
    blocking_status = severity in (Severity.BLOCKING,)
    return GateResult(
        gate_name="data_resolution_eligibility",
        gate_category=GateCategory.DATA_INTEGRITY,
        metric_name="promotion_eligibility_impact",
        threshold=None,
        observed_value=tag.promotion_eligibility_impact.value,
        comparison_operator="==",
        pass_fail=(tag.promotion_eligibility_impact != PromotionEligibility.INELIGIBLE),
        severity=severity,
        blocking_status=blocking_status,
        reason_code=(
            "DATA_INELIGIBLE_FOR_PROMOTION"
            if tag.promotion_eligibility_impact == PromotionEligibility.INELIGIBLE
            else f"DATA_{tag.promotion_eligibility_impact.value.upper()}"
        ),
        artifact_reference="data_resolution.json",
    )
