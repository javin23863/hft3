from __future__ import annotations

from typing import Any

from ..config import TrialConfig
from .base import ConnectorInterface


class RithmicApiConnector(ConnectorInterface):
    """Placeholder for future direct R|API connector."""

    def __init__(self, cfg: TrialConfig) -> None:
        self.cfg = cfg

    def connect(self) -> None:
        raise NotImplementedError(
            "RithmicApiConnector is not implemented yet. "
            "Swap from RTraderBridgeConnector once R|API SDK is approved. "
            "See docs/rithmic_trial/README.md"
        )

    def poll_events(self) -> list[dict[str, Any]]:
        return []

    def detected_event_types(self) -> set[str]:
        return set()

    def limitations(self) -> dict[str, Any]:
        return {"connector": "rithmic_api", "status": "not_implemented"}

    def close(self) -> None:
        pass
