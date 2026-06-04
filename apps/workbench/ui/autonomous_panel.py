"""Compatibility panel for the shared autonomous Workbench monitor.

The production Workbench app renders autonomous state through
``evidence_panels.render_autonomous_run`` from a selected ``RunEvidenceSnapshot``.
This module stays importable for older tests/extensions, but it does not start
standalone candidate loops or read legacy latest artifacts.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import streamlit as st


def _latest_crypto_reports(repo: Path) -> list[dict[str, Any]]:
    """Legacy helper retained for compatibility; fresh all-lane runs own evidence."""

    return []


def render_autonomous_panel(repo: Path) -> None:
    st.header("Autonomous Pipeline Monitor")
    st.info("Autonomous evidence is rendered from the selected run snapshot in the main Workbench tabs.")
