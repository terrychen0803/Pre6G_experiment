import unittest

from pre6g_experiment.power_adapter import (
    build_power_smoke_result,
    feature_source_map,
    summarize_predicted_series,
)


class PowerAdapterTests(unittest.TestCase):
    def test_feature_source_map_matches_rtx5090_bundle(self):
        required = [
            "Top2 CPU%",
            "Top1 CPU%",
            "Top3 CPU%",
            "Mem Used(MB)",
            "Mem Free(MB)",
            "CPU User%",
            "GPU Power(W)",
        ]
        sources = feature_source_map(required)
        self.assertEqual(sources["Top2 CPU%"], "netdata")
        self.assertEqual(sources["Top1 CPU%"], "netdata")
        self.assertEqual(sources["Top3 CPU%"], "netdata")
        self.assertEqual(sources["Mem Used(MB)"], "netdata")
        self.assertEqual(sources["Mem Free(MB)"], "netdata")
        self.assertEqual(sources["GPU Power(W)"], "dcgm")

    def test_feature_source_map_matches_uploaded_bundle(self):
        required = [
            "GPU Mem Used(MB)",
            "CPU User%",
            "GPU Power(W)",
            "GPU Temp(°C)",
            "CPU Temp(°C)",
        ]
        self.assertEqual(
            feature_source_map(required),
            {
                "GPU Mem Used(MB)": "dcgm",
                "CPU User%": "netdata",
                "GPU Power(W)": "dcgm",
                "GPU Temp(°C)": "dcgm",
                "CPU Temp(°C)": "netdata",
            },
        )

    def test_feature_source_map_matches_rtx5090_bundle(self):
        required = [
            "Top2 CPU%",
            "Top1 CPU%",
            "Top3 CPU%",
            "Mem Used(MB)",
            "Mem Free(MB)",
            "CPU User%",
            "GPU Power(W)",
        ]
        self.assertEqual(
            feature_source_map(required),
            {
                "Top2 CPU%": "netdata",
                "Top1 CPU%": "netdata",
                "Top3 CPU%": "netdata",
                "Mem Used(MB)": "netdata",
                "Mem Free(MB)": "netdata",
                "CPU User%": "netdata",
                "GPU Power(W)": "dcgm",
            },
        )

    def test_time_weighted_power_summary(self):
        rows = [
            {"timestamp_ns": 0, "PREDICTED_POWER_W": 100.0},
            {"timestamp_ns": 1_000_000_000, "PREDICTED_POWER_W": 200.0},
            {"timestamp_ns": 2_000_000_000, "PREDICTED_POWER_W": 300.0},
        ]
        result = summarize_predicted_series(rows)
        self.assertEqual(result["samples"], 3)
        self.assertAlmostEqual(result["observed_window_energy_j"], 400.0)
        self.assertAlmostEqual(
            result["time_weighted_mean_predicted_power_w"], 200.0
        )

    def test_unbound_bundle_is_not_ranking_eligible(self):
        manifest = {
            "status": "validation_required",
            "model_id": "demo",
            "model_version": "1.0.0",
            "model_format": "onnx",
            "node_binding": {
                "kubernetes_node": None,
                "gpu_uuid": None,
                "gpu_model": None,
            },
            "target": {
                "source_field": "ACTUAL_POWER_W",
                "inferred_semantics": "pdu-outlet-power",
                "semantics_verified": False,
                "unit": "W",
            },
        }
        result = build_power_smoke_result(
            manifest=manifest,
            required_features=["CPU User%", "GPU Power(W)"],
            predicted_rows=[
                {"timestamp_ns": 0, "PREDICTED_POWER_W": 100.0},
                {"timestamp_ns": 1_000_000_000, "PREDICTED_POWER_W": 110.0},
            ],
            ood_messages=[],
            alignment_quality={"pass": True},
        )
        self.assertEqual(result["status"], "validation_required")
        self.assertFalse(result["ranking_eligible"])
        self.assertIn("Kubernetes node binding is missing", result["blockers"])
        self.assertIn("power target semantics are not verified", result["blockers"])


if __name__ == "__main__":
    unittest.main()
