"""
Thin client for the OpenF1 API (https://openf1.org).

Handles:
- Rate limiting (free tier: 3 req/s, 30 req/min)
- Retries with backoff on transient errors
- Timestamp normalization (API sometimes returns ISO strings,
  sometimes Unix microseconds, depending on endpoint/response)

Docs: https://openf1.org/docs/
"""

from __future__ import annotations

import time
import logging
from datetime import datetime, timezone
from typing import Any

import requests

logger = logging.getLogger(__name__)

BASE_URL = "https://api.openf1.org/v1"

# Free tier: 3 req/s and 30 req/min. We stay well under that.
# Note: in sustained multi-endpoint pulls across many race weekends, OpenF1
# throttles harder than the nominal limit suggests, so we're conservative
# here and retry with a longer backoff rather than giving up fast.
MIN_SECONDS_BETWEEN_REQUESTS = 1.0
MAX_RETRIES = 6
BACKOFF_SECONDS = 3


class OpenF1Client:
    """Simple REST client for OpenF1 with basic rate limiting + retry.

    Historical data (30+ min after a session ends) is free, no auth needed.
    Real-time data (during the live window) requires a paid OpenF1 account
    and a bearer token -- see https://openf1.org/auth.html. Pass one via
    access_token if you have one; without it, live endpoints will 401/403
    during the live window (historical_backfill.py is unaffected).
    """

    def __init__(self, base_url: str = BASE_URL, session: requests.Session | None = None,
                 access_token: str | None = None):
        self.base_url = base_url
        self.session = session or requests.Session()
        self._last_request_ts: float = 0.0
        if access_token:
            self.session.headers["Authorization"] = f"Bearer {access_token}"

    def _throttle(self) -> None:
        elapsed = time.monotonic() - self._last_request_ts
        wait = MIN_SECONDS_BETWEEN_REQUESTS - elapsed
        if wait > 0:
            time.sleep(wait)

    def _get(self, endpoint: str, params: dict[str, Any] | None = None) -> list[dict]:
        url = f"{self.base_url}/{endpoint}"
        last_err = None

        for attempt in range(1, MAX_RETRIES + 1):
            self._throttle()
            try:
                resp = self.session.get(url, params=params, timeout=15)
                self._last_request_ts = time.monotonic()

                if resp.status_code == 429:
                    wait = BACKOFF_SECONDS * (2 ** (attempt - 1))
                    logger.warning("Rate limited on %s, backing off %ss (attempt %d/%d)", endpoint, wait, attempt, MAX_RETRIES)
                    time.sleep(wait)
                    continue

                resp.raise_for_status()
                return resp.json()

            except requests.RequestException as exc:
                last_err = exc
                wait = BACKOFF_SECONDS * (2 ** (attempt - 1))
                logger.warning("Request to %s failed (%s), retrying in %ss (attempt %d/%d)", endpoint, exc, wait, attempt, MAX_RETRIES)
                time.sleep(wait)

        raise RuntimeError(f"Failed to fetch {endpoint} after {MAX_RETRIES} attempts") from last_err

    # ---- Reference / general endpoints ----

    def meetings(self, year: int | None = None) -> list[dict]:
        params = {"year": year} if year else {}
        return self._get("meetings", params)

    def sessions(self, meeting_key: int | None = None, year: int | None = None) -> list[dict]:
        params = {}
        if meeting_key is not None:
            params["meeting_key"] = meeting_key
        if year is not None:
            params["year"] = year
        return self._get("sessions", params)

    def drivers(self, session_key: int) -> list[dict]:
        return self._get("drivers", {"session_key": session_key})

    def weather(self, session_key: int) -> list[dict]:
        return self._get("weather", {"session_key": session_key})

    # ---- Session-scoped endpoints ----

    def laps(self, session_key: int, driver_number: int | None = None) -> list[dict]:
        params = {"session_key": session_key}
        if driver_number is not None:
            params["driver_number"] = driver_number
        return self._get("laps", params)

    def position(self, session_key: int) -> list[dict]:
        return self._get("position", {"session_key": session_key})

    def stints(self, session_key: int) -> list[dict]:
        """Tire stint data: compound, stint length, tyre age."""
        return self._get("stints", {"session_key": session_key})

    def pit(self, session_key: int) -> list[dict]:
        """Pit stop duration and lap number."""
        return self._get("pit", {"session_key": session_key})

    def race_control(self, session_key: int) -> list[dict]:
        """Flags, safety car, incident messages."""
        return self._get("race_control", {"session_key": session_key})

    def intervals(self, session_key: int) -> list[dict]:
        """Gap to leader / car ahead. Live races only, ~4s update cadence."""
        return self._get("intervals", {"session_key": session_key})


def normalize_timestamp(value: Any) -> str | None:
    """
    Normalize OpenF1 timestamps to ISO 8601 UTC strings.

    Handles both ISO strings and Unix epoch (seconds or microseconds),
    since the API is inconsistent about this across endpoints.
    """
    if value is None:
        return None

    if isinstance(value, str):
        # Already ISO-ish; just make sure it round-trips cleanly.
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return dt.astimezone(timezone.utc).isoformat()
        except ValueError:
            return value  # leave as-is, better than crashing the pipeline

    if isinstance(value, (int, float)):
        # Distinguish seconds vs microseconds by magnitude.
        if value > 1e14:  # microseconds
            dt = datetime.fromtimestamp(value / 1_000_000, tz=timezone.utc)
        elif value > 1e11:  # milliseconds
            dt = datetime.fromtimestamp(value / 1000, tz=timezone.utc)
        else:  # seconds
            dt = datetime.fromtimestamp(value, tz=timezone.utc)
        return dt.isoformat()

    return None
