import json
from pathlib import Path
from typing import Any
from unittest.mock import Mock

import pytest
from google.api_core.exceptions import NotFound

from src.load.bigquery_loader import LIVE_TABLE_SCHEMAS, BigQueryLoader


class FakeBigQueryClient:
    def __init__(self, errors: list[dict[str, Any]] | None = None) -> None:
        self.errors = errors or []
        self.calls: list[dict[str, Any]] = []

    def insert_rows_json(self, table: str, rows: list[dict], **kwargs: Any) -> list[dict]:
        self.calls.append({"table": table, "rows": rows, **kwargs})
        return self.errors


def test_stream_insert_uses_event_ids_for_idempotency() -> None:
    client = FakeBigQueryClient()
    loader = BigQueryLoader(
        project_id="test-project",
        dataset="test_dataset",
        client=client,  # type: ignore[arg-type]
    )
    rows = [{"_event_id": "event-1", "session_key": 1}]

    loader.stream_insert("raw_live_laps", rows)

    assert client.calls[0]["row_ids"] == ["event-1"]
    assert client.calls[0]["ignore_unknown_values"] is True


def test_stream_insert_raises_so_watermark_is_not_advanced() -> None:
    client = FakeBigQueryClient(errors=[{"index": 0, "errors": ["bad row"]}])
    loader = BigQueryLoader(
        project_id="test-project",
        dataset="test_dataset",
        client=client,  # type: ignore[arg-type]
    )

    with pytest.raises(RuntimeError, match="streaming insert failed"):
        loader.stream_insert(
            "raw_live_laps",
            [{"_event_id": "event-1", "session_key": 1}],
        )


def test_stream_insert_skips_empty_batches() -> None:
    client = FakeBigQueryClient()
    loader = BigQueryLoader(
        project_id="test-project",
        dataset="test_dataset",
        client=client,  # type: ignore[arg-type]
    )
    loader.stream_insert("raw_live_laps", [])
    assert client.calls == []


def test_load_records_serializes_ndjson_and_waits_for_job() -> None:
    client = Mock()
    job = Mock()
    uploaded_payloads: list[bytes] = []

    def capture_upload(file_object, *_args, **_kwargs):
        uploaded_payloads.append(file_object.read())
        return job

    client.load_table_from_file.side_effect = capture_upload
    loader = BigQueryLoader(
        project_id="test-project",
        dataset="test_dataset",
        client=client,
    )

    loader.load_records(
        "raw_laps",
        [{"lap_number": 1}, {"lap_number": 2}],
        write_disposition="WRITE_TRUNCATE",
    )

    assert [json.loads(line) for line in uploaded_payloads[0].decode().splitlines()] == [
        {"lap_number": 1},
        {"lap_number": 2},
    ]
    assert client.load_table_from_file.call_args.args[1] == ("test-project.test_dataset.raw_laps")
    job.result.assert_called_once_with()


def test_load_json_file_delegates_to_record_loader(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "laps.json"
    path.write_text('[{"lap_number": 1}]', encoding="utf-8")
    loader = BigQueryLoader(
        project_id="test-project",
        dataset="test_dataset",
        client=Mock(),
    )
    load_records = Mock()
    monkeypatch.setattr(loader, "load_records", load_records)

    loader.load_json_file("raw_laps", path)

    load_records.assert_called_once_with(
        "raw_laps",
        [{"lap_number": 1}],
        write_disposition="WRITE_APPEND",
    )


def test_ensure_dataset_and_live_tables_create_missing_resources() -> None:
    client = Mock()
    client.get_dataset.side_effect = NotFound("missing")
    client.get_table.side_effect = NotFound("missing")
    loader = BigQueryLoader(
        project_id="test-project",
        dataset="test_dataset",
        location="EU",
        client=client,
    )

    loader.ensure_live_tables_exist()

    created_dataset = client.create_dataset.call_args.args[0]
    assert created_dataset.location == "EU"
    assert client.create_table.call_count == len(LIVE_TABLE_SCHEMAS)
    created_tables = {
        call.args[0].table_id: call.args[0] for call in client.create_table.call_args_list
    }
    assert created_tables["raw_live_laps"].time_partitioning.field == ("_source_event_time")
    assert created_tables["raw_live_laps"].clustering_fields == [
        "session_key",
        "driver_number",
    ]
