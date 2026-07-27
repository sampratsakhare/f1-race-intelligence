import json
from pathlib import Path
from typing import Any

import pytest

from src.load.load_historical_to_bq import (
    _strip_fields,
    load_historical_directory,
)


class FakeLoader:
    def __init__(self, failing_table: str | None = None) -> None:
        self.failing_table = failing_table
        self.dataset_checked = False
        self.loads: list[dict[str, Any]] = []

    def ensure_dataset_exists(self) -> None:
        self.dataset_checked = True

    def load_records(
        self,
        table_name: str,
        records: list[dict],
        write_disposition: str,
    ) -> None:
        if table_name == self.failing_table:
            raise RuntimeError("load failed")
        self.loads.append(
            {
                "table": table_name,
                "records": records,
                "write_disposition": write_disposition,
            }
        )


def _write(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_strip_fields_removes_unstable_lap_segments() -> None:
    rows = [{"lap_number": 1, "segments_sector_1": [1, 2]}]
    assert _strip_fields("raw_laps", rows) == [{"lap_number": 1}]
    assert _strip_fields("raw_pit", rows) is rows


def test_load_historical_groups_files_and_skips_manifests(tmp_path: Path) -> None:
    _write(tmp_path / "2025/1/10/laps.json", [{"lap_number": 1}])
    _write(tmp_path / "2025/1/11/laps.json", [{"lap_number": 2}])
    _write(tmp_path / "2025/1/10/pit.json", [])
    _write(tmp_path / "2025/1/10/_manifest.json", {"status": "success"})
    loader = FakeLoader()

    load_historical_directory(tmp_path, loader)  # type: ignore[arg-type]

    assert loader.dataset_checked is True
    assert len(loader.loads) == 1
    assert loader.loads[0]["table"] == "raw_laps"
    assert loader.loads[0]["records"] == [{"lap_number": 1}, {"lap_number": 2}]
    assert loader.loads[0]["write_disposition"] == "WRITE_TRUNCATE"


def test_load_historical_reports_invalid_payload(tmp_path: Path) -> None:
    _write(tmp_path / "2025/1/10/laps.json", {"not": "a list"})
    loader = FakeLoader()

    with pytest.raises(RuntimeError, match="1 failures"):
        load_historical_directory(tmp_path, loader)  # type: ignore[arg-type]


def test_load_historical_reports_warehouse_failure(tmp_path: Path) -> None:
    _write(tmp_path / "2025/1/10/laps.json", [{"lap_number": 1}])
    loader = FakeLoader(failing_table="raw_laps")

    with pytest.raises(RuntimeError, match="1 failures"):
        load_historical_directory(tmp_path, loader)  # type: ignore[arg-type]
