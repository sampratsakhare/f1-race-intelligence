# Data dictionary

This document defines the analytical grain and meaning of the curated
BigQuery tables. Raw tables intentionally preserve OpenF1 source fields.

## `driver_race_performance`

Grain: one row per `session_key, driver_number` for race sessions.

| Column | Type | Definition |
|---|---|---|
| `session_key` | integer | OpenF1 session identifier |
| `session_start_ts` | timestamp | Scheduled UTC session start; table partition key |
| `driver_number` | integer | Driver number within the session |
| `starting_position` | integer | Position from OpenF1 `starting_grid`; extraction records both the qualifying source key and intended race target key |
| `finishing_position` | integer | Final classified position from OpenF1 `session_result` |
| `positions_gained` | integer | `starting_position - finishing_position`; positive means a net gain |
| `laps_completed` | integer | Classified laps completed from `session_result` |
| `clean_laps_analyzed` | integer | Laps retained by the clean-lap rule |
| `avg_lap_seconds` | float | Mean retained lap duration |
| `best_lap_seconds` | float | Minimum retained lap duration |
| `lap_consistency_stddev` | float | Sample standard deviation of retained lap duration |
| `pit_stop_count` | integer | Number of OpenF1 pit events |
| `avg_pit_duration_seconds` | float | Mean pit-lane duration |
| `total_pit_time_seconds` | float | Sum of pit-lane duration |
| `avg_stationary_stop_seconds` | float | Mean stationary duration where available |
| `did_not_finish` | boolean | OpenF1 final-result DNF flag |
| `did_not_start` | boolean | OpenF1 final-result DNS flag |
| `disqualified` | boolean | OpenF1 final-result DSQ flag |

Clean-lap rule:

- exclude pit-out laps;
- calculate the race-level median lap duration;
- retain laps between 80% and 120% of that median.

The rule removes obvious non-representative laps but is not a full traffic,
weather, flag, or safety-car adjustment.

## `pit_stop_analysis`

Grain: one row per pit event, identified by session, driver, lap, and event
timestamp.

| Column | Type | Definition |
|---|---|---|
| `pit_ts` | timestamp | Source pit event time; table partition key |
| `lap_number` | integer | Lap associated with the pit event |
| `lane_duration_seconds` | float | Time through the pit lane; falls back to deprecated `pit_duration` |
| `stop_duration_seconds` | float | Stationary stop time; only present in newer source data |
| `tire_compound_fitted` | string | Compound for the nearest matching post-stop stint |
| `position_before_stop` | integer | Latest observed position at or before `pit_ts` |
| `position_after_stop` | integer | First observed position after `pit_ts` |
| `positions_changed_around_stop` | integer | `position_before_stop - position_after_stop` |

`positions_changed_around_stop` is observational context. It must not be
described as time gained, strategy value, or causal pit-stop impact.

## `raw_live_*`

Grain: one source event. All live tables share this ingestion envelope:

| Column | Type | Definition |
|---|---|---|
| `_event_id` | string | SHA-256 of canonical endpoint + source record |
| `_source_endpoint` | string | `laps`, `position`, `intervals`, or `pit` |
| `_source_event_time` | timestamp | Endpoint-specific source timestamp |
| `_ingested_at` | timestamp | UTC time at which the producer prepared the event |
| `session_key` | integer | OpenF1 session identifier |
| `driver_number` | integer | Driver number where applicable |

Pub/Sub and BigQuery streaming are at-least-once. Consumers and dashboard
queries deduplicate by `_event_id`, retaining the latest ingestion.

## ML evaluation metrics

| Metric | Definition |
|---|---|
| Candidate MAE | Mean absolute difference between classified finish and predicted within-race rank |
| Grid baseline MAE | Mean absolute difference between classified finish and starting position |
| Top-3 accuracy | Share of actual top-three drivers also predicted in the top three |
| Mean Spearman | Average race-level rank correlation between actual and predicted order |
| Race win rate | Share of holdout races where candidate MAE is lower than grid MAE |
| 95% improvement interval | Race-cluster bootstrap interval for mean `(grid MAE - candidate MAE)` |
| Promotion | At least five test races, lower candidate MAE, and a positive lower bootstrap bound |

All rows from each test race stay together. MAE, top-three accuracy, and rank
correlation use rows with a non-null classified finishing position. DNF/DNS/DSQ
rows without a source position are retained when building prior-status
features, counted in target-coverage metadata, and excluded from supervised
evaluation. No post-race pace, pit, or result fields are used as pre-race
features.
