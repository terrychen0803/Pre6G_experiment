import json
import tempfile
import unittest
from pathlib import Path

from pre6g_experiment.runtime_model import (
    RuntimeModelError,
    load_feature_artifact,
    load_model,
    predict_runtime,
)


MODEL = Path(
    "models/runtime/RTX5090_yolo_trace_only_v1.json"
)


class RuntimeModelTests(unittest.TestCase):
    def test_frozen_model_predicts_known_c03_smoke_value(self):
        model = load_model(MODEL)

        sample = {
            "schema_version": "pre6g.runtime-features/v1",
            "node": "mirc516-20250605",
            "device_id": "RTX5090",
            "workload_id": "C03",
            "detected_unit": "execution_cycle",
            "detector_profile": "yolo-v1",
            "detected_period_ms": 129.988614,
            "detector_confidence": 0.8399946282048405,
            "trace_features": {
                "log_detected_period_ms": 4.867446862004441,
                "log_kernel_event_rate_hz": 10.428770661391045,
                "log_unique_kernel_count": 4.330733340286331,
                "log_median_kernel_duration_us": -0.2231435513142097,
                "log_mean_kernel_duration_us": 0.9942339709525143,
                "log_kernel_busy_fraction": -2.3925059256207146,
                "anchor_robust_cv": 0.17429492307741615,
            },
        }

        prediction = predict_runtime(model, sample)

        self.assertAlmostEqual(
            prediction.predicted_runtime_ms,
            109.25171448874927,
            places=9,
        )

    def test_device_mismatch_is_rejected(self):
        model = load_model(MODEL)
        sample = {
            "schema_version": "pre6g.runtime-features/v1",
            "node": "worker",
            "device_id": "RTX4090",
            "detected_unit": "execution_cycle",
            "detector_profile": "yolo-v1",
            "trace_features": {
                name: 0.0
                for name in model["feature_names"]
            },
        }

        with self.assertRaisesRegex(
            RuntimeModelError,
            "device mismatch",
        ):
            predict_runtime(model, sample)

    def test_missing_feature_is_rejected(self):
        model = load_model(MODEL)
        names = list(model["feature_names"])
        sample = {
            "schema_version": "pre6g.runtime-features/v1",
            "node": "worker",
            "device_id": "RTX5090",
            "detected_unit": "execution_cycle",
            "detector_profile": "yolo-v1",
            "trace_features": {
                name: 0.0
                for name in names[:-1]
            },
        }

        with self.assertRaisesRegex(
            RuntimeModelError,
            "missing runtime features",
        ):
            predict_runtime(model, sample)

    def test_feature_artifact_schema_is_checked(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "features.json"
            path.write_text(
                json.dumps(
                    {
                        "schema_version": "wrong",
                        "trace_features": {},
                    }
                ),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(
                RuntimeModelError,
                "unsupported runtime feature artifact schema",
            ):
                load_feature_artifact(path)


if __name__ == "__main__":
    unittest.main()
