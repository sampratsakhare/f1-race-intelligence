-- Mart: pit_stop_analysis
-- One row per pit stop event, enriched with tire compound context and
-- the position change it produced. Useful for undercut/overcut analysis
-- and as ML features for pit-strategy prediction.
--
-- Note: OpenF1's position data is timestamped, not lap-indexed, so we
-- match each pit stop to the nearest position reading just before and
-- just after the stop's timestamp. BigQuery doesn't support correlated
-- subqueries with multiple correlated columns, so this is done via
-- inequality joins + ROW_NUMBER() rather than scalar subqueries.

CREATE OR REPLACE TABLE `PROJECT.DATASET.pit_stop_analysis` AS

WITH pit_stops AS (
    SELECT
        session_key,
        driver_number,
        lap_number,
        pit_duration_seconds,
        pit_ts
    FROM `PROJECT.DATASET.stg_pit`
),

position_before AS (
    SELECT
        ps.session_key,
        ps.driver_number,
        ps.lap_number,
        pos.position,
        ROW_NUMBER() OVER (
            PARTITION BY ps.session_key, ps.driver_number, ps.lap_number
            ORDER BY pos.position_ts DESC
        ) AS rn
    FROM pit_stops ps
    JOIN `PROJECT.DATASET.stg_position` pos
        ON pos.session_key = ps.session_key
        AND pos.driver_number = ps.driver_number
        AND pos.position_ts <= ps.pit_ts
),

position_after AS (
    SELECT
        ps.session_key,
        ps.driver_number,
        ps.lap_number,
        pos.position,
        ROW_NUMBER() OVER (
            PARTITION BY ps.session_key, ps.driver_number, ps.lap_number
            ORDER BY pos.position_ts ASC
        ) AS rn
    FROM pit_stops ps
    JOIN `PROJECT.DATASET.stg_position` pos
        ON pos.session_key = ps.session_key
        AND pos.driver_number = ps.driver_number
        AND pos.position_ts > ps.pit_ts
)

SELECT
    p.session_key,
    p.driver_number,
    d.full_name,
    d.team_name,
    p.lap_number,
    p.pit_duration_seconds,
    st.compound AS tire_compound_fitted,
    pb.position AS position_before_stop,
    pa.position AS position_after_stop,
    (pb.position - pa.position) AS positions_change_from_stop
FROM pit_stops p
JOIN `PROJECT.DATASET.stg_drivers` d USING (session_key, driver_number)
LEFT JOIN `PROJECT.DATASET.stg_stints` st
    ON p.session_key = st.session_key
    AND p.driver_number = st.driver_number
    AND p.lap_number = st.lap_start
LEFT JOIN position_before pb
    ON p.session_key = pb.session_key
    AND p.driver_number = pb.driver_number
    AND p.lap_number = pb.lap_number
    AND pb.rn = 1
LEFT JOIN position_after pa
    ON p.session_key = pa.session_key
    AND p.driver_number = pa.driver_number
    AND p.lap_number = pa.lap_number
    AND pa.rn = 1;
