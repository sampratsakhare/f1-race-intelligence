import base64
import json

import pytest

from src.stream.consumer_app import decode_pubsub_event, healthcheck


def _envelope(event: dict) -> dict:
    encoded = base64.b64encode(json.dumps(event).encode("utf-8")).decode("ascii")
    return {"message": {"data": encoded}}


def test_decode_pubsub_event_validates_required_envelope() -> None:
    event = {
        "_event_id": "event-1",
        "_source_endpoint": "laps",
        "session_key": 1,
    }

    assert decode_pubsub_event(_envelope(event)) == event


@pytest.mark.parametrize(
    "envelope",
    [
        {},
        {"message": {"data": "not-base64!"}},
        _envelope({"_event_id": "event-1", "_source_endpoint": "unknown"}),
        _envelope({"_source_endpoint": "laps"}),
    ],
)
def test_decode_pubsub_event_rejects_invalid_messages(envelope: dict) -> None:
    with pytest.raises(ValueError):
        decode_pubsub_event(envelope)


def test_healthcheck() -> None:
    assert healthcheck() == {"status": "ok"}
