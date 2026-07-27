"""Bounded BigQuery reads used by the Streamlit dashboard."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

import pandas as pd
from google.cloud import bigquery

_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")


@dataclass(frozen=True)
class WarehouseConfig:
    project_id: str
    dataset: str

    def __post_init__(self) -> None:
        for label, value in (
            ("GCP_PROJECT_ID", self.project_id),
            ("BQ_DATASET", self.dataset),
        ):
            if not value or not _IDENTIFIER_PATTERN.fullmatch(value):
                raise ValueError(f"{label} contains an invalid BigQuery identifier")

    @property
    def prefix(self) -> str:
        return f"{self.project_id}.{self.dataset}"


def load_race_catalog(
    client: bigquery.Client,
    config: WarehouseConfig,
) -> pd.DataFrame:
    query = f"""
        SELECT
            session_key,
            ANY_VALUE(year) AS year,
            ANY_VALUE(circuit_short_name) AS circuit_short_name,
            MIN(session_start_ts) AS session_start_ts
        FROM `{config.prefix}.driver_race_performance`
        WHERE finishing_position IS NOT NULL
        GROUP BY session_key
        ORDER BY session_start_ts DESC
        LIMIT 150
    """
    return client.query(query).to_dataframe()


def load_driver_performance(
    client: bigquery.Client,
    config: WarehouseConfig,
    *,
    year: int,
    session_key: int | None = None,
) -> pd.DataFrame:
    filters = ["year = @year"]
    parameters: list[bigquery.ScalarQueryParameter] = [
        bigquery.ScalarQueryParameter("year", "INT64", year),
    ]
    if session_key is not None:
        filters.append("session_key = @session_key")
        parameters.append(bigquery.ScalarQueryParameter("session_key", "INT64", session_key))

    query = f"""
        SELECT
            session_key,
            circuit_short_name,
            country_name,
            year,
            session_start_ts,
            driver_number,
            full_name,
            team_name,
            laps_completed,
            clean_laps_analyzed,
            avg_lap_seconds,
            best_lap_seconds,
            lap_consistency_stddev,
            pit_stop_count,
            avg_pit_duration_seconds,
            total_pit_time_seconds,
            avg_stationary_stop_seconds,
            starting_position,
            finishing_position,
            did_not_finish,
            did_not_start,
            disqualified,
            positions_gained
        FROM `{config.prefix}.driver_race_performance`
        WHERE {" AND ".join(filters)}
        ORDER BY session_start_ts DESC, finishing_position
        LIMIT 3000
    """
    job_config = bigquery.QueryJobConfig(query_parameters=parameters)
    return client.query(query, job_config=job_config).to_dataframe()


def load_pit_analysis(
    client: bigquery.Client,
    config: WarehouseConfig,
    *,
    session_key: int,
) -> pd.DataFrame:
    query = f"""
        SELECT
            session_key,
            driver_number,
            full_name,
            team_name,
            lap_number,
            pit_ts,
            lane_duration_seconds,
            stop_duration_seconds,
            tire_compound_fitted,
            position_before_stop,
            position_after_stop,
            positions_changed_around_stop
        FROM `{config.prefix}.pit_stop_analysis`
        WHERE session_key = @session_key
        ORDER BY lap_number, driver_number
        LIMIT 200
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[bigquery.ScalarQueryParameter("session_key", "INT64", session_key)]
    )
    return client.query(query, job_config=job_config).to_dataframe()


def load_table_freshness(
    client: bigquery.Client,
    config: WarehouseConfig,
) -> pd.DataFrame:
    query = f"""
        SELECT
            table_id AS table_name,
            TIMESTAMP_MILLIS(last_modified_time) AS last_modified_at
        FROM `{config.prefix}.__TABLES__`
        WHERE table_id IN (
            'driver_race_performance',
            'pit_stop_analysis',
            'raw_live_laps',
            'raw_live_position',
            'raw_live_intervals',
            'raw_live_pit'
        )
        ORDER BY table_name
    """
    return client.query(query).to_dataframe()


def load_live_sessions(
    client: bigquery.Client,
    config: WarehouseConfig,
) -> pd.DataFrame:
    query = f"""
        WITH events AS (
            SELECT session_key, _source_event_time, _ingested_at, _event_id
            FROM `{config.prefix}.raw_live_laps`
            UNION ALL
            SELECT session_key, _source_event_time, _ingested_at, _event_id
            FROM `{config.prefix}.raw_live_position`
            UNION ALL
            SELECT session_key, _source_event_time, _ingested_at, _event_id
            FROM `{config.prefix}.raw_live_intervals`
            UNION ALL
            SELECT session_key, _source_event_time, _ingested_at, _event_id
            FROM `{config.prefix}.raw_live_pit`
        )
        SELECT
            session_key,
            COUNT(DISTINCT _event_id) AS event_count,
            MIN(_source_event_time) AS first_source_event_at,
            MAX(_source_event_time) AS latest_source_event_at,
            MAX(_ingested_at) AS latest_ingested_at
        FROM events
        WHERE session_key IS NOT NULL
        GROUP BY session_key
        ORDER BY latest_ingested_at DESC
        LIMIT 25
    """
    return client.query(query).to_dataframe()


def load_live_snapshot(
    client: bigquery.Client,
    config: WarehouseConfig,
    *,
    session_key: int,
) -> pd.DataFrame:
    query = f"""
        WITH lap_events AS (
            SELECT * EXCEPT (duplicate_rank)
            FROM (
                SELECT
                    *,
                    ROW_NUMBER() OVER (
                        PARTITION BY _event_id ORDER BY _ingested_at DESC
                    ) AS duplicate_rank
                FROM `{config.prefix}.raw_live_laps`
                WHERE session_key = @session_key
            )
            WHERE duplicate_rank = 1
        ),
        latest_lap AS (
            SELECT
                session_key,
                driver_number,
                lap_number,
                lap_duration,
                duration_sector_1,
                duration_sector_2,
                duration_sector_3,
                is_pit_out_lap,
                _source_event_time AS lap_event_at,
                _ingested_at AS lap_ingested_at
            FROM lap_events
            QUALIFY ROW_NUMBER() OVER (
                PARTITION BY driver_number
                ORDER BY lap_number DESC, _source_event_time DESC
            ) = 1
        ),
        position_events AS (
            SELECT * EXCEPT (duplicate_rank)
            FROM (
                SELECT
                    *,
                    ROW_NUMBER() OVER (
                        PARTITION BY _event_id ORDER BY _ingested_at DESC
                    ) AS duplicate_rank
                FROM `{config.prefix}.raw_live_position`
                WHERE session_key = @session_key
            )
            WHERE duplicate_rank = 1
        ),
        latest_position AS (
            SELECT
                session_key,
                driver_number,
                position,
                _source_event_time AS position_event_at,
                _ingested_at AS position_ingested_at
            FROM position_events
            QUALIFY ROW_NUMBER() OVER (
                PARTITION BY driver_number
                ORDER BY _source_event_time DESC
            ) = 1
        ),
        interval_events AS (
            SELECT * EXCEPT (duplicate_rank)
            FROM (
                SELECT
                    *,
                    ROW_NUMBER() OVER (
                        PARTITION BY _event_id ORDER BY _ingested_at DESC
                    ) AS duplicate_rank
                FROM `{config.prefix}.raw_live_intervals`
                WHERE session_key = @session_key
            )
            WHERE duplicate_rank = 1
        ),
        latest_interval AS (
            SELECT
                session_key,
                driver_number,
                gap_to_leader,
                interval,
                _source_event_time AS interval_event_at,
                _ingested_at AS interval_ingested_at
            FROM interval_events
            QUALIFY ROW_NUMBER() OVER (
                PARTITION BY driver_number
                ORDER BY _source_event_time DESC
            ) = 1
        ),
        driver_keys AS (
            SELECT driver_number FROM latest_lap
            UNION DISTINCT
            SELECT driver_number FROM latest_position
            UNION DISTINCT
            SELECT driver_number FROM latest_interval
        ),
        drivers AS (
            SELECT driver_number, ANY_VALUE(full_name) AS full_name
            FROM `{config.prefix}.stg_drivers`
            WHERE session_key = @session_key
            GROUP BY driver_number
        )
        SELECT
            @session_key AS session_key,
            keys.driver_number,
            COALESCE(
                drivers.full_name,
                CONCAT('Driver #', CAST(keys.driver_number AS STRING))
            ) AS driver,
            position.position,
            lap.lap_number,
            lap.lap_duration,
            lap.duration_sector_1,
            lap.duration_sector_2,
            lap.duration_sector_3,
            lap.is_pit_out_lap,
            intervals.gap_to_leader,
            intervals.interval,
            (
                SELECT MAX(event_at)
                FROM UNNEST([
                    lap.lap_event_at,
                    position.position_event_at,
                    intervals.interval_event_at
                ]) AS event_at
            ) AS latest_source_event_at,
            (
                SELECT MAX(ingested_at)
                FROM UNNEST([
                    lap.lap_ingested_at,
                    position.position_ingested_at,
                    intervals.interval_ingested_at
                ]) AS ingested_at
            ) AS latest_ingested_at
        FROM driver_keys keys
        LEFT JOIN latest_lap lap USING (driver_number)
        LEFT JOIN latest_position position USING (driver_number)
        LEFT JOIN latest_interval intervals USING (driver_number)
        LEFT JOIN drivers USING (driver_number)
        ORDER BY position, driver_number
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[bigquery.ScalarQueryParameter("session_key", "INT64", session_key)]
    )
    return client.query(query, job_config=job_config).to_dataframe()


def dataframe_records(frame: pd.DataFrame) -> list[dict[str, Any]]:
    """Return JSON-compatible records for bounded display or tests."""
    normalized = frame.astype(object).where(pd.notna(frame), None)
    return normalized.to_dict(orient="records")
