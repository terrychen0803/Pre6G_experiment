import csv
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from pre6g_experiment.runtime_features import (
    extract_trace_features,
    pre_run_window,
    read_timestamps,
    summarize_telemetry_window,
)


class RuntimeFeatureTests(unittest.TestCase):
    def test_pre_run_window_uses_absolute_application_timestamp(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "timestamps.json"
            path.write_text(
                json.dumps(
                    {
                        "application_start_ns": 10_000_000_000,
                        "profile_start_ns": 9_500_000_000,
                    }
                ),
                encoding="utf-8",
            )

            timestamps = read_timestamps(path)
            anchor, lower, upper = pre_run_window(
                timestamps,
                seconds=5.0,
            )

            self.assertEqual(anchor, "application_start_ns")
            self.assertEqual(lower, 5_000_000_000)
            self.assertEqual(upper, 10_000_000_000)

    def test_telemetry_summary_does_not_need_iterations_csv(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            timestamps_path = root / "timestamps.json"
            telemetry_path = root / "telemetry-aligned.csv"

            timestamps_path.write_text(
                json.dumps(
                    {"application_start_ns": 10_000_000_000}
                ),
                encoding="utf-8",
            )

            with telemetry_path.open(
                "w",
                encoding="utf-8",
                newline="",
            ) as handle:
                writer = csv.DictWriter(
                    handle,
                    fieldnames=[
                        "timestamp_ns",
                        "GPU Util%",
                        "CPU User%",
                    ],
                )
                writer.writeheader()
                writer.writerows(
                    [
                        {
                            "timestamp_ns": 4_000_000_000,
                            "GPU Util%": 1,
                            "CPU User%": 2,
                        },
                        {
                            "timestamp_ns": 6_000_000_000,
                            "GPU Util%": 10,
                            "CPU User%": 20,
                        },
                        {
                            "timestamp_ns": 8_000_000_000,
                            "GPU Util%": 30,
                            "CPU User%": 40,
                        },
                        {
                            "timestamp_ns": 10_500_000_000,
                            "GPU Util%": 99,
                            "CPU User%": 99,
                        },
                    ]
                )

            summary = summarize_telemetry_window(
                telemetry_path,
                timestamps_path,
                seconds=5.0,
                columns=("GPU Util%", "CPU User%"),
            )

            self.assertEqual(summary["sample_count"], 2)
            self.assertEqual(
                summary["features"]["pre_gpu_utilpct"],
                20.0,
            )
            self.assertEqual(
                summary["features"]["pre_cpu_userpct"],
                30.0,
            )

    def test_trace_features_are_filtered_by_target_process_context(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            sqlite_path = root / "trace.sqlite"

            connection = sqlite3.connect(sqlite_path)
            try:
                connection.execute(
                    """
                    CREATE TABLE CUPTI_ACTIVITY_KIND_KERNEL (
                        start INTEGER,
                        end INTEGER,
                        shortName INTEGER,
                        globalPid INTEGER,
                        contextId INTEGER
                    )
                    """
                )
                for index in range(10):
                    start = index * 100_000_000
                    connection.execute(
                        """
                        INSERT INTO CUPTI_ACTIVITY_KIND_KERNEL
                        VALUES (?, ?, ?, ?, ?)
                        """,
                        (
                            start,
                            start + 1_000_000,
                            11,
                            123,
                            1,
                        ),
                    )
                    connection.execute(
                        """
                        INSERT INTO CUPTI_ACTIVITY_KIND_KERNEL
                        VALUES (?, ?, ?, ?, ?)
                        """,
                        (
                            start,
                            start + 50_000_000,
                            99,
                            456,
                            2,
                        ),
                    )
                connection.commit()
            finally:
                connection.close()

            detection = {
                "capture_start_ns": 0,
                "capture_end_ns": 900_000_000,
                "horizon_seconds": 0.9,
                "detected_period_ms": 100.0,
                "anchor_robust_cv": 0.01,
            }

            features = extract_trace_features(
                sqlite_path,
                detection,
                global_pid=123,
                context_id=1,
            )

            self.assertEqual(features["kernel_event_count"], 10)
            self.assertEqual(features["unique_kernel_count"], 1)
            self.assertAlmostEqual(
                features["median_kernel_duration_us"],
                1000.0,
            )


if __name__ == "__main__":
    unittest.main()
