from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from pre6g_experiment.long_trace_detector import PROFILE, analyze_long_trace
from pre6g_experiment.marker_free import load_trace


def make_events(first_cycles: int, first_gap_ns: int, second_cycles: int = 0,
                second_gap_ns: int = 0) -> list[tuple[int, int]]:
    events = []
    timestamp = 0
    for gap, count in ((first_gap_ns, first_cycles), (second_gap_ns, second_cycles)):
        for _ in range(count):
            events.extend(((timestamp, 11), (timestamp + 20_000_000, 12)))
            timestamp += gap
    return events


class LongTraceDetectorTests(unittest.TestCase):
    def test_120_second_stable_trace_is_versioned_without_markers(self):
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "no-nvtx.sqlite"
            connection = sqlite3.connect(path)
            try:
                connection.execute("CREATE TABLE CUPTI_ACTIVITY_KIND_KERNEL (start INTEGER, shortName INTEGER, globalPid INTEGER, contextId INTEGER)")
                connection.execute("CREATE TABLE StringIds (id INTEGER, value TEXT)")
                connection.executemany("INSERT INTO StringIds VALUES (?, ?)", [(11, "kernel_a"), (12, "kernel_b")])
                connection.executemany(
                    "INSERT INTO CUPTI_ACTIVITY_KIND_KERNEL VALUES (?, ?, 1001, 1)",
                    make_events(1250, 100_000_000),
                )
                connection.commit()
            finally:
                connection.close()
            trace = load_trace(path)
            self.assertFalse(trace["nvtx_available"])
            summary, windows = analyze_long_trace(trace["events"], trace["names"])
            self.assertEqual(summary["detector_profile"], PROFILE)
            self.assertEqual(summary["base_window_detector_profile"], "yolo-v1")
            self.assertEqual(summary["status"], "stable_observed")
            self.assertGreaterEqual(len(windows), 6)
            self.assertAlmostEqual(summary["recommended_period_ms"], 100, delta=1)
            self.assertFalse(summary["used_as_runtime_model_input"])

    def test_large_drift_is_not_extrapolated(self):
        events = make_events(650, 100_000_000, 500, 150_000_000)
        summary, _ = analyze_long_trace(events, {11: "kernel_a", 12: "kernel_b"})
        self.assertEqual(summary["status"], "unreliable_for_extrapolation")
        self.assertIsNone(summary["recommended_period_ms"])
        self.assertIn("observed_period_drift", summary["reasons"])


if __name__ == "__main__":
    unittest.main()
