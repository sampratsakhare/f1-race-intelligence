"""
F1 Race Intelligence dashboard.

Two views:
1. Historical: pull from the driver_race_performance / pit_stop_analysis
   marts for season-level insights.
2. Live: pull from the raw_live_* tables populated by live_poller.py
   during an active session, auto-refreshing every few seconds.

Run:
    streamlit run dashboard/app.py
"""

import os
import time

import pandas as pd
import streamlit as st
from google.cloud import bigquery

st.set_page_config(page_title="F1 Race Intelligence", layout="wide")

PROJECT_ID = os.environ["GCP_PROJECT_ID"]
DATASET = os.environ.get("BQ_DATASET", "f1_raw")


@st.cache_resource
def get_client() -> bigquery.Client:
    return bigquery.Client(project=PROJECT_ID)


@st.cache_data(ttl=300)
def load_driver_performance() -> pd.DataFrame:
    client = get_client()
    query = f"""
        SELECT *
        FROM `{PROJECT_ID}.{DATASET}.driver_race_performance`
        ORDER BY year DESC, session_key DESC
    """
    return client.query(query).to_dataframe()


@st.cache_data(ttl=300)
def load_pit_analysis() -> pd.DataFrame:
    client = get_client()
    query = f"""
        SELECT *
        FROM `{PROJECT_ID}.{DATASET}.pit_stop_analysis`
        ORDER BY session_key DESC, lap_number ASC
    """
    return client.query(query).to_dataframe()


def load_live_laps() -> pd.DataFrame:
    client = get_client()
    query = f"""
        SELECT *
        FROM `{PROJECT_ID}.{DATASET}.raw_live_laps`
        ORDER BY date DESC
        LIMIT 50
    """
    return client.query(query).to_dataframe()


st.title("F1 Race Intelligence")

tab_historical, tab_live = st.tabs(["Historical Analysis", "Live Session"])

with tab_historical:
    st.subheader("Driver Race Performance")
    perf_df = load_driver_performance()

    col1, col2 = st.columns(2)
    with col1:
        st.metric("Races in dataset", perf_df["session_key"].nunique())
    with col2:
        st.metric("Drivers in dataset", perf_df["driver_number"].nunique())

    st.dataframe(
        perf_df[[
            "year", "circuit_short_name", "full_name", "team_name",
            "starting_position", "finishing_position", "positions_gained",
            "avg_lap_seconds", "pit_stop_count",
        ]],
        use_container_width=True,
    )

    st.subheader("Lap Consistency vs. Finishing Position")
    st.scatter_chart(
        perf_df,
        x="lap_consistency_stddev",
        y="finishing_position",
        color="team_name",
    )

    st.subheader("Pit Stop Strategy Impact")
    pit_df = load_pit_analysis()
    st.dataframe(
        pit_df[[
            "full_name", "team_name", "lap_number",
            "pit_duration_seconds", "tire_compound_fitted",
            "position_before_stop", "position_after_stop", "positions_change_from_stop",
        ]],
        use_container_width=True,
    )

with tab_live:
    st.subheader("Live Lap Feed")
    st.caption(
        "Populated by live_poller.py during an active session. "
        "Refreshes automatically every 10 seconds."
    )

    placeholder = st.empty()
    refresh = st.button("Refresh now")

    if refresh:
        with placeholder.container():
            live_df = load_live_laps()
            if live_df.empty:
                st.info("No live data yet -- start live_poller.py during a session weekend.")
            else:
                st.dataframe(live_df, use_container_width=True)
