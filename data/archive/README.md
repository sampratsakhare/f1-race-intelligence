# Legacy mart snapshot

`driver_race_performance_legacy.csv` is preserved from the earlier iteration
but does not implement the current table contract: it lacks session timestamps
and explicit DNF/DNS/DSQ fields, and its grid values predate the
qualifying-to-race key correction.

Use `data/demo/driver_race_performance.csv` for the runnable dashboard demo,
or rebuild the current BigQuery mart from raw data. Do not use the archived
file for the current model evaluation.
