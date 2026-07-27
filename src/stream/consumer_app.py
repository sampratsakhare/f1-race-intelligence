"""Cloud Run HTTP consumer for Pub/Sub push messages."""

from __future__ import annotations

import base64
import json
from functools import lru_cache
from typing import Any

from fastapi import FastAPI, HTTPException, Response, status

from src.ingest.live_poller import ENDPOINTS
from src.load.bigquery_loader import BigQueryLoader

app = FastAPI(title="F1 Race Intelligence Event Consumer")


@lru_cache(maxsize=1)
def get_loader() -> BigQueryLoader:
    loader = BigQueryLoader()
    loader.ensure_live_tables_exist()
    return loader


def decode_pubsub_event(envelope: dict[str, Any]) -> dict[str, Any]:
    try:
        encoded = envelope["message"]["data"]
        payload = base64.b64decode(encoded, validate=True)
        event = json.loads(payload)
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError("Invalid Pub/Sub push envelope") from exc

    if not isinstance(event, dict):
        raise ValueError("Pub/Sub event data must decode to a JSON object")

    endpoint = event.get("_source_endpoint")
    if endpoint not in ENDPOINTS:
        raise ValueError(f"Unsupported source endpoint: {endpoint!r}")
    if not event.get("_event_id"):
        raise ValueError("Event is missing _event_id")
    return event


@app.get("/healthz")
def healthcheck() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/", status_code=status.HTTP_204_NO_CONTENT)
def consume_pubsub_message(envelope: dict[str, Any]) -> Response:
    try:
        event = decode_pubsub_event(envelope)
    except ValueError as exc:
        # Non-2xx responses are retried and eventually routed to the configured
        # dead-letter topic.
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    endpoint = str(event["_source_endpoint"])
    table = ENDPOINTS[endpoint]["table"]
    # BigQuery errors propagate as 5xx, which makes Pub/Sub retry the message.
    get_loader().stream_insert(table, [event])
    return Response(status_code=status.HTTP_204_NO_CONTENT)
