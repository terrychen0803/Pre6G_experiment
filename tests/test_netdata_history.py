import importlib.util
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "query_netdata_window.py"
SPEC = importlib.util.spec_from_file_location("query_netdata_window", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class NetdataHistoricalQueryTests(unittest.TestCase):
    def test_parse_objectrows_payload(self):
        payload = {
            "labels": ["time", "user", "system"],
            "data": [
                {"time": 100, "user": 10.5, "system": 2.5},
                {"time": 101, "user": 20.0, "system": 3.0},
            ],
        }
        parsed = MODULE._parse_data_payload(payload)
        self.assertEqual(parsed[100]["user"], 10.5)
        self.assertEqual(parsed[101]["system"], 3.0)

    def test_parse_array_payload(self):
        payload = {
            "labels": ["time", "used", "free"],
            "data": [
                [100, 4096, 8192],
                [101, 5000, 7000],
            ],
        }
        parsed = MODULE._parse_data_payload(payload)
        self.assertEqual(parsed[100]["used"], 4096.0)
        self.assertEqual(parsed[101]["free"], 7000.0)

    def test_top_cpu_requires_three_finite_series(self):
        series = [
            {100: {"user": 10.0, "system": 1.0}},
            {100: {"user": 20.0, "system": 2.0}},
            {100: {"user": 5.0, "system": 0.5}},
        ]
        top = MODULE._top_cpu_at(series, 100)
        self.assertEqual(top, [22.0, 11.0, 5.5])

        missing = MODULE._top_cpu_at(series[:2], 100)
        self.assertEqual(missing, [None, None, None])

    def test_historical_url_uses_absolute_window(self):
        url = MODULE._data_url(
            "http://netdata:19999/host/node-a",
            "system.cpu",
            100,
            120,
        )
        self.assertIn("/api/v1/data?", url)
        self.assertIn("chart=system.cpu", url)
        self.assertIn("after=100", url)
        self.assertIn("before=120", url)
        self.assertIn("objectrows", url)
        self.assertIn("unaligned", url)


if __name__ == "__main__":
    unittest.main()
