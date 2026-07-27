"""
BigQuery loading utilities: batch load raw JSON (historical) and
streaming insert (live polling).

Requires:
    pip install google-cloud-bigquery
    GOOGLE_APPLICATION_CREDENTIALS env var pointing at a service account key,
    or `gcloud auth application-default login` for local dev.

Config comes from environment variables (see .env.example):
    GCP_PROJECT_ID
    BQ_DATASET
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from google.api_core.exceptions import NotFound
from google.cloud import bigquery

logger = logging.getLogger(__name__)
load_dotenv()

COMMON_LIVE_FIELDS = [
    bigquery.SchemaField("_event_id", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("_ingested_at", "TIMESTAMP", mode="REQUIRED"),
    bigquery.SchemaField("_source_event_time", "TIMESTAMP"),
    bigquery.SchemaField("_source_endpoint", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("meeting_key", "INTEGER"),
    bigquery.SchemaField("session_key", "INTEGER"),
    bigquery.SchemaField("driver_number", "INTEGER"),
]

LIVE_TABLE_SCHEMAS = {
    "raw_live_laps": COMMON_LIVE_FIELDS
    + [
        bigquery.SchemaField("date_start", "TIMESTAMP"),
        bigquery.SchemaField("lap_number", "INTEGER"),
        bigquery.SchemaField("lap_duration", "FLOAT"),
        bigquery.SchemaField("duration_sector_1", "FLOAT"),
        bigquery.SchemaField("duration_sector_2", "FLOAT"),
        bigquery.SchemaField("duration_sector_3", "FLOAT"),
        bigquery.SchemaField("is_pit_out_lap", "BOOLEAN"),
        bigquery.SchemaField("i1_speed", "INTEGER"),
        bigquery.SchemaField("i2_speed", "INTEGER"),
        bigquery.SchemaField("st_speed", "INTEGER"),
    ],
    "raw_live_position": COMMON_LIVE_FIELDS
    + [
        bigquery.SchemaField("date", "TIMESTAMP"),
        bigquery.SchemaField("position", "INTEGER"),
    ],
    "raw_live_intervals": COMMON_LIVE_FIELDS
    + [
        bigquery.SchemaField("date", "TIMESTAMP"),
        bigquery.SchemaField("gap_to_leader", "STRING"),
        bigquery.SchemaField("interval", "STRING"),
    ],
    "raw_live_pit": COMMON_LIVE_FIELDS
    + [
        bigquery.SchemaField("date", "TIMESTAMP"),
        bigquery.SchemaField("lap_number", "INTEGER"),
        bigquery.SchemaField("lane_duration", "FLOAT"),
        bigquery.SchemaField("pit_duration", "FLOAT"),
        bigquery.SchemaField("stop_duration", "FLOAT"),
    ],
}


class BigQueryLoader:
    def __init__(
        self,
        project_id: str | None = None,
        dataset: str | None = None,
        location: str | None = None,
        client: bigquery.Client | None = None,
    ):
        self.project_id = project_id or os.environ["GCP_PROJECT_ID"]
        self.dataset = dataset or os.environ.get("BQ_DATASET", "f1_raw")
        self.location = location or os.environ.get("BQ_LOCATION", "US")
        self.client = client or bigquery.Client(project=self.project_id)

    def _table_ref(self, table_name: str) -> str:
        return f"{self.project_id}.{self.dataset}.{table_name}"

    def load_json_file(
        self,
        table_name: str,
        json_path: Path,
        write_disposition: str = "WRITE_APPEND",
    ) -> None:
        """Batch-load a raw JSON file (list of records) into a BigQuery table.

        Schema is auto-detected on first load. For a portfolio project this
        is fine; a production version would pin an explicit schema per table.
        """
        records = json.loads(json_path.read_text())
        self.load_records(table_name, records, write_disposition=write_disposition)

    def load_records(
        self,
        table_name: str,
        records: list[dict[str, Any]],
        write_disposition: str = "WRITE_TRUNCATE",
    ) -> None:
        """Load a list of records directly into a BigQuery table.

        Prefer this over repeated load_json_file calls when combining many
        small files into one table: BigQuery's schema autodetect only looks
        at the rows in a single load job, so loading race-by-race causes
        schema drift (a field that's non-null in one file but null in
        another, or numeric in one and string in another). Loading everything
        for a table in one job lets autodetect see the full range of data
        and pick a schema that actually fits.
        """
        if not records:
            return

        job_config = bigquery.LoadJobConfig(
            source_format=bigquery.SourceFormat.NEWLINE_DELIMITED_JSON,
            autodetect=True,
            write_disposition=write_disposition,
            # Belt-and-suspenders: even with combined loads, allow BigQuery to
            # relax a field to NULLABLE or add new fields rather than fail
            # the whole job over a schema mismatch.
            schema_update_options=[
                bigquery.SchemaUpdateOption.ALLOW_FIELD_ADDITION,
                bigquery.SchemaUpdateOption.ALLOW_FIELD_RELAXATION,
            ]
            if write_disposition == "WRITE_APPEND"
            else [],
        )

        with tempfile.NamedTemporaryFile(mode="w", suffix=".ndjson", delete=False) as tmp:
            for record in records:
                tmp.write(json.dumps(record) + "\n")
            tmp_path = tmp.name

        try:
            with open(tmp_path, "rb") as f:
                job = self.client.load_table_from_file(
                    f,
                    self._table_ref(table_name),
                    job_config=job_config,
                )
            job.result()  # wait for completion, raises on failure
            logger.info("Loaded %d rows into %s", len(records), self._table_ref(table_name))
        finally:
            Path(tmp_path).unlink(missing_ok=True)

    def stream_insert(
        self,
        table_name: str,
        rows: list[dict[str, Any]],
    ) -> None:
        """Streaming insert for live polling -- lower latency than batch load,
        appropriate for the small, frequent row counts live polling produces.

        Streaming inserts require the table to already exist (unlike batch
        loads, which can autodetect + create it). If the table doesn't exist
        yet -- true the very first time a live session is polled -- fall
        back to a one-off batch load to create it with an inferred schema,
        then this and all subsequent calls stream normally.
        """
        if not rows:
            return

        row_ids = [str(row["_event_id"]) for row in rows]
        errors = self.client.insert_rows_json(
            self._table_ref(table_name),
            rows,
            row_ids=row_ids,
            ignore_unknown_values=True,
        )
        if errors:
            raise RuntimeError(f"BigQuery streaming insert failed for {table_name}: {errors}")
        logger.debug(
            "Streamed %d rows into %s",
            len(rows),
            self._table_ref(table_name),
        )

    def ensure_dataset_exists(self) -> None:
        dataset_ref = bigquery.DatasetReference(self.project_id, self.dataset)
        try:
            self.client.get_dataset(dataset_ref)
        except NotFound:
            logger.info("Dataset %s not found, creating it", self.dataset)
            dataset = bigquery.Dataset(dataset_ref)
            dataset.location = self.location
            self.client.create_dataset(dataset)

    def ensure_live_tables_exist(self) -> None:
        """Provision the live tables required by the poller."""
        self.ensure_dataset_exists()
        for table_name, schema in LIVE_TABLE_SCHEMAS.items():
            table_ref = self._table_ref(table_name)
            try:
                self.client.get_table(table_ref)
                continue
            except NotFound:
                pass

            table = bigquery.Table(table_ref, schema=schema)
            table.time_partitioning = bigquery.TimePartitioning(
                type_=bigquery.TimePartitioningType.DAY,
                field="_source_event_time",
            )
            table.clustering_fields = ["session_key", "driver_number"]
            self.client.create_table(table)
            logger.info("Created live table %s", table_ref)
