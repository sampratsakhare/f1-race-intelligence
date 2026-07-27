from pathlib import Path
from unittest.mock import Mock

import pytest

from src.ingest import replay_poller


def test_compat_replay_delegates_to_canonical_pipeline(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session_dir = tmp_path / "2025" / "10" / "20"
    session_dir.mkdir(parents=True)
    events = [{"_event_id": "event-1"}]
    sink = Mock()
    load_events = Mock(return_value=events)
    replay_events = Mock(return_value=1)
    monkeypatch.setattr(replay_poller, "load_replay_events", load_events)
    monkeypatch.setattr(replay_poller, "replay_events", replay_events)
    monkeypatch.setattr(replay_poller, "BigQuerySink", Mock(return_value=sink))

    published = replay_poller.replay(
        2025,
        10,
        20,
        100,
        raw_data_dir=tmp_path,
    )

    assert published == 1
    load_events.assert_called_once_with(session_dir)
    replay_events.assert_called_once_with(events, sink, speed=100)


def test_compat_replay_requires_backfilled_directory(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="backfill"):
        replay_poller.replay(
            2025,
            10,
            20,
            100,
            raw_data_dir=tmp_path,
        )
