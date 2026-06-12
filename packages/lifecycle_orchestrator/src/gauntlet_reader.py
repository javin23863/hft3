"""Disposer adapter — read a Stage-B universe_result.json into a pass/fail verdict.

The gauntlet (scripts/run_event_universe.py) writes a ``robustness`` block
({dsr_by_cell, pbo, bootstrap_by_cell, fee_stress_by_cell}) and a ``corrections``
block (Holm/BH). This parses, per cell, the five robustness signals and reduces
them to one verdict. Fail-closed: any missing/unparseable signal => that signal
fails => the cell does not pass. The orchestrator advances a candidate ONLY on a
passing verdict.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

# Thresholds (mirrors the plan's gauntlet gate).
DSR_MIN = 0.0
PBO_MAX = 0.5
CI_LOWER_MIN = 0.0


def _f(value: Any) -> Optional[float]:
    try:
        if isinstance(value, bool):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _dig(d: Any, *keys: str) -> Any:
    """First present key in a dict (tolerant to schema variants)."""
    if not isinstance(d, dict):
        return None
    for k in keys:
        if k in d:
            return d[k]
    return None


@dataclass
class GauntletVerdict:
    slug: str
    holm_survivor: bool
    dsr: Optional[float]
    pbo: Optional[float]
    ci_lower: Optional[float]
    fee_x2_pass: bool
    passed: bool
    reasons: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "slug": self.slug, "holm_survivor": self.holm_survivor, "dsr": self.dsr,
            "pbo": self.pbo, "ci_lower": self.ci_lower, "fee_x2_pass": self.fee_x2_pass,
            "passed": self.passed, "reasons": self.reasons,
        }


def _holm_survivor(universe: dict, slug: str, event_type: Optional[str]) -> bool:
    corrections = universe.get("corrections")
    if not isinstance(corrections, dict):
        return False
    # corrections may be keyed by event_type, or hold a flat passed list.
    candidates = []
    if event_type and isinstance(corrections.get(event_type), dict):
        candidates.append(corrections[event_type])
    candidates.extend(v for v in corrections.values() if isinstance(v, dict))
    for block in candidates:
        holm = _dig(block, "holm", "holm_010", "holm_005")
        passed = None
        if isinstance(holm, dict):
            passed = _dig(holm, "passed_slugs", "passed", "survivors")
        passed = passed if passed is not None else _dig(block, "passed_slugs", "passed")
        if isinstance(passed, (list, tuple, set)) and slug in passed:
            return True
    return False


def read_verdict(universe: dict, slug: str, *, event_type: Optional[str] = None) -> GauntletVerdict:
    rob = universe.get("robustness") if isinstance(universe, dict) else None
    rob = rob if isinstance(rob, dict) else {}

    dsr_cell = _dig(rob.get("dsr_by_cell") or {}, slug) or {}
    dsr = _f(_dig(dsr_cell, "dsr", "deflated_sharpe", "value")) if isinstance(dsr_cell, dict) else _f(dsr_cell)

    pbo_block = rob.get("pbo")
    pbo = None
    if isinstance(pbo_block, dict):
        # pbo may be global or per-cell
        pbo = _f(_dig(pbo_block, "pbo", "value"))
        if pbo is None and slug in pbo_block:
            pbo = _f(_dig(pbo_block.get(slug) or {}, "pbo", "value"))
    else:
        pbo = _f(pbo_block)

    boot_cell = _dig(rob.get("bootstrap_by_cell") or {}, slug) or {}
    ci_lower = _f(_dig(boot_cell, "ci_lower", "lower", "ci_low")) if isinstance(boot_cell, dict) else None

    fee_cell = _dig(rob.get("fee_stress_by_cell") or {}, slug) or {}
    fee_x2 = bool(_dig(fee_cell, "fee_x2_pass", "fee_2x_pass", "passed")) if isinstance(fee_cell, dict) else False

    holm = _holm_survivor(universe, slug, event_type)

    reasons = []
    if not holm:
        reasons.append("not holm survivor")
    if dsr is None or dsr <= DSR_MIN:
        reasons.append(f"dsr {dsr} <= {DSR_MIN}")
    if pbo is None or pbo >= PBO_MAX:
        reasons.append(f"pbo {pbo} >= {PBO_MAX}")
    if ci_lower is None or ci_lower <= CI_LOWER_MIN:
        reasons.append(f"bootstrap ci_lower {ci_lower} <= {CI_LOWER_MIN}")
    if not fee_x2:
        reasons.append("fee-x2 stress fail")

    passed = not reasons
    return GauntletVerdict(slug, holm, dsr, pbo, ci_lower, fee_x2, passed, reasons)
