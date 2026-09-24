import unittest

from pre6g_experiment.decision import (
    integrate_power_samples,
    production_job,
    rank_nodes,
)
from pre6g_experiment.work import estimate_work


class WorkTests(unittest.TestCase):
    def test_yolo_estimate(self):
        job = {
            "metadata": {
                "annotations": {
                    "pre6g.io/application-container": "trainer",
                    "pre6g.io/workload-family": "yolo26-training",
                    "pre6g.io/dataset-train-samples": "512",
                }
            },
            "spec": {
                "template": {
                    "spec": {
                        "containers": [
                            {
                                "name": "trainer",
                                "args": ["--epochs", "20", "--batch", "16"],
                            }
                        ]
                    }
                }
            },
        }
        estimate = estimate_work(job)
        self.assertEqual(estimate.status, "estimated")
        self.assertEqual(estimate.total_iterations, 640)


class DecisionTests(unittest.TestCase):
    def test_rank_uses_incremental_energy_and_confidence(self):
        def node(name, runtime, power, idle, confidence=1.0):
            return {
                "node": name,
                "eligible": True,
                "gpu_sharing": {
                    "strategy": "time-slicing",
                    "resource_name": "nvidia.com/gpu.shared",
                    "replicas_per_gpu": 4,
                },
                "runtime": {
                    "status": "ready",
                    "backend": "target-process-cuda-trace",
                    "supported_sharing_strategies": ["time-slicing"],
                    "predicted_runtime_ms_per_iteration": runtime,
                    "confidence": confidence,
                    "ood": False,
                },
                "power": {
                    "status": "ready",
                    "steady_power_w": power,
                    "idle_power_w": idle,
                    "confidence": confidence,
                    "ood": False,
                    "missing_features": [],
                },
                "quality": {
                    "complete_cycles": 10,
                    "target_process_identified": True,
                    "hardware_trace": True,
                    "netdata_samples": 12,
                    "max_netdata_gap_s": 1.1,
                },
            }

        ranked, rejected = rank_nodes(
            {"nodes": [node("a", 52, 330, 80), node("b", 39, 410, 95)]},
            640,
        )
        self.assertFalse(rejected)
        self.assertEqual(ranked[0].node, "b")
        self.assertAlmostEqual(ranked[0].total_energy_j, 7862.4)

    def test_trapezoid_power_integration(self):
        samples = [
            {"timestamp_unix_ns": 0, "predicted_power_w": 100.0},
            {"timestamp_unix_ns": 1_000_000_000, "predicted_power_w": 200.0},
            {"timestamp_unix_ns": 2_000_000_000, "predicted_power_w": 300.0},
        ]
        self.assertAlmostEqual(integrate_power_samples(samples), 400.0)

    def test_render_pins_selected_node(self):
        source = {
            "apiVersion": "batch/v1",
            "kind": "Job",
            "metadata": {"name": "demo"},
            "spec": {"template": {"spec": {"containers": [], "restartPolicy": "Never"}}},
        }
        rendered = production_job(source, "worker-1")
        self.assertEqual(rendered["metadata"]["name"], "demo-energy-selected")
        self.assertEqual(
            rendered["spec"]["template"]["spec"]["nodeSelector"]["kubernetes.io/hostname"],
            "worker-1",
        )
        self.assertNotIn("nodeSelector", source["spec"]["template"]["spec"])


if __name__ == "__main__":
    unittest.main()
