import json
from pathlib import Path
from typing import Any

from src.stream.replay import load_replay_events, replay_events


class FakeSink:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []
        self.closed = False

    def publish(self, event: dict[str, Any]) -> None:
        self.events.append(event)

    def close(self) -> None:
        self.closed = True


def test_load_replay_events_orders_across_endpoints(tmp_path: Path) -> None:
    (tmp_path / "laps.json").write_text(
        json.dumps(
            [
                {
                    "session_key": 1,
                    "driver_number": 44,
                    "lap_number": 1,
                    "date_start": "2025-01-01T00:00:10Z",
                }
            ]
        ),
        encoding="utf-8",
    )
    (tmp_path / "position.json").write_text(
        json.dumps(
            [
                {
                    "session_key": 1,
                    "driver_number": 44,
                    "position": 2,
                    "date": "2025-01-01T00:00:05Z",
                }
            ]
        ),
        encoding="utf-8",
    )

    events = load_replay_events(tmp_path)

    assert [event["_source_endpoint"] for event in events] == [
        "position",
        "laps",
    ]


def test_replay_scales_event_time_delays() -> None:
    sink = FakeSink()
    delays: list[float] = []
    events = [
        {
            "_event_id": "1",
            "_source_endpoint": "position",
            "_source_event_time": "2025-01-01T00:00:00+00:00",
        },
        {
            "_event_id": "2",
            "_source_endpoint": "position",
            "_source_event_time": "2025-01-01T00:00:10+00:00",
        },
    ]

    published = replay_events(
        events,
        sink,
        speed=2,
        sleep=delays.append,
    )

    assert published == 2
    assert delays == [5]
    assert sink.closed is True
