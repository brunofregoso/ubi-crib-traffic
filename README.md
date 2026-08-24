# Commute ETA Rush-Hour Regression System

Quantifies how time-of-day affects commute time between a fixed home and
workspace location: a collector polls the Google Maps Distance Matrix API
on a schedule and writes readings to a database, and a separate regression
engine fits a polynomial curve (ETA as a function of time-of-day) to
answer questions like "how much worse is 8am than 11am" and "when is the
actual worst time to leave."

## Architecture

Three independent, loosely coupled pieces:

- **`collector/`** — a stateless, cron-driven script. Calls the Distance
  Matrix API for one direction, then hands a reading to the DB layer. It
  never queries the DB for reads.
- **`db/`** — the only module that talks to the database. Everything else
  goes through `db/db_client.py`'s `write_reading()` / `get_readings()`.
  Backed by SQLAlchemy so the actual backend (Postgres, SQLite, ...) is an
  implementation detail behind `DATABASE_URL`.
- **`regression/`** — an on-demand analysis script (not part of the cron
  job). Reads via the DB layer, fits a polynomial regression per segment,
  and emits a plot + JSON summary.

```
Collector (cron) --writes--> DB layer (.env DATABASE_URL) --reads--> Regression engine
```

## Why polynomial, not straight-line, regression

Commute traffic is bimodal — a morning peak, a midday trough, an evening
peak — so a plain linear fit either flattens both peaks into a near-flat
line or gets pulled in whatever direction the sampled hours happen to
lean. Expanding `minutes_since_midnight` into polynomial terms via
`PolynomialFeatures` and fitting ordinary `LinearRegression` on the
expanded features keeps the model linear-in-its-coefficients (fast, no
convergence risk) while letting the fitted curve in `x` bend enough to
trace both rush-hour humps.

- **Feature:** `minutes_since_midnight` (0–1439), derived from the
  reading's local timestamp.
- **Target:** `eta_minutes` — `duration_in_traffic_sec` when available,
  else `duration_sec`.
- **Degree:** `POLY_DEGREE` in `.env`, default 6. Push it up if the fit
  still looks flat through both peaks; pull it down if the curve starts
  wiggling to chase noise between points.
- **Sanity check:** every plot and the JSON summary include the binned
  hourly mean/median alongside the fitted curve — if the polynomial curve
  disagrees wildly with those bins, the degree or segmentation needs
  adjusting.
- **Segmentation:** a separate model is fit per `direction` (commute
  isn't symmetric) × weekday/weekend. Nothing is pooled across those.

## Setup

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # fill in GOOGLE_MAPS_API_KEY, HOME_COORDS, WORK_COORDS, DATABASE_URL
```

`DATABASE_URL` defaults to a local SQLite file (`sqlite:///./eta.db`),
which is enough for a single-machine personal setup. Point it at Postgres
(`postgresql://user:pass@host:5432/dbname`) for anything long-running —
the DB layer's interface doesn't change either way.

## Running the collector

One-off, for one direction:

```bash
python collector/collect_eta.py --direction home_to_work
python collector/collect_eta.py --direction work_to_home
```

It checks `COLLECTION_WINDOW_START_HOUR`/`COLLECTION_WINDOW_END_HOUR`
(local time) and exits without calling the API if outside that window;
pass `--ignore-window` to bypass that for manual testing. On any API or
DB error it exits non-zero and logs to stderr, so failures show up in
cron/container logs instead of silently producing gaps.

### Deploying on a schedule

Two options, pick based on where this runs — the script itself is
environment-agnostic and only reads `.env`:

**Host cron / systemd timer**, e.g. crontab entries running every
`COLLECTION_INTERVAL_MINUTES`:

```cron
*/15 * * * * cd /path/to/repo && .venv/bin/python collector/collect_eta.py --direction home_to_work >> /var/log/eta-collector.log 2>&1
*/15 * * * * cd /path/to/repo && .venv/bin/python collector/collect_eta.py --direction work_to_home >> /var/log/eta-collector.log 2>&1
```

**Containerized cron** (`collector/Dockerfile`):

```bash
docker build -f collector/Dockerfile -t eta-collector .
docker run -d --env-file .env --name eta-collector eta-collector
```

The container's `entrypoint.sh` builds a crontab from
`COLLECTION_INTERVAL_MINUTES` at startup, runs both directions every
cycle, and streams cron job output to `docker logs`. The window
restriction is enforced inside `collect_eta.py`, not the cron schedule,
so the schedule itself stays a simple every-N-minutes rule.

## Running the regression engine

On demand, any time after readings exist:

```bash
python regression/train_and_evaluate.py               # uses POLY_DEGREE from .env
python regression/train_and_evaluate.py --degree 8     # override for one run
```

Writes to `regression/output/` (gitignored):

- `<direction>__<weekday|weekend>.png` — scatter of readings, the fitted
  polynomial curve, and hourly-mean markers, per segment.
- `summary.json` — per segment: R², worst predicted ETA + time, the
  hourly mean/median sanity table, and reading counts. Segments without
  enough readings to fit (`degree + 1` minimum) are marked `skipped`
  rather than silently dropped.

Model persistence: the engine refits from the DB on every run rather than
serializing fitted models. For this dataset size that's simpler and
avoids stale-model bugs — there's no meaningful cost to refitting on
demand.

## Data schema

See `db/schema.sql` for the reference `eta_readings` layout (the table
`db/db_client.py` actually creates via SQLAlchemy metadata).

## Repo layout

```
/collector/
    collect_eta.py     # single-run script, called by cron
    entrypoint.sh       # builds crontab from .env, starts cron in foreground
    Dockerfile
/db/
    schema.sql           # reference schema
    db_client.py          # write_reading / get_readings interface
/regression/
    train_and_evaluate.py # loads via db_client, fits models, outputs plot+JSON
.env.example
```
