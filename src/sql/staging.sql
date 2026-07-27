-- Staging layer: one clean view per raw source table.
-- Run these against your BigQuery dataset after historical data is loaded.
-- Replace `PROJECT.DATASET` with your actual project.dataset.

-- ============================================================
-- stg_laps: one row per driver per lap, with clean types
-- ============================================================
CREATE OR REPLACE VIEW `PROJECT.DATASET.stg_laps` AS
SELECT
    SAFE_CAST(session_key AS INT64)                  AS session_key,
    SAFE_CAST(driver_number AS INT64)                AS driver_number,
    SAFE_CAST(lap_number AS INT64)                   AS lap_number,
    SAFE_CAST(lap_duration AS FLOAT64)               AS lap_duration_seconds,
    SAFE_CAST(duration_sector_1 AS FLOAT64)          AS sector_1_seconds,
    SAFE_CAST(duration_sector_2 AS FLOAT64)          AS sector_2_seconds,
    SAFE_CAST(duration_sector_3 AS FLOAT64)          AS sector_3_seconds,
    COALESCE(SAFE_CAST(is_pit_out_lap AS BOOL), FALSE) AS is_pit_out_lap,
    SAFE_CAST(date_start AS TIMESTAMP)               AS lap_start_ts
FROM `PROJECT.DATASET.raw_laps`
WHERE SAFE_CAST(lap_duration AS FLOAT64) > 0;

-- ============================================================
-- stg_stints: tire stint info per driver per session
-- ============================================================
CREATE OR REPLACE VIEW `PROJECT.DATASET.stg_stints` AS
SELECT
    SAFE_CAST(session_key AS INT64)       AS session_key,
    SAFE_CAST(driver_number AS INT64)     AS driver_number,
    SAFE_CAST(stint_number AS INT64)      AS stint_number,
    compound,
    SAFE_CAST(lap_start AS INT64)         AS lap_start,
    SAFE_CAST(lap_end AS INT64)           AS lap_end,
    (
        SAFE_CAST(lap_end AS INT64)
        - SAFE_CAST(lap_start AS INT64)
        + 1
    )                                     AS stint_length_laps,
    SAFE_CAST(tyre_age_at_start AS INT64) AS tyre_age_at_start
FROM `PROJECT.DATASET.raw_stints`;

-- ============================================================
-- stg_pit: pit stop events, cleaned
-- ============================================================
CREATE OR REPLACE VIEW `PROJECT.DATASET.stg_pit` AS
SELECT
    SAFE_CAST(session_key AS INT64)        AS session_key,
    SAFE_CAST(driver_number AS INT64)      AS driver_number,
    SAFE_CAST(lap_number AS INT64)         AS lap_number,
    COALESCE(
        SAFE_CAST(lane_duration AS FLOAT64),
        SAFE_CAST(pit_duration AS FLOAT64)
    )                                      AS lane_duration_seconds,
    SAFE_CAST(stop_duration AS FLOAT64)    AS stop_duration_seconds,
    SAFE_CAST(date AS TIMESTAMP)           AS pit_ts
FROM `PROJECT.DATASET.raw_pit`
WHERE COALESCE(
    SAFE_CAST(lane_duration AS FLOAT64),
    SAFE_CAST(pit_duration AS FLOAT64)
) > 0;

-- ============================================================
-- stg_position: final and intermediate classification per driver
-- ============================================================
CREATE OR REPLACE VIEW `PROJECT.DATASET.stg_position` AS
SELECT
    SAFE_CAST(session_key AS INT64)       AS session_key,
    SAFE_CAST(driver_number AS INT64)     AS driver_number,
    SAFE_CAST(position AS INT64)          AS position,
    SAFE_CAST(date AS TIMESTAMP)          AS position_ts
FROM `PROJECT.DATASET.raw_position`;

-- ============================================================
-- stg_sessions / stg_meetings: race weekend metadata
-- ============================================================
CREATE OR REPLACE VIEW `PROJECT.DATASET.stg_sessions` AS
SELECT
    SAFE_CAST(session_key AS INT64)       AS session_key,
    SAFE_CAST(meeting_key AS INT64)       AS meeting_key,
    session_name,
    session_type,
    circuit_short_name,
    country_name,
    SAFE_CAST(date_start AS TIMESTAMP)    AS session_start_ts,
    SAFE_CAST(date_end AS TIMESTAMP)      AS session_end_ts,
    SAFE_CAST(year AS INT64)              AS year
FROM `PROJECT.DATASET.raw_session`;

CREATE OR REPLACE VIEW `PROJECT.DATASET.stg_drivers` AS
SELECT
    SAFE_CAST(session_key AS INT64)       AS session_key,
    SAFE_CAST(driver_number AS INT64)     AS driver_number,
    full_name,
    team_name,
    country_code
FROM `PROJECT.DATASET.raw_drivers`
QUALIFY ROW_NUMBER() OVER (
    PARTITION BY session_key, driver_number
    ORDER BY full_name
) = 1;

-- ============================================================
-- Source starting grid and final classified result
-- ============================================================
CREATE OR REPLACE VIEW `PROJECT.DATASET.stg_starting_grid` AS
SELECT
    SAFE_CAST(meeting_key AS INT64)       AS meeting_key,
    SAFE_CAST(session_key AS INT64)       AS grid_session_key,
    SAFE_CAST(_target_session_key AS INT64) AS session_key,
    SAFE_CAST(driver_number AS INT64)     AS driver_number,
    SAFE_CAST(position AS INT64)          AS starting_position
FROM `PROJECT.DATASET.raw_starting_grid`
WHERE SAFE_CAST(position AS INT64) > 0
  AND SAFE_CAST(_target_session_key AS INT64) IS NOT NULL
QUALIFY ROW_NUMBER() OVER (
    PARTITION BY _target_session_key, driver_number
    ORDER BY SAFE_CAST(position AS INT64)
) = 1;

CREATE OR REPLACE VIEW `PROJECT.DATASET.stg_session_result` AS
SELECT
    SAFE_CAST(session_key AS INT64)       AS session_key,
    SAFE_CAST(driver_number AS INT64)     AS driver_number,
    SAFE_CAST(position AS INT64)          AS finishing_position,
    SAFE_CAST(number_of_laps AS INT64)    AS official_laps_completed,
    COALESCE(SAFE_CAST(dnf AS BOOL), FALSE) AS did_not_finish,
    COALESCE(SAFE_CAST(dns AS BOOL), FALSE) AS did_not_start,
    COALESCE(SAFE_CAST(dsq AS BOOL), FALSE) AS disqualified
FROM `PROJECT.DATASET.raw_session_result`
QUALIFY ROW_NUMBER() OVER (
    PARTITION BY session_key, driver_number
    ORDER BY SAFE_CAST(position AS INT64)
) = 1;
