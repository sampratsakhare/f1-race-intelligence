# F1 Race Intelligence

An end-to-end data platform that ingests Formula 1 race data, models it in
BigQuery, powers a finishing-position prediction model, and streams live
session data during race weekends for near-real-time analytics.

## The one-sentence pitch

I built a pipeline that ingests historical and live F1 timing/telemetry
data into a cloud data warehouse, models it into analyst-ready marts,
trains a finishing-position prediction model, and serves both through a
live-updating dashboard.

## Architecture

```
OpenF1 API ──► historical_backfill.py ──► Cloud Storage (raw JSON)
                                              │
                                              ▼
                                     load_historical_to_bq.py
                                              │
                                              ▼
                                    BigQuery (raw ──► staging ──► marts)
                                              │
                       ┌──────────────────────┼──────────────────────┐
                       ▼                      ▼                      ▼
              driver_race_performance   pit_stop_analysis      train_model.py
                       │                      │                (LightGBM,
                       └──────────┬───────────┘                finishing
                                  ▼                             position)
                          Streamlit dashboard
                                  ▲
                                  │
OpenF1 API (live) ──► live_poller.py ──► BigQuery raw_live_* tables
```

**Batch path** (historical): `historical_backfill.py` → raw JSON → BigQuery
raw tables → SQL staging views → marts → ML training set.

**Streaming path** (live, during a race weekend): `live_poller.py` polls
OpenF1's real-time endpoints every ~8 seconds and streams new rows straight
into BigQuery, which the dashboard's "Live Session" tab reads from.

## Why two separate paths

Batch and streaming have different correctness requirements: historical
backfill needs completeness (every lap, every session), live polling
needs freshness (a few seconds old is fine, but stale data isn't). Keeping
them as separate code paths that land in the same warehouse is a common
real-world pattern — it's also a good thing to be able to explain in an
interview.

## Project layout

```
src/
  ingest/
    openf1_client.py        # rate-limited API client, timestamp normalization
    historical_backfill.py  # batch pull of past seasons -> raw JSON
    live_poller.py          # live polling loop -> BigQuery streaming insert
  load/
    bigquery_loader.py       # batch load + streaming insert helpers
    load_historical_to_bq.py # walks data/raw/ and loads everything
  sql/
    staging.sql               # cleaned views over raw tables
    marts/
      driver_race_performance.sql
      pit_stop_analysis.sql
  ml/
    train_model.py            # LightGBM finishing-position model
dashboard/
  app.py                     # Streamlit UI (historical + live tabs)
```

## Setup

```bash
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # fill in GCP_PROJECT_ID etc.
export $(cat .env | xargs)
```

You'll need a GCP project with the BigQuery API enabled and either:
- `gcloud auth application-default login` (local dev), or
- a service account key referenced by `GOOGLE_APPLICATION_CREDENTIALS`

## Running it

**1. Backfill historical data**
```bash
python -m src.ingest.historical_backfill --years 2023 2024 2025
python -m src.load.load_historical_to_bq
```

**2. Build staging views + marts**

Run `src/sql/staging.sql` then the mart SQL files in BigQuery (console,
`bq query`, or your SQL client of choice), after replacing `PROJECT.DATASET`
with your actual project/dataset.

**3. Train the model**
```bash
# Export driver_race_performance mart to CSV first (BigQuery console
# "Save results" -> CSV, or `bq query --format=csv`)
python -m src.ml.train_model --input data/marts/driver_race_performance.csv
```

**4. Run the dashboard**
```bash
streamlit run dashboard/app.py
```

**5. (During a live race weekend) start the live poller**
```bash
python -m src.ingest.live_poller --session-key latest --poll-seconds 8
```

## Scaling this up (what I'd add for production)

This is a two-weekend portfolio build, scoped deliberately lean. In a
production setting I'd add:

- **Pub/Sub + Dataflow** in place of the polling loop, for real streaming
  ingestion with proper windowing and exactly-once semantics
- **dbt** instead of hand-written SQL views, for testing, lineage, and
  documentation of the staging/mart layer
- **Airflow / Cloud Composer** to orchestrate the batch backfill and
  schedule mart refreshes, instead of manual script runs
- **Vertex AI** for model versioning, monitoring, and automated retraining
  instead of a local joblib file
- **Explicit BigQuery schemas** instead of autodetect, plus data quality
  checks (e.g. Great Expectations) on the staging layer

## Data sources

- [OpenF1 API](https://openf1.org) — real-time and historical timing,
  telemetry, tire, and pit data (2023 season onward)
- Rate limits: 3 req/s / 30 req/min on the free tier — the client in
  `openf1_client.py` throttles to stay well under this

## Known data quirks handled

- OpenF1 timestamps are inconsistent (ISO strings vs. Unix epoch depending
  on endpoint) — normalized in `openf1_client.normalize_timestamp`
- No bulk download endpoint — data is fetched session-by-session
- Team radio coverage dropped sharply starting in 2026 and isn't used here
