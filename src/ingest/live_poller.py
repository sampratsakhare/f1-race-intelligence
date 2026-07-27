"""Poll an OpenF1 session into BigQuery with durable endpoint watermarks."""

from __future__ import annotations

import argparse
import json
import logging
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TypedDict

from src.ingest.openf1_client import (
    OpenF1Client,
    build_event_id,
    normalize_record_timestamps,
    normalize_timestamp,
)
from src.load.bigquery_loader import BigQueryLoader

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

DEFAULT_POLL_SECONDS = 8
DEFAULT_STATE_PATH = Path("state/live_watermarks.json")


class EndpointConfig(TypedDict):
    table: str
    timestamp_field: str
    fetch: Callable[[OpenF1Client, int], list[dict[str, Any]]]


ENDPOINTS: dict[str, EndpointConfig] = {
    "laps": {
        "table": "raw_live_laps",
        "timestamp_field": "date_start",
        "fetch": lambda client, session_key: client.laps(session_key),
    },
    "position": {
        "table": "raw_live_position",
        "timestamp_field": "date",
        "fetch": lambda client, session_key: client.position(session_key),
    },
    "intervals": {
        "table": "raw_live_intervals",
        "timestamp_field": "date",
        "fetch": lambda client, session_key: client.intervals(session_key),
    },
    "pit": {
        "table": "raw_live_pit",
        "timestamp_field": "date",
        "fetch": lambda client, session_key: client.pit(session_key),
    },
}


def get_latest_session_key(client: OpenF1Client) -> int:
    """Resolve OpenF1's documented latest/current session selector."""
    sessions = client.sessions(session_key="latest")
    if not sessions:
        raise RuntimeError("OpenF1 returned no latest/current session")
    latest = max(sessions, key=lambda s: s["date_start"])
    logger.info("Latest session: %s (%s)", latest.get("session_name"), latest["session_key"])
    return int(latest["session_key"])


class DurableWatermark:
    """Persist the last committed timestamp and boundary IDs per endpoint."""

    def __init__(self, state_path: Path, session_key: int):
        self.state_path = state_path
        self.session_key = session_key
        self._state = self._load()

    def _load(self) -> dict[str, dict[str, Any]]:
        if not self.state_path.exists():
            return {}
        try:
            payload = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            raise RuntimeError(f"Cannot read watermark state {self.state_path}: {exc}") from exc
        if not isinstance(payload, dict):
            raise RuntimeError(f"Invalid watermark state in {self.state_path}")
        return payload

    def _key(self, endpoint: str) -> str:
        return f"{self.session_key}:{endpoint}"

    def filter_new(
        self,
        endpoint: str,
        rows: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        state = self._state.get(self._key(endpoint), {})
        last_timestamp = state.get("timestamp")
        boundary_ids = set(state.get("event_ids_at_timestamp", []))
        undated_ids = set(state.get("undated_event_ids", []))
        new_rows = []

        for row in rows:
            event_id = str(row["_event_id"])
            event_timestamp = row.get("_source_event_time")
            if event_timestamp is None:
                if event_id not in undated_ids:
                    new_rows.append(row)
                continue

            if last_timestamp is None or event_timestamp > last_timestamp:
                new_rows.append(row)
            elif event_timestamp == last_timestamp and event_id not in boundary_ids:
                new_rows.append(row)

        return new_rows

    def commit(self, endpoint: str, rows: list[dict[str, Any]]) -> None:
        if not rows:
            return

        key = self._key(endpoint)
        current = self._state.get(key, {})
        timestamped = [row for row in rows if row.get("_source_event_time") is not None]
        undated_ids = set(current.get("undated_event_ids", []))
        undated_ids.update(
            str(row["_event_id"]) for row in rows if row.get("_source_event_time") is None
        )

        next_state: dict[str, Any] = {
            "timestamp": current.get("timestamp"),
            "event_ids_at_timestamp": current.get("event_ids_at_timestamp", []),
            # Keep this bounded; documented endpoints should normally be dated.
            "undated_event_ids": sorted(undated_ids)[-5_000:],
        }

        if timestamped:
            max_timestamp = max(str(row["_source_event_time"]) for row in timestamped)
            max_ids = {
                str(row["_event_id"])
                for row in timestamped
                if row["_source_event_time"] == max_timestamp
            }
            if current.get("timestamp") == max_timestamp:
                max_ids.update(current.get("event_ids_at_timestamp", []))
            next_state["timestamp"] = max_timestamp
            next_state["event_ids_at_timestamp"] = sorted(max_ids)

        self._state[key] = next_state
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = self.state_path.with_suffix(f"{self.state_path.suffix}.tmp")
        temporary_path.write_text(
            json.dumps(self._state, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        temporary_path.replace(self.state_path)


def prepare_rows(
    endpoint: str,
    rows: list[dict[str, Any]],
    timestamp_field: str,
) -> list[dict[str, Any]]:
    """Normalize timestamps and add a deterministic ingestion envelope."""
    ingested_at = datetime.now(UTC).isoformat()
    prepared = []

    for raw_row in rows:
        normalized = normalize_record_timestamps(raw_row)
        source_event_time = normalize_timestamp(raw_row.get(timestamp_field))
        event_id = build_event_id(endpoint, normalized)

        if endpoint == "intervals":
            for field in ("gap_to_leader", "interval"):
                if normalized.get(field) is not None:
                    normalized[field] = str(normalized[field])

        prepared.append(
            {
                **normalized,
                "_event_id": event_id,
                "_ingested_at": ingested_at,
                "_source_event_time": source_event_time,
                "_source_endpoint": endpoint,
            }
        )

    return prepared


def poll_loop(
    session_key: int,
    poll_seconds: int,
    max_polls: int | None = None,
    *,
    state_path: Path = DEFAULT_STATE_PATH,
    client: OpenF1Client | None = None,
    loader: BigQueryLoader | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> None:
    client = client or OpenF1Client()
    loader = loader or BigQueryLoader()
    loader.ensure_live_tables_exist()
    watermark = DurableWatermark(state_path, session_key)

    poll_count = 0
    logger.info("Starting live poll for session_key=%s every %ss", session_key, poll_seconds)

    while max_polls is None or poll_count < max_polls:
        poll_count += 1
        counts: dict[str, int] = {}

        for endpoint, config in ENDPOINTS.items():
            try:
                raw_rows = config["fetch"](client, session_key)
                prepared_rows = prepare_rows(
                    endpoint,
                    raw_rows,
                    config["timestamp_field"],
                )
                new_rows = watermark.filter_new(endpoint, prepared_rows)
                if new_rows:
                    loader.stream_insert(config["table"], new_rows)
                    # Advance the durable state only after BigQuery confirms success.
                    watermark.commit(endpoint, new_rows)
                counts[endpoint] = len(new_rows)
            except Exception:
                counts[endpoint] = 0
                logger.exception(
                    "Poll %d failed endpoint=%s; state was not advanced",
                    poll_count,
                    endpoint,
                )

        logger.info(
            "Poll %d: %s",
            poll_count,
            ", ".join(f"+{count} {name}" for name, count in counts.items()),
        )

        if max_polls is None or poll_count < max_polls:
            sleep(poll_seconds)


def main() -> None:
    parser = argparse.ArgumentParser(description="Poll OpenF1 live session data into BigQuery")
    parser.add_argument("--session-key", default="latest", help="OpenF1 session_key, or 'latest'")
    parser.add_argument("--poll-seconds", type=int, default=DEFAULT_POLL_SECONDS)
    parser.add_argument(
        "--max-polls",
        type=int,
        default=None,
        help="Stop after N polls (for testing)",
    )
    parser.add_argument(
        "--state-path",
        type=Path,
        default=DEFAULT_STATE_PATH,
        help="Durable local watermark path",
    )
    parser.add_argument(
        "--reset-state",
        action="store_true",
        help="Remove the selected session's local watermark before polling",
    )
    args = parser.parse_args()

    client = OpenF1Client()
    session_key = (
        get_latest_session_key(client) if args.session_key == "latest" else int(args.session_key)
    )

    if args.reset_state and args.state_path.exists():
        payload = json.loads(args.state_path.read_text(encoding="utf-8"))
        prefix = f"{session_key}:"
        payload = {key: value for key, value in payload.items() if not key.startswith(prefix)}
        args.state_path.write_text(
            json.dumps(payload, indent=2, sort_keys=True),
            encoding="utf-8",
        )

    poll_loop(
        session_key,
        args.poll_seconds,
        args.max_polls,
        state_path=args.state_path,
        client=client,
    )


if __name__ == "__main__":
    main()
