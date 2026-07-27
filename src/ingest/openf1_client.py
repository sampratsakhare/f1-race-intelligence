"""Resilient client for the OpenF1 REST API."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import random
import time
from collections import deque
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import requests

logger = logging.getLogger(__name__)

BASE_URL = "https://api.openf1.org/v1"
MAX_RETRIES = 5
BACKOFF_SECONDS = 2.0
MAX_BACKOFF_SECONDS = 60.0
TIMESTAMP_FIELDS = frozenset({"date", "date_start", "date_end"})
SessionKey = int | str


class SlidingWindowRateLimiter:
    """Enforce both short- and long-window API limits.

    The clock and sleep functions are injectable so the behavior can be tested
    without making the test suite wait in real time.
    """

    def __init__(
        self,
        *,
        max_requests: int = 30,
        window_seconds: float = 60.0,
        min_interval_seconds: float = 1 / 3,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self.min_interval_seconds = min_interval_seconds
        self.clock = clock
        self.sleep = sleep
        self._requests: deque[float] = deque()

    def acquire(self) -> None:
        while True:
            now = self.clock()
            cutoff = now - self.window_seconds
            while self._requests and self._requests[0] <= cutoff:
                self._requests.popleft()

            interval_wait = 0.0
            if self._requests:
                interval_wait = self.min_interval_seconds - (now - self._requests[-1])

            window_wait = 0.0
            if len(self._requests) >= self.max_requests:
                window_wait = self.window_seconds - (now - self._requests[0]) + 0.001

            wait_seconds = max(interval_wait, window_wait, 0.0)
            if wait_seconds <= 0:
                self._requests.append(now)
                return
            self.sleep(wait_seconds)


class OpenF1Client:
    """OpenF1 client with authentication, throttling, and bounded retries."""

    def __init__(
        self,
        base_url: str = BASE_URL,
        session: requests.Session | None = None,
        access_token: str | None = None,
        rate_limiter: SlidingWindowRateLimiter | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.session = session or requests.Session()
        self.sleep = sleep
        self.rate_limiter = rate_limiter or SlidingWindowRateLimiter(sleep=sleep)

        token = access_token or os.getenv("OPENF1_ACCESS_TOKEN")
        if token:
            self.session.headers.update({"Authorization": f"Bearer {token}"})
        self.session.headers.setdefault("User-Agent", "f1-race-intelligence/1.0")

    def _retry_wait(self, response: requests.Response | None, attempt: int) -> float:
        if response is not None:
            retry_after = response.headers.get("Retry-After")
            if retry_after:
                try:
                    return min(float(retry_after), MAX_BACKOFF_SECONDS)
                except ValueError:
                    logger.warning("Ignoring invalid Retry-After header: %s", retry_after)

        exponential = min(
            BACKOFF_SECONDS * (2 ** (attempt - 1)),
            MAX_BACKOFF_SECONDS,
        )
        return exponential + random.uniform(0, min(exponential * 0.1, 1.0))

    def _get(
        self,
        endpoint: str,
        params: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        url = f"{self.base_url}/{endpoint}"
        last_error: Exception | None = None

        for attempt in range(1, MAX_RETRIES + 1):
            self.rate_limiter.acquire()
            response: requests.Response | None = None
            try:
                response = self.session.get(url, params=params or {}, timeout=30)

                if response.status_code == 429 or 500 <= response.status_code < 600:
                    response.raise_for_status()

                # Do not spend several minutes retrying permanent 4xx errors.
                response.raise_for_status()
                payload = response.json()
                if not isinstance(payload, list):
                    raise ValueError(
                        f"Expected a list from OpenF1 {endpoint}, received {type(payload).__name__}"
                    )
                return payload
            except (requests.ConnectionError, requests.Timeout) as exc:
                last_error = exc
            except requests.HTTPError as exc:
                last_error = exc
                status = response.status_code if response is not None else None
                if status is not None and status != 429 and status < 500:
                    raise
            except requests.RequestException as exc:
                last_error = exc

            if attempt == MAX_RETRIES:
                break

            wait_seconds = self._retry_wait(response, attempt)
            logger.warning(
                "OpenF1 request failed endpoint=%s attempt=%d/%d; retrying in %.1fs",
                endpoint,
                attempt,
                MAX_RETRIES,
                wait_seconds,
            )
            self.sleep(wait_seconds)

        raise RuntimeError(
            f"Failed to fetch OpenF1 endpoint {endpoint!r} after {MAX_RETRIES} attempts"
        ) from last_error

    def fetch(
        self,
        endpoint: str,
        **params: Any,
    ) -> list[dict[str, Any]]:
        """Fetch any documented endpoint while preserving the raw payload."""
        return self._get(endpoint, params)

    # Reference and general endpoints.
    def meetings(self, year: int | None = None) -> list[dict[str, Any]]:
        params = {"year": year} if year is not None else {}
        return self._get("meetings", params)

    def sessions(
        self,
        meeting_key: int | None = None,
        year: int | None = None,
        session_key: SessionKey | None = None,
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {}
        if meeting_key is not None:
            params["meeting_key"] = meeting_key
        if year is not None:
            params["year"] = year
        if session_key is not None:
            params["session_key"] = session_key
        return self._get("sessions", params)

    def drivers(self, session_key: SessionKey) -> list[dict[str, Any]]:
        return self._get("drivers", {"session_key": session_key})

    def weather(self, session_key: SessionKey) -> list[dict[str, Any]]:
        return self._get("weather", {"session_key": session_key})

    # Session-scoped endpoints.
    def laps(
        self,
        session_key: SessionKey,
        driver_number: int | None = None,
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"session_key": session_key}
        if driver_number is not None:
            params["driver_number"] = driver_number
        return self._get("laps", params)

    def position(self, session_key: SessionKey) -> list[dict[str, Any]]:
        return self._get("position", {"session_key": session_key})

    def stints(self, session_key: SessionKey) -> list[dict[str, Any]]:
        return self._get("stints", {"session_key": session_key})

    def pit(self, session_key: SessionKey) -> list[dict[str, Any]]:
        return self._get("pit", {"session_key": session_key})

    def race_control(self, session_key: SessionKey) -> list[dict[str, Any]]:
        return self._get("race_control", {"session_key": session_key})

    def intervals(self, session_key: SessionKey) -> list[dict[str, Any]]:
        return self._get("intervals", {"session_key": session_key})

    def starting_grid(self, session_key: SessionKey) -> list[dict[str, Any]]:
        return self._get("starting_grid", {"session_key": session_key})

    def session_result(self, session_key: SessionKey) -> list[dict[str, Any]]:
        return self._get("session_result", {"session_key": session_key})


def normalize_timestamp(value: Any) -> str | None:
    """Normalize ISO or Unix timestamps to an ISO 8601 UTC string."""
    if value is None:
        return None

    if isinstance(value, str):
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return dt.astimezone(UTC).isoformat()
        except ValueError:
            return value

    if isinstance(value, (int, float)):
        if value > 1e14:
            dt = datetime.fromtimestamp(value / 1_000_000, tz=UTC)
        elif value > 1e11:
            dt = datetime.fromtimestamp(value / 1_000, tz=UTC)
        else:
            dt = datetime.fromtimestamp(value, tz=UTC)
        return dt.isoformat()

    return None


def normalize_record_timestamps(record: dict[str, Any]) -> dict[str, Any]:
    """Return a copy with documented timestamp fields normalized."""
    return {
        key: normalize_timestamp(value) if key in TIMESTAMP_FIELDS else value
        for key, value in record.items()
    }


def build_event_id(endpoint: str, record: dict[str, Any]) -> str:
    """Build a deterministic ID used for idempotent streaming and deduplication."""
    canonical = json.dumps(
        {"endpoint": endpoint, "record": record},
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
