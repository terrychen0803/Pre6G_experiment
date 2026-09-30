import json
import tempfile
import unittest
from pathlib import Path

from pre6g_experiment.power_eq_model import load_records, load_scaler, prepare_inputs


class PowerEqModelTests(unittest.TestCase):
    def test_load_records_accepts_wrapped_json(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "input.json"
            path.write_text('{"records": [{"CPU User%": 12.5}]}', encoding="utf-8")
            self.assertEqual(load_records(path), [{"CPU User%": 12.5}])

    def test_bundle_scaler_has_consistent_feature_lengths(self):
        path = (
            Path(__file__).resolve().parents[1]
            / "models"
            / "power"
            / "bundles"
            / "pdu1-outlet1-20260416-20260612"
            / "scaler.json"
        )
        scaler = load_scaler(path)
        self.assertEqual(len(scaler["feature_cols"]), 5)
        self.assertEqual(len(scaler["x_mins"]), 5)
        self.assertEqual(len(scaler["x_data_ranges"]), 5)

    def test_rtx5090_bundle_scaler_has_consistent_feature_lengths(self):
        path = (
            Path(__file__).resolve().parents[1]
            / "models"
            / "power"
            / "bundles"
            / "pdu1-outlet7-20260416-20260612"
            / "scaler.json"
        )
        scaler = load_scaler(path)
        self.assertEqual(len(scaler["feature_cols"]), 7)
        self.assertEqual(len(scaler["x_mins"]), 7)
        self.assertEqual(len(scaler["x_data_ranges"]), 7)
        self.assertEqual(scaler["core_indices"], ["CPU User%"])
        self.assertEqual(scaler["direct_indices"], ["GPU Power(W)"])
        self.assertEqual(len(scaler["other_indices"]), 5)


    def test_scaler_range_exceedance_is_diagnostic_only(self):
        scaler = {
            "feature_cols": ["GPU Power(W)"],
            "x_mins": [0.0],
            "x_data_ranges": [100.0],
            "core_indices": [],
            "direct_indices": ["GPU Power(W)"],
            "other_indices": [],
            "y_min": 0.0,
            "y_data_range": 1.0,
        }
        inputs, warnings = prepare_inputs(
            [{"GPU Power(W)": 125.0}],
            scaler,
        )
        self.assertAlmostEqual(float(inputs["x_direct"][0, 0]), 1.25)
        self.assertEqual(len(warnings), 1)
        self.assertIn("scaler reference range", warnings[0])


if __name__ == "__main__":
    unittest.main()
