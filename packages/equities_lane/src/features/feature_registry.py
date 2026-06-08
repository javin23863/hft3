"""Feature module registry and ablation helpers."""
from __future__ import annotations

from equities_lane.src.features.book_adapter import FeatureSnapshot, compute_features
from equities_lane.src.models import SessionTick
from equities_lane.src.types import DegradedModeFlags, FeatureToggles, UniverseConfig


def run_feature_pipeline(
    ticks: list[SessionTick],
    universe: UniverseConfig,
    degraded: DegradedModeFlags,
    *,
    ablation: str | None = None,
    options_loader=None,
) -> list[FeatureSnapshot]:
    toggles = (
        universe.features.with_ablation(ablation)
        if ablation
        else universe.features
    )
    return compute_features(ticks, toggles, degraded, options_loader=options_loader)


def ablation_modules(universe: UniverseConfig) -> list[str]:
    return list(universe.experiment.ablation_modules)
