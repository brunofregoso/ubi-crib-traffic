-- Reference schema for eta_readings. This is documentation of the shape
-- db_client.py creates via SQLAlchemy metadata (create_all) — it is not
-- run directly by the application. Kept here so the table layout is
-- reviewable without reading ORM code, and as a starting point if the
-- project later moves to an explicit migration tool.

CREATE TABLE IF NOT EXISTS eta_readings (
    id                          SERIAL PRIMARY KEY,
    timestamp_utc               TIMESTAMP NOT NULL,
    day_of_week                 TEXT NOT NULL,       -- derived from timestamp_utc + LOCAL_TIMEZONE
    hour                        INTEGER NOT NULL,     -- 0-23, local time
    minute                      INTEGER NOT NULL,     -- 0-59, local time
    direction                   TEXT NOT NULL,        -- 'home_to_work' | 'work_to_home'
    duration_sec                INTEGER NOT NULL,     -- no-traffic baseline duration
    duration_in_traffic_sec     INTEGER,              -- live traffic-aware duration, nullable
    distance_m                  INTEGER NOT NULL,
    origin_coords                TEXT NOT NULL,        -- "lat,lng"
    destination_coords           TEXT NOT NULL         -- "lat,lng"
);

CREATE INDEX IF NOT EXISTS idx_eta_readings_direction_ts
    ON eta_readings (direction, timestamp_utc);
