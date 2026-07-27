-- Warehouse contract checks. These fail the transformation job if a core
-- analytical invariant is violated.

ASSERT (
    SELECT COUNT(*) = COUNT(
        DISTINCT FORMAT('%d:%d', session_key, driver_number)
    )
    FROM `PROJECT.DATASET.driver_race_performance`
) AS 'driver_race_performance must be unique by session_key and driver_number';

ASSERT (
    SELECT COUNTIF(
        session_key IS NULL
        OR driver_number IS NULL
        OR session_start_ts IS NULL
    ) = 0
    FROM `PROJECT.DATASET.driver_race_performance`
) AS 'performance mart keys and session timestamps must be populated';

ASSERT (
    SELECT COUNTIF(
        starting_position IS NOT NULL
        AND starting_position < 1
    ) = 0
    FROM `PROJECT.DATASET.driver_race_performance`
) AS 'starting positions must be positive';

ASSERT (
    SELECT COUNTIF(
        finishing_position IS NOT NULL
        AND finishing_position < 1
    ) = 0
    FROM `PROJECT.DATASET.driver_race_performance`
) AS 'finishing positions must be positive';

ASSERT (
    SELECT SAFE_DIVIDE(
        COUNTIF(
            starting_position IS NOT NULL
            AND (
                finishing_position IS NOT NULL
                OR did_not_finish
                OR did_not_start
                OR disqualified
            )
        ),
        COUNT(*)
    ) >= 0.95
    FROM `PROJECT.DATASET.driver_race_performance`
) AS 'at least 95% of rows need a grid plus a classified position or status';

ASSERT (
    SELECT COUNTIF(
        lane_duration_seconds IS NULL
        OR lane_duration_seconds <= 0
    ) = 0
    FROM `PROJECT.DATASET.pit_stop_analysis`
) AS 'pit-stop rows must have a positive pit-lane duration';

ASSERT (
    SELECT COUNT(*) = COUNT(
        DISTINCT FORMAT(
            '%d:%d:%d:%s',
            session_key,
            driver_number,
            lap_number,
            CAST(pit_ts AS STRING)
        )
    )
    FROM `PROJECT.DATASET.pit_stop_analysis`
) AS 'pit_stop_analysis must be unique at the pit-event grain';
