import json
from pathlib import Path

import pytest

from src.ingest import historical_backfill


class FakeOpenF1Client:
    def __init__(self, failing_endpoint: str | None = None) -> None:
        self.failing_endpoint = failing_endpoint
        self.fetch_calls: list[str] = []

    def meetings(self, year: int) -> list[dict]:
        return [{"meeting_key": 10, "meeting_name": "Test GP", "year": year}]

    def sessions(self, meeting_key: int) -> list[dict]:
        assert meeting_key == 10
        return [
            {
                "meeting_key": meeting_key,
                "session_key": 19,
                "session_name": "Qualifying",
                "session_type": "Qualifying",
                "date_start": "2025-01-01T12:00:00Z",
            },
            {
                "meeting_key": meeting_key,
                "session_key": 20,
                "session_name": "Race",
                "session_type": "Race",
                "date_start": "2025-01-02T12:00:00Z",
            },
            {
                "meeting_key": meeting_key,
                "session_key": 21,
                "session_name": "Practice 1",
                "session_type": "Practice",
            },
        ]

    def fetch(self, endpoint: str, session_key: int) -> list[dict]:
        expected_key = 19 if endpoint == "starting_grid" else 20
        assert session_key == expected_key
        self.fetch_calls.append(endpoint)
        if endpoint == self.failing_endpoint:
            raise RuntimeError("upstream unavailable")
        return [{"session_key": session_key, "endpoint": endpoint}]


def test_write_and_inspect_json_are_checksum_stable(tmp_path: Path) -> None:
    path = tmp_path / "payload.json"
    checksum = historical_backfill._write_json(path, [{"value": 1}])
    payload, inspected_checksum = historical_backfill._inspect_existing_json(path)

    assert payload == [{"value": 1}]
    assert inspected_checksum == checksum
    assert not path.with_suffix(".json.tmp").exists()


def test_gcs_object_name_is_partitioned() -> None:
    name = historical_backfill._gcs_object_name(
        "laps",
        2025,
        10,
        20,
        "data.json",
    )
    assert name == ("openf1/raw/endpoint=laps/year=2025/meeting_key=10/session_key=20/data.json")


def test_grid_session_key_uses_standard_qualifying_for_grand_prix() -> None:
    race = {
        "session_key": 20,
        "session_name": "Race",
        "date_start": "2025-01-02T12:00:00Z",
    }
    sessions = [
        {
            "session_key": 18,
            "session_name": "Sprint Qualifying",
            "date_start": "2025-01-01T08:00:00Z",
        },
        {
            "session_key": 19,
            "session_name": "Qualifying",
            "date_start": "2025-01-01T12:00:00Z",
        },
        race,
    ]

    assert historical_backfill._grid_session_key(race, sessions) == 19


def test_grid_session_key_requires_matching_qualifying() -> None:
    race = {"session_key": 20, "session_name": "Race"}
    with pytest.raises(RuntimeError, match="No grid-setting"):
        historical_backfill._grid_session_key(race, [race])


def test_backfill_writes_manifest_and_is_idempotent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(historical_backfill, "RAW_DATA_DIR", tmp_path)
    client = FakeOpenF1Client()

    failures = historical_backfill.backfill_year(client, 2025, {"Race"})

    assert failures == []
    session_dir = tmp_path / "2025" / "10" / "20"
    manifest = json.loads((session_dir / "_manifest.json").read_text())
    grid_rows = json.loads((session_dir / "starting_grid.json").read_text())
    assert manifest["status"] == "success"
    assert manifest["endpoints"]["laps"]["row_count"] == 1
    assert grid_rows[0]["session_key"] == 19
    assert grid_rows[0]["_target_session_key"] == 20
    assert not (tmp_path / "2025" / "10" / "21").exists()
    assert set(client.fetch_calls) == set(historical_backfill.SESSION_ENDPOINTS)

    client.fetch_calls.clear()
    historical_backfill.backfill_year(client, 2025, {"Race"})
    assert client.fetch_calls == []


def test_backfill_refreshes_legacy_grid_without_target_key(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(historical_backfill, "RAW_DATA_DIR", tmp_path)
    client = FakeOpenF1Client()
    historical_backfill.backfill_year(client, 2025, {"Race"})
    grid_path = tmp_path / "2025" / "10" / "20" / "starting_grid.json"
    grid_path.write_text(
        json.dumps([{"session_key": 19, "driver_number": 44}]),
        encoding="utf-8",
    )
    client.fetch_calls.clear()

    historical_backfill.backfill_year(client, 2025, {"Race"})

    assert client.fetch_calls == ["starting_grid"]
    refreshed = json.loads(grid_path.read_text())
    assert refreshed[0]["_target_session_key"] == 20


def test_backfill_records_endpoint_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(historical_backfill, "RAW_DATA_DIR", tmp_path)
    client = FakeOpenF1Client(failing_endpoint="weather")

    failures = historical_backfill.backfill_year(client, 2025, {"Race"})

    assert failures[0]["endpoint"] == "weather"
    manifest = json.loads((tmp_path / "2025" / "10" / "20" / "_manifest.json").read_text())
    assert manifest["status"] == "failed"
    assert manifest["endpoints"]["weather"]["status"] == "failed"
