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
OpenF1 API ──► historical_backfill.py ──► local raw JSON (data/raw/)
                                              │  (mirrors what a GCS bucket
                                              │   would hold in production --
                                              │   see "Known limitations" below)
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
export $(cat .env | grep -v '^#' | xargs)
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

**5. Real-time data: two options**

OpenF1's genuinely live (in-session) data now requires a paid account and
bearer token (historical data stays free -- see openf1.org/auth.html).
Two ways to demo the streaming architecture depending on whether you have one:

```bash
# Option A: genuinely live, during an actual F1 session, with a paid account
python -m src.ingest.live_poller --session-key active --poll-seconds 8 --access-token YOUR_TOKEN

# Option B: replay a historical race through the same streaming path at
# accelerated speed -- no paid access needed, works any time
python -m src.ingest.replay_poller --year 2024 --meeting-key 1229 --session-key 9472 --speed 60
```

Both write to the same `raw_live_*` BigQuery tables the dashboard's Live tab
reads from, so the dashboard doesn't need to know which one produced the data.

## Known limitations (read before presenting this)

Being direct about what's a genuine gap here, rather than glossing over it:

- **The ML model has a feature-leakage caveat.** Several features
  (`avg_lap_seconds`, `pit_stop_count`, etc.) are computed from the race
  whose outcome is being predicted -- this is a "what correlates with how
  the race ended" model, not a true pre-race or in-race prediction system.
  `train_model.py` reports a naive baseline (predict finish = start
  position) alongside the model's MAE specifically so this gets evaluated
  honestly rather than oversold. A proper fix would redefine the task with
  a fixed horizon (e.g. "using only data through lap 10, predict final
  position") built from cumulative, point-in-time features -- flagged as
  future work, not yet built.
- **Raw storage is local, not actually Cloud Storage.** `data/raw/` mirrors
  what a GCS bucket would hold in a production version, but no GCS client
  or upload path is implemented yet.
- **Live access requires a paid OpenF1 account** (see above); `replay_poller.py`
  is the practical workaround for demoing the real-time path without one.
- **No automated tests, CI, or infrastructure-as-code.** This is a portfolio
  build, not a production system -- see below for what a production version
  would add.

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
- **A lap-indexed feature horizon** for the ML model (see limitation above),
  so predictions are made from only the data available at that point in
  the race, not the full race outcome
- **pytest + GitHub Actions CI**, a Dockerfile, and Terraform for the GCP
  resources, none of which exist yet in this portfolio version
- **Actual GCS upload** in the ingestion path, replacing the local
  `data/raw/` mirror described above

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
