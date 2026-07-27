"""Build a small, source-backed dashboard demo snapshot from OpenF1.

The snapshot keeps two seasons of source grid/results for season analysis
and full lap/pit/position context for the latest completed race in that range.
"""

from __future__ import annotations

import argparse
import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd

from src.ingest.openf1_client import OpenF1Client
from src.ml.train_model import train_and_evaluate

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

DEFAULT_OUTPUT_DIR = Path("data/demo")


def _index_rows(
    rows: list[dict[str, Any]],
) -> dict[tuple[int, int], dict[str, Any]]:
    indexed: dict[tuple[int, int], dict[str, Any]] = {}
    for row in rows:
        session_key = row.get("session_key")
        driver_number = row.get("driver_number")
        if session_key is None or driver_number is None:
            continue
        indexed[(int(session_key), int(driver_number))] = row
    return indexed


def _timestamp_series(frame: pd.DataFrame, column: str) -> pd.Series:
    return pd.to_datetime(frame[column], utc=True, errors="coerce")


def build_performance_rows(
    sessions: list[dict[str, Any]],
    drivers: list[dict[str, Any]],
    grids: list[dict[str, Any]],
    results: list[dict[str, Any]],
) -> pd.DataFrame:
    drivers_by_key = _index_rows(drivers)
    grids_by_key = {
        (int(row["meeting_key"]), int(row["driver_number"])): row
        for row in grids
        if row.get("meeting_key") is not None and row.get("driver_number") is not None
    }
    sessions_by_key = {int(session["session_key"]): session for session in sessions}
    selected_sessions = set(sessions_by_key)

    rows: list[dict[str, Any]] = []
    for result in results:
        session_key = result.get("session_key")
        driver_number = result.get("driver_number")
        if session_key is None or int(session_key) not in selected_sessions:
            continue
        if driver_number is None:
            continue

        key = (int(session_key), int(driver_number))
        session = sessions_by_key[int(session_key)]
        driver = drivers_by_key.get(key, {})
        grid = grids_by_key.get(
            (int(session["meeting_key"]), int(driver_number)),
            {},
        )
        starting_position = grid.get("position")
        finishing_position = result.get("position")
        positions_gained = (
            int(starting_position) - int(finishing_position)
            if starting_position is not None and finishing_position is not None
            else None
        )
        rows.append(
            {
                "session_key": int(session_key),
                "circuit_short_name": session.get("circuit_short_name"),
                "country_name": session.get("country_name"),
                "year": int(session["year"]),
                "session_start_ts": session.get("date_start"),
                "driver_number": int(driver_number),
                "full_name": driver.get(
                    "full_name",
                    f"Driver #{driver_number}",
                ),
                "team_name": driver.get("team_name", "Unknown team"),
                "laps_completed": result.get("number_of_laps"),
                "clean_laps_analyzed": None,
                "avg_lap_seconds": None,
                "best_lap_seconds": None,
                "lap_consistency_stddev": None,
                "pit_stop_count": 0,
                "avg_pit_duration_seconds": None,
                "total_pit_time_seconds": None,
                "avg_stationary_stop_seconds": None,
                "starting_position": starting_position,
                "finishing_position": finishing_position,
                "did_not_finish": bool(result.get("dnf", False)),
                "did_not_start": bool(result.get("dns", False)),
                "disqualified": bool(result.get("dsq", False)),
                "positions_gained": positions_gained,
            }
        )

    performance = pd.DataFrame(rows)
    if performance.empty:
        raise RuntimeError("OpenF1 returned no race-result rows for the selected years")
    return performance.sort_values(
        ["session_start_ts", "session_key", "finishing_position"]
    ).reset_index(drop=True)


def add_latest_race_lap_and_pit_stats(
    performance: pd.DataFrame,
    laps: list[dict[str, Any]],
    pits: list[dict[str, Any]],
    session_key: int,
) -> pd.DataFrame:
    enriched = performance.copy()

    lap_frame = pd.DataFrame(laps)
    lap_frame["lap_duration"] = pd.to_numeric(
        lap_frame["lap_duration"],
        errors="coerce",
    )
    lap_frame = lap_frame[
        lap_frame["lap_duration"].notna()
        & (lap_frame["lap_duration"] > 0)
        & ~lap_frame["is_pit_out_lap"].fillna(False).astype(bool)
    ].copy()
    median_lap = lap_frame["lap_duration"].median()
    clean_laps = lap_frame[lap_frame["lap_duration"].between(median_lap * 0.8, median_lap * 1.2)]
    lap_stats = (
        clean_laps.groupby("driver_number", as_index=False)
        .agg(
            clean_laps_analyzed=("lap_number", "count"),
            avg_lap_seconds=("lap_duration", "mean"),
            best_lap_seconds=("lap_duration", "min"),
            lap_consistency_stddev=("lap_duration", "std"),
        )
        .set_index("driver_number")
    )

    pit_frame = pd.DataFrame(pits)
    if not pit_frame.empty:
        pit_frame["lane_duration_seconds"] = pd.to_numeric(
            pit_frame["lane_duration"].fillna(pit_frame.get("pit_duration")),
            errors="coerce",
        )
        pit_frame["stop_duration_seconds"] = pd.to_numeric(
            pit_frame.get("stop_duration"),
            errors="coerce",
        )
        pit_stats = (
            pit_frame.groupby("driver_number", as_index=False)
            .agg(
                pit_stop_count=("lap_number", "count"),
                avg_pit_duration_seconds=("lane_duration_seconds", "mean"),
                total_pit_time_seconds=("lane_duration_seconds", "sum"),
                avg_stationary_stop_seconds=("stop_duration_seconds", "mean"),
            )
            .set_index("driver_number")
        )
    else:
        pit_stats = pd.DataFrame()

    target_mask = enriched["session_key"] == session_key
    for row_index in enriched[target_mask].index:
        driver_number = int(enriched.at[row_index, "driver_number"])
        if driver_number in lap_stats.index:
            for column, value in lap_stats.loc[driver_number].items():
                enriched.at[row_index, column] = value
        if not pit_stats.empty and driver_number in pit_stats.index:
            for column, value in pit_stats.loc[driver_number].items():
                enriched.at[row_index, column] = value
    return enriched


def build_pit_rows(
    pits: list[dict[str, Any]],
    positions: list[dict[str, Any]],
    stints: list[dict[str, Any]],
    drivers: list[dict[str, Any]],
    session_key: int,
) -> pd.DataFrame:
    drivers_by_key = _index_rows(drivers)
    position_frame = pd.DataFrame(positions)
    position_frame["position_ts"] = _timestamp_series(position_frame, "date")
    stint_frame = pd.DataFrame(stints)

    rows: list[dict[str, Any]] = []
    for pit in pits:
        driver_number = int(pit["driver_number"])
        lap_number = int(pit["lap_number"])
        pit_ts = pd.to_datetime(pit["date"], utc=True)
        driver_positions = position_frame[
            position_frame["driver_number"] == driver_number
        ].sort_values("position_ts")
        before = driver_positions[driver_positions["position_ts"] <= pit_ts]
        after = driver_positions[driver_positions["position_ts"] > pit_ts]
        position_before = before.iloc[-1]["position"] if not before.empty else None
        position_after = after.iloc[0]["position"] if not after.empty else None

        candidate_stints = stint_frame[
            (stint_frame["driver_number"] == driver_number)
            & stint_frame["lap_start"].between(lap_number, lap_number + 2)
        ].copy()
        if not candidate_stints.empty:
            candidate_stints["lap_distance"] = (
                candidate_stints["lap_start"] - (lap_number + 1)
            ).abs()
            compound = candidate_stints.sort_values("lap_distance").iloc[0]["compound"]
        else:
            compound = None

        driver = drivers_by_key.get((session_key, driver_number), {})
        lane_duration = pit.get("lane_duration", pit.get("pit_duration"))
        rows.append(
            {
                "session_key": session_key,
                "driver_number": driver_number,
                "full_name": driver.get(
                    "full_name",
                    f"Driver #{driver_number}",
                ),
                "team_name": driver.get("team_name", "Unknown team"),
                "lap_number": lap_number,
                "pit_ts": pit_ts.isoformat(),
                "lane_duration_seconds": lane_duration,
                "stop_duration_seconds": pit.get("stop_duration"),
                "tire_compound_fitted": compound,
                "position_before_stop": position_before,
                "position_after_stop": position_after,
                "positions_changed_around_stop": (
                    int(position_before) - int(position_after)
                    if position_before is not None and position_after is not None
                    else None
                ),
            }
        )
    return pd.DataFrame(rows).sort_values(["lap_number", "driver_number"])


def _latest_by_driver(
    rows: list[dict[str, Any]],
    timestamp_field: str,
    extra_sort: str | None = None,
) -> pd.DataFrame:
    frame = pd.DataFrame(rows)
    frame["_event_at"] = _timestamp_series(frame, timestamp_field)
    sort_columns = ["driver_number"]
    if extra_sort:
        sort_columns.append(extra_sort)
    sort_columns.append("_event_at")
    return frame.sort_values(sort_columns).groupby("driver_number").tail(1)


def build_live_snapshot(
    *,
    session_key: int,
    laps: list[dict[str, Any]],
    positions: list[dict[str, Any]],
    intervals: list[dict[str, Any]],
    drivers: list[dict[str, Any]],
    generated_at: str,
) -> pd.DataFrame:
    latest_lap = _latest_by_driver(laps, "date_start", "lap_number").rename(
        columns={"_event_at": "lap_event_at"}
    )
    latest_position = _latest_by_driver(positions, "date").rename(
        columns={"_event_at": "position_event_at"}
    )
    latest_interval = _latest_by_driver(intervals, "date").rename(
        columns={"_event_at": "interval_event_at"}
    )

    snapshot = latest_lap[
        [
            "driver_number",
            "lap_number",
            "lap_duration",
            "duration_sector_1",
            "duration_sector_2",
            "duration_sector_3",
            "is_pit_out_lap",
            "lap_event_at",
        ]
    ].merge(
        latest_position[["driver_number", "position", "position_event_at"]],
        on="driver_number",
        how="outer",
    )
    snapshot = snapshot.merge(
        latest_interval[
            [
                "driver_number",
                "gap_to_leader",
                "interval",
                "interval_event_at",
            ]
        ],
        on="driver_number",
        how="outer",
    )

    driver_map = {
        driver_number: row.get("full_name", f"Driver #{driver_number}")
        for (row_session, driver_number), row in _index_rows(drivers).items()
        if row_session == session_key
    }
    snapshot["session_key"] = session_key
    snapshot["driver"] = snapshot["driver_number"].map(driver_map)
    snapshot["latest_source_event_at"] = snapshot[
        ["lap_event_at", "position_event_at", "interval_event_at"]
    ].max(axis=1)
    snapshot["latest_ingested_at"] = generated_at
    return snapshot[
        [
            "session_key",
            "driver_number",
            "driver",
            "position",
            "lap_number",
            "lap_duration",
            "duration_sector_1",
            "duration_sector_2",
            "duration_sector_3",
            "is_pit_out_lap",
            "gap_to_leader",
            "interval",
            "latest_source_event_at",
            "latest_ingested_at",
        ]
    ].sort_values(["position", "driver_number"])


def build_demo_snapshot(
    client: OpenF1Client,
    years: list[int],
    output_dir: Path,
) -> None:
    generated_at = datetime.now(UTC).isoformat()
    all_sessions: list[dict[str, Any]] = []
    for year in years:
        all_sessions.extend(client.fetch("sessions", year=year))
    sessions = [session for session in all_sessions if session.get("session_name") == "Race"]
    if not sessions:
        raise RuntimeError("No race sessions returned for the selected years")

    session_keys = {int(session["session_key"]) for session in sessions}
    meeting_keys = {int(session["meeting_key"]) for session in sessions}
    qualifying_session_keys = {
        int(session["session_key"])
        for session in all_sessions
        if int(session.get("meeting_key", -1)) in meeting_keys
        and session.get("session_name") == "Qualifying"
    }
    drivers = [
        row for row in client.fetch("drivers") if int(row.get("session_key", -1)) in session_keys
    ]
    grids = [
        row
        for row in client.fetch("starting_grid")
        if int(row.get("session_key", -1)) in qualifying_session_keys
    ]
    results = [
        row
        for row in client.fetch("session_result")
        if int(row.get("session_key", -1)) in session_keys
    ]

    performance = build_performance_rows(sessions, drivers, grids, results)
    completed_keys = set(performance["session_key"].astype(int))
    completed_sessions = [
        session for session in sessions if int(session["session_key"]) in completed_keys
    ]
    latest_session = max(
        completed_sessions,
        key=lambda session: session["date_start"],
    )
    latest_session_key = int(latest_session["session_key"])
    logger.info(
        "Building detailed demo for %s session_key=%s",
        latest_session.get("circuit_short_name"),
        latest_session_key,
    )

    laps = client.fetch("laps", session_key=latest_session_key)
    pits = client.fetch("pit", session_key=latest_session_key)
    positions = client.fetch("position", session_key=latest_session_key)
    stints = client.fetch("stints", session_key=latest_session_key)
    intervals = client.fetch("intervals", session_key=latest_session_key)

    performance = add_latest_race_lap_and_pit_stats(
        performance,
        laps,
        pits,
        latest_session_key,
    )
    pit_analysis = build_pit_rows(
        pits,
        positions,
        stints,
        drivers,
        latest_session_key,
    )
    live_snapshot = build_live_snapshot(
        session_key=latest_session_key,
        laps=laps,
        positions=positions,
        intervals=intervals,
        drivers=drivers,
        generated_at=generated_at,
    )

    source_times = pd.concat(
        [
            pd.to_datetime(
                pd.Series(
                    [row.get("date_start") for row in laps]
                    + [row.get("date") for row in positions]
                    + [row.get("date") for row in intervals]
                    + [row.get("date") for row in pits]
                ),
                utc=True,
                errors="coerce",
            )
        ]
    ).dropna()
    live_sessions = pd.DataFrame(
        [
            {
                "session_key": latest_session_key,
                "event_count": len(laps) + len(positions) + len(intervals) + len(pits),
                "first_source_event_at": source_times.min(),
                "latest_source_event_at": source_times.max(),
                "latest_ingested_at": generated_at,
            }
        ]
    )

    _, metrics, _, _ = train_and_evaluate(performance, test_fraction=0.2)

    output_dir.mkdir(parents=True, exist_ok=True)
    performance.to_csv(
        output_dir / "driver_race_performance.csv",
        index=False,
    )
    pit_analysis.to_csv(output_dir / "pit_stop_analysis.csv", index=False)
    live_snapshot.to_csv(output_dir / "live_snapshot.csv", index=False)
    live_sessions.to_csv(output_dir / "live_sessions.csv", index=False)
    (output_dir / "model_metrics.json").write_text(
        json.dumps(metrics, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    (output_dir / "manifest.json").write_text(
        json.dumps(
            {
                "generated_at": generated_at,
                "source": "https://api.openf1.org/v1",
                "years": years,
                "race_rows": len(performance),
                "latest_detailed_session_key": latest_session_key,
                "latest_detailed_race": latest_session.get("circuit_short_name"),
                "files": [
                    "driver_race_performance.csv",
                    "pit_stop_analysis.csv",
                    "live_snapshot.csv",
                    "live_sessions.csv",
                    "model_metrics.json",
                ],
            },
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Build the source-backed dashboard demo snapshot")
    parser.add_argument(
        "--years",
        nargs="+",
        type=int,
        default=[2024, 2025],
    )
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    args = parser.parse_args()

    build_demo_snapshot(OpenF1Client(), args.years, args.output_dir)
    logger.info("Demo snapshot written to %s", args.output_dir)


if __name__ == "__main__":
    main()
