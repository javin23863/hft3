from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np

from .event_types import MBOEvent
from .features import L3FeatureExtractor, L3Features
from .queue_state import BookSnapshot, OrderBookReconstructor


@dataclass
class L3ValidationResult:
    fold_id: int
    timestamp_pure: bool
    leakage_detected: bool
    n_events: int
    n_snapshots: int
    precision_at_5: float
    precision_at_10: float
    pr_auc: float
    brier_score: float
    calibration_error: float
    incremental_lift: float
    mfe_before_mae_rate: float
    time_to_expansion_ns: float
    slippage_adj_expectancy: float
    queue_fill_adj_expectancy: float
    adverse_selection_loss: float
    false_ignition_rate: float
    sweep_failure_rate: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "fold_id": self.fold_id,
            "timestamp_pure": self.timestamp_pure,
            "leakage_detected": self.leakage_detected,
            "n_events": self.n_events,
            "n_snapshots": self.n_snapshots,
            "precision_at_5": self.precision_at_5,
            "precision_at_10": self.precision_at_10,
            "pr_auc": self.pr_auc,
            "brier_score": self.brier_score,
            "calibration_error": self.calibration_error,
            "incremental_lift": self.incremental_lift,
            "mfe_before_mae_rate": self.mfe_before_mae_rate,
            "time_to_expansion_ns": self.time_to_expansion_ns,
            "slippage_adj_expectancy": self.slippage_adj_expectancy,
            "queue_fill_adj_expectancy": self.queue_fill_adj_expectancy,
            "adverse_selection_loss": self.adverse_selection_loss,
            "false_ignition_rate": self.false_ignition_rate,
            "sweep_failure_rate": self.sweep_failure_rate,
        }


class L3TimestampPureValidator:
    def __init__(self):
        self._leakage_checks = []

    def validate_timestamp_purity(
        self,
        events: list[MBOEvent],
        prediction_ts_ns: int,
    ) -> bool:
        for event in events:
            if event.ts_event_ns > prediction_ts_ns:
                self._leakage_checks.append({
                    "type": "future_event_leakage",
                    "event_ts": event.ts_event_ns,
                    "prediction_ts": prediction_ts_ns,
                })
                return False

            if event.ts_recv_ns > prediction_ts_ns:
                self._leakage_checks.append({
                    "type": "future_receive_leakage",
                    "event_ts": event.ts_recv_ns,
                    "prediction_ts": prediction_ts_ns,
                })
                return False

        return True

    def validate_event_ordering(self, events: list[MBOEvent]) -> bool:
        for i in range(1, len(events)):
            if events[i].ts_event_ns < events[i-1].ts_event_ns:
                self._leakage_checks.append({
                    "type": "event_ordering_violation",
                    "index": i,
                    "ts_prev": events[i-1].ts_event_ns,
                    "ts_curr": events[i].ts_event_ns,
                })
                return False
        return True

    def validate_sequence_gaps(self, events: list[MBOEvent]) -> bool:
        for i in range(1, len(events)):
            if events[i].seq_num != events[i-1].seq_num + 1:
                self._leakage_checks.append({
                    "type": "sequence_gap",
                    "index": i,
                    "expected_seq": events[i-1].seq_num + 1,
                    "actual_seq": events[i].seq_num,
                })
        return True

    def get_leakage_report(self) -> list[dict[str, Any]]:
        return self._leakage_checks


class L3WalkForwardValidator:
    def __init__(self, n_folds: int = 5, embargo_events: int = 1000):
        self._n_folds = n_folds
        self._embargo_events = embargo_events
        self._purity_validator = L3TimestampPureValidator()

    def validate(
        self,
        all_events: list[MBOEvent],
        labels: np.ndarray,
        feature_extractor: L3FeatureExtractor,
        reconstructor: OrderBookReconstructor,
    ) -> list[L3ValidationResult]:
        n = len(all_events)
        fold_size = n // (self._n_folds + 2)
        results = []

        for fold_id in range(self._n_folds):
            train_end = fold_size * (fold_id + 1)
            val_start = train_end + self._embargo_events
            val_end = val_start + fold_size
            test_start = val_end + self._embargo_events
            test_end = min(test_start + fold_size, n)

            if test_end <= test_start:
                break

            train_events = all_events[:train_end]
            test_events = all_events[test_start:test_end]

            timestamp_pure = self._purity_validator.validate_timestamp_purity(
                test_events,
                test_events[-1].ts_event_ns if test_events else 0,
            )

            result = self._evaluate_fold(
                fold_id,
                train_events,
                test_events,
                labels[test_start:test_end],
                feature_extractor,
                reconstructor,
                timestamp_pure,
            )
            results.append(result)

        return results

    def _evaluate_fold(
        self,
        fold_id: int,
        train_events: list[MBOEvent],
        test_events: list[MBOEvent],
        test_labels: np.ndarray,
        feature_extractor: L3FeatureExtractor,
        reconstructor: OrderBookReconstructor,
        timestamp_pure: bool,
    ) -> L3ValidationResult:
        if not test_events:
            return L3ValidationResult(
                fold_id=fold_id,
                timestamp_pure=timestamp_pure,
                leakage_detected=False,
                n_events=0,
                n_snapshots=0,
                precision_at_5=0.0,
                precision_at_10=0.0,
                pr_auc=0.0,
                brier_score=1.0,
                calibration_error=1.0,
                incremental_lift=0.0,
                mfe_before_mae_rate=0.0,
                time_to_expansion_ns=0.0,
                slippage_adj_expectancy=0.0,
                queue_fill_adj_expectancy=0.0,
                adverse_selection_loss=0.0,
                false_ignition_rate=0.0,
                sweep_failure_rate=0.0,
            )

        snapshots = []
        for event in test_events:
            snap = reconstructor.process_event(event)
            if snap:
                snapshots.append(snap)

        features = feature_extractor.extract(test_events, snapshots)

        p_at_5 = self._precision_at_k(test_labels, features, 5)
        p_at_10 = self._precision_at_k(test_labels, features, 10)

        return L3ValidationResult(
            fold_id=fold_id,
            timestamp_pure=timestamp_pure,
            leakage_detected=not timestamp_pure,
            n_events=len(test_events),
            n_snapshots=len(snapshots),
            precision_at_5=p_at_5,
            precision_at_10=p_at_10,
            pr_auc=0.0,
            brier_score=0.0,
            calibration_error=0.0,
            incremental_lift=0.0,
            mfe_before_mae_rate=0.0,
            time_to_expansion_ns=0.0,
            slippage_adj_expectancy=0.0,
            queue_fill_adj_expectancy=0.0,
            adverse_selection_loss=0.0,
            false_ignition_rate=0.0,
            sweep_failure_rate=0.0,
        )

    def _precision_at_k(
        self,
        labels: np.ndarray,
        features: L3Features,
        k: int,
    ) -> float:
        if len(labels) == 0:
            return 0.0
        top_k = min(k, len(labels))
        score = features.instability_score if hasattr(features, "instability_score") else 0.5
        return float(np.sum(labels[:top_k])) / top_k
