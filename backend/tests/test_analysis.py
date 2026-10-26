"""Integration checks for the saved January data and analysis pipeline."""
from __future__ import annotations

import csv
import json
import unittest
from collections import Counter
from datetime import time
from pathlib import Path

from backend import app as backend_app

ROOT = Path(__file__).resolve().parents[2]
DATA_FILE = ROOT / "backend" / "data" / "january_2026.json"


class AnalysisPipelineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.payload = json.loads(DATA_FILE.read_text(encoding="utf-8"))

    def test_actual_and_forecast_target_cadence(self) -> None:
        actual_starts = sorted(
            {row["startTime"] for row in self.payload["actuals"]}
        )
        forecast_starts = sorted(
            {row["startTime"] for row in self.payload["forecasts"]}
        )
        self.assertEqual(len(actual_starts), 1488)
        self.assertEqual(len(forecast_starts), 744)
        self.assertTrue(all(value[14:16] in {"00", "30"} for value in actual_starts))
        self.assertTrue(all(value[14:16] == "00" for value in forecast_starts))

    def test_every_delivery_day_has_24_hourly_matches(self) -> None:
        result = backend_app.analyze()
        daily_counts = Counter(point["date"] for point in result["points"])
        self.assertEqual(result["count"], 744)
        self.assertEqual(len(daily_counts), 31)
        self.assertEqual(set(daily_counts.values()), {24})
        self.assertEqual(min(daily_counts), "2026-01-02")
        self.assertEqual(max(daily_counts), "2026-02-01")

    def test_cutoff_selects_a_forecast_published_by_that_time(self) -> None:
        result = backend_app.analyze(time(11, 0), "2026-01-03")
        self.assertEqual(result["count"], 24)
        for point in result["points"]:
            self.assertLessEqual(point["publishTime"], "2026-01-02T11:00:00Z")

        earlier = backend_app.analyze(time(8, 0), "2026-01-03")
        self.assertEqual(earlier["count"], 24)
        for point in earlier["points"]:
            self.assertLessEqual(point["publishTime"], "2026-01-02T08:00:00Z")

    def test_daily_metrics_are_calculated_from_returned_points(self) -> None:
        result = backend_app.analyze(time(11, 0), "2026-01-05")
        errors = [point["forecast"] - point["actual"] for point in result["points"]]
        count = len(errors)
        expected_mae = sum(abs(error) for error in errors) / count
        expected_rmse = (sum(error**2 for error in errors) / count) ** 0.5
        expected_bias = sum(errors) / count
        expected_wape = (
            sum(abs(error) for error in errors)
            / sum(point["actual"] for point in result["points"])
            * 100
        )
        self.assertEqual(result["count"], 24)
        self.assertAlmostEqual(result["metrics"]["maeMw"], expected_mae, places=1)
        self.assertAlmostEqual(result["metrics"]["rmseMw"], expected_rmse, places=1)
        self.assertAlmostEqual(result["metrics"]["biasMw"], expected_bias, places=1)
        self.assertAlmostEqual(result["metrics"]["wapePct"], expected_wape, places=2)

    def test_comparison_csv_matches_default_api_analysis(self) -> None:
        csv_file = (
            ROOT
            / "backend"
            / "data"
            / "day_ahead_comparison_jan_decisions.csv"
        )
        with csv_file.open(encoding="utf-8-sig", newline="") as stream:
            rows = list(csv.DictReader(stream))

        api_points = backend_app.analyze()["points"]
        self.assertEqual(len(rows), len(api_points))
        for csv_row, point in zip(rows, api_points, strict=True):
            self.assertEqual(csv_row["deliveryStartUtc"], point["startTime"])
            self.assertAlmostEqual(
                float(csv_row["forecast_mw"]), point["forecast"], places=2
            )
            self.assertAlmostEqual(
                float(csv_row["actual_mw"]), point["actual"], places=2
            )
            self.assertAlmostEqual(
                float(csv_row["error_mw"]), point["error"], places=2
            )


if __name__ == "__main__":
    unittest.main()
