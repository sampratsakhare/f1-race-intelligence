from datetime import UTC, datetime
from unittest.mock import Mock

import pytest
import requests

from src.ingest import openf1_client
from src.ingest.openf1_client import (
    OpenF1Client,
    SlidingWindowRateLimiter,
    build_event_id,
    normalize_record_timestamps,
    normalize_timestamp,
)


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


def test_rate_limiter_enforces_interval_and_window() -> None:
    clock = FakeClock()
    limiter = SlidingWindowRateLimiter(
        max_requests=2,
        window_seconds=10,
        min_interval_seconds=1,
        clock=clock,
        sleep=clock.sleep,
    )

    limiter.acquire()
    limiter.acquire()
    limiter.acquire()

    assert clock.now >= 10


def test_timestamp_normalization_handles_iso_and_epoch_units() -> None:
    expected = datetime(2024, 1, 1, tzinfo=UTC).isoformat()
    seconds = 1_704_067_200

    assert normalize_timestamp("2024-01-01T00:00:00Z") == expected
    assert normalize_timestamp(seconds) == expected
    assert normalize_timestamp(seconds * 1_000) == expected
    assert normalize_timestamp(seconds * 1_000_000) == expected


def test_record_normalization_preserves_non_timestamp_fields() -> None:
    row = {
        "date_start": "2024-01-01T00:00:00Z",
        "driver_number": 44,
        "lap_number": 1,
    }

    normalized = normalize_record_timestamps(row)

    assert normalized["date_start"] == "2024-01-01T00:00:00+00:00"
    assert normalized["driver_number"] == 44
    assert normalized["lap_number"] == 1
    assert row["date_start"].endswith("Z")


def test_event_id_is_stable_and_endpoint_specific() -> None:
    row_a = {"session_key": 1, "driver_number": 44, "lap_number": 2}
    row_b = {"lap_number": 2, "driver_number": 44, "session_key": 1}

    assert build_event_id("laps", row_a) == build_event_id("laps", row_b)
    assert build_event_id("laps", row_a) != build_event_id("position", row_a)


def _response(
    status_code: int,
    payload: object,
    *,
    headers: dict[str, str] | None = None,
) -> Mock:
    response = Mock()
    response.status_code = status_code
    response.headers = headers or {}
    response.json.return_value = payload
    if status_code >= 400:
        response.raise_for_status.side_effect = requests.HTTPError(response=response)
    return response


def test_client_sets_auth_and_user_agent() -> None:
    session = Mock()
    session.headers = {}

    OpenF1Client(session=session, access_token="secret")

    assert session.headers["Authorization"] == "Bearer secret"
    assert session.headers["User-Agent"] == "f1-race-intelligence/1.0"


def test_client_returns_json_list() -> None:
    session = Mock()
    session.headers = {}
    session.get.return_value = _response(200, [{"session_key": 1}])
    limiter = Mock()
    client = OpenF1Client(
        session=session,
        rate_limiter=limiter,
        sleep=Mock(),
    )

    rows = client.fetch("sessions", session_key=1)

    assert rows == [{"session_key": 1}]
    session.get.assert_called_once_with(
        "https://api.openf1.org/v1/sessions",
        params={"session_key": 1},
        timeout=30,
    )
    limiter.acquire.assert_called_once_with()


def test_client_retries_transient_failure_and_honors_retry_after() -> None:
    session = Mock()
    session.headers = {}
    session.get.side_effect = [
        _response(503, [], headers={"Retry-After": "0.25"}),
        _response(200, [{"ok": True}]),
    ]
    sleep = Mock()
    client = OpenF1Client(
        session=session,
        rate_limiter=Mock(),
        sleep=sleep,
    )

    assert client.fetch("laps", session_key=1) == [{"ok": True}]
    sleep.assert_called_once_with(0.25)


def test_client_does_not_retry_permanent_http_error() -> None:
    session = Mock()
    session.headers = {}
    session.get.return_value = _response(401, [])
    sleep = Mock()
    client = OpenF1Client(
        session=session,
        rate_limiter=Mock(),
        sleep=sleep,
    )

    with pytest.raises(requests.HTTPError):
        client.fetch("laps", session_key=1)

    sleep.assert_not_called()
    assert session.get.call_count == 1


def test_client_rejects_non_list_payload() -> None:
    session = Mock()
    session.headers = {}
    session.get.return_value = _response(200, {"unexpected": "object"})
    client = OpenF1Client(session=session, rate_limiter=Mock(), sleep=Mock())

    with pytest.raises(ValueError, match="Expected a list"):
        client.fetch("sessions")


def test_client_exhausts_bounded_retries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(openf1_client, "MAX_RETRIES", 2)
    session = Mock()
    session.headers = {}
    session.get.side_effect = requests.Timeout("slow")
    sleep = Mock()
    client = OpenF1Client(session=session, rate_limiter=Mock(), sleep=sleep)

    with pytest.raises(RuntimeError, match="after 2 attempts"):
        client.fetch("laps", session_key=1)

    assert session.get.call_count == 2
    assert sleep.call_count == 1


def test_timestamp_normalization_preserves_unparseable_strings() -> None:
    assert normalize_timestamp("not-a-date") == "not-a-date"
    assert normalize_timestamp(None) is None
    assert normalize_timestamp(object()) is None
