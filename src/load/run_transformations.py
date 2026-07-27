"""Render and execute the BigQuery staging, mart, and quality SQL."""

from __future__ import annotations

import argparse
import logging
import os
import re
from pathlib import Path

from dotenv import load_dotenv
from google.cloud import bigquery

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

DEFAULT_SQL_ROOT = Path("src/sql")
_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")


def warehouse_prefix(project_id: str, dataset: str) -> str:
    for label, value in (("project_id", project_id), ("dataset", dataset)):
        if not value or not _IDENTIFIER_PATTERN.fullmatch(value):
            raise ValueError(f"Invalid BigQuery {label}: {value!r}")
    return f"{project_id}.{dataset}"


def transformation_files(
    sql_root: Path,
    *,
    include_quality_checks: bool = True,
) -> list[Path]:
    files = [
        sql_root / "staging.sql",
        *sorted((sql_root / "marts").glob("*.sql")),
    ]
    if include_quality_checks:
        files.append(sql_root / "quality_checks.sql")

    missing = [path for path in files if not path.is_file()]
    if missing:
        raise FileNotFoundError(
            "Missing transformation SQL: " + ", ".join(str(path) for path in missing)
        )
    return files


def render_sql(sql: str, project_id: str, dataset: str) -> str:
    prefix = warehouse_prefix(project_id, dataset)
    if "PROJECT.DATASET" not in sql:
        raise ValueError("SQL template does not contain PROJECT.DATASET")
    return sql.replace("PROJECT.DATASET", prefix)


def run_transformations(
    client: bigquery.Client,
    *,
    project_id: str,
    dataset: str,
    sql_root: Path = DEFAULT_SQL_ROOT,
    include_quality_checks: bool = True,
    dry_run: bool = False,
) -> None:
    for sql_path in transformation_files(
        sql_root,
        include_quality_checks=include_quality_checks,
    ):
        rendered = render_sql(
            sql_path.read_text(encoding="utf-8"),
            project_id,
            dataset,
        )
        job_config = bigquery.QueryJobConfig(
            dry_run=dry_run,
            use_query_cache=False if dry_run else True,
        )
        logger.info("%s %s", "Validating" if dry_run else "Running", sql_path)
        job = client.query(rendered, job_config=job_config)
        if not dry_run:
            job.result()
        logger.info(
            "%s %s",
            "Validated" if dry_run else "Completed",
            sql_path,
        )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build BigQuery staging views and analytical marts"
    )
    parser.add_argument(
        "--project-id",
        default=os.environ.get("GCP_PROJECT_ID"),
    )
    parser.add_argument(
        "--dataset",
        default=os.environ.get("BQ_DATASET", "f1_raw"),
    )
    parser.add_argument("--sql-root", type=Path, default=DEFAULT_SQL_ROOT)
    parser.add_argument("--skip-quality-checks", action="store_true")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Ask BigQuery to validate each SQL script without executing it",
    )
    args = parser.parse_args()

    if not args.project_id:
        parser.error("--project-id or GCP_PROJECT_ID is required")

    client = bigquery.Client(project=args.project_id)
    run_transformations(
        client,
        project_id=args.project_id,
        dataset=args.dataset,
        sql_root=args.sql_root,
        include_quality_checks=not args.skip_quality_checks,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    main()
