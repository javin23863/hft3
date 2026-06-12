"""Adapters for each lane's native backtester.

Each adapter wraps a lane's native code to satisfy the Backtester Protocol.
Adapters are wrappers only; they do NOT modify teammate-owned lane internals.

Registered adapters:
  - cme_adapter          Lane.CME_FUTURES
  - cme_options_adapter  Lane.CME_OPTIONS  (research_only, Phases 0-1)
  (Lane.EQUITIES — the legacy options/parity shim — registers inline in
  registration.py; the dedicated equities/crypto adapters moved out with
  their lanes.)
"""
