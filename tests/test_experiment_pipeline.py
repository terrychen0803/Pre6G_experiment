from __future__ import annotations

import argparse
import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "run_experiment_pipeline",
    ROOT / "scripts" / "run_experiment_pipeline.py",
)
assert SPEC is not None and SPEC.loader is not None
PIPELINE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = PIPELINE
SPEC.loader.exec_module(PIPELINE)


class ExperimentPipelineTests(unittest.TestCase):
    def base_args(self, output_dir: Path) -> argparse.Namespace:
        return argparse.Namespace(
            mode="demo",
            job=ROOT / "examples" / "yolo26" / "user-job.yaml",
            output_dir=output_dir,
            resume=False,
            node_results=None,
            allow_synthetic=False,
            sqlite=None,
            node=None,
            device_id=None,
            runtime_model=None,
            task_id="test-run",
            workload_id="test-workload",
            detector_profile="yolo-v1",
            global_pid=None,
            context_id=None,
            audit_nvtx=False,
            aligned_telemetry=None,
            alignment_quality=None,
            netdata_telemetry=None,
            dcgm_telemetry=None,
            power_bundle=None,
            ranking_input=None,
            validation_id=None,
            target_minutes=40.0,
            validation_plan=None,
            pods_json=None,
            pdu=[],
            pdu_interval=None,
            timestamp_column="timestamp",
            power_column="power_w",
            cross_node_config=None,
            run_id=None,
            worker_commit=None,
            preflight_only=False,
            smoke_only=False,
            execute=False,
            kube_context=None,
            timeout_seconds=900,
        )

    def test_demo_uses_repository_synthetic_results(self):
        with tempfile.TemporaryDirectory() as raw_tmp:
            args = self.base_args(Path(raw_tmp))
            PIPELINE.validate(args)
            stages = PIPELINE.build_stages(args, Path(raw_tmp))

        self.assertEqual([stage.name for stage in stages], [
            "01-discover-work",
            "02-rank-and-render",
        ])
        self.assertTrue(str(args.node_results).endswith("synthetic-node-results.json"))

    def test_profile_requires_trace_identity_and_model(self):
        with tempfile.TemporaryDirectory() as raw_tmp:
            args = self.base_args(Path(raw_tmp))
            args.mode = "profile"
            with self.assertRaisesRegex(ValueError, "profile mode requires"):
                PIPELINE.validate(args)

    def test_power_bundle_and_telemetry_are_paired(self):
        with tempfile.TemporaryDirectory() as raw_tmp:
            args = self.base_args(Path(raw_tmp))
            args.power_bundle = Path("bundle")
            with self.assertRaisesRegex(ValueError, "power-bundle"):
                PIPELINE.validate(args)

    def test_evaluation_requires_frozen_plan_and_pods(self):
        with tempfile.TemporaryDirectory() as raw_tmp:
            args = self.base_args(Path(raw_tmp))
            args.mode = "validation-evaluate"
            args.job = None
            with self.assertRaisesRegex(ValueError, "validation-evaluate requires"):
                PIPELINE.validate(args)
            args.validation_plan = Path("plan.json")
            args.pods_json = Path("pods.json")
            PIPELINE.validate(args)
            stages = PIPELINE.build_stages(args, Path(raw_tmp))
            self.assertEqual([stage.name for stage in stages], ["01-evaluate-all-node-runs"])

    def test_profile_power_stage_uses_current_range_policy(self):
        with tempfile.TemporaryDirectory() as raw_tmp:
            args = self.base_args(Path(raw_tmp))
            args.mode = "profile"
            args.sqlite = Path("trace.sqlite")
            args.node = "worker-a"
            args.device_id = "RTX4090"
            args.runtime_model = Path("runtime.json")
            args.aligned_telemetry = Path("aligned.csv")
            args.power_bundle = Path("power-bundle")
            PIPELINE.validate(args)
            stages = PIPELINE.build_stages(args, Path(raw_tmp))
            power = next(stage for stage in stages if stage.name == "08-predict-power")
            self.assertNotIn("--reject-ood", power.command)

    def test_cross_node_mode_plans_without_kubectl_by_default(self):
        with tempfile.TemporaryDirectory() as raw_tmp:
            args = self.base_args(Path(raw_tmp))
            args.mode = "cross-node"
            args.job = None
            args.cross_node_config = ROOT / "examples/yolo26/cross-node-dryrun.yaml"
            args.run_id = "test-run-001"
            args.worker_commit = "767756535d215293f7c6d96b65ae0c07134248c0"
            PIPELINE.validate(args)
            stages = PIPELINE.build_stages(args, Path(raw_tmp))
            self.assertEqual(len(stages), 1)
            self.assertNotIn("--execute", stages[0].command)
            args.preflight_only = True
            with self.assertRaisesRegex(ValueError, "kube-context"):
                PIPELINE.validate(args)
            args.kube_context = "test-context"
            PIPELINE.validate(args)
            preflight = PIPELINE.build_stages(args, Path(raw_tmp))[0]
            self.assertIn("--preflight-only", preflight.command)
            self.assertNotIn("--execute", preflight.command)
            args.preflight_only = False
            args.smoke_only = True
            PIPELINE.validate(args)
            smoke = PIPELINE.build_stages(args, Path(raw_tmp))[0]
            self.assertIn("--smoke-only", smoke.command)
            self.assertIn(Path(raw_tmp) / "smoke-result.json", smoke.outputs)
            args.execute = True
            with self.assertRaisesRegex(ValueError, "mutually exclusive"):
                PIPELINE.validate(args)


if __name__ == "__main__":
    unittest.main()
