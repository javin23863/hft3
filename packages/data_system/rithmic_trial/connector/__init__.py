from __future__ import annotations

from typing import Any

from ..config import TrialConfig
from .base import ConnectorInterface
from .fixture_connector import FixtureConnector
from .rithmic_api_connector import RithmicApiConnector
from .rtrader_bridge import RTraderBridgeConnector


def build_connector(cfg: TrialConfig) -> ConnectorInterface:
    name = cfg.connector.lower()
    if name == "fixture":
        return FixtureConnector(cfg)
    if name in ("rtrader", "rtrader_bridge", "r-trader"):
        return RTraderBridgeConnector(cfg)
    if name in ("rithmic_api", "rithmicapi", "api"):
        config_path = getattr(cfg, "rithmic_api_config", None)
        return RithmicApiConnector(config_path=config_path) if config_path else RithmicApiConnector()
    raise ValueError(f"Unknown connector: {cfg.connector}")
