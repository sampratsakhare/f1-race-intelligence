"""
Historical backfill: pulls race weekends for given years from OpenF1
and lands raw JSON files locally (mirroring what would go to
Cloud Storage in the cloud version).

Run:
    python -m src.ingest.historical_backfill --years 2023 2024 2025

Output layout (mirrors a GCS bucket you'd create later):
    data/raw/{year}/{meeting_key}/{session_key}/
        drivers.json
        laps.json
        stints.json
        pit.json
        position.json
        weather.json
        race_control.json
"""

from __future__ import annotations

import argparse
import json
import logging
import time
from pathlib import Path

from src.ingest.openf1_client import OpenF1Client

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

RAW_DATA_DIR = Path("data/raw")

# We only care about Race sessions for the first pass -- Qualifying/Practice
# can be added later once the core pipeline works end to end.
RELEVANT_SESSION_TYPES = {"Race"}


def _write_json(path: Path, payload: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as f:
        json.dump(payload, f)
    logger.info("Wrote %s (%d records)", path, len(payload))


def backfill_year(client: OpenF1Client, year: int) -> None:
    meetings = client.meetings(year=year)
    logger.info("Year %s: found %d meetings", year, len(meetings))

    for meeting in meetings:
        meeting_key = meeting["meeting_key"]
        sessions = client.sessions(meeting_key=meeting_key)

        race_sessions = [s for s in sessions if s.get("session_name") in RELEVANT_SESSION_TYPES]

        for session in race_sessions:
            session_key = session["session_key"]
            out_dir = RAW_DATA_DIR / str(year) / str(meeting_key) / str(session_key)

            logger.info(
                "Backfilling %s - %s (session_key=%s)",
                meeting.get("meeting_name"),
                session.get("session_name"),
                session_key,
            )

            endpoints = {
                "drivers.json": client.drivers(session_key),
                "laps.json": client.laps(session_key),
                "stints.json": client.stints(session_key),
                "pit.json": client.pit(session_key),
                "position.json": client.position(session_key),
                "weather.json": client.weather(session_key),
                "race_control.json": client.race_control(session_key),
            }

            for filename, payload in endpoints.items():
                _write_json(out_dir / filename, payload)

            # Also stash meeting/session metadata for convenience downstream.
            _write_json(out_dir / "meeting.json", [meeting])
            _write_json(out_dir / "session.json", [session])
        time.sleep(2)       


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill historical F1 data from OpenF1")
    parser.add_argument("--years", type=int, nargs="+", required=True, help="e.g. --years 2023 2024 2025")
    args = parser.parse_args()

    client = OpenF1Client()
    for year in args.years:
        backfill_year(client, year)


if __name__ == "__main__":
    main()
