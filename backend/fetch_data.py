"""Fetch January 2026 wind actuals and WINDFOR publication history from Elexon."""
from __future__ import annotations

import asyncio
import csv
import json
import math
import sys
import time as time_module
from datetime import datetime, timedelta, timezone
from pathlib import Path
import urllib.parse
import urllib.request
from urllib.error import HTTPError, URLError
from zoneinfo import ZoneInfo

BASE = "https://data.elexon.co.uk/bmrs/api/v1"
OUT = Path(__file__).resolve().parent / "data/january_2026.json"
DATA_DIR = OUT.parent
START = datetime(2026, 1, 2, tzinfo=timezone.utc)
END = datetime(2026, 2, 2, tzinfo=timezone.utc)


def request_json(url: str, params: dict) -> list[dict]:
    """Request Elexon JSON, retrying temporary network and service errors."""
    query = urllib.parse.urlencode(params)
    request = urllib.request.Request(
        f"{url}?{query}",
        headers={"User-Agent": "WindDesk/1.0"},
    )
    for attempt in range(4):
        try:
            with urllib.request.urlopen(request, timeout=40) as response:
                payload = json.load(response)
            return payload.get("data", payload) if isinstance(payload, dict) else payload
        except HTTPError as exc:
            if exc.code not in {429, 500, 502, 503, 504} or attempt == 3:
                raise
            retry_after = exc.headers.get("Retry-After")
            delay = float(retry_after) if retry_after and retry_after.isdigit() else 2**attempt
        except (TimeoutError, URLError):
            if attempt == 3:
                raise
            delay = 2**attempt
        time_module.sleep(delay)
    raise RuntimeError("Elexon request failed after retries")


async def main() -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    # The January decision period covers deliveries Jan 2-Feb 1. Settlement-date
    # history has a bounded response size; request it in weekly slices.
    actual_rows = []
    first_date = START.date()
    last_date = (END - timedelta(days=1)).date()
    current_date = first_date
    while current_date <= last_date:
        end_date = min(current_date + timedelta(days=6), last_date)
        chunk = await asyncio.to_thread(
            request_json,
            f"{BASE}/datasets/FUELHH/stream",
            {
                "settlementDateFrom": current_date.isoformat(),
                "settlementDateTo": end_date.isoformat(),
            },
        )
        actual_rows.extend(chunk)
        current_date = end_date + timedelta(days=1)
    actuals = [
        {"startTime": row["startTime"], "generation": row["generation"]}
        for row in actual_rows
        if row.get("fuelType") == "WIND" and row.get("generation") is not None
    ]
    actuals.sort(key=lambda row: row["startTime"])

    # Use the exact WINDFOR stream linked in the assignment. Query by publish
    # time in weekly windows so superseded forecast versions remain available.
    # The January 2026 stream returns hourly target startTimes; this cadence is
    # validated below rather than assuming half-hourly forecasts from the brief.
    publish_start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    publish_end = datetime(2026, 2, 1, tzinfo=timezone.utc)
    forecasts_by_version: dict[tuple[str, str], dict] = {}
    window_start = publish_start
    while window_start < publish_end:
        window_end = min(window_start + timedelta(days=7), publish_end)
        rows = await asyncio.to_thread(
            request_json,
            f"{BASE}/datasets/WINDFOR/stream",
            {
                "publishDateTimeFrom": window_start.isoformat().replace("+00:00", "Z"),
                "publishDateTimeTo": window_end.isoformat().replace("+00:00", "Z"),
            },
        )
        for row in rows:
            if (
                not row.get("startTime")
                or not row.get("publishTime")
                or row.get("generation") is None
            ):
                continue
            target = datetime.fromisoformat(row["startTime"].replace("Z", "+00:00"))
            if START <= target < END:
                version = {
                    "startTime": row["startTime"],
                    "publishTime": row["publishTime"],
                    "generation": row["generation"],
                }
                forecasts_by_version[(version["startTime"], version["publishTime"])] = version
        window_start = window_end
    forecasts = sorted(
        forecasts_by_version.values(),
        key=lambda row: (row["startTime"], row["publishTime"]),
    )
    expected_actual_count = int((END - START).total_seconds() / 1800)
    expected_actual_targets = {
        (START + timedelta(minutes=30 * offset)).isoformat().replace("+00:00", "Z")
        for offset in range(expected_actual_count)
    }
    expected_forecast_count = int((END - START).total_seconds() / 3600)
    expected_forecast_targets = {
        (START + timedelta(hours=offset)).isoformat().replace("+00:00", "Z")
        for offset in range(expected_forecast_count)
    }
    actual_targets = {row["startTime"] for row in actuals}
    forecast_targets = {row["startTime"] for row in forecasts}
    if len(actuals) != expected_actual_count or len(actual_targets) != expected_actual_count:
        raise RuntimeError(
            f"Expected {expected_actual_count} unique WIND actual periods; "
            f"got {len(actuals)} records"
        )
    missing_actuals = expected_actual_targets - actual_targets
    unexpected_actuals = actual_targets - expected_actual_targets
    if missing_actuals or unexpected_actuals:
        raise RuntimeError(
            "FUELHH actual timestamps do not match the expected delivery window "
            f"(missing={len(missing_actuals)}, unexpected={len(unexpected_actuals)})"
        )
    missing_targets = expected_forecast_targets - forecast_targets
    if missing_targets:
        raise RuntimeError(
            f"WINDFOR history is missing {len(missing_targets)} expected "
            "hourly delivery targets"
        )
    unexpected_targets = forecast_targets - expected_forecast_targets
    if unexpected_targets:
        raise RuntimeError(
            f"WINDFOR history contains {len(unexpected_targets)} "
            "unexpected delivery targets"
        )
    for row in actuals + forecasts:
        if not math.isfinite(float(row["generation"])):
            raise RuntimeError("Elexon returned a non-finite generation value")
    payload = {
        "period": "January 2026 purchase decisions",
        "fetchedAt": datetime.now(timezone.utc).isoformat(),
        "source": {
            "actuals": f"{BASE}/datasets/FUELHH/stream",
            "forecasts": f"{BASE}/datasets/WINDFOR/stream",
            "actualRecords": len(actuals),
            "forecastRecords": len(forecasts),
        },
        "actuals": actuals,
        "forecasts": forecasts,
    }
    temporary = OUT.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
    temporary.replace(OUT)
    export_csvs(payload)
    print(
        f"Saved {len(actuals)} WIND actual records and "
        f"{len(forecasts)} forecast versions to {OUT}"
    )


def export_csvs(payload: dict) -> None:
    """Write source rows and the default-cutoff matched analysis as readable CSVs."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    def write(name: str, columns: list[str], rows: list[dict]) -> None:
        target = DATA_DIR / name
        temporary = target.with_suffix(target.suffix + ".tmp")
        with temporary.open("w", newline="", encoding="utf-8-sig") as stream:
            writer = csv.DictWriter(stream, fieldnames=columns)
            writer.writeheader()
            writer.writerows(rows)
        temporary.replace(target)

    write(
        "wind_actuals_jan_decisions.csv",
        ["startTime", "generation_mw"],
        [
            {"startTime": row["startTime"], "generation_mw": row["generation"]}
            for row in payload["actuals"]
        ],
    )
    write(
        "wind_forecast_history_jan_decisions.csv",
        ["startTime", "publishTime", "generation_mw"],
        [
            {
                "startTime": row["startTime"],
                "publishTime": row["publishTime"],
                "generation_mw": row["generation"],
            }
            for row in payload["forecasts"]
        ],
    )

    # Reproduce the application's default 11:00 London day-ahead selection.
    london = ZoneInfo("Europe/London")
    actuals_by_start = {
        row["startTime"]: float(row["generation"])
        for row in payload["actuals"]
    }
    forecasts_by_start: dict[str, list[dict]] = {}
    for row in payload["forecasts"]:
        forecasts_by_start.setdefault(row["startTime"], []).append(row)
    grouped: dict[str, list[dict]] = {}
    for start, actual in actuals_by_start.items():
        target = datetime.fromisoformat(start.replace("Z", "+00:00"))
        hour = target.replace(minute=0, second=0, microsecond=0)
        hour_key = hour.isoformat().replace("+00:00", "Z")
        delivery_local = target.astimezone(london)
        decision = datetime.combine(
            delivery_local.date() - timedelta(days=1),
            datetime.min.time(),
            london,
        ).replace(hour=11)
        decision_utc = decision.astimezone(timezone.utc)
        eligible = [
            row
            for row in forecasts_by_start.get(hour_key, [])
            if datetime.fromisoformat(
                row["publishTime"].replace("Z", "+00:00")
            )
            <= decision_utc
        ]
        if not eligible:
            continue
        chosen = max(eligible, key=lambda row: row["publishTime"])
        grouped.setdefault(hour_key, []).append(
            {
                "actual": actual,
                "forecast": float(chosen["generation"]),
                "publishTime": chosen["publishTime"],
                "deliveryHourLocal": hour.astimezone(london),
                "decisionLocal": decision,
            }
        )
    comparison = []
    for hour_key, pair in sorted(grouped.items()):
        if len(pair) != 2:
            continue
        actual = sum(row["actual"] for row in pair) / 2
        forecast = sum(row["forecast"] for row in pair) / 2
        error = forecast - actual
        comparison.append(
            {
                "deliveryStartUtc": hour_key,
                "deliveryStartLondon": pair[0]["deliveryHourLocal"].isoformat(),
                "decisionCutoffLondon": pair[0]["decisionLocal"].isoformat(),
                "forecastPublishTimeUtc": max(
                    row["publishTime"] for row in pair
                ),
                "forecast_mw": round(forecast, 2),
                "actual_mw": round(actual, 2),
                "error_mw": round(error, 2),
                "absolute_error_mw": round(abs(error), 2),
            }
        )

    write(
        "day_ahead_comparison_jan_decisions.csv",
        [
            "deliveryStartUtc",
            "deliveryStartLondon",
            "decisionCutoffLondon",
            "forecastPublishTimeUtc",
            "forecast_mw",
            "actual_mw",
            "error_mw",
            "absolute_error_mw",
        ],
        comparison,
    )
    print(
        f"Exported {len(payload['actuals'])} actuals, "
        f"{len(payload['forecasts'])} forecast versions, and "
        f"{len(comparison)} matched hourly rows to {DATA_DIR}"
    )


if __name__ == "__main__":
    if "--export-only" in sys.argv:
        export_csvs(json.loads(OUT.read_text(encoding="utf-8")))
    else:
        asyncio.run(main())
