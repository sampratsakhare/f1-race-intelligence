-- Mart: pit_stop_analysis
-- One row per pit stop event, enriched with tire and nearest-position context.
-- Position movement around a stop is descriptive; it is not a causal estimate
-- of the stop's impact.

CREATE OR REPLACE TABLE `PROJECT.DATASET.pit_stop_analysis`
PARTITION BY DATE(pit_ts)
CLUSTER BY session_key, driver_number AS

WITH pit_stops AS (
    SELECT
        pit.session_key,
        pit.driver_number,
        pit.lap_number,
        pit.pit_ts,
        ARRAY_AGG(
            position.position IGNORE NULLS
            ORDER BY position.position_ts DESC
            LIMIT 1
        )[SAFE_OFFSET(0)] AS position_before_stop
    FROM `PROJECT.DATASET.stg_pit` pit
    LEFT JOIN `PROJECT.DATASET.stg_position` position
        ON pit.session_key = position.session_key
        AND pit.driver_number = position.driver_number
        AND position.position_ts <= pit.pit_ts
    GROUP BY 1, 2, 3, 4
),

position_after AS (
    SELECT
        pit.session_key,
        pit.driver_number,
        pit.lap_number,
        pit.pit_ts,
        ARRAY_AGG(
            position.position IGNORE NULLS
            ORDER BY position.position_ts ASC
            LIMIT 1
        )[SAFE_OFFSET(0)] AS position_after_stop
    FROM `PROJECT.DATASET.stg_pit` pit
    LEFT JOIN `PROJECT.DATASET.stg_position` position
        ON pit.session_key = position.session_key
        AND pit.driver_number = position.driver_number
        AND position.position_ts > pit.pit_ts
    GROUP BY 1, 2, 3, 4
),

stint_after AS (
    SELECT
        pit.session_key,
        pit.driver_number,
        pit.lap_number,
        pit.pit_ts,
        ARRAY_AGG(
            stint.compound IGNORE NULLS
            ORDER BY ABS(stint.lap_start - (pit.lap_number + 1))
            LIMIT 1
        )[SAFE_OFFSET(0)] AS tire_compound_fitted
    FROM `PROJECT.DATASET.stg_pit` pit
    LEFT JOIN `PROJECT.DATASET.stg_stints` stint
        ON pit.session_key = stint.session_key
        AND pit.driver_number = stint.driver_number
        AND stint.lap_start BETWEEN pit.lap_number AND pit.lap_number + 2
    GROUP BY 1, 2, 3, 4
)

SELECT
    p.session_key,
    p.driver_number,
    d.full_name,
    d.team_name,
    p.lap_number,
    p.pit_ts,
    p.lane_duration_seconds,
    p.stop_duration_seconds,
    stint.tire_compound_fitted,
    before.position_before_stop,
    after.position_after_stop,
    (
        before.position_before_stop - after.position_after_stop
    ) AS positions_changed_around_stop
FROM `PROJECT.DATASET.stg_pit` p
JOIN `PROJECT.DATASET.stg_drivers` d USING (session_key, driver_number)
LEFT JOIN position_before before
    USING (session_key, driver_number, lap_number, pit_ts)
LEFT JOIN position_after after
    USING (session_key, driver_number, lap_number, pit_ts)
LEFT JOIN stint_after stint
    USING (session_key, driver_number, lap_number, pit_ts);
