from pathlib import Path
from unittest.mock import Mock

import pytest

from src.load.run_transformations import (
    render_sql,
    run_transformations,
    transformation_files,
    warehouse_prefix,
)


def test_warehouse_prefix_rejects_unsafe_identifier() -> None:
    with pytest.raises(ValueError, match="Invalid BigQuery"):
        warehouse_prefix("project`; DROP TABLE x; --", "dataset")


def test_render_sql_replaces_template_prefix() -> None:
    rendered = render_sql(
        "SELECT * FROM `PROJECT.DATASET.raw_laps`",
        "my-project",
        "f1_raw",
    )
    assert rendered == "SELECT * FROM `my-project.f1_raw.raw_laps`"


def test_transformation_files_are_in_dependency_order(tmp_path: Path) -> None:
    sql_root = tmp_path / "sql"
    marts = sql_root / "marts"
    marts.mkdir(parents=True)
    (sql_root / "staging.sql").write_text("PROJECT.DATASET")
    (marts / "z.sql").write_text("PROJECT.DATASET")
    (marts / "a.sql").write_text("PROJECT.DATASET")
    (sql_root / "quality_checks.sql").write_text("PROJECT.DATASET")

    paths = transformation_files(sql_root)

    assert [path.name for path in paths] == [
        "staging.sql",
        "a.sql",
        "z.sql",
        "quality_checks.sql",
    ]


def test_run_transformations_waits_for_each_job(tmp_path: Path) -> None:
    sql_root = tmp_path / "sql"
    marts = sql_root / "marts"
    marts.mkdir(parents=True)
    (sql_root / "staging.sql").write_text("SELECT 'PROJECT.DATASET'")
    (marts / "mart.sql").write_text("SELECT 'PROJECT.DATASET'")
    (sql_root / "quality_checks.sql").write_text("SELECT 'PROJECT.DATASET'")

    jobs = [Mock(), Mock(), Mock()]
    client = Mock()
    client.query.side_effect = jobs

    run_transformations(
        client,
        project_id="test-project",
        dataset="f1_raw",
        sql_root=sql_root,
    )

    assert client.query.call_count == 3
    for job in jobs:
        job.result.assert_called_once_with()
