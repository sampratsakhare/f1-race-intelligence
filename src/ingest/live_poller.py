"""
Live poller: during an active race session, repeatedly polls OpenF1's
real-time endpoints and streams new rows into BigQuery.

This is the "real-time analytics" piece of the project. It's intentionally
a simple polling loop rather than Pub/Sub + Dataflow -- that's a deliberate
scope choice for a portfolio build (see README "Scaling this up" section).

IMPORTANT: as of OpenF1's current policy, real-time (live-window) data
requires a paid account and bearer token -- see openf1_client.py and
https://openf1.org/auth.html. Without one, this script will fail against
a genuinely live session. For a demoable "real-time" story without paying
for access, use replay_poller.py instead, which replays already-backfilled
historical data through this same streaming path at accelerated speed.

Run:
    python -m src.ingest.live_poller --session-key active --poll-seconds 8
    python -m src.ingest.live_poller --session-key active --poll-seconds 8 --access-token YOUR_TOKEN

Notes:
- OpenF1 considers a session "live" from 30 min before it starts to 30 min
  after it ends. Outside that window, use historical_backfill.py instead.
- Different endpoints use different timestamp field names (laps use
  "date_start", most others use "date") -- the watermark is endpoint-aware
  about this rather than assuming one field name for everything.
- The watermark only advances AFTER a BigQuery insert is confirmed
  successful. If an insert fails, those rows are retried on the next poll
  (which may create a small number of duplicate rows downstream -- safer
  than the alternative of silently losing data).
"""

from __future__ import annotations

import argparse
import logging
import time
from datetime import datetime, timedelta, timezone

from src.ingest.openf1_client import OpenF1Client
from src.load.bigquery_loader import BigQueryLoader

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

DEFAULT_POLL_SECONDS = 8
LIVE_WINDOW = timedelta(minutes=30)

# Each OpenF1 endpoint uses a different field name for its timestamp.
ENDPOINT_DATE_FIELDS = {
    "laps": "date_start",
    "position": "date",
    "intervals": "date",
    "pit": "date",
}


def _parse_dt(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def get_active_session_key(client: OpenF1Client) -> int:
    """Find the session that's actually live right now (within its 30-min
    live window on either side), not just the one with the latest scheduled
    start time -- a naive "max start date" pick can select a future race
    that hasn't happened yet.
    """
    now = datetime.now(timezone.utc)
    sessions = client.sessions(year=now.year)
    if not sessions:
        raise RuntimeError("No sessions found for current year")

    live_now = []
    for s in sessions:
        start = _parse_dt(s["date_start"])
        end = _parse_dt(s.get("date_end", s["date_start"]))
        if (start - LIVE_WINDOW) <= now <= (end + LIVE_WINDOW):
            live_now.append(s)

    if live_now:
        chosen = min(live_now, key=lambda s: s["date_end"])
        logger.info("Active session: %s (session_key=%s)", chosen.get("session_name"), chosen["session_key"])
        return chosen["session_key"]

    # Nothing live right now -- fall back to the most recent past session so
    # the script is still usable for a quick smoke test outside a race weekend.
    past = [s for s in sessions if _parse_dt(s.get("date_end", s["date_start"])) <= now]
    if past:
        latest_past = max(past, key=lambda s: s["date_start"])
        logger.warning(
            "No session is live right now. Falling back to most recent past session: %s (session_key=%s). "
            "Historical data for it is free; this is fine for testing the pipeline, not a real live demo.",
            latest_past.get("session_name"), latest_past["session_key"],
        )
        return latest_past["session_key"]

    raise RuntimeError("No active or past session found for the current year")


class Watermark:
    """Tracks the last-seen timestamp per endpoint. Rows are only considered
    "committed" (excluded from future polls) once commit() is called --
    callers should call commit() only after successfully writing the rows
    downstream, so a failed write doesn't silently lose data.
    """

    def __init__(self):
        self._seen: dict[str, str] = {}

    def get_new(self, endpoint: str, rows: list[dict], date_field: str) -> list[dict]:
        last_seen = self._seen.get(endpoint)
        return [r for r in rows if not last_seen or r.get(date_field, "") > last_seen]

    def commit(self, endpoint: str, rows: list[dict], date_field: str) -> None:
        if not rows:
            return
        newest = max(r.get(date_field, "") for r in rows)
        if newest > self._seen.get(endpoint, ""):
            self._seen[endpoint] = newest


def _poll_endpoint(name: str, fetch_fn, table_name: str, watermark: Watermark, loader: BigQueryLoader) -> int:
    """Fetch, filter to new rows, insert, and only commit the watermark on
    success. Returns the number of rows inserted."""
    date_field = ENDPOINT_DATE_FIELDS[name]
    rows = fetch_fn()
    new_rows = watermark.get_new(name, rows, date_field)

    if not new_rows:
        return 0

    loader.stream_insert(table_name, new_rows)   # raises on real failure
    watermark.commit(name, new_rows, date_field)  # only reached if insert succeeded
    return len(new_rows)


def poll_loop(session_key: int, poll_seconds: int, max_polls: int | None = None) -> None:
    client = OpenF1Client()
    loader = BigQueryLoader()
    watermark = Watermark()

    poll_count = 0
    logger.info("Starting live poll for session_key=%s every %ss", session_key, poll_seconds)

    while max_polls is None or poll_count < max_polls:
        poll_count += 1
        counts = {}

        for name, table_name, fetch_fn in [
            ("laps", "raw_live_laps", lambda: client.laps(session_key)),
            ("position", "raw_live_position", lambda: client.position(session_key)),
            ("intervals", "raw_live_intervals", lambda: client.intervals(session_key)),
            ("pit", "raw_live_pit", lambda: client.pit(session_key)),
        ]:
            try:
                counts[name] = _poll_endpoint(name, fetch_fn, table_name, watermark, loader)
            except Exception:
                # Log and move on to the next endpoint -- one bad endpoint
                # shouldn't stop the others from being polled this cycle.
                logger.exception("Poll %d: %s endpoint failed, will retry next cycle", poll_count, name)
                counts[name] = 0

        logger.info(
            "Poll %d: +%d laps, +%d positions, +%d intervals, +%d pit stops",
            poll_count, counts.get("laps", 0), counts.get("position", 0),
            counts.get("intervals", 0), counts.get("pit", 0),
        )

        time.sleep(poll_seconds)


def main() -> None:
    parser = argparse.ArgumentParser(description="Poll OpenF1 live session data into BigQuery")
    parser.add_argument("--session-key", default="active",
                         help="OpenF1 session_key, or 'active' (also accepts legacy 'latest')")
    parser.add_argument("--poll-seconds", type=int, default=DEFAULT_POLL_SECONDS)
    parser.add_argument("--max-polls", type=int, default=None, help="Stop after N polls (for testing)")
    parser.add_argument("--access-token", default=None,
                         help="OpenF1 bearer token, required for genuinely live data (paid account)")
    args = parser.parse_args()

    client = OpenF1Client(access_token=args.access_token)
    if args.session_key in ("active", "latest"):
        session_key = get_active_session_key(client)
    else:
        session_key = int(args.session_key)

    poll_loop(session_key, args.poll_seconds, args.max_polls)


if __name__ == "__main__":
    main()
