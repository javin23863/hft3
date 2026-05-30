"""Base protocol for PDF structural models."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Generic, TypeVar

T = TypeVar("T")


@dataclass
class ModelOutput(Generic[T]):
    model_id: str
    timestamp_ns: int = 0
    payload: T | None = None
    metadata: dict[str, Any] | None = None


class BaseStructuralModel(ABC, Generic[T]):
    """Event-driven or batch structural model; separate from HYP evaluate()."""

    model_id: str = ""

    @abstractmethod
    def evaluate(self, **kwargs: Any) -> ModelOutput[T]:
        ...
