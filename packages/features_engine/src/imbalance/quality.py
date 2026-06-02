"""Data-quality checks for imbalance features."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List


@dataclass
class QualityCheckResult:
    check_id: str
    passed: bool
    detail: str = ""

    def to_dict(self) -> dict:
        return {"check_id": self.check_id, "passed": self.passed, "detail": self.detail}


@dataclass
class ImbalanceQualityReport:
    results: List[QualityCheckResult] = field(default_factory=list)
    promotion_blocked: bool = False

    @property
    def passed(self) -> bool:
        return all(r.passed for r in self.results) and not self.promotion_blocked

    def to_dict(self) -> dict:
        return {
            "passed": self.passed,
            "promotion_blocked": self.promotion_blocked,
            "results": [r.to_dict() for r in self.results],
        }


def run_quality_checks(
    *,
    timestamps_ns: List[int],
    spreads: List[float],
    book_states: List[str],
    feature_timestamps_ns: List[int],
    has_future_leak: bool = False,
) -> ImbalanceQualityReport:
    report = ImbalanceQualityReport()

    if len(timestamps_ns) >= 2:
        mono = all(timestamps_ns[i] <= timestamps_ns[i + 1] for i in range(len(timestamps_ns) - 1))
        report.results.append(
            QualityCheckResult("timestamp_monotonicity", mono, "event timestamps non-decreasing")
        )
    else:
        report.results.append(
            QualityCheckResult("timestamp_monotonicity", True, "insufficient samples")
        )

    crossed = sum(1 for s in book_states if s == "crossed")
    report.results.append(
        QualityCheckResult(
            "crossed_book_detection",
            crossed == 0,
            f"crossed_book_events={crossed}",
        )
    )

    locked = sum(1 for s in book_states if s == "locked")
    report.results.append(
        QualityCheckResult(
            "locked_book_detection",
            True,
            f"locked_book_events={locked}",
        )
    )

    bad_spread = sum(1 for s in spreads if s is not None and s < 0)
    report.results.append(
        QualityCheckResult(
            "impossible_spread_detection",
            bad_spread == 0,
            f"negative_spreads={bad_spread}",
        )
    )

    if feature_timestamps_ns and timestamps_ns:
        leak = any(ft > mt for ft, mt in zip(feature_timestamps_ns, timestamps_ns))
        report.results.append(
            QualityCheckResult(
                "feature_lookahead_leakage",
                not leak and not has_future_leak,
                "feature ts must not exceed book/trade ts",
            )
        )

    report.promotion_blocked = not report.passed
    return report
