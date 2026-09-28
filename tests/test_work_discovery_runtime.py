from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from pre6g_experiment.runtime_aggregation import (
    RuntimeAggregationError,
    aggregate_runtime,
)
from pre6g_experiment.work_discovery import discover_work


class WorkDiscoveryTests(unittest.TestCase):
    def _yolo_job(self, data_yaml: Path) -> dict:
        return {
            "apiVersion": "batch/v1",
            "kind": "Job",
            "metadata": {
                "annotations": {
                    "pre6g.io/application-container": "trainer",
                    "pre6g.io/workload-family": "YOLO26",
                    "pre6g.io/workload-adapter": "yolo",
                }
            },
            "spec": {
                "template": {
                    "spec": {
                        "containers": [
                            {
                                "name": "trainer",
                                "command": ["yolo"],
                                "args": [
                                    "detect",
                                    "train",
                                    "model=yolo26n.yaml",
                                    f"data={data_yaml}",
                                    "epochs=4",
                                    "imgsz=320",
                                    "batch=16",
                                    "workers=0",
                                    "device=0",
                                    "pretrained=False",
                                    "amp=False",
                                ],
                            }
                        ]
                    }
                }
            },
        }

    def test_yolo_mounted_dataset_discovers_128_work_units(self):
        with tempfile.TemporaryDirectory() as raw_tmp:
            root = Path(raw_tmp)
            train = root / "images" / "train"
            train.mkdir(parents=True)

            for index in range(512):
                (train / f"{index:06d}.jpg").touch()

            data_yaml = root / "data.yaml"
            data_yaml.write_text(
                "path: " + str(root) + "\n"
                "train: images/train\n"
                "val: images/val\n"
                "names:\n"
                "  0: object\n",
                encoding="utf-8",
            )

            result = discover_work(self._yolo_job(data_yaml))

        self.assertEqual(result["discovery_mode"], "mounted-dataset")
        self.assertEqual(
            result["parameters"]["dataset_train_samples"],
            512,
        )
        self.assertEqual(result["work"]["unit"], "training_iteration")
        self.assertEqual(result["work"]["steps_per_epoch"], 32)
        self.assertEqual(result["work"]["total_units"], 128)

        policy = result["production_input_policy"]
        self.assertFalse(policy["uses_iterations_csv"])
        self.assertFalse(policy["uses_nvtx"])
        self.assertFalse(policy["uses_callbacks"])
        self.assertFalse(policy["uses_epoch_timestamps"])
        self.assertFalse(policy["uses_batch_timestamps"])

    def test_semantic_runtime_uses_discovered_total_work(self):
        workload = {
            "schema_version": "pre6g.work-discovery/v1",
            "workload_family": "YOLO26",
            "adapter": "yolo",
            "profileable": True,
            "discovery_mode": "mounted-dataset",
            "parameters": {
                "epochs": 4,
                "batch_size": 16,
                "dataset_train_samples": 512,
            },
            "dataset": {"status": "discovered"},
            "work": {
                "status": "estimated",
                "unit": "training_iteration",
                "steps_per_epoch": 32,
                "total_units": 128,
                "source": (
                    "adapter:yolo argv/config + mounted dataset cardinality"
                ),
                "assumptions": [],
                "missing": [],
            },
            "production_input_policy": {
                "uses_iterations_csv": False,
                "uses_nvtx": False,
                "uses_callbacks": False,
                "uses_epoch_timestamps": False,
                "uses_batch_timestamps": False,
            },
        }
        prediction = {
            "schema_version": "pre6g.runtime-prediction/v1",
            "model_id": "RTX5090_yolo_trace_only_v1",
            "device_id": "RTX5090",
            "detected_unit": "execution_cycle",
            "predicted_runtime_ms": 115.60792921841606,
            "feature_count": 7,
            "node": "mirc516-20250605",
            "workload_id": "yolo26-synthetic",
            "detector_profile": "yolo-v1",
            "detector_confidence": 0.7304067523438615,
            "model_role": "deployment-smoke",
        }

        result = aggregate_runtime(prediction, workload)

        self.assertEqual(result["work_unit"], "training_iteration")
        self.assertEqual(result["total_work_units"], 128)
        self.assertAlmostEqual(
            result["predicted_runtime_ms_per_work_unit"],
            115.60792921841606,
        )
        self.assertAlmostEqual(
            result["predicted_steady_runtime_s"],
            14.797814939957256,
        )
        self.assertIsNone(result["predicted_total_job_runtime_s"])
        self.assertEqual(
            result["total_job_runtime_status"],
            "pending-non-steady-overhead-model",
        )

    def test_binding_fails_closed_for_unknown_adapter(self):
        workload = {
            "workload_family": "custom",
            "adapter": "generic",
            "work": {
                "unit": "training_iteration",
                "total_units": 10,
            },
        }
        prediction = {
            "detected_unit": "execution_cycle",
            "detector_profile": "yolo-v1",
            "predicted_runtime_ms": 10.0,
        }

        with self.assertRaises(RuntimeAggregationError):
            aggregate_runtime(prediction, workload)


if __name__ == "__main__":
    unittest.main()
