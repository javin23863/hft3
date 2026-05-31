"""L3-only feature stubs (no-op in degraded mode)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from equities_lane.src.types import DegradedModeFlags, FeatureToggles


@dataclass
class L3FeatureSnapshot:
    queue_position: float | None
    cancellation_delta: float | None
    iceberg_detected: bool
    degraded: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "queue_position": self.queue_position,
            "cancellation_delta": self.cancellation_delta,
            "iceberg_detected": self.iceberg_detected,
            "degraded": self.degraded,
        }


def compute_l3_features(
    toggles: FeatureToggles,
    degraded: DegradedModeFlags,
) -> L3FeatureSnapshot:
    if degraded.degraded_mode:
        return L3FeatureSnapshot(
            queue_position=None,
            cancellation_delta=None,
            iceberg_detected=False,
            degraded=True,
        )
    qp = 0.5 if toggles.l3_queue else None
    cd = 0.0 if toggles.l3_cancellation else None
    iceberg = False
    return L3FeatureSnapshot(
        queue_position=qp,
        cancellation_delta=cd,
        iceberg_detected=iceberg,
        degraded=False,
    )
