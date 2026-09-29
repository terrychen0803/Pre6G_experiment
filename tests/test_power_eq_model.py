import json
import tempfile
import unittest
from pathlib import Path

from pre6g_experiment.power_eq_model import load_records, load_scaler


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


if __name__ == "__main__":
    unittest.main()
