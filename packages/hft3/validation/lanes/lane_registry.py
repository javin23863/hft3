"""LaneRegistry: single source of truth for lane -> adapter/validator/test-path mapping.

Adapters register themselves at import time via @register_lane. The
certification runner and promotion gate use this registry to discover
all lanes and route to the right validator.

Replaces the brittle prefix-string matching in asset_class_routing.py
with a proper, testable registry.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from .lane import Lane


@dataclass
class LaneRegistration:
    """A lane's registered adapter, config loader, and validator."""

    lane: Lane
    adapter_factory: Callable[[], Any]
    config_loader: Callable[[], Any]
    validator: Callable[[], Any]
    test_paths: list[str] = field(default_factory=list)
    model_id_prefixes: tuple[str, ...] = ()


class LaneRegistry:
    """Singleton registry. Use LaneRegistry.instance() to access."""

    _instance: "LaneRegistry | None" = None

    def __init__(self) -> None:
        self._lanes: dict[Lane, LaneRegistration] = {}
        self._prefix_to_lane: dict[str, Lane] = {}

    @classmethod
    def instance(cls) -> "LaneRegistry":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @classmethod
    def reset(cls) -> None:
        """Reset the registry (for tests)."""
        cls._instance = None

    def register(self, reg: LaneRegistration) -> None:
        self._lanes[reg.lane] = reg
        for prefix in reg.model_id_prefixes:
            self._prefix_to_lane[prefix.upper()] = reg.lane

    def get(self, lane: Lane) -> LaneRegistration | None:
        return self._lanes.get(lane)

    def resolve_lane(self, model_id: str) -> Lane:
        """Resolve a Lane from a model_id via prefix matching, falling back to Lane.from_model_id()."""
        upper = (model_id or "").upper()
        for prefix, lane in self._prefix_to_lane.items():
            if upper.startswith(prefix):
                return lane
        return Lane.from_model_id(model_id)

    def all_lanes(self) -> list[Lane]:
        return list(self._lanes.keys())

    def all_registrations(self) -> list[LaneRegistration]:
        return list(self._lanes.values())


def register_lane(
    lane: Lane,
    adapter_factory: Callable[[], Any],
    config_loader: Callable[[], Any],
    validator: Callable[[], Any],
    test_paths: list[str],
    model_id_prefixes: tuple[str, ...] = (),
) -> None:
    """Convenience decorator/factory for lane registration."""
    LaneRegistry.instance().register(
        LaneRegistration(
            lane=lane,
            adapter_factory=adapter_factory,
            config_loader=config_loader,
            validator=validator,
            test_paths=test_paths,
            model_id_prefixes=model_id_prefixes,
        )
    )
