-- Staging layer: one clean view per raw source table.
-- Run these against your BigQuery dataset after historical data is loaded.
-- Replace `PROJECT.DATASET` with your actual project.dataset.

-- ============================================================
-- stg_laps: one row per driver per lap, with clean types
-- ============================================================
CREATE OR REPLACE VIEW `PROJECT.DATASET.stg_laps` AS
SELECT
    session_key,
    driver_number,
    lap_number,
    CAST(lap_duration AS FLOAT64)          AS lap_duration_seconds,
    CAST(duration_sector_1 AS FLOAT64)     AS sector_1_seconds,
    CAST(duration_sector_2 AS FLOAT64)     AS sector_2_seconds,
    CAST(duration_sector_3 AS FLOAT64)     AS sector_3_seconds,
    is_pit_out_lap,
    TIMESTAMP(date_start)                  AS lap_start_ts
FROM `PROJECT.DATASET.raw_laps`
WHERE lap_duration IS NOT NULL;  -- drop incomplete/DNF laps for clean analysis

-- ============================================================
-- stg_stints: tire stint info per driver per session
-- ============================================================
CREATE OR REPLACE VIEW `PROJECT.DATASET.stg_stints` AS
SELECT
    session_key,
    driver_number,
    stint_number,
    compound,
    lap_start,
    lap_end,
    (lap_end - lap_start + 1)              AS stint_length_laps,
    tyre_age_at_start
FROM `PROJECT.DATASET.raw_stints`;

-- ============================================================
-- stg_pit: pit stop events, cleaned
-- ============================================================
CREATE OR REPLACE VIEW `PROJECT.DATASET.stg_pit` AS
SELECT
    session_key,
    driver_number,
    lap_number,
    CAST(pit_duration AS FLOAT64)          AS pit_duration_seconds,
    TIMESTAMP(date)                        AS pit_ts
FROM `PROJECT.DATASET.raw_pit`
WHERE pit_duration IS NOT NULL;

-- ============================================================
-- stg_position: final and intermediate classification per driver
-- ============================================================
CREATE OR REPLACE VIEW `PROJECT.DATASET.stg_position` AS
SELECT
    session_key,
    driver_number,
    position,
    TIMESTAMP(date)                        AS position_ts
FROM `PROJECT.DATASET.raw_position`;

-- ============================================================
-- stg_sessions / stg_meetings: race weekend metadata
-- ============================================================
CREATE OR REPLACE VIEW `PROJECT.DATASET.stg_sessions` AS
SELECT
    session_key,
    meeting_key,
    session_name,
    session_type,
    circuit_short_name,
    country_name,
    TIMESTAMP(date_start)                  AS session_start_ts,
    TIMESTAMP(date_end)                    AS session_end_ts,
    year
FROM `PROJECT.DATASET.raw_session`;

CREATE OR REPLACE VIEW `PROJECT.DATASET.stg_drivers` AS
SELECT
    session_key,
    driver_number,
    full_name,
    team_name,
    country_code
FROM `PROJECT.DATASET.raw_drivers`;
