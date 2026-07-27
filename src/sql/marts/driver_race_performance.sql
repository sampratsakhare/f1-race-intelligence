-- Mart: driver_race_performance
-- One row per driver per race. This is the main table your dashboard
-- and ML feature set will read from.

CREATE OR REPLACE TABLE `PROJECT.DATASET.driver_race_performance`
PARTITION BY DATE(session_start_ts)
CLUSTER BY session_key, driver_number AS

WITH lap_distribution AS (
    SELECT
        session_key,
        APPROX_QUANTILES(lap_duration_seconds, 100)[OFFSET(50)] AS median_lap_seconds
    FROM `PROJECT.DATASET.stg_laps`
    WHERE NOT is_pit_out_lap
    GROUP BY session_key
),

clean_laps AS (
    SELECT laps.*
    FROM `PROJECT.DATASET.stg_laps` laps
    JOIN lap_distribution distribution USING (session_key)
    WHERE NOT laps.is_pit_out_lap
      AND laps.lap_duration_seconds BETWEEN
          distribution.median_lap_seconds * 0.80
          AND distribution.median_lap_seconds * 1.20
),

lap_stats AS (
    SELECT
        session_key,
        driver_number,
        COUNT(*)                                       AS clean_laps_analyzed,
        AVG(lap_duration_seconds)                       AS avg_lap_seconds,
        MIN(lap_duration_seconds)                       AS best_lap_seconds,
        STDDEV(lap_duration_seconds)                    AS lap_consistency_stddev
    FROM clean_laps
    GROUP BY session_key, driver_number
),

pit_stats AS (
    SELECT
        session_key,
        driver_number,
        COUNT(*)                                       AS pit_stop_count,
        AVG(lane_duration_seconds)                      AS avg_pit_duration_seconds,
        SUM(lane_duration_seconds)                      AS total_pit_time_seconds,
        AVG(stop_duration_seconds)                      AS avg_stationary_stop_seconds
    FROM `PROJECT.DATASET.stg_pit`
    GROUP BY session_key, driver_number
)

SELECT
    s.session_key,
    s.circuit_short_name,
    s.country_name,
    s.year,
    s.session_start_ts,
    d.driver_number,
    d.full_name,
    d.team_name,
    result.official_laps_completed                 AS laps_completed,
    ls.clean_laps_analyzed,
    ls.avg_lap_seconds,
    ls.best_lap_seconds,
    ls.lap_consistency_stddev,
    COALESCE(ps.pit_stop_count, 0)                 AS pit_stop_count,
    ps.avg_pit_duration_seconds,
    ps.total_pit_time_seconds,
    ps.avg_stationary_stop_seconds,
    grid.starting_position,
    result.finishing_position,
    result.did_not_finish,
    result.did_not_start,
    result.disqualified,
    (
        grid.starting_position - result.finishing_position
    )                                              AS positions_gained
FROM `PROJECT.DATASET.stg_sessions` s
JOIN `PROJECT.DATASET.stg_drivers` d USING (session_key)
LEFT JOIN lap_stats ls USING (session_key, driver_number)
LEFT JOIN pit_stats ps USING (session_key, driver_number)
LEFT JOIN `PROJECT.DATASET.stg_starting_grid` grid
    USING (session_key, driver_number)
LEFT JOIN `PROJECT.DATASET.stg_session_result` result
    USING (session_key, driver_number)
WHERE s.session_name = 'Race';
