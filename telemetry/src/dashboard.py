import json
import os
from pathlib import Path

import pandas as pd
import streamlit as st

from telemetry.src.metrics import TelemetryMetrics

st.set_page_config(page_title="HFT3 Microstructure Dashboard", layout="wide")

REPO = Path(__file__).resolve().parents[2]

st.title("Chicago CME Microstructure - Telemetry Dashboard")

st.sidebar.header("Navigation")
page = st.sidebar.radio("Go to", ["Live/Sim Performance", "Databento Budget", "Research Models"])

manifest_path = REPO / "data" / "manifest.parquet"
research_index_path = REPO / "research_cards" / "all_hypotheses.json"
research_card_path = REPO / "research_cards" / "matrix_smoke.json"
fills_path = REPO / "research_cards" / "fills.csv"

if page == "Live/Sim Performance":
    st.header("Execution Quality & Disagreement")

    if fills_path.exists():
        fills = pd.read_csv(fills_path)
        tick_size = 0.25
        as_metrics = TelemetryMetrics.adverse_selection(fills, tick_size=tick_size)
        cols = st.columns(len(as_metrics) or 1)
        for i, (horizon, val) in enumerate(as_metrics.items()):
            cols[i % len(cols)].metric(horizon, f"{val:.3f} ticks")
        if "pnl" in fills.columns:
            es = TelemetryMetrics.expected_shortfall(fills["pnl"].values)
            st.metric("Expected Shortfall (5%)", f"{es:.2f}")
    else:
        st.info(f"No fills yet. Expected at {fills_path}")

elif page == "Databento Budget":
    st.header("Databento Credit Management")
    from data_system.src.budget_manager import BudgetManager

    initial_credit = BudgetManager.INITIAL_CREDIT
    operating_cap = BudgetManager.OPERATING_CAP
    total_used = 0.0

    if manifest_path.exists():
        df = pd.read_parquet(manifest_path)
        if "cost" in df.columns:
            total_used = float(df["cost"].sum())
        st.dataframe(df.tail(20))

    remaining = initial_credit - total_used
    col1, col2, col3 = st.columns(3)
    col1.metric("Total Used", f"${total_used:.2f}")
    col2.metric("Remaining Credit", f"${remaining:.2f}")
    col3.metric("Operating Cap", f"${operating_cap:.2f}")
    st.progress(min(total_used / operating_cap, 1.0) if operating_cap else 0.0)

elif page == "Research Models":
    st.header("Research Cards")
    if research_index_path.exists():
        index = json.loads(research_index_path.read_text(encoding="utf-8"))
        cards = index.get("cards", {})
        st.metric("Hypotheses tested", index.get("hypothesis_count", len(cards)))
        rows = []
        for key, card in cards.items():
            rows.append(
                {
                    "model_id": card.get("model_id", key),
                    "family": card.get("alpha_family_or_discovered_behavior"),
                    "net_pnl": card.get("net_pnl"),
                    "num_trades": card.get("num_trades", 0),
                    "approval": card.get("approval_status"),
                }
            )
        st.dataframe(pd.DataFrame(rows))
        with st.expander("HftBacktest combined"):
            st.json(index.get("hftbacktest_combined", {}))
    elif research_card_path.exists():
        st.json(json.loads(research_card_path.read_text(encoding="utf-8")))
    else:
        st.warning("Run backtest_pipeline/src/research_runner.py first.")
