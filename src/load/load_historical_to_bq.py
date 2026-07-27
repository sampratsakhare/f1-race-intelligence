"""
Walks data/raw/ (produced by historical_backfill.py), groups every JSON
file by its target table (e.g. all laps.json files -> raw_laps), and
loads each table in ONE combined job.

Why combined loads instead of one job per file: BigQuery's schema
autodetect only looks at the rows in a single load job. Loading race by
race causes schema drift -- a field that's null in one race's file but
populated in another's, or numeric in one file and string in another,
makes each subsequent load job reject the table's existing schema.
Combining everything for a table into one job lets autodetect see the
full range of values across every race and land on a schema that
actually fits all of them.

Run:
    python -m src.load.load_historical_to_bq
"""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from pathlib import Path

from src.load.bigquery_loader import BigQueryLoader

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

RAW_DATA_DIR = Path("data/raw")

# Fields to strip per table before loading. segments_sector_* on laps are
# mini-sector performance flags we don't use anywhere in staging/marts, and
# their inconsistent presence across rows confuses BigQuery's schema
# autodetect (it infers "required" from an early sample, then fails on a
# later row where the field is null). Simplest fix: just don't load them.
FIELDS_TO_STRIP = {
    "raw_laps": {"segments_sector_1", "segments_sector_2", "segments_sector_3"},
}


def _strip_fields(table_name: str, records: list[dict]) -> list[dict]:
    exclude = FIELDS_TO_STRIP.get(table_name)
    if not exclude:
        return records
    return [{k: v for k, v in record.items() if k not in exclude} for record in records]


def load_historical_directory(
    raw_data_dir: Path,
    loader: BigQueryLoader,
) -> None:
    """Load all endpoint files from a historical backfill into raw tables."""
    loader.ensure_dataset_exists()

    json_files = sorted(
        path for path in raw_data_dir.rglob("*.json") if not path.name.startswith("_")
    )
    logger.info("Found %d raw JSON files to load", len(json_files))

    files_by_table: dict[str, list[Path]] = defaultdict(list)
    for json_path in json_files:
        table_name = f"raw_{json_path.stem}"
        files_by_table[table_name].append(json_path)

    logger.info("Grouped into %d tables: %s", len(files_by_table), sorted(files_by_table))

    failures: list[str] = []
    for table_name, paths in sorted(files_by_table.items()):
        all_records = []
        for path in paths:
            try:
                payload = json.loads(path.read_text())
                if not isinstance(payload, list):
                    raise ValueError("expected a top-level JSON list")
                all_records.extend(payload)
            except Exception as exc:
                failures.append(f"{path}: {exc}")
                logger.exception("Failed to read %s", path)

        if not all_records:
            logger.warning("No records found for %s, skipping table", table_name)
            continue

        all_records = _strip_fields(table_name, all_records)

        try:
            loader.load_records(table_name, all_records, write_disposition="WRITE_TRUNCATE")
        except Exception as exc:
            failures.append(f"{table_name}: {exc}")
            logger.exception("Failed to load table %s", table_name)

    if failures:
        raise RuntimeError(
            f"Historical load completed with {len(failures)} failures; "
            "inspect the logs before running transformations"
        )


def main() -> None:
    load_historical_directory(RAW_DATA_DIR, BigQueryLoader())


if __name__ == "__main__":
    main()
