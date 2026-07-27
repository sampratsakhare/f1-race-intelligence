"""Idempotent historical OpenF1 backfill to local storage and optional GCS."""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from src.ingest.openf1_client import OpenF1Client

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)
load_dotenv()

RAW_DATA_DIR = Path("data/raw")
SESSION_ENDPOINTS = (
    "drivers",
    "laps",
    "stints",
    "pit",
    "position",
    "intervals",
    "weather",
    "race_control",
    "starting_grid",
    "session_result",
)


def _grid_session_key(
    target_session: dict[str, Any],
    meeting_sessions: list[dict[str, Any]],
) -> int:
    """Return the qualifying session whose result sets the target grid."""
    target_name = target_session.get("session_name")
    qualifying_names = (
        {"Qualifying"} if target_name == "Race" else {"Sprint Qualifying", "Sprint Shootout"}
    )
    candidates = [
        session for session in meeting_sessions if session.get("session_name") in qualifying_names
    ]
    if not candidates:
        raise RuntimeError(
            f"No grid-setting qualifying session found for "
            f"{target_name!r} session_key={target_session.get('session_key')}"
        )

    target_start = target_session.get("date_start", "")
    preceding = [
        session
        for session in candidates
        if not target_start or session.get("date_start", "") < target_start
    ]
    selected = max(
        preceding or candidates,
        key=lambda session: session.get("date_start", ""),
    )
    return int(selected["session_key"])


def _fetch_session_payload(
    client: OpenF1Client,
    endpoint: str,
    target_session: dict[str, Any],
    meeting_sessions: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    target_session_key = int(target_session["session_key"])
    session_key = target_session_key
    if endpoint == "starting_grid":
        session_key = _grid_session_key(target_session, meeting_sessions)
    payload = client.fetch(endpoint, session_key=session_key)
    if endpoint == "starting_grid":
        return [
            {
                **row,
                "_target_session_key": target_session_key,
            }
            for row in payload
        ]
    return payload


def _write_json(path: Path, payload: Any) -> str:
    """Atomically write JSON and return its SHA-256 checksum."""
    path.parent.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(payload, separators=(",", ":"), default=str)
    temporary_path = path.with_suffix(f"{path.suffix}.tmp")
    temporary_path.write_text(serialized, encoding="utf-8")
    temporary_path.replace(path)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _inspect_existing_json(path: Path) -> tuple[Any, str]:
    serialized = path.read_text(encoding="utf-8")
    return json.loads(serialized), hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _upload_to_gcs(local_path: Path, bucket_name: str, object_name: str) -> None:
    try:
        from google.cloud import storage
    except ImportError as exc:
        raise RuntimeError(
            "GCS upload requested but google-cloud-storage is not installed"
        ) from exc

    client = storage.Client()
    bucket = client.bucket(bucket_name)
    bucket.blob(object_name).upload_from_filename(local_path)
    logger.info("Uploaded gs://%s/%s", bucket_name, object_name)


def _gcs_object_name(
    endpoint: str,
    year: int,
    meeting_key: int,
    session_key: int,
    filename: str,
) -> str:
    return (
        "openf1/raw/"
        f"endpoint={endpoint}/year={year}/meeting_key={meeting_key}/"
        f"session_key={session_key}/{filename}"
    )


def _store_payload(
    *,
    endpoint: str,
    payload: Any,
    out_dir: Path,
    year: int,
    meeting_key: int,
    session_key: int,
    gcs_bucket: str | None,
    force: bool,
) -> dict[str, Any]:
    path = out_dir / f"{endpoint}.json"
    if path.exists() and not force:
        stored_payload, checksum = _inspect_existing_json(path)
        source = "existing"
    else:
        checksum = _write_json(path, payload)
        stored_payload = payload
        source = "fetched"

    if gcs_bucket:
        _upload_to_gcs(
            path,
            gcs_bucket,
            _gcs_object_name(
                endpoint,
                year,
                meeting_key,
                session_key,
                "data.json",
            ),
        )

    row_count = len(stored_payload) if isinstance(stored_payload, list) else 1
    logger.info(
        "Stored endpoint=%s session=%s rows=%d source=%s",
        endpoint,
        session_key,
        row_count,
        source,
    )
    return {
        "status": "success",
        "source": source,
        "row_count": row_count,
        "sha256": checksum,
        "local_path": str(path),
    }


def backfill_year(
    client: OpenF1Client,
    year: int,
    session_names: set[str],
    *,
    gcs_bucket: str | None = None,
    force: bool = False,
) -> list[dict[str, Any]]:
    failures: list[dict[str, Any]] = []
    meetings = client.meetings(year=year)
    logger.info("Year %s: found %d meetings", year, len(meetings))

    for meeting in meetings:
        meeting_key = meeting["meeting_key"]
        sessions = client.sessions(meeting_key=meeting_key)

        # Session names are intentional here: OpenF1 labels both Grands Prix
        # and Sprints with session_type="Race".
        relevant_sessions = [
            session for session in sessions if session.get("session_name") in session_names
        ]

        for session in relevant_sessions:
            session_key = session["session_key"]
            out_dir = RAW_DATA_DIR / str(year) / str(meeting_key) / str(session_key)

            logger.info(
                "Backfilling %s - %s (session_key=%s)",
                meeting.get("meeting_name"),
                session.get("session_name"),
                session_key,
            )

            manifest: dict[str, Any] = {
                "captured_at": datetime.now(UTC).isoformat(),
                "year": year,
                "meeting_key": meeting_key,
                "session_key": session_key,
                "session_name": session.get("session_name"),
                "endpoints": {},
            }

            static_payloads = {
                "meeting": [meeting],
                "session": [session],
            }
            for endpoint, payload in static_payloads.items():
                manifest["endpoints"][endpoint] = _store_payload(
                    endpoint=endpoint,
                    payload=payload,
                    out_dir=out_dir,
                    year=year,
                    meeting_key=meeting_key,
                    session_key=session_key,
                    gcs_bucket=gcs_bucket,
                    force=force,
                )

            for endpoint in SESSION_ENDPOINTS:
                try:
                    endpoint_path = out_dir / f"{endpoint}.json"
                    endpoint_force = force
                    if endpoint == "starting_grid" and endpoint_path.exists() and not force:
                        existing_grid, _ = _inspect_existing_json(endpoint_path)
                        has_target_contract = (
                            isinstance(existing_grid, list)
                            and bool(existing_grid)
                            and all(
                                isinstance(row, dict) and row.get("_target_session_key") is not None
                                for row in existing_grid
                            )
                        )
                        endpoint_force = not has_target_contract
                    payload = (
                        []
                        if endpoint_path.exists() and not endpoint_force
                        else _fetch_session_payload(
                            client,
                            endpoint,
                            session,
                            sessions,
                        )
                    )
                    manifest["endpoints"][endpoint] = _store_payload(
                        endpoint=endpoint,
                        payload=payload,
                        out_dir=out_dir,
                        year=year,
                        meeting_key=meeting_key,
                        session_key=session_key,
                        gcs_bucket=gcs_bucket,
                        force=endpoint_force,
                    )
                except Exception as exc:
                    logger.exception(
                        "Backfill failed endpoint=%s session_key=%s",
                        endpoint,
                        session_key,
                    )
                    failure = {
                        "year": year,
                        "meeting_key": meeting_key,
                        "session_key": session_key,
                        "endpoint": endpoint,
                        "error": str(exc),
                    }
                    failures.append(failure)
                    manifest["endpoints"][endpoint] = {
                        "status": "failed",
                        "error": str(exc),
                    }

            manifest["status"] = (
                "failed"
                if any(result["status"] == "failed" for result in manifest["endpoints"].values())
                else "success"
            )
            manifest_path = out_dir / "_manifest.json"
            _write_json(manifest_path, manifest)
            if gcs_bucket:
                _upload_to_gcs(
                    manifest_path,
                    gcs_bucket,
                    _gcs_object_name(
                        "_manifest",
                        year,
                        meeting_key,
                        session_key,
                        "manifest.json",
                    ),
                )

    return failures


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill historical F1 data from OpenF1")
    parser.add_argument(
        "--years",
        type=int,
        nargs="+",
        required=True,
        help="e.g. --years 2023 2024 2025",
    )
    parser.add_argument(
        "--session-names",
        "--session-types",
        dest="session_names",
        nargs="+",
        default=["Race"],
        help="OpenF1 session names to ingest (default: Race; excludes Sprints)",
    )
    parser.add_argument(
        "--gcs-bucket",
        default=os.getenv("GCS_BUCKET"),
        help="Optional GCS bucket; defaults to GCS_BUCKET",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Refetch and overwrite payloads that already exist locally",
    )
    parser.add_argument(
        "--continue-on-error",
        action="store_true",
        help="Return success even if some endpoints fail",
    )
    args = parser.parse_args()

    client = OpenF1Client()
    failures: list[dict[str, Any]] = []
    for year in args.years:
        failures.extend(
            backfill_year(
                client,
                year,
                set(args.session_names),
                gcs_bucket=args.gcs_bucket,
                force=args.force,
            )
        )

    if failures:
        logger.error("Backfill completed with %d endpoint failures", len(failures))
        if not args.continue_on_error:
            raise RuntimeError("Historical backfill was incomplete; inspect session manifests")


if __name__ == "__main__":
    main()
