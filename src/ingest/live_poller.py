"""
Live poller: during an active race session, repeatedly polls OpenF1's
real-time endpoints and streams new rows into BigQuery.

This is the "real-time analytics" piece of the project. It's intentionally
a simple polling loop rather than Pub/Sub + Dataflow -- that's a deliberate
scope choice for a portfolio build (see README "Scaling this up" section
for how it would grow into a proper streaming pipeline).

Run:
    python -m src.ingest.live_poller --session-key latest --poll-seconds 8

Notes:
- OpenF1 considers a session "live" from 30 min before it starts to 30 min
  after it ends. Outside that window, use historical_backfill.py instead.
- We track the max `date`/timestamp we've already seen per endpoint so we
  only push *new* rows to BigQuery on each poll (poor-man's watermarking).
"""

from __future__ import annotations

import argparse
import logging
import time
from datetime import datetime, timezone

from src.ingest.openf1_client import OpenF1Client
from src.load.bigquery_loader import BigQueryLoader

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

DEFAULT_POLL_SECONDS = 8


def get_latest_session_key(client: OpenF1Client) -> int:
    """Fetch the most recent session (used when --session-key latest)."""
    sessions = client.sessions(year=datetime.now(timezone.utc).year)
    if not sessions:
        raise RuntimeError("No sessions found for current year")
    latest = max(sessions, key=lambda s: s["date_start"])
    logger.info("Latest session: %s (%s)", latest.get("session_name"), latest["session_key"])
    return latest["session_key"]


class Watermark:
    """Tracks the last-seen timestamp per endpoint so we only send new rows."""

    def __init__(self):
        self._seen: dict[str, str] = {}

    def filter_new(self, endpoint: str, rows: list[dict], date_field: str = "date") -> list[dict]:
        last_seen = self._seen.get(endpoint)
        new_rows = [r for r in rows if not last_seen or r.get(date_field, "") > last_seen]
        if new_rows:
            self._seen[endpoint] = max(r.get(date_field, "") for r in new_rows)
        return new_rows


def poll_loop(session_key: int, poll_seconds: int, max_polls: int | None = None) -> None:
    client = OpenF1Client()
    loader = BigQueryLoader()
    watermark = Watermark()

    poll_count = 0
    logger.info("Starting live poll for session_key=%s every %ss", session_key, poll_seconds)

    while max_polls is None or poll_count < max_polls:
        poll_count += 1

        try:
            new_laps = watermark.filter_new("laps", client.laps(session_key))
            new_positions = watermark.filter_new("position", client.position(session_key))
            new_intervals = watermark.filter_new("intervals", client.intervals(session_key))
            new_pit = watermark.filter_new("pit", client.pit(session_key))

            if new_laps:
                loader.stream_insert("raw_live_laps", new_laps)
            if new_positions:
                loader.stream_insert("raw_live_position", new_positions)
            if new_intervals:
                loader.stream_insert("raw_live_intervals", new_intervals)
            if new_pit:
                loader.stream_insert("raw_live_pit", new_pit)

            logger.info(
                "Poll %d: +%d laps, +%d positions, +%d intervals, +%d pit stops",
                poll_count, len(new_laps), len(new_positions), len(new_intervals), len(new_pit),
            )

        except Exception:
            # Never let one bad poll kill the whole loop -- log and keep going.
            logger.exception("Poll %d failed, will retry on next cycle", poll_count)

        time.sleep(poll_seconds)


def main() -> None:
    parser = argparse.ArgumentParser(description="Poll OpenF1 live session data into BigQuery")
    parser.add_argument("--session-key", default="latest", help="OpenF1 session_key, or 'latest'")
    parser.add_argument("--poll-seconds", type=int, default=DEFAULT_POLL_SECONDS)
    parser.add_argument("--max-polls", type=int, default=None, help="Stop after N polls (for testing)")
    args = parser.parse_args()

    client = OpenF1Client()
    session_key = get_latest_session_key(client) if args.session_key == "latest" else int(args.session_key)

    poll_loop(session_key, args.poll_seconds, args.max_polls)


if __name__ == "__main__":
    main()
