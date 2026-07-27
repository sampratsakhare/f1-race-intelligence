# Operations runbook

## Pipeline order

1. Backfill source JSON.
2. Review each session `_manifest.json`; do not load an incomplete run without
   understanding the missing endpoints.
3. Load endpoint files into BigQuery raw tables.
4. Run staging, marts, and warehouse assertions.
5. Train the candidate and inspect the baseline comparison.
6. Run the dashboard or deploy the containers.

The corresponding commands are:

```bash
make backfill YEARS="2023 2024 2025 2026"
make load
make transform
make train
make dashboard
```

## Before a deployment

```bash
make check
python -m src.load.run_transformations --dry-run
terraform -chdir=infra/terraform fmt -check
terraform -chdir=infra/terraform validate
terraform -chdir=infra/terraform plan
```

Use immutable container tags or digests. Confirm the dashboard service account
has only BigQuery viewer/job permissions and that the consumer is invokable
only by the Pub/Sub push identity.

## Replay smoke test

Start with a bounded dry run:

```bash
python -m src.stream.replay \
  --session-key <SESSION_KEY> \
  --sink stdout \
  --max-events 100
```

Then publish a bounded sample to Pub/Sub and inspect:

- Cloud Run request/error count;
- Pub/Sub oldest unacked message age;
- dead-letter subscription message count;
- BigQuery `raw_live_*` row arrival;
- dashboard ingestion freshness.

## Failure handling

### Historical endpoint failure

Symptom: a session manifest has `status: failed`.

Action:

1. Read the endpoint error in `_manifest.json`.
2. Correct credentials, rate-limit pressure, or upstream availability.
3. Rerun without `--force`; successful endpoint files are reused and missing
   endpoints are fetched.
4. Use `--force` only when existing source data must be replaced.

### BigQuery load failure

Symptom: `load_historical_to_bq` exits non-zero.

Action:

1. Inspect the named file/table in logs.
2. Validate that the source file is a top-level JSON list.
3. Correct the source/schema issue.
4. Rerun. Each raw endpoint table is replaced in one combined load job.

### Transformation assertion failure

Symptom: `run_transformations` fails in `quality_checks.sql`.

Action:

1. Do not train or refresh public conclusions.
2. Query the violated grain or field condition in BigQuery.
3. Determine whether the problem is source drift, duplicated raw input, or a
   transformation bug.
4. Fix and rerun the complete transformation sequence.

### Live write failure

The direct poller advances its watermark only after a confirmed BigQuery
write. Restarting safely retries the uncommitted boundary.

The Pub/Sub consumer returns a non-2xx response for invalid or failed writes.
Pub/Sub retries with backoff and forwards exhausted messages to the dead-letter
topic. Inspect and replay dead-letter messages only after fixing the cause.

### Model fails the baseline

This is a valid result, not an operational failure. The candidate artifact is
retained for analysis, but no champion is promoted. Investigate feature
coverage, time stability, and calibration without weakening the chronological
test or changing the baseline after seeing test results.

## Rollback

- Deploy the previous immutable Cloud Run image revision.
- Keep raw GCS object versioning enabled.
- Rebuild derived BigQuery views and marts from preserved raw data.
- Do not overwrite a previous promoted model without preserving its artifact
  and evaluation metadata.
