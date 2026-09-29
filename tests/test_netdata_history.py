import importlib.util
import unittest
from pathlib import Path
from unittest.mock import patch


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


    def test_query_window_skips_transient_unavailable_app_chart(self):
        current = {
            "system.cpu": {"dimensions": {"user": {"value": 1}}},
            "system.load": {"dimensions": {"load1": {"value": 1}}},
            "system.ram": {"dimensions": {"used": {"value": 1}}},
            "sensors.temperature_k10temp_tctl_input": {
                "dimensions": {"input": {"value": 50}}
            },
            "app.worker1_cpu_utilization": {
                "dimensions": {"user": {"value": 1}}
            },
            "app.worker2_cpu_utilization": {
                "dimensions": {"user": {"value": 1}}
            },
            "app.worker3_cpu_utilization": {
                "dimensions": {"user": {"value": 1}}
            },
            "app.transient_cpu_utilization": {
                "dimensions": {"user": {"value": 1}}
            },
        }

        def fake_query(_base, chart, *_args):
            if chart == "app.transient_cpu_utilization":
                raise RuntimeError("chart unavailable for historical window")
            if chart == "system.cpu":
                return {100: {"user": 10.0, "system": 2.0, "iowait": 1.0}}
            if chart == "system.load":
                return {100: {"load1": 1.0, "load5": 2.0, "load15": 3.0}}
            if chart == "system.ram":
                return {100: {"used": 4096.0, "free": 8192.0}}
            if chart.startswith("sensors.temperature_"):
                return {100: {"input": 55.0}}
            if chart == "app.worker1_cpu_utilization":
                return {100: {"user": 20.0, "system": 2.0}}
            if chart == "app.worker2_cpu_utilization":
                return {100: {"user": 10.0, "system": 1.0}}
            if chart == "app.worker3_cpu_utilization":
                return {100: {"user": 5.0, "system": 0.5}}
            raise AssertionError(chart)

        with patch.object(MODULE, "_fetch_json", return_value=current), patch.object(
            MODULE, "_query_chart", side_effect=fake_query
        ):
            rows, metadata = MODULE.query_window(
                base_url="http://netdata/host/node",
                node="node",
                after_ns=99_000_000_000,
                before_ns=101_000_000_000,
                timeout_s=1.0,
                retries=0,
            )

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["Top1 CPU%"], 22.0)
        self.assertEqual(rows[0]["Top2 CPU%"], 11.0)
        self.assertEqual(rows[0]["Top3 CPU%"], 5.5)
        self.assertEqual(metadata["app_cpu_chart_count_discovered"], 4)
        self.assertEqual(metadata["app_cpu_chart_count_queried"], 3)
        self.assertEqual(metadata["app_cpu_chart_count_unavailable"], 1)

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
