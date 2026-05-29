import streamlit as st
import pandas as pd
import numpy as np
from metrics import TelemetryMetrics
import json
import os

st.set_page_config(page_title="HFT3 Microstructure Dashboard", layout="wide")

st.title("Chicago CME Microstructure - Telemetry Dashboard")

st.sidebar.header("Navigation")
page = st.sidebar.radio("Go to", ["Live/Sim Performance", "Databento Budget", "Research Models"])

if page == "Live/Sim Performance":
    st.header("Execution Quality & Disagreement")
    
    # Mock data for dashboard rendering
    st.subheader("Adverse Selection (Markouts)")
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("100ms", "-0.15 ticks")
    col2.metric("500ms", "-0.05 ticks")
    col3.metric("1s", "+0.10 ticks")
    col4.metric("5s", "+0.25 ticks")
    
    st.subheader("Sim vs Live Disagreement")
    st.metric("Disagreement Rate", "1.2%", "-0.1%")
    st.metric("Average Slippage Diff", "0.02 ticks")

elif page == "Databento Budget":
    st.header("Databento Credit Management")
    
    # Read from budget state if exists
    budget_state = {"total_used": 0.0}
    budget_file = "../data_system/config/budget_state.json"
    if os.path.exists(budget_file):
        with open(budget_file, "r") as f:
            budget_state = json.load(f)
            
    total_used = budget_state.get("total_used", 0.0)
    initial_credit = 125.00
    operating_cap = 112.50
    remaining = initial_credit - total_used
    
    col1, col2, col3 = st.columns(3)
    col1.metric("Total Used", f"${total_used:.2f}")
    col2.metric("Remaining Credit", f"${remaining:.2f}")
    col3.metric("Operating Cap", f"${operating_cap:.2f}")
    
    st.progress(min(total_used / operating_cap, 1.0))
    
elif page == "Research Models":
    st.header("Approved Models")
    st.dataframe(pd.DataFrame({
        "Model ID": ["HYP_1", "HYP_12"],
        "Family": ["Second-wave continuation", "Absorption fade"],
        "Net Expectancy": [0.45, 0.32],
        "Tail Risk (ES)": [-150.0, -90.0],
        "Status": ["PASS", "PASS"]
    }))
