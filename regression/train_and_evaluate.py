#!/usr/bin/env python3
"""
On-demand analysis step. Not part of the cron job.

Pulls readings via the DB layer (never queries the DB directly), builds
minutes_since_midnight / eta_minutes, segments by direction and
weekday/weekend, and fits a polynomial regression per segment so the
curve can trace a morning peak, a midday trough, and an evening peak
instead of being forced into a single straight-line slope.

For each segment, emits:
  - a PNG plot (scatter of readings, fitted curve, binned hourly means)
  - an entry in a single JSON summary: R^2, worst predicted ETA + time,
    and the hourly-mean/median sanity table

Usage:
    python train_and_evaluate.py [--degree 6] [--outdir output]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from dotenv import load_dotenv
from sklearn.linear_model import LinearRegression
from sklearn.metrics import r2_score
from sklearn.preprocessing import PolynomialFeatures

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from db.db_client import get_readings  # noqa: E402

load_dotenv()

WEEKEND_DAYS = {"Saturday", "Sunday"}


def minutes_to_clock(minutes: float) -> str:
    minutes = int(round(minutes)) % 1440
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def build_dataframe() -> pd.DataFrame:
    readings = get_readings()
    if not readings:
        return pd.DataFrame()

    df = pd.DataFrame(readings)
    df["eta_minutes"] = (
        df["duration_in_traffic_sec"].fillna(df["duration_sec"]) / 60.0
    )
    df["minutes_since_midnight"] = df["hour"] * 60 + df["minute"]
    df["is_weekend"] = df["day_of_week"].isin(WEEKEND_DAYS)
    return df


def hourly_sanity_table(segment: pd.DataFrame) -> list[dict]:
    grouped = segment.groupby("hour")["eta_minutes"].agg(["mean", "median", "count"])
    grouped = grouped.reindex(range(24))
    return [
        {
            "hour": int(hour),
            "mean_eta_minutes": None if pd.isna(row["mean"]) else round(float(row["mean"]), 2),
            "median_eta_minutes": None if pd.isna(row["median"]) else round(float(row["median"]), 2),
            "count": 0 if pd.isna(row["count"]) else int(row["count"]),
        }
        for hour, row in grouped.iterrows()
    ]


def fit_segment(segment: pd.DataFrame, degree: int, grid_start_min: int, grid_end_min: int) -> dict | None:
    if len(segment) < degree + 1:
        return None

    X = segment[["minutes_since_midnight"]].to_numpy()
    y = segment["eta_minutes"].to_numpy()

    poly = PolynomialFeatures(degree=degree)
    X_poly = poly.fit_transform(X)

    model = LinearRegression()
    model.fit(X_poly, y)

    y_pred = model.predict(X_poly)
    r2 = r2_score(y, y_pred)

    # Only evaluate the fitted curve within the range readings actually
    # cover (the collection window). A degree-6+ polynomial has no data
    # constraining it outside that range and swings wildly there — e.g.
    # claiming "worst time to leave is midnight" when nobody ever
    # collected a midnight reading. Restricting the grid keeps both the
    # worst-time search and the plot honest about what was measured.
    grid = np.arange(grid_start_min, grid_end_min + 1).reshape(-1, 1)
    grid_pred = model.predict(poly.transform(grid))
    worst_idx = int(np.argmax(grid_pred))
    worst_minute = grid_start_min + worst_idx

    return {
        "r_squared": round(float(r2), 4),
        "n_readings": int(len(segment)),
        "worst_predicted_time": minutes_to_clock(worst_minute),
        "worst_predicted_eta_minutes": round(float(grid_pred[worst_idx]), 2),
        "curve_minutes": grid.flatten().tolist(),
        "curve_eta_minutes": grid_pred.tolist(),
    }


def plot_segment(segment: pd.DataFrame, fit: dict, title: str, outfile: Path) -> None:
    fig, ax = plt.subplots(figsize=(10, 6))

    ax.scatter(
        segment["minutes_since_midnight"],
        segment["eta_minutes"],
        alpha=0.4,
        s=15,
        label="readings",
    )
    ax.plot(
        fit["curve_minutes"],
        fit["curve_eta_minutes"],
        color="crimson",
        linewidth=2,
        label=f"polynomial fit (R²={fit['r_squared']})",
    )

    hourly = segment.groupby("hour")["eta_minutes"].mean()
    ax.scatter(
        hourly.index * 60 + 30,
        hourly.values,
        color="black",
        marker="x",
        s=60,
        label="hourly mean (sanity)",
    )

    grid_start_min, grid_end_min = fit["curve_minutes"][0], fit["curve_minutes"][-1]
    ax.set_xlim(grid_start_min, grid_end_min)
    ticks = list(range(grid_start_min, grid_end_min + 1, 60))
    ax.set_xticks(ticks)
    ax.set_xticklabels([minutes_to_clock(m) for m in ticks], rotation=45, ha="right")
    ax.set_xlabel("time of day")
    ax.set_ylabel("ETA (minutes)")
    ax.set_title(title)
    ax.legend()
    fig.tight_layout()
    fig.savefig(outfile, dpi=150)
    plt.close(fig)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--degree",
        type=int,
        default=int(os.environ.get("POLY_DEGREE", 6)),
        help="Polynomial degree for the regression (default: POLY_DEGREE from .env, or 6).",
    )
    parser.add_argument(
        "--outdir",
        default=str(Path(__file__).resolve().parent / "output"),
        help="Directory to write plots + summary.json into.",
    )
    args = parser.parse_args()

    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    df = build_dataframe()
    if df.empty:
        print("No readings found in the database yet — nothing to fit.", file=sys.stderr)
        sys.exit(1)

    summary = {
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "poly_degree": args.degree,
        "total_readings": int(len(df)),
        "segments": {},
    }

    for direction, direction_df in df.groupby("direction"):
        for is_weekend, segment in direction_df.groupby("is_weekend"):
            day_type = "weekend" if is_weekend else "weekday"
            segment_key = f"{direction}__{day_type}"

            # Bound the fit's evaluation grid to the range of minutes
            # actually observed in this segment, so the "worst time"
            # search and the plot never extrapolate the polynomial past
            # the data that constrains it.
            grid_start_min = int(segment["minutes_since_midnight"].min())
            grid_end_min = int(segment["minutes_since_midnight"].max())
            fit = fit_segment(segment, args.degree, grid_start_min, grid_end_min)
            hourly_table = hourly_sanity_table(segment)

            if fit is None:
                summary["segments"][segment_key] = {
                    "direction": direction,
                    "day_type": day_type,
                    "n_readings": int(len(segment)),
                    "status": f"skipped: need at least degree+1 ({args.degree + 1}) readings to fit",
                    "hourly_sanity_table": hourly_table,
                }
                print(f"[{segment_key}] skipped, only {len(segment)} readings")
                continue

            plot_path = outdir / f"{segment_key}.png"
            plot_segment(segment, fit, f"{direction} ({day_type})", plot_path)

            summary["segments"][segment_key] = {
                "direction": direction,
                "day_type": day_type,
                "status": "ok",
                "n_readings": fit["n_readings"],
                "r_squared": fit["r_squared"],
                "worst_predicted_time": fit["worst_predicted_time"],
                "worst_predicted_eta_minutes": fit["worst_predicted_eta_minutes"],
                "hourly_sanity_table": hourly_table,
                "plot": str(plot_path),
            }
            print(
                f"[{segment_key}] n={fit['n_readings']} R²={fit['r_squared']} "
                f"worst={fit['worst_predicted_time']} ({fit['worst_predicted_eta_minutes']} min) "
                f"-> {plot_path}"
            )

    summary_path = outdir / "summary.json"
    with open(summary_path, "w") as f:
        json.dump(summary, f, indent=2)
    print(f"\nWrote summary to {summary_path}")


if __name__ == "__main__":
    main()
