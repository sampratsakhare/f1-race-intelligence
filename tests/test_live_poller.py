from pathlib import Path

from src.ingest.live_poller import DurableWatermark, prepare_rows


def _lap(lap_number: int, date_start: str) -> dict:
    return {
        "session_key": 999,
        "meeting_key": 10,
        "driver_number": 44,
        "lap_number": lap_number,
        "date_start": date_start,
        "lap_duration": 90.0,
    }


def test_laps_use_date_start_and_survive_restart(tmp_path: Path) -> None:
    state_path = tmp_path / "watermarks.json"
    rows = prepare_rows(
        "laps",
        [
            _lap(1, "2025-01-01T00:00:01Z"),
            _lap(2, "2025-01-01T00:01:31Z"),
        ],
        "date_start",
    )
    watermark = DurableWatermark(state_path, session_key=999)

    new_rows = watermark.filter_new("laps", rows)
    assert len(new_rows) == 2
    assert new_rows[0]["_source_event_time"] == "2025-01-01T00:00:01+00:00"

    watermark.commit("laps", new_rows)
    restarted = DurableWatermark(state_path, session_key=999)

    assert restarted.filter_new("laps", rows) == []


def test_equal_timestamp_accepts_only_unseen_event_ids(tmp_path: Path) -> None:
    state_path = tmp_path / "watermarks.json"
    initial = prepare_rows(
        "laps",
        [_lap(1, "2025-01-01T00:00:01Z")],
        "date_start",
    )
    watermark = DurableWatermark(state_path, session_key=999)
    watermark.commit("laps", initial)

    same_event = initial[0]
    different_event = prepare_rows(
        "laps",
        [_lap(2, "2025-01-01T00:00:01Z")],
        "date_start",
    )[0]

    assert watermark.filter_new("laps", [same_event, different_event]) == [different_event]


def test_interval_values_are_coerced_for_stable_bigquery_schema() -> None:
    rows = prepare_rows(
        "intervals",
        [
            {
                "session_key": 999,
                "driver_number": 44,
                "date": "2025-01-01T00:00:01Z",
                "gap_to_leader": 1.25,
                "interval": "+1 LAP",
            }
        ],
        "date",
    )

    assert rows[0]["gap_to_leader"] == "1.25"
    assert rows[0]["interval"] == "+1 LAP"
