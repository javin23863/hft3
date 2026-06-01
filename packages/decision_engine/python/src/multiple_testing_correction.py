"""Multiple hypothesis testing correction for champion promotion gate.

Traces to: Ultimate_Quantitative_Finance_Researcher.pdf — walk-forward validation
with proper statistical controls. Implements Bonferroni, Holm-Bonferroni (FWER),
and Benjamini-Hochberg (FDR) procedures.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

import numpy as np
from scipy import stats


@dataclass
class HypothesisTestResult:
    slug: str
    legacy_id: str
    metric_name: str
    metric_value: float
    p_value: float
    t_statistic: float
    num_trades: int

    def __post_init__(self) -> None:
        self.is_significant: bool = False
        self.adjusted_alpha: float = 1.0


@dataclass
class ChampionReport:
    passed_slugs: List[str] = field(default_factory=list)
    failed_slugs: List[str] = field(default_factory=list)
    method: str = "holm"
    original_alpha: float = 0.05
    total_tested: int = 0
    sorted_results: List[HypothesisTestResult] = field(default_factory=list)


class MultipleTestingGate:
    """Given N hypotheses tested on the same holdout period, computes adjusted
    significance thresholds to control family-wise error rate or false discovery rate.
    """

    def __init__(self, alpha: float = 0.05):
        self.alpha = alpha

    def apply_correction(
        self,
        results: List[HypothesisTestResult],
        method: str = "holm",
    ) -> ChampionReport:
        n = len(results)
        if n == 0:
            return ChampionReport(method=method, original_alpha=self.alpha)

        sorted_by_metric = sorted(results, key=lambda r: r.p_value)
        report = ChampionReport(
            method=method,
            original_alpha=self.alpha,
            total_tested=n,
            sorted_results=sorted_by_metric,
        )

        if method == "bonferroni":
            adjusted = self.alpha / n
            for r in sorted_by_metric:
                r.adjusted_alpha = adjusted
                r.is_significant = r.p_value < adjusted

        elif method == "holm":
            for i, r in enumerate(sorted_by_metric):
                rank = i + 1
                adjusted = self.alpha / (n - rank + 1)
                r.adjusted_alpha = adjusted
                r.is_significant = r.p_value < adjusted

        elif method == "benjamini_hochberg":
            for i, r in enumerate(sorted_by_metric):
                rank = i + 1
                adjusted = (rank / n) * self.alpha
                r.adjusted_alpha = adjusted
                r.is_significant = r.p_value < adjusted

        else:
            for r in sorted_by_metric:
                r.adjusted_alpha = self.alpha
                r.is_significant = r.p_value < self.alpha

        report.passed_slugs = [r.slug for r in sorted_by_metric if r.is_significant]
        report.failed_slugs = [r.slug for r in sorted_by_metric if not r.is_significant]
        return report

    def champion_eligible(
        self,
        model_metrics: List[dict],
        method: str = "holm",
    ) -> ChampionReport:
        test_results: List[HypothesisTestResult] = []
        for m in model_metrics:
            num_trades = m.get("num_trades", 0)
            if num_trades < 5:
                continue
            metric = m.get("expectancy", 0.0)
            raw_metrics = m.get("trade_pnls", [])
            if num_trades >= 2 and len(raw_metrics) >= 2:
                arr = np.array(raw_metrics, dtype=float)
                se = float(np.std(arr, ddof=1) / np.sqrt(len(arr)))
                if se < 1e-15:
                    t_stat = 0.0
                    p_val = 1.0
                else:
                    t_stat = float(np.mean(arr) / se)
                    p_val = 2.0 * (1.0 - stats.t.cdf(abs(t_stat), df=len(arr) - 1))
                    p_val = min(max(p_val, 1e-15), 1.0)
            else:
                t_stat = 0.0
                p_val = 1.0

            test_results.append(HypothesisTestResult(
                slug=m.get("slug", ""),
                legacy_id=m.get("legacy_model_id", ""),
                metric_name="expectancy",
                metric_value=metric,
                p_value=p_val,
                t_statistic=t_stat,
                num_trades=num_trades,
            ))

        return self.apply_correction(test_results, method=method)

    @staticmethod
    def compute_p_value(
        metrics: List[float],
        null_mean: float = 0.0,
    ) -> float:
        if len(metrics) < 2:
            return 1.0
        arr = np.array(metrics, dtype=float)
        t_stat, p_val = stats.ttest_1samp(arr, null_mean)
        return float(p_val)
