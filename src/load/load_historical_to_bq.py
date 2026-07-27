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


def main() -> None:
    loader = BigQueryLoader()
    loader.ensure_dataset_exists()

    json_files = sorted(RAW_DATA_DIR.rglob("*.json"))
    logger.info("Found %d raw JSON files to load", len(json_files))

    files_by_table: dict[str, list[Path]] = defaultdict(list)
    for json_path in json_files:
        table_name = f"raw_{json_path.stem}"
        files_by_table[table_name].append(json_path)

    logger.info("Grouped into %d tables: %s", len(files_by_table), sorted(files_by_table))

    for table_name, paths in sorted(files_by_table.items()):
        all_records = []
        for path in paths:
            try:
                all_records.extend(json.loads(path.read_text()))
            except Exception:
                logger.exception("Failed to read %s, skipping this file", path)

        if not all_records:
            logger.warning("No records found for %s, skipping table", table_name)
            continue

        all_records = _strip_fields(table_name, all_records)

        try:
            loader.load_records(table_name, all_records, write_disposition="WRITE_TRUNCATE")
        except Exception:
            logger.exception("Failed to load table %s, skipping", table_name)


if __name__ == "__main__":
    main()
