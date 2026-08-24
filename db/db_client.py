"""
Single interface for all reads and writes against eta_readings.

Nothing else in the codebase should open a DB connection directly — the
collector only calls write_reading(), the regression engine only calls
get_readings(). The backend (Postgres, SQLite, ...) is fully hidden
behind DATABASE_URL and SQLAlchemy Core.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from sqlalchemy import (
    Column,
    DateTime,
    Integer,
    MetaData,
    String,
    Table,
    create_engine,
    insert,
    select,
)

load_dotenv()

DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///./eta.db")
LOCAL_TIMEZONE = os.environ.get("LOCAL_TIMEZONE", "UTC")

_engine = create_engine(DATABASE_URL, future=True)
_metadata = MetaData()

eta_readings = Table(
    "eta_readings",
    _metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("timestamp_utc", DateTime, nullable=False),
    Column("day_of_week", String, nullable=False),
    Column("hour", Integer, nullable=False),
    Column("minute", Integer, nullable=False),
    Column("direction", String, nullable=False),
    Column("duration_sec", Integer, nullable=False),
    Column("duration_in_traffic_sec", Integer, nullable=True),
    Column("distance_m", Integer, nullable=False),
    Column("origin_coords", String, nullable=False),
    Column("destination_coords", String, nullable=False),
)


def init_db() -> None:
    """Create the eta_readings table if it doesn't exist yet."""
    _metadata.create_all(_engine)


def write_reading(reading: dict) -> None:
    """
    Persist one reading.

    Expected keys: timestamp_utc (tz-aware UTC datetime), direction,
    duration_sec, duration_in_traffic_sec (optional), distance_m,
    origin_coords, destination_coords.

    day_of_week/hour/minute are derived here from timestamp_utc,
    converted to LOCAL_TIMEZONE, so callers never compute them.
    """
    ts_utc: datetime = reading["timestamp_utc"]
    if ts_utc.tzinfo is None:
        ts_utc = ts_utc.replace(tzinfo=timezone.utc)

    local_ts = ts_utc.astimezone(ZoneInfo(LOCAL_TIMEZONE))

    row = {
        "timestamp_utc": ts_utc.astimezone(timezone.utc).replace(tzinfo=None),
        "day_of_week": local_ts.strftime("%A"),
        "hour": local_ts.hour,
        "minute": local_ts.minute,
        "direction": reading["direction"],
        "duration_sec": reading["duration_sec"],
        "duration_in_traffic_sec": reading.get("duration_in_traffic_sec"),
        "distance_m": reading["distance_m"],
        "origin_coords": reading["origin_coords"],
        "destination_coords": reading["destination_coords"],
    }

    init_db()
    with _engine.begin() as conn:
        conn.execute(insert(eta_readings), row)


def get_readings(
    direction: str | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
) -> list[dict]:
    """
    Read back readings, optionally filtered by direction and/or a
    timestamp_utc range [start, end]. Returns plain dicts, newest last.
    """
    init_db()
    query = select(eta_readings)
    if direction is not None:
        query = query.where(eta_readings.c.direction == direction)
    if start is not None:
        query = query.where(eta_readings.c.timestamp_utc >= start)
    if end is not None:
        query = query.where(eta_readings.c.timestamp_utc <= end)
    query = query.order_by(eta_readings.c.timestamp_utc.asc())

    with _engine.connect() as conn:
        result = conn.execute(query)
        return [dict(row._mapping) for row in result]
