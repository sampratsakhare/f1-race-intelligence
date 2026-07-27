"""
Replay poller: replays an already-backfilled historical race through the
SAME BigQuery streaming path that live_poller.py uses, at accelerated
speed, ordered by each event's real timestamp.

Why this exists: OpenF1's genuinely live (in-session) data now requires a
paid account (see openf1_client.py docstring). Rather than block the
"real-time analytics" story on a paywall, this replays real historical
race data through the identical streaming architecture -- same tables,
same dashboard Live tab, same watermarking logic -- just sourced from
data/raw/ instead of a live session. This is labeled honestly as replay,
not disguised as genuine live data.

Run:
    python -m src.ingest.replay_poller --year 2024 --meeting-key 1229 --session-key 9472 --speed 60

--speed 60 means 60x real time (an hour of race becomes a minute of replay).
"""

from __future__ import annotations

import argparse
import json
import logging
import time
from datetime import datetime
from pathlib import Path

from src.load.bigquery_loader import BigQueryLoader

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

RAW_DATA_DIR = Path("data/raw")

# Matches live_poller.py's per-endpoint timestamp field names, so replayed
# data lands in the same shape the dashboard's Live tab already expects.
REPLAY_SOURCES = {
    "laps": ("laps.json", "raw_live_laps", "date_start"),
    "position": ("position.json", "raw_live_position", "date"),
    "pit": ("pit.json", "raw_live_pit", "date"),
}


def _parse_dt(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def load_session_events(year: int, meeting_key: int, session_key: int) -> list[tuple[datetime, str, str, dict]]:
    """Loads all replayable rows for a session and returns them as a single
    chronologically sorted list of (timestamp, endpoint_name, table_name, row).
    """
    session_dir = RAW_DATA_DIR / str(year) / str(meeting_key) / str(session_key)
    if not session_dir.exists():
        raise FileNotFoundError(
            f"{session_dir} not found -- run historical_backfill.py for this race first"
        )

    events: list[tuple[datetime, str, str, dict]] = []
    for endpoint_name, (filename, table_name, date_field) in REPLAY_SOURCES.items():
        path = session_dir / filename
        if not path.exists():
            logger.warning("%s not found for this session, skipping", filename)
            continue
        rows = json.loads(path.read_text())
        for row in rows:
            raw_ts = row.get(date_field)
            if not raw_ts:
                continue
            events.append((_parse_dt(raw_ts), endpoint_name, table_name, row))

    events.sort(key=lambda e: e[0])
    logger.info("Loaded %d replayable events across %d endpoints", len(events), len(REPLAY_SOURCES))
    return events


def replay(year: int, meeting_key: int, session_key: int, speed: float) -> None:
    events = load_session_events(year, meeting_key, session_key)
    if not events:
        logger.warning("No events to replay")
        return

    loader = BigQueryLoader()
    start_wall_clock = time.monotonic()
    start_event_time = events[0][0]

    for i, (event_time, endpoint_name, table_name, row) in enumerate(events):
        # How far into the race this event occurred, compressed by `speed`.
        race_elapsed = (event_time - start_event_time).total_seconds()
        target_wall_elapsed = race_elapsed / speed
        actual_wall_elapsed = time.monotonic() - start_wall_clock
        wait = target_wall_elapsed - actual_wall_elapsed
        if wait > 0:
            time.sleep(wait)

        try:
            loader.stream_insert(table_name, [row])
        except Exception:
            logger.exception("Failed to insert replayed %s row, skipping", endpoint_name)

        if i % 50 == 0:
            logger.info("Replayed %d/%d events (%s)", i + 1, len(events), endpoint_name)

    logger.info("Replay complete: %d events streamed", len(events))


def main() -> None:
    parser = argparse.ArgumentParser(description="Replay a historical F1 session through the live streaming path")
    parser.add_argument("--year", type=int, required=True)
    parser.add_argument("--meeting-key", type=int, required=True)
    parser.add_argument("--session-key", type=int, required=True)
    parser.add_argument("--speed", type=float, default=60.0,
                         help="Replay speed multiplier (60 = 1 real hour becomes 1 replay minute)")
    args = parser.parse_args()

    replay(args.year, args.meeting_key, args.session_key, args.speed)


if __name__ == "__main__":
    main()
