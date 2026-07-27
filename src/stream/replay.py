"""Replay a historical OpenF1 session through BigQuery or Google Pub/Sub."""

from __future__ import annotations

import argparse
import json
import logging
import os
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol

from dotenv import load_dotenv
from google.cloud import pubsub_v1

from src.ingest.live_poller import ENDPOINTS, prepare_rows
from src.load.bigquery_loader import BigQueryLoader

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


class ReplaySink(Protocol):
    def publish(self, event: dict[str, Any]) -> None: ...

    def close(self) -> None: ...


class StdoutSink:
    """Dry-run sink that reports progress without printing full event payloads."""

    def __init__(self) -> None:
        self.count = 0

    def publish(self, event: dict[str, Any]) -> None:
        self.count += 1
        if self.count == 1 or self.count % 100 == 0:
            logger.info(
                "Dry run published=%d latest_endpoint=%s event_time=%s",
                self.count,
                event["_source_endpoint"],
                event.get("_source_event_time"),
            )

    def close(self) -> None:
        logger.info("Dry run complete events=%d", self.count)


class BigQuerySink:
    def __init__(self, loader: BigQueryLoader | None = None) -> None:
        self.loader = loader or BigQueryLoader()
        self.loader.ensure_live_tables_exist()

    def publish(self, event: dict[str, Any]) -> None:
        endpoint = str(event["_source_endpoint"])
        table = ENDPOINTS[endpoint]["table"]
        self.loader.stream_insert(table, [event])

    def close(self) -> None:
        return


class PubSubSink:
    def __init__(
        self,
        project_id: str,
        topic_id: str,
        publisher: pubsub_v1.PublisherClient | None = None,
    ) -> None:
        self.publisher = publisher or pubsub_v1.PublisherClient()
        self.topic_path = self.publisher.topic_path(project_id, topic_id)

    def publish(self, event: dict[str, Any]) -> None:
        data = json.dumps(event, separators=(",", ":"), default=str).encode("utf-8")
        self.publisher.publish(
            self.topic_path,
            data,
            endpoint=str(event["_source_endpoint"]),
            session_key=str(event.get("session_key", "")),
        ).result(timeout=30)

    def close(self) -> None:
        self.publisher.stop()


def resolve_session_dir(raw_dir: Path, session_key: int) -> Path:
    candidates = [
        path
        for path in raw_dir.rglob(str(session_key))
        if path.is_dir() and path.name == str(session_key)
    ]
    if len(candidates) != 1:
        raise RuntimeError(
            f"Expected one raw directory for session_key={session_key}, "
            f"found {len(candidates)} under {raw_dir}"
        )
    return candidates[0]


def load_replay_events(session_dir: Path) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []

    for endpoint, config in ENDPOINTS.items():
        path = session_dir / f"{endpoint}.json"
        if not path.exists():
            logger.warning("Replay endpoint missing: %s", path)
            continue

        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, list):
            raise ValueError(f"Expected a JSON list in {path}")
        events.extend(prepare_rows(endpoint, payload, config["timestamp_field"]))

    events.sort(
        key=lambda event: (
            event.get("_source_event_time") or "",
            event["_source_endpoint"],
            event["_event_id"],
        )
    )
    return events


def replay_events(
    events: list[dict[str, Any]],
    sink: ReplaySink,
    *,
    speed: float,
    max_events: int | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> int:
    if speed < 0:
        raise ValueError("Replay speed must be zero or greater")

    published = 0
    previous_time: datetime | None = None
    selected_events = events[:max_events] if max_events is not None else events

    try:
        for event in selected_events:
            event_time_raw = event.get("_source_event_time")
            event_time = datetime.fromisoformat(str(event_time_raw)) if event_time_raw else None

            if speed > 0 and event_time is not None and previous_time is not None:
                delay = max((event_time - previous_time).total_seconds() / speed, 0)
                if delay:
                    sleep(delay)

            sink.publish(event)
            published += 1
            if event_time is not None:
                previous_time = event_time
    finally:
        sink.close()

    return published


def build_sink(name: str) -> ReplaySink:
    if name == "stdout":
        return StdoutSink()
    if name == "bigquery":
        return BigQuerySink()
    if name == "pubsub":
        project_id = os.environ["GCP_PROJECT_ID"]
        topic_id = os.environ.get("PUBSUB_TOPIC", "f1-live-events")
        return PubSubSink(project_id, topic_id)
    raise ValueError(f"Unsupported sink: {name}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Replay a historical OpenF1 session")
    parser.add_argument("--session-key", type=int, required=True)
    parser.add_argument("--raw-dir", type=Path, default=Path("data/raw"))
    parser.add_argument(
        "--sink",
        choices=["stdout", "bigquery", "pubsub"],
        default="stdout",
    )
    parser.add_argument(
        "--speed",
        type=float,
        default=0,
        help="Replay acceleration; 0 publishes as fast as possible",
    )
    parser.add_argument("--max-events", type=int)
    args = parser.parse_args()

    session_dir = resolve_session_dir(args.raw_dir, args.session_key)
    events = load_replay_events(session_dir)
    logger.info(
        "Loaded replay session=%s events=%d sink=%s speed=%s",
        args.session_key,
        len(events),
        args.sink,
        args.speed,
    )
    published = replay_events(
        events,
        build_sink(args.sink),
        speed=args.speed,
        max_events=args.max_events,
    )
    logger.info("Replay complete events=%d", published)


if __name__ == "__main__":
    main()
