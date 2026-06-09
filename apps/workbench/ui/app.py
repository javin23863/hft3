"""Lane-first workbench — command center entry point.

All rendering consumes WorkbenchTruth. No independent state assembly.
Lane Command Center -> CME | Equities | Options/Parity | Crypto detail.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
from hft3_bootstrap import setup_repo_paths

setup_repo_paths()

import pandas as pd
import streamlit as st

from workbench.ui.campaign_panel import init_session, personal_lock_sidebar
from workbench.ui.lane_command import render_lane_command_center, render_lane_detail

try:
    st.set_page_config(page_title="HFT3 Workbench", layout="wide")
except st.errors.StreamlitAPIException:
    pass

init_session(REPO)

# ---- Sidebar ----
with st.sidebar:
    personal_lock_sidebar(REPO)
    st.caption("`docs/workbench/GRADER_CHECKLIST.md`")
    st.caption("`python scripts/run_stocks_lane.py --discover`")
    st.caption("`powershell -File scripts/launch_workbench.ps1`")

# ---- Main ----
lane_detail = st.session_state.get("wb_lane_detail", "")

if lane_detail:
    render_lane_detail(REPO)
else:
    render_lane_command_center(REPO)
