from __future__ import annotations

from typing import Any

from ..config import TrialConfig
from .base import ConnectorInterface
from .fixture_connector import FixtureConnector
from .rtrader_bridge import RTraderBridgeConnector


def build_connector(cfg: TrialConfig) -> ConnectorInterface:
    name = cfg.connector.lower()
    if name == "fixture":
        return FixtureConnector(cfg)
    if name in ("rtrader", "rtrader_bridge", "r-trader"):
        return RTraderBridgeConnector(cfg)
    if name in ("rithmic_api", "api"):
        raise ValueError(
            "connector rithmic_api is not implemented yet; use rtrader or fixture until R|API SDK arrives"
        )
    raise ValueError(f"Unknown connector: {cfg.connector}")
