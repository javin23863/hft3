"""Execution sub-package for the equities lane.

Provides:
  IbkrWebClient — thin REST client for IBKR Client Portal (Web) API.
  GatewayAuth   — cookie/session auth against clientportal.gw on localhost.
  OAuthAuth     — OAuth 1.0a RSA-SHA256 + DH live-session-token auth.
  LiveAccountRefusal — raised when an order targets a non-paper account.
"""

from equities_lane.src.execution.ibkr_web_client import (
    GatewayAuth,
    IbkrWebClient,
    LiveAccountRefusal,
    OAuthAuth,
)

__all__ = [
    "GatewayAuth",
    "IbkrWebClient",
    "LiveAccountRefusal",
    "OAuthAuth",
]
