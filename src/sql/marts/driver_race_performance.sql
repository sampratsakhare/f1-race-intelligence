-- Mart: driver_race_performance
-- One row per driver per race. This is the main table your dashboard
-- and ML feature set will read from.

CREATE OR REPLACE TABLE `PROJECT.DATASET.driver_race_performance` AS

WITH lap_stats AS (
    SELECT
        session_key,
        driver_number,
        COUNT(*)                                       AS laps_completed,
        AVG(lap_duration_seconds)                       AS avg_lap_seconds,
        MIN(lap_duration_seconds)                       AS best_lap_seconds,
        STDDEV(lap_duration_seconds)                     AS lap_consistency_stddev
    FROM `PROJECT.DATASET.stg_laps`
    GROUP BY session_key, driver_number
),

pit_stats AS (
    SELECT
        session_key,
        driver_number,
        COUNT(*)                                       AS pit_stop_count,
        AVG(pit_duration_seconds)                        AS avg_pit_duration_seconds,
        SUM(pit_duration_seconds)                        AS total_pit_time_seconds
    FROM `PROJECT.DATASET.stg_pit`
    GROUP BY session_key, driver_number
),

final_position AS (
    -- last recorded position per driver per session = finishing position
    SELECT
        session_key,
        driver_number,
        position AS finishing_position
    FROM (
        SELECT
            session_key,
            driver_number,
            position,
            ROW_NUMBER() OVER (
                PARTITION BY session_key, driver_number
                ORDER BY position_ts DESC
            ) AS rn
        FROM `PROJECT.DATASET.stg_position`
    )
    WHERE rn = 1
),

starting_position AS (
    -- first recorded position per driver per session ~= grid position
    SELECT
        session_key,
        driver_number,
        position AS starting_position
    FROM (
        SELECT
            session_key,
            driver_number,
            position,
            ROW_NUMBER() OVER (
                PARTITION BY session_key, driver_number
                ORDER BY position_ts ASC
            ) AS rn
        FROM `PROJECT.DATASET.stg_position`
    )
    WHERE rn = 1
)

SELECT
    s.session_key,
    s.circuit_short_name,
    s.country_name,
    s.year,
    d.driver_number,
    d.full_name,
    d.team_name,
    ls.laps_completed,
    ls.avg_lap_seconds,
    ls.best_lap_seconds,
    ls.lap_consistency_stddev,
    ps.pit_stop_count,
    ps.avg_pit_duration_seconds,
    ps.total_pit_time_seconds,
    sp.starting_position,
    fp.finishing_position,
    (sp.starting_position - fp.finishing_position)   AS positions_gained
FROM `PROJECT.DATASET.stg_sessions` s
JOIN `PROJECT.DATASET.stg_drivers` d USING (session_key)
LEFT JOIN lap_stats ls USING (session_key, driver_number)
LEFT JOIN pit_stats ps USING (session_key, driver_number)
LEFT JOIN starting_position sp USING (session_key, driver_number)
LEFT JOIN final_position fp USING (session_key, driver_number)
WHERE s.session_name = 'Race';
