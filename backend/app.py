from __future__ import annotations

import json
import os
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

ROOT = Path(__file__).resolve().parent.parent
DATA_PATH = Path(os.getenv("WIND_DATA_PATH", ROOT / "backend/data/january_2026.json"))
LONDON = ZoneInfo("Europe/London")

app = FastAPI(title="Wind Desk API", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=os.getenv("CORS_ORIGINS", "*").split(","),
    allow_methods=["GET"],
    allow_headers=["*"],
)


def read_data() -> dict:
    """Load the checked-in Elexon snapshot used by the API."""
    try:
        return json.loads(DATA_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise HTTPException(
            status_code=503,
            detail="Data snapshot missing. Run python backend/fetch_data.py",
        ) from exc


def analyze(cutoff: time = time(11, 0), selected_day: str | None = None) -> dict:
    """Compare hourly WINDFOR forecasts with hourly means of FUELHH actuals."""
    data = read_data()
    actuals = {row["startTime"]: float(row["generation"]) for row in data["actuals"]}

    forecast_groups: dict[str, list[dict]] = {}
    for row in data["forecasts"]:
        forecast_groups.setdefault(row["startTime"], []).append(row)

    half_hour_points = []
    for start, actual_mw in actuals.items():
        target = datetime.fromisoformat(start.replace("Z", "+00:00"))
        hour = target.replace(minute=0, second=0, microsecond=0)
        hour_key = hour.isoformat().replace("+00:00", "Z")
        forecast_rows = forecast_groups.get(hour_key, [])
        if not forecast_rows:
            continue

        delivery_local = target.astimezone(LONDON)
        decision_local = datetime.combine(
            delivery_local.date() - timedelta(days=1),
            cutoff,
            LONDON,
        )
        decision_utc = decision_local.astimezone(timezone.utc)
        eligible = [
            row
            for row in forecast_rows
            if datetime.fromisoformat(
                row["publishTime"].replace("Z", "+00:00")
            )
            <= decision_utc
        ]
        if not eligible:
            continue

        chosen = max(eligible, key=lambda row: row["publishTime"])
        half_hour_points.append(
            {
                "startTime": start,
                "hour": hour_key,
                "localTime": hour.astimezone(LONDON).isoformat(),
                "date": delivery_local.date().isoformat(),
                "forecast": float(chosen["generation"]),
                "actual": actual_mw,
                "publishTime": chosen["publishTime"],
            }
        )

    # Average each pair of FUELHH half-hours to match the hourly WINDFOR target.
    grouped: dict[str, list[dict]] = {}
    for point in half_hour_points:
        grouped.setdefault(point["hour"], []).append(point)

    hourly_points = []
    for hour_key, pair in sorted(grouped.items()):
        if len(pair) != 2:
            continue

        actual = sum(point["actual"] for point in pair) / 2
        forecast = sum(point["forecast"] for point in pair) / 2
        local_time = datetime.fromisoformat(pair[0]["localTime"])
        error = forecast - actual
        hourly_points.append(
            {
                "startTime": hour_key,
                "localTime": local_time.isoformat(),
                "date": pair[0]["date"],
                "forecast": round(forecast, 2),
                "actual": round(actual, 2),
                "error": round(error, 2),
                "absError": round(abs(error), 2),
                "publishTime": max(point["publishTime"] for point in pair),
            }
        )

    if selected_day:
        hourly_points = [
            point for point in hourly_points if point["date"] == selected_day
        ]

    errors = [point["error"] for point in hourly_points]
    count = len(hourly_points)
    if count:
        mae = sum(abs(error) for error in errors) / count
        rmse = (sum(error**2 for error in errors) / count) ** 0.5
        bias = sum(errors) / count
        total_actual = sum(point["actual"] for point in hourly_points)
        wape = (
            sum(abs(error) for error in errors) / total_actual * 100
            if total_actual
            else None
        )
    else:
        mae = rmse = bias = wape = None

    metrics = {
        "maeMw": round(mae, 1) if mae is not None else None,
        "rmseMw": round(rmse, 1) if rmse is not None else None,
        "biasMw": round(bias, 1) if bias is not None else None,
        "wapePct": round(wape, 2) if wape is not None else None,
    }
    return {
        "period": data.get("period", "Jan 2026 decision dates"),
        "timezone": "Europe/London",
        "cutoff": cutoff.strftime("%H:%M"),
        "resolution": "hourly",
        "count": count,
        "metrics": metrics,
        "points": hourly_points,
        "source": data.get("source", {}),
        "refreshedAt": data.get("fetchedAt"),
    }


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok", "dataAvailable": DATA_PATH.exists()}


@app.get("/api/analysis")
def get_analysis(
    cutoff: str = Query("11:00", pattern=r"^([01]\d|2[0-3]):[0-5]\d$"),
    day: str | None = None,
) -> dict:
    hour, minute = map(int, cutoff.split(":"))
    try:
        return analyze(time(hour, minute), day)
    except (KeyError, TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Could not analyze Elexon data: {exc}",
        ) from exc


@app.get("/api/days")
def days() -> list[str]:
    result = analyze()
    return sorted({point["date"] for point in result["points"]})


if (ROOT / "frontend/dist").exists():
    DIST = (ROOT / "frontend/dist").resolve()
    app.mount("/assets", StaticFiles(directory=DIST / "assets"), name="assets")

    @app.get("/{path:path}")
    def frontend(path: str):
        target = (DIST / path).resolve()
        if DIST not in target.parents or not target.is_file():
            target = DIST / "index.html"
        return FileResponse(target)
