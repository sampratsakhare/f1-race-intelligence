"""Compatibility entrypoint for the original replay-poller CLI.

The canonical implementation is ``src.stream.replay``. This wrapper preserves
the earlier year/meeting/session arguments while using the corrected event
envelope, deterministic IDs, schemas, and failure behavior.
"""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

from src.stream.replay import (
    BigQuerySink,
    load_replay_events,
    replay_events,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

RAW_DATA_DIR = Path("data/raw")


def replay(
    year: int,
    meeting_key: int,
    session_key: int,
    speed: float,
    *,
    raw_data_dir: Path = RAW_DATA_DIR,
) -> int:
    session_dir = raw_data_dir / str(year) / str(meeting_key) / str(session_key)
    if not session_dir.is_dir():
        raise FileNotFoundError(f"{session_dir} not found; backfill this race first")

    logger.warning(
        "src.ingest.replay_poller is retained for compatibility; prefer python -m src.stream.replay"
    )
    events = load_replay_events(session_dir)
    published = replay_events(
        events,
        BigQuerySink(),
        speed=speed,
    )
    logger.info("Replay complete events=%d", published)
    return published


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Replay a historical F1 session into the live tables"
    )
    parser.add_argument("--year", type=int, required=True)
    parser.add_argument("--meeting-key", type=int, required=True)
    parser.add_argument("--session-key", type=int, required=True)
    parser.add_argument(
        "--speed",
        type=float,
        default=60.0,
        help="Replay acceleration; 0 publishes as fast as possible",
    )
    args = parser.parse_args()

    replay(
        args.year,
        args.meeting_key,
        args.session_key,
        args.speed,
    )


if __name__ == "__main__":
    main()
