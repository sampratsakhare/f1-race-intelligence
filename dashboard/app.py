"""Decision-first Streamlit app for F1 Race Intelligence.

Run from the repository root:
    streamlit run dashboard/app.py
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path

import altair as alt
import pandas as pd
import streamlit as st
from dotenv import load_dotenv
from google.cloud import bigquery

from dashboard.data_access import (
    WarehouseConfig,
    load_driver_performance,
    load_live_sessions,
    load_live_snapshot,
    load_pit_analysis,
    load_race_catalog,
    load_table_freshness,
)

load_dotenv()
st.set_page_config(
    page_title="F1 Race Intelligence",
    page_icon="🏁",
    layout="wide",
)

TEAM_PALETTE = [
    "#E10600",
    "#1F77B4",
    "#FF8700",
    "#2CA02C",
    "#9467BD",
    "#17BECF",
    "#D627A1",
    "#BCBD22",
    "#7F7F7F",
    "#8C564B",
]
DEMO_PROJECT_ID = "demo"
DEMO_DATASET = "snapshot"
DEMO_DIR = Path("data/demo")
MODEL_METRICS_PATH = Path(
    os.environ.get(
        "MODEL_METRICS_PATH",
        "models/finishing_position_metrics.json",
    )
)


def _warehouse_config() -> WarehouseConfig | None:
    mode = os.environ.get("DASHBOARD_MODE", "auto").lower()
    if mode not in {"auto", "demo", "warehouse"}:
        st.error("DASHBOARD_MODE must be one of: auto, demo, warehouse")
        return None

    project_id = os.environ.get("GCP_PROJECT_ID")
    if mode == "demo" or (mode == "auto" and not project_id):
        return WarehouseConfig(DEMO_PROJECT_ID, DEMO_DATASET)
    if not project_id:
        return None
    try:
        return WarehouseConfig(
            project_id=project_id,
            dataset=os.environ.get("BQ_DATASET", "f1_raw"),
        )
    except ValueError as exc:
        st.error(str(exc))
        return None


@st.cache_resource
def get_client(project_id: str) -> bigquery.Client:
    return bigquery.Client(project=project_id)


def _is_demo(project_id: str) -> bool:
    return project_id == DEMO_PROJECT_ID


def _read_demo_csv(filename: str) -> pd.DataFrame:
    return pd.read_csv(DEMO_DIR / filename)


@st.cache_data(ttl=300)
def cached_race_catalog(project_id: str, dataset: str) -> pd.DataFrame:
    if _is_demo(project_id):
        performance = _read_demo_csv("driver_race_performance.csv")
        performance["session_start_ts"] = pd.to_datetime(
            performance["session_start_ts"],
            utc=True,
        )
        return (
            performance.groupby("session_key", as_index=False)
            .agg(
                year=("year", "first"),
                circuit_short_name=("circuit_short_name", "first"),
                session_start_ts=("session_start_ts", "min"),
            )
            .sort_values("session_start_ts", ascending=False)
        )
    return load_race_catalog(
        get_client(project_id),
        WarehouseConfig(project_id, dataset),
    )


@st.cache_data(ttl=300)
def cached_performance(
    project_id: str,
    dataset: str,
    year: int,
    session_key: int | None,
) -> pd.DataFrame:
    if _is_demo(project_id):
        performance = _read_demo_csv("driver_race_performance.csv")
        performance["session_start_ts"] = pd.to_datetime(
            performance["session_start_ts"],
            utc=True,
        )
        selected = performance[performance["year"] == year]
        if session_key is not None:
            selected = selected[selected["session_key"] == session_key]
        return selected.copy()
    return load_driver_performance(
        get_client(project_id),
        WarehouseConfig(project_id, dataset),
        year=year,
        session_key=session_key,
    )


@st.cache_data(ttl=300)
def cached_pits(
    project_id: str,
    dataset: str,
    session_key: int,
) -> pd.DataFrame:
    if _is_demo(project_id):
        pits = _read_demo_csv("pit_stop_analysis.csv")
        pits["pit_ts"] = pd.to_datetime(pits["pit_ts"], utc=True)
        return pits[pits["session_key"] == session_key].copy()
    return load_pit_analysis(
        get_client(project_id),
        WarehouseConfig(project_id, dataset),
        session_key=session_key,
    )


@st.cache_data(ttl=60)
def cached_freshness(project_id: str, dataset: str) -> pd.DataFrame:
    if _is_demo(project_id):
        return pd.DataFrame()
    return load_table_freshness(
        get_client(project_id),
        WarehouseConfig(project_id, dataset),
    )


@st.cache_data(ttl=5)
def cached_live_sessions(project_id: str, dataset: str) -> pd.DataFrame:
    if _is_demo(project_id):
        sessions = _read_demo_csv("live_sessions.csv")
        for column in (
            "first_source_event_at",
            "latest_source_event_at",
            "latest_ingested_at",
        ):
            sessions[column] = pd.to_datetime(sessions[column], utc=True)
        return sessions
    return load_live_sessions(
        get_client(project_id),
        WarehouseConfig(project_id, dataset),
    )


@st.cache_data(ttl=5)
def cached_live_snapshot(
    project_id: str,
    dataset: str,
    session_key: int,
) -> pd.DataFrame:
    if _is_demo(project_id):
        snapshot = _read_demo_csv("live_snapshot.csv")
        for column in ("latest_source_event_at", "latest_ingested_at"):
            snapshot[column] = pd.to_datetime(snapshot[column], utc=True)
        return snapshot[snapshot["session_key"] == session_key].copy()
    return load_live_snapshot(
        get_client(project_id),
        WarehouseConfig(project_id, dataset),
        session_key=session_key,
    )


def _format_timestamp(value: object) -> str:
    timestamp = pd.to_datetime(value, utc=True, errors="coerce")
    if pd.isna(timestamp):
        return "Unavailable"
    return timestamp.strftime("%Y-%m-%d %H:%M UTC")


def _format_seconds(value: object) -> str:
    if pd.isna(value):
        return "—"
    return f"{float(value):.3f}s"


def _safe_query(callable_, *args, **kwargs) -> pd.DataFrame | None:
    try:
        return callable_(*args, **kwargs)
    except Exception as exc:
        st.error(
            "The data read failed. For warehouse mode, confirm Application Default "
            "Credentials, project/dataset settings, and transformation tables."
        )
        with st.expander("Technical detail"):
            st.code(f"{type(exc).__name__}: {exc}")
        return None


def _grid_finish_chart(race: pd.DataFrame) -> alt.LayerChart:
    chart_data = race.dropna(subset=["full_name", "starting_position", "finishing_position"]).copy()
    sort_order = chart_data.sort_values("finishing_position")["full_name"].astype(str).tolist()
    tooltip = [
        alt.Tooltip("full_name:N", title="Driver"),
        alt.Tooltip("team_name:N", title="Team"),
        alt.Tooltip("starting_position:Q", title="Grid", format=".0f"),
        alt.Tooltip("finishing_position:Q", title="Finish", format=".0f"),
        alt.Tooltip("positions_gained:Q", title="Positions gained", format="+.0f"),
    ]
    base = alt.Chart(chart_data).encode(
        y=alt.Y(
            "full_name:N",
            sort=sort_order,
            title=None,
            axis=alt.Axis(labelLimit=150),
        ),
        tooltip=tooltip,
    )
    connectors = base.mark_rule(color="#B9C0CC", strokeWidth=2).encode(
        x=alt.X(
            "starting_position:Q",
            title="Race position (lower is better)",
            scale=alt.Scale(domain=[1, 20], reverse=False),
        ),
        x2="finishing_position:Q",
    )
    grid = base.mark_circle(size=90, color="#7A8494").encode(
        x="starting_position:Q",
    )
    finish = base.mark_circle(size=120, color="#E10600").encode(
        x="finishing_position:Q",
    )
    return (connectors + grid + finish).properties(
        title="Starting grid and finishing position",
        height=max(360, len(chart_data) * 22),
    )


def _pace_consistency_chart(race: pd.DataFrame) -> alt.Chart:
    chart_data = race.dropna(
        subset=["full_name", "team_name", "avg_lap_seconds", "lap_consistency_stddev"]
    ).copy()
    median_pace = chart_data["avg_lap_seconds"].median()
    chart_data["pace_delta_pct"] = ((chart_data["avg_lap_seconds"] / median_pace) - 1) * 100
    return (
        alt.Chart(chart_data)
        .mark_circle(size=130, opacity=0.85)
        .encode(
            x=alt.X(
                "pace_delta_pct:Q",
                title="Average clean-lap pace vs field median (%)",
                axis=alt.Axis(format="+.1f"),
            ),
            y=alt.Y(
                "lap_consistency_stddev:Q",
                title="Clean-lap standard deviation (seconds)",
            ),
            color=alt.Color(
                "team_name:N",
                title="Team",
                scale=alt.Scale(range=TEAM_PALETTE),
            ),
            tooltip=[
                alt.Tooltip("full_name:N", title="Driver"),
                alt.Tooltip("team_name:N", title="Team"),
                alt.Tooltip("avg_lap_seconds:Q", title="Average lap", format=".3f"),
                alt.Tooltip(
                    "lap_consistency_stddev:Q",
                    title="Lap variability",
                    format=".3f",
                ),
                alt.Tooltip("clean_laps_analyzed:Q", title="Clean laps", format=".0f"),
            ],
        )
        .properties(
            title="Clean-lap pace and consistency",
            height=420,
        )
        .interactive()
    )


def _season_driver_chart(performance: pd.DataFrame) -> alt.Chart:
    eligible = performance.dropna(subset=["full_name", "finishing_position"]).copy()
    result_summary = eligible.groupby(
        ["full_name", "team_name"],
        as_index=False,
    ).agg(
        average_finish=("finishing_position", "mean"),
        average_positions_gained=("positions_gained", "mean"),
        races=("session_key", "nunique"),
    )
    status_summary = performance.groupby(
        ["full_name", "team_name"],
        as_index=False,
    ).agg(
        dnf_rate=("did_not_finish", "mean"),
    )
    summary = result_summary.merge(
        status_summary,
        on=["full_name", "team_name"],
        how="left",
        validate="one_to_one",
    )
    summary["dnf_rate_pct"] = summary["dnf_rate"] * 100
    return (
        alt.Chart(summary)
        .mark_circle(opacity=0.85)
        .encode(
            x=alt.X(
                "average_finish:Q",
                title="Average finishing position (lower is better)",
                scale=alt.Scale(reverse=True),
            ),
            y=alt.Y(
                "average_positions_gained:Q",
                title="Average positions gained",
            ),
            size=alt.Size("races:Q", title="Races", scale=alt.Scale(range=[80, 500])),
            color=alt.Color(
                "team_name:N",
                title="Team",
                scale=alt.Scale(range=TEAM_PALETTE),
            ),
            tooltip=[
                alt.Tooltip("full_name:N", title="Driver"),
                alt.Tooltip("team_name:N", title="Team"),
                alt.Tooltip("average_finish:Q", title="Average finish", format=".2f"),
                alt.Tooltip(
                    "average_positions_gained:Q",
                    title="Average positions gained",
                    format="+.2f",
                ),
                alt.Tooltip("dnf_rate_pct:Q", title="DNF rate", format=".1f"),
                alt.Tooltip("races:Q", title="Races"),
            ],
        )
        .properties(title="Driver results across the selected season", height=450)
        .interactive()
    )


def _pit_duration_chart(pits: pd.DataFrame) -> alt.Chart:
    chart_data = pits.dropna(subset=["full_name", "lap_number", "lane_duration_seconds"]).copy()
    return (
        alt.Chart(chart_data)
        .mark_circle(size=120, opacity=0.85)
        .encode(
            x=alt.X("lap_number:Q", title="Pit-stop lap"),
            y=alt.Y(
                "lane_duration_seconds:Q",
                title="Pit-lane duration (seconds)",
                scale=alt.Scale(zero=False),
            ),
            color=alt.Color(
                "team_name:N",
                title="Team",
                scale=alt.Scale(range=TEAM_PALETTE),
            ),
            shape=alt.Shape("tire_compound_fitted:N", title="Fitted compound"),
            tooltip=[
                alt.Tooltip("full_name:N", title="Driver"),
                alt.Tooltip("team_name:N", title="Team"),
                alt.Tooltip("lap_number:Q", title="Lap"),
                alt.Tooltip(
                    "lane_duration_seconds:Q",
                    title="Pit-lane duration",
                    format=".3f",
                ),
                alt.Tooltip(
                    "stop_duration_seconds:Q",
                    title="Stationary duration",
                    format=".3f",
                ),
                alt.Tooltip(
                    "positions_changed_around_stop:Q",
                    title="Observed position change",
                    format="+.0f",
                ),
            ],
        )
        .properties(title="Pit-lane duration by stop lap", height=380)
        .interactive()
    )


def _render_model_validation(config: WarehouseConfig) -> None:
    st.subheader("Model validation")
    st.caption(
        "The candidate is promoted only when a chronological, whole-race holdout "
        "beats the starting-grid baseline. MAE uses rows with a classified "
        "finishing position; unclassified DNF/DNS/DSQ rows remain in history "
        "features and are reported as excluded."
    )
    metrics_path = (
        DEMO_DIR / "model_metrics.json" if _is_demo(config.project_id) else MODEL_METRICS_PATH
    )
    if not metrics_path.exists():
        st.info(
            "No validation artifact yet. Run `python -m src.ml.train_model` after "
            "building the historical mart."
        )
        return

    try:
        metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        st.warning(f"Could not read model metrics: {exc}")
        return

    model_mae = float(metrics["model_mae"])
    baseline_mae = float(metrics["grid_baseline_mae"])
    promoted = bool(metrics.get("promoted", False))
    columns = st.columns(4)
    columns[0].metric("Candidate MAE", f"{model_mae:.2f} positions")
    columns[1].metric("Grid baseline MAE", f"{baseline_mae:.2f} positions")
    columns[2].metric(
        "MAE vs baseline",
        f"{baseline_mae - model_mae:+.2f}",
        help="Positive means the candidate improves on the grid baseline.",
    )
    columns[3].metric("Promotion decision", "Promoted" if promoted else "Rejected")
    status = (
        "The candidate cleared the baseline and race-cluster uncertainty gates."
        if promoted
        else (
            "The candidate did not clear every promotion gate and is not "
            "presented as production-ready."
        )
    )
    (st.success if promoted else st.warning)(status)
    target_coverage = metrics.get("classified_target_coverage_pct")
    coverage_label = (
        f"{float(target_coverage) * 100:.1f}%" if target_coverage is not None else "not recorded"
    )
    st.caption(
        f"Holdout: {metrics.get('test_races', '—')} races, "
        f"{metrics.get('test_start', 'unknown')} to {metrics.get('test_end', 'unknown')} · "
        f"classified target coverage {coverage_label} · race win rate "
        f"{float(metrics.get('model_race_win_rate', 0)) * 100:.0f}% · "
        f"95% race-bootstrap improvement interval "
        f"[{float(metrics.get('race_mae_improvement_ci95_low', float('nan'))):.2f}, "
        f"{float(metrics.get('race_mae_improvement_ci95_high', float('nan'))):.2f}]."
    )


def _render_historical(config: WarehouseConfig) -> None:
    if _is_demo(config.project_id):
        st.info(
            "Demo mode: bundled OpenF1 snapshot for 2024–2025, with full "
            "lap/pit context for the 2025 Yas Marina race."
        )
    catalog = _safe_query(
        cached_race_catalog,
        config.project_id,
        config.dataset,
    )
    if catalog is None or catalog.empty:
        st.info("No completed races are available in the performance mart.")
        return

    catalog["session_start_ts"] = pd.to_datetime(catalog["session_start_ts"], utc=True)
    years = sorted(catalog["year"].dropna().astype(int).unique(), reverse=True)
    selected_year = st.sidebar.selectbox("Season", years, index=0)
    year_races = catalog[catalog["year"] == selected_year].copy()
    race_labels = {
        int(row.session_key): (
            f"{row.circuit_short_name} · {row.session_start_ts.strftime('%d %b %Y')}"
        )
        for row in year_races.itertuples()
    }
    race_options: list[int | None] = [None, *race_labels]
    selected_session = st.sidebar.selectbox(
        "Race",
        race_options,
        index=1 if len(race_options) > 1 else 0,
        format_func=lambda value: "Season overview" if value is None else race_labels[value],
    )

    performance = _safe_query(
        cached_performance,
        config.project_id,
        config.dataset,
        int(selected_year),
        selected_session,
    )
    if performance is None or performance.empty:
        st.info("No performance rows match the selected filters.")
        return

    teams = sorted(performance["team_name"].dropna().astype(str).unique())
    selected_teams = st.sidebar.multiselect(
        "Teams",
        teams,
        default=[],
        placeholder="All teams",
    )
    if selected_teams:
        performance = performance[performance["team_name"].isin(selected_teams)]
    if performance.empty:
        st.info("No performance rows match the selected teams.")
        return

    source_coverage = performance["session_start_ts"].max()
    st.caption(
        f"OpenF1 grid and session-result endpoints · source coverage through "
        f"{_format_timestamp(source_coverage)}"
    )

    if selected_session is not None:
        race_name = race_labels[selected_session].split(" · ")[0]
        st.header(f"{race_name} race review")
        classified = performance.dropna(subset=["finishing_position"]).copy()
        winner_rows = classified[classified["finishing_position"] == 1]
        winner = str(winner_rows.iloc[0]["full_name"]) if not winner_rows.empty else "Unavailable"
        movers = classified.dropna(subset=["positions_gained"])
        biggest_mover = (
            str(movers.loc[movers["positions_gained"].idxmax(), "full_name"])
            if not movers.empty
            else "Unavailable"
        )
        best_pace_rows = performance.dropna(subset=["avg_lap_seconds"])
        best_pace = (
            str(best_pace_rows.loc[best_pace_rows["avg_lap_seconds"].idxmin(), "full_name"])
            if not best_pace_rows.empty
            else "Unavailable"
        )
        dnf_count = int(performance["did_not_finish"].fillna(False).sum())

        metrics = st.columns(4)
        metrics[0].metric("Winner", winner)
        metrics[1].metric("Biggest grid mover", biggest_mover)
        metrics[2].metric("Best average clean-lap pace", best_pace)
        metrics[3].metric("DNFs", dnf_count)

        left, right = st.columns([1, 1])
        with left:
            grid_finish_rows = performance.dropna(
                subset=["full_name", "starting_position", "finishing_position"]
            )
            if grid_finish_rows.empty:
                st.info("Grid/result data is unavailable for this race.")
            else:
                st.altair_chart(
                    _grid_finish_chart(grid_finish_rows),
                    width="stretch",
                )
                st.caption("Gray = starting grid; red = final classified position.")
        with right:
            pace_rows = performance.dropna(
                subset=[
                    "full_name",
                    "team_name",
                    "avg_lap_seconds",
                    "lap_consistency_stddev",
                ]
            )
            if pace_rows.empty:
                st.info("Clean-lap pace data is unavailable for this race.")
            else:
                st.altair_chart(
                    _pace_consistency_chart(pace_rows),
                    width="stretch",
                )
                st.caption(
                    "Only laps within ±20% of the race median are included; "
                    "pit-out laps are excluded."
                )

        st.subheader("Pit-stop context")
        pits = _safe_query(
            cached_pits,
            config.project_id,
            config.dataset,
            selected_session,
        )
        if pits is None or pits.empty:
            st.info("No pit-stop records are available for this race.")
        else:
            st.altair_chart(_pit_duration_chart(pits), width="stretch")
            st.caption(
                "Position change is measured immediately around the stop and is "
                "descriptive—not a causal estimate of strategy impact."
            )
            with st.expander("Pit-stop detail"):
                display = pits.rename(
                    columns={
                        "full_name": "Driver",
                        "team_name": "Team",
                        "lap_number": "Lap",
                        "lane_duration_seconds": "Pit lane (s)",
                        "stop_duration_seconds": "Stationary (s)",
                        "tire_compound_fitted": "Compound fitted",
                        "position_before_stop": "Position before",
                        "position_after_stop": "Position after",
                        "positions_changed_around_stop": "Observed change",
                    }
                )
                st.dataframe(
                    display[
                        [
                            "Driver",
                            "Team",
                            "Lap",
                            "Pit lane (s)",
                            "Stationary (s)",
                            "Compound fitted",
                            "Position before",
                            "Position after",
                            "Observed change",
                        ]
                    ],
                    hide_index=True,
                    width="stretch",
                )
    else:
        st.header(f"{selected_year} season overview")
        finished = performance.dropna(subset=["finishing_position"])
        metrics = st.columns(4)
        metrics[0].metric("Races", int(performance["session_key"].nunique()))
        metrics[1].metric("Drivers", int(performance["driver_number"].nunique()))
        metrics[2].metric(
            "Average grid movement",
            f"{finished['positions_gained'].abs().mean():.1f} places",
        )
        metrics[3].metric(
            "DNF rate",
            f"{performance['did_not_finish'].fillna(False).mean() * 100:.1f}%",
        )
        st.altair_chart(
            _season_driver_chart(performance),
            width="stretch",
        )

    _render_model_validation(config)

    with st.expander("Driver-race detail"):
        detail_columns = [
            "year",
            "circuit_short_name",
            "full_name",
            "team_name",
            "starting_position",
            "finishing_position",
            "positions_gained",
            "avg_lap_seconds",
            "lap_consistency_stddev",
            "pit_stop_count",
            "did_not_finish",
        ]
        st.dataframe(
            performance[detail_columns],
            hide_index=True,
            width="stretch",
        )


@st.fragment(run_every="10s")
def _render_live_fragment(config: WarehouseConfig) -> None:
    sessions = _safe_query(
        cached_live_sessions,
        config.project_id,
        config.dataset,
    )
    if sessions is None:
        st.info(
            "Live tables are not ready. Provision them with the poller, then publish "
            "events from replay or an authenticated OpenF1 live source."
        )
        return
    if sessions.empty:
        st.info(
            "No live or replay events yet. Start `python -m src.stream.replay "
            "--session-dir ... --sink bigquery` or the live poller."
        )
        return

    session_keys = sessions["session_key"].dropna().astype(int).tolist()
    selected_session = st.selectbox(
        "Session",
        session_keys,
        format_func=lambda key: f"Session {key}",
        key="live_session",
    )
    session_row = sessions[sessions["session_key"] == selected_session].iloc[0]
    snapshot = _safe_query(
        cached_live_snapshot,
        config.project_id,
        config.dataset,
        selected_session,
    )
    if snapshot is None or snapshot.empty:
        st.info("The selected session has events but no driver snapshot yet.")
        return

    demo_mode = _is_demo(config.project_id)
    ingested_at = pd.to_datetime(
        session_row["latest_ingested_at"],
        utc=True,
        errors="coerce",
    )
    latency_seconds = (
        None
        if demo_mode or pd.isna(ingested_at)
        else max(
            0,
            int((datetime.now(UTC) - ingested_at.to_pydatetime()).total_seconds()),
        )
    )
    latest_lap = pd.to_numeric(snapshot["lap_number"], errors="coerce").max()
    leader_rows = snapshot[snapshot["position"] == 1]
    leader = str(leader_rows.iloc[0]["driver"]) if not leader_rows.empty else "Unavailable"

    metrics = st.columns(4)
    metrics[0].metric("Leader", leader)
    metrics[1].metric(
        "Latest lap",
        int(latest_lap) if pd.notna(latest_lap) else "—",
    )
    metrics[2].metric("Unique events", f"{int(session_row['event_count']):,}")
    metrics[3].metric(
        "Ingestion freshness",
        (
            "Static replay"
            if demo_mode
            else (f"{latency_seconds}s ago" if latency_seconds is not None else "Unavailable")
        ),
    )
    st.caption(
        f"{'Bundled replay snapshot' if demo_mode else 'Auto-refresh: 10 seconds'} "
        f"· latest source event "
        f"{_format_timestamp(session_row['latest_source_event_at'])} · latest ingestion "
        f"{_format_timestamp(session_row['latest_ingested_at'])}"
    )

    leaderboard = snapshot.copy()
    leaderboard["position_sort"] = pd.to_numeric(leaderboard["position"], errors="coerce").fillna(
        999
    )
    leaderboard = leaderboard.sort_values(["position_sort", "driver_number"])
    display = leaderboard.rename(
        columns={
            "position": "Position",
            "driver": "Driver",
            "driver_number": "Number",
            "lap_number": "Lap",
            "lap_duration": "Latest lap (s)",
            "gap_to_leader": "Gap to leader",
            "interval": "Interval",
        }
    )
    st.dataframe(
        display[
            [
                "Position",
                "Driver",
                "Number",
                "Lap",
                "Latest lap (s)",
                "Gap to leader",
                "Interval",
            ]
        ],
        hide_index=True,
        width="stretch",
    )

    if st.button("Refresh now", key="live_refresh"):
        cached_live_sessions.clear()
        cached_live_snapshot.clear()
        st.rerun(scope="fragment")


def _render_live(config: WarehouseConfig) -> None:
    st.header("Live and replay session")
    st.caption(
        "The same event contract powers authenticated live polling and reproducible "
        "historical replay. Warehouse rows are deduplicated by deterministic event ID."
    )
    _render_live_fragment(config)


def main() -> None:
    st.title("F1 Race Intelligence")
    st.write(
        "A source-backed view of race outcomes, pace, pit-stop context, "
        "streaming freshness, and model validation."
    )

    config = _warehouse_config()
    if config is None:
        st.error(
            "Set DASHBOARD_MODE=demo, or set GCP_PROJECT_ID and authenticate "
            "with Application Default Credentials for warehouse mode."
        )
        st.stop()

    try:
        freshness = cached_freshness(config.project_id, config.dataset)
    except Exception:
        freshness = pd.DataFrame()
    if _is_demo(config.project_id):
        manifest_path = DEMO_DIR / "manifest.json"
        manifest = (
            json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {}
        )
        st.caption(
            "Bundled OpenF1 demo snapshot · generated "
            f"{_format_timestamp(manifest.get('generated_at'))}"
        )
    elif not freshness.empty:
        latest_refresh = freshness["last_modified_at"].max()
        st.caption(
            f"Warehouse `{config.prefix}` · latest table update {_format_timestamp(latest_refresh)}"
        )
    else:
        st.caption(f"Warehouse `{config.prefix}`")

    historical_tab, live_tab, methodology_tab = st.tabs(
        ["Race intelligence", "Live / replay", "Methodology"]
    )
    with historical_tab:
        _render_historical(config)
    with live_tab:
        _render_live(config)
    with methodology_tab:
        st.subheader("What the dashboard does—and does not claim")
        st.markdown(
            """
- Race outcomes use OpenF1 starting-grid and session-result endpoints.
- Pace summaries exclude pit-out laps and laps outside ±20% of the race median.
- Position movement around a pit stop is descriptive, not causal.
- Live and replay events use deterministic IDs and are deduplicated at read time.
- The finishing-order candidate uses only pre-race information, holds out complete
  future races, and must beat the starting grid before promotion.
            """
        )
        st.caption(
            "Missing or delayed upstream data can make a session partial. Source and "
            "ingestion timestamps remain visible so viewers can judge freshness."
        )


if __name__ == "__main__":
    main()
