import unittest

from pre6g_experiment.decision import (
    integrate_power_samples,
    production_job,
    rank_nodes,
)
from pre6g_experiment.work import estimate_work, execution_contract


class WorkTests(unittest.TestCase):
    def test_explicit_generic_work_metadata(self):
        job = {
            "metadata": {
                "annotations": {
                    "pre6g.io/application-container": "app",
                    "pre6g.io/workload-family": "video-encoding",
                    "pre6g.io/work-unit": "frame",
                    "pre6g.io/total-work-units": "18000",
                    "pre6g.io/workload-parameters-json": (
                        '{"codec":"h265","resolution":"3840x2160","preset":"slow"}'
                    ),
                }
            },
            "spec": {
                "template": {
                    "spec": {
                        "containers": [
                            {
                                "name": "app",
                                "image": "example/ffmpeg@sha256:demo",
                                "command": ["ffmpeg"],
                                "args": ["-i", "/data/input.mp4"],
                            }
                        ]
                    }
                }
            },
        }

        estimate = estimate_work(job)
        self.assertEqual(estimate.status, "declared")
        self.assertEqual(estimate.work_unit, "frame")
        self.assertEqual(estimate.total_work_units, 18000)
        self.assertEqual(estimate.parameters["codec"], "h265")
        self.assertTrue(estimate.profileable)

    def test_yolo_adapter_estimate(self):
        job = {
            "metadata": {
                "annotations": {
                    "pre6g.io/application-container": "trainer",
                    "pre6g.io/workload-family": "vision-training",
                    "pre6g.io/workload-adapter": "yolo",
                    "pre6g.io/dataset-train-samples": "512",
                }
            },
            "spec": {
                "template": {
                    "spec": {
                        "containers": [
                            {
                                "name": "trainer",
                                "args": [
                                    "--model",
                                    "yolo26n.yaml",
                                    "--epochs",
                                    "20",
                                    "--batch",
                                    "16",
                                    "--imgsz",
                                    "640",
                                ],
                            }
                        ]
                    }
                }
            },
        }

        estimate = estimate_work(job)
        self.assertEqual(estimate.status, "estimated")
        self.assertEqual(estimate.adapter, "yolo")
        self.assertEqual(estimate.work_unit, "training_iteration")
        self.assertEqual(estimate.total_work_units, 640)
        self.assertEqual(estimate.parameters["batch_size"], 16)
        self.assertEqual(estimate.parameters["input_size"], 640)

    def test_unknown_workload_remains_profileable(self):
        job = {
            "metadata": {
                "annotations": {
                    "pre6g.io/application-container": "app",
                }
            },
            "spec": {
                "template": {
                    "spec": {
                        "containers": [
                            {
                                "name": "app",
                                "image": "example/custom@sha256:demo",
                                "command": ["/app/run"],
                                "args": ["--opaque", "value"],
                            }
                        ]
                    }
                }
            },
        }

        estimate = estimate_work(job)
        self.assertEqual(estimate.status, "unknown")
        self.assertIsNone(estimate.total_work_units)
        self.assertTrue(estimate.profileable)
        self.assertTrue(estimate.to_dict()["work"]["runtime_discovery_required"])

    def test_legacy_total_iterations_annotation(self):
        job = {
            "metadata": {
                "annotations": {
                    "pre6g.io/application-container": "app",
                    "pre6g.io/total-iterations": "640",
                }
            },
            "spec": {
                "template": {
                    "spec": {
                        "containers": [
                            {
                                "name": "app",
                                "image": "example/train@sha256:demo",
                            }
                        ]
                    }
                }
            },
        }

        estimate = estimate_work(job)
        self.assertEqual(estimate.status, "declared")
        self.assertEqual(estimate.work_unit, "training_iteration")
        self.assertEqual(estimate.total_work_units, 640)
        self.assertEqual(estimate.total_iterations, 640)

    def test_execution_contract_does_not_interpret_arguments(self):
        job = {
            "metadata": {
                "annotations": {
                    "pre6g.io/application-container": "app",
                }
            },
            "spec": {
                "template": {
                    "spec": {
                        "runtimeClassName": "nvidia",
                        "restartPolicy": "Never",
                        "containers": [
                            {
                                "name": "app",
                                "image": "example/app@sha256:demo",
                                "command": ["python3"],
                                "args": ["train.py", "--anything", "123"],
                                "env": [{"name": "SECRET_REF", "valueFrom": {}}],
                                "resources": {
                                    "limits": {"nvidia.com/gpu.shared": "1"}
                                },
                            }
                        ],
                    }
                }
            },
        }

        contract = execution_contract(job)
        self.assertEqual(contract["command"], ["python3"])
        self.assertEqual(contract["args"], ["train.py", "--anything", "123"])
        self.assertEqual(contract["env_names"], ["SECRET_REF"])
        self.assertNotIn("parameters", contract)


class DecisionTests(unittest.TestCase):
    def _node(self, name, runtime, power, idle, confidence=1.0):
        gpu_uuid = f"GPU-{name}"
        return {
            "node": name,
            "eligible": True,
            "gpu_sharing": {
                "strategy": "time-slicing",
                "resource_name": "nvidia.com/gpu.shared",
                "replicas_per_gpu": 4,
                "physical_gpu_uuid": gpu_uuid,
            },
            "runtime": {
                "status": "ready",
                "backend": "target-process-cuda-trace",
                "supported_sharing_strategies": ["time-slicing"],
                "work_unit": "training_iteration",
                "predicted_runtime_ms_per_work_unit": runtime,
                "confidence": confidence,
                "ood": False,
            },
            "power": {
                "status": "ready",
                "model_scope": "node-bound",
                "model_id": f"power-{name}",
                "model_version": "test-v1",
                "bound_node": name,
                "bound_gpu_uuid": gpu_uuid,
                "target_semantics": "node-total-power",
                "target_unit": "W",
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
                "clock_synchronized": True,
                "netdata_samples": 12,
                "max_netdata_gap_s": 1.1,
                "dcgm_samples": 12,
                "max_dcgm_gap_s": 1.1,
                "alignment_coverage": 1.0,
                "max_alignment_delta_ms": 490.0,
            },
        }

    def test_rank_uses_gross_node_energy_and_confidence(self):
        ranked, rejected = rank_nodes(
            {
                "nodes": [
                    self._node("a", 52, 330, 80),
                    self._node("b", 39, 410, 95),
                ]
            },
            640,
            work_unit="training_iteration",
        )
        self.assertFalse(rejected)
        self.assertEqual(ranked[0].node, "b")
        self.assertEqual(ranked[0].work_unit, "training_iteration")
        self.assertAlmostEqual(ranked[0].total_energy_j, 10233.6)

    def test_idle_power_is_not_required_for_gross_energy_ranking(self):
        item = self._node("worker-5090", 40, 400, 90)
        item["power"].pop("idle_power_w")

        ranked, rejected = rank_nodes(
            {"nodes": [item]},
            100,
            work_unit="training_iteration",
        )

        self.assertFalse(rejected)
        self.assertEqual(len(ranked), 1)
        self.assertAlmostEqual(ranked[0].total_energy_j, 1600.0)

    def test_rejects_runtime_work_unit_mismatch(self):
        item = self._node("worker-4090", 52, 330, 80)
        ranked, rejected = rank_nodes(
            {"nodes": [item]},
            100,
            work_unit="frame",
        )

        self.assertFalse(ranked)
        self.assertIn("work unit", rejected["worker-4090"])

    def test_rejects_power_model_binding_mismatch(self):
        item = self._node("worker-4090", 52, 330, 80)
        item["power"]["bound_gpu_uuid"] = "GPU-wrong"

        ranked, rejected = rank_nodes(
            {"nodes": [item]},
            640,
            work_unit="training_iteration",
        )

        self.assertFalse(ranked)
        self.assertIn("worker-4090", rejected)
        self.assertIn("GPU UUID binding", rejected["worker-4090"])

    def test_rejects_bad_alignment_quality(self):
        item = self._node("worker-5090", 39, 410, 95)
        item["quality"]["alignment_coverage"] = 0.75

        ranked, rejected = rank_nodes(
            {"nodes": [item]},
            640,
            work_unit="training_iteration",
        )

        self.assertFalse(ranked)
        self.assertIn("alignment coverage", rejected["worker-5090"])

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
            "spec": {
                "template": {
                    "spec": {"containers": [], "restartPolicy": "Never"}
                }
            },
        }
        rendered = production_job(source, "worker-1")
        self.assertEqual(rendered["metadata"]["name"], "demo-energy-selected")
        self.assertEqual(
            rendered["spec"]["template"]["spec"]["nodeSelector"][
                "kubernetes.io/hostname"
            ],
            "worker-1",
        )
        self.assertNotIn("nodeSelector", source["spec"]["template"]["spec"])


if __name__ == "__main__":
    unittest.main()
