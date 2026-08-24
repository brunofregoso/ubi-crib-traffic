#!/usr/bin/env python3
"""
Single-run ETA collector, meant to be invoked by cron (or any scheduler)
every COLLECTION_INTERVAL_MINUTES.

Usage:
    python collect_eta.py --direction home_to_work
    python collect_eta.py --direction work_to_home

Stateless: all config comes from .env / the environment. Calls the Google
Maps Distance Matrix API for one direction with departure_time=now and
traffic_model=best_guess, then hands a structured reading to the DB
layer's write_reading(). Fails loudly (non-zero exit, stderr log) on API
errors instead of swallowing them, so gaps in the data are visible in
cron/container logs.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import googlemaps
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from db.db_client import write_reading  # noqa: E402

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger("collect_eta")

DIRECTIONS = {
    "home_to_work": ("HOME_COORDS", "WORK_COORDS"),
    "work_to_home": ("WORK_COORDS", "HOME_COORDS"),
}


def in_collection_window() -> bool:
    start_hour = int(os.environ.get("COLLECTION_WINDOW_START_HOUR", 0))
    end_hour = int(os.environ.get("COLLECTION_WINDOW_END_HOUR", 24))
    now_hour = datetime.now().astimezone().hour
    return start_hour <= now_hour < end_hour


def require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        log.error("Missing required environment variable: %s", name)
        sys.exit(1)
    return value


def collect(direction: str) -> None:
    if direction not in DIRECTIONS:
        log.error("Unknown direction %r (expected one of %s)", direction, list(DIRECTIONS))
        sys.exit(1)

    origin_var, dest_var = DIRECTIONS[direction]
    api_key = require_env("GOOGLE_MAPS_API_KEY")
    origin = require_env(origin_var)
    destination = require_env(dest_var)

    client = googlemaps.Client(key=api_key)

    try:
        response = client.distance_matrix(
            origins=[origin],
            destinations=[destination],
            departure_time="now",
            traffic_model="best_guess",
        )
    except Exception as exc:  # googlemaps raises various ApiError subclasses
        log.error("Distance Matrix API request failed: %s", exc)
        sys.exit(1)

    if response.get("status") != "OK":
        log.error("Distance Matrix API returned top-level status %r: %s", response.get("status"), response)
        sys.exit(1)

    rows = response.get("rows", [])
    element = rows[0]["elements"][0] if rows and rows[0].get("elements") else None
    if element is None or element.get("status") != "OK":
        log.error(
            "Distance Matrix API returned no usable element (status=%r): %s",
            element.get("status") if element else None,
            response,
        )
        sys.exit(1)

    duration_sec = element["duration"]["value"]
    duration_in_traffic_sec = element.get("duration_in_traffic", {}).get("value")
    distance_m = element["distance"]["value"]

    reading = {
        "timestamp_utc": datetime.now(timezone.utc),
        "direction": direction,
        "duration_sec": duration_sec,
        "duration_in_traffic_sec": duration_in_traffic_sec,
        "distance_m": distance_m,
        "origin_coords": origin,
        "destination_coords": destination,
    }

    try:
        write_reading(reading)
    except Exception as exc:
        log.error("Failed to write reading to DB: %s", exc)
        sys.exit(1)

    log.info(
        "Recorded %s reading: duration=%ss duration_in_traffic=%ss distance=%sm",
        direction,
        duration_sec,
        duration_in_traffic_sec,
        distance_m,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--direction",
        required=True,
        choices=list(DIRECTIONS.keys()),
        help="Which commute leg to sample.",
    )
    parser.add_argument(
        "--ignore-window",
        action="store_true",
        help="Collect even if outside COLLECTION_WINDOW_START_HOUR/END_HOUR.",
    )
    args = parser.parse_args()

    if not args.ignore_window and not in_collection_window():
        log.info("Outside collection window, skipping.")
        return

    collect(args.direction)


if __name__ == "__main__":
    main()
