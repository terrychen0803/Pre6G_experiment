from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import yaml


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("run_cross_node_dryrun", ROOT / "scripts" / "run_cross_node_dryrun.py")
assert SPEC and SPEC.loader
DRYRUN = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(DRYRUN)
CONFIG = ROOT / "examples/yolo26/cross-node-dryrun.yaml"
COMMIT = "767756535d215293f7c6d96b65ae0c07134248c0"


class CrossNodeDryrunTests(unittest.TestCase):
    def test_plan_creates_concurrent_node_jobs_without_deploying(self):
        plan, jobs, collector = DRYRUN.prepare(CONFIG, run_id="test-run-001", worker_commit=COMMIT)
        self.assertEqual(plan["source_total_work_units"], 960)
        self.assertEqual(plan["experiment_stage"], "functional-validation")
        self.assertEqual(plan["test_purpose"], "cross-node-pipeline-integration")
        self.assertFalse(plan["production_result"])
        self.assertEqual(len(jobs), 2)
        self.assertEqual(collector["spec"]["volumes"][0]["persistentVolumeClaim"]["claimName"], "pre6g-artifacts")
        self.assertEqual(plan["collector_node"], "icclz2")
        self.assertEqual(collector["spec"]["nodeSelector"]["kubernetes.io/hostname"], "icclz2")
        self.assertEqual({job["spec"]["template"]["spec"]["nodeSelector"]["kubernetes.io/hostname"] for job in jobs}, {"iccl-s3-251230", "mirc516-20250605"})
        for job in jobs:
            annotations = job["metadata"]["annotations"]
            self.assertEqual(annotations["pre6g.io/experiment-stage"], "functional-validation")
            self.assertEqual(annotations["pre6g.io/test-purpose"], "cross-node-pipeline-integration")
            self.assertEqual(annotations["pre6g.io/production-result"], "false")
            container = job["spec"]["template"]["spec"]["containers"][0]
            self.assertIn(COMMIT, container["command"][2])
            self.assertIn("--duration=120", container["command"][2])
            self.assertIn("run_command_with_end_marker.py", container["command"][2])
            env = {item["name"]: item["value"] for item in container["env"]}
            self.assertEqual(env["TASK_ID"], "test-run-001")
            self.assertEqual(job["metadata"]["labels"]["pre6g.io/candidate-node"], env["NODE_NAME"])
            self.assertEqual(env["DCGM_URL"], "DCGM_ENDPOINT_UNRESOLVED")

    def test_control_side_power_dependencies_load_before_cluster_execution(self):
        plan, _, _ = DRYRUN.prepare(CONFIG, run_id="test-run-001", worker_commit=COMMIT)
        DRYRUN._check_control_side_dependencies(plan)

    def test_wrong_bundle_binding_is_rejected(self):
        config = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
        config["candidates"][0]["power_bundle"] = config["candidates"][1]["power_bundle"]
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "config.yaml"
            path.write_text(yaml.safe_dump(config), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "bound to another node"):
                DRYRUN.prepare(path, run_id="test-run-001", worker_commit=COMMIT)

    def test_invalid_or_repeated_identity_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "worker_commit"):
            DRYRUN.prepare(CONFIG, run_id="test-run-001", worker_commit="short")
        with self.assertRaisesRegex(ValueError, "run_id"):
            DRYRUN.prepare(CONFIG, run_id="../unsafe", worker_commit=COMMIT)

    def test_cluster_preflight_needs_kubectl_and_never_submits_without_it(self):
        plan, jobs, _ = DRYRUN.prepare(CONFIG, run_id="test-run-001", worker_commit=COMMIT)
        with tempfile.TemporaryDirectory() as raw, patch.object(DRYRUN.shutil, "which", return_value=None), patch.object(DRYRUN.subprocess, "run", return_value=SimpleNamespace(returncode=0)):
            with self.assertRaisesRegex(RuntimeError, "kubectl is unavailable"):
                DRYRUN._preflight(plan, jobs, "missing-context", Path(raw))

    def test_preflight_checks_context_nfs_nodes_and_resolves_dcgm_per_node(self):
        plan, jobs, _ = DRYRUN.prepare(CONFIG, run_id="test-run-001", worker_commit=COMMIT)

        def command_result(command, **_kwargs):
            return SimpleNamespace(returncode=0 if command[0] == "git" else 1)

        def kubectl_result(command, **_kwargs):
            if command[:3] == ["kubectl", "config", "current-context"]:
                return "test-context\n"
            if "pv" in command:
                return json.dumps({"status": {"phase": "Bound"}, "spec": {"accessModes": ["ReadWriteMany"], "claimRef": {"namespace": "experiments", "name": "pre6g-artifacts"}, "nfs": {"server": "nfs.example", "path": "/srv/pre6g-artifacts"}}})
            if "pvc" in command:
                return json.dumps({"status": {"phase": "Bound"}, "spec": {"accessModes": ["ReadWriteMany"], "volumeName": "pre6g-artifacts-nfs"}})
            if "jobs" in command:
                return json.dumps({"items": []})
            if "node" in command:
                return json.dumps({"status": {"conditions": [{"type": "Ready", "status": "True"}, {"type": "DiskPressure", "status": "False"}], "allocatable": {"nvidia.com/gpu.shared": "1"}}})
            if "pods" in command:
                return json.dumps({"items": [{"spec": {"nodeName": item["node"]}, "status": {"phase": "Running", "podIP": f"10.42.0.{index + 1}", "conditions": [{"type": "Ready", "status": "True"}]}} for index, item in enumerate(plan["candidates"])]})
            return ""

        with tempfile.TemporaryDirectory() as raw, patch.object(DRYRUN.shutil, "which", return_value="kubectl"), patch.object(DRYRUN.subprocess, "run", side_effect=command_result), patch.object(DRYRUN, "_invoke", side_effect=kubectl_result) as invoke:
            resolved = DRYRUN._preflight(plan, jobs, "test-context", Path(raw))
            self.assertEqual(invoke.call_count, 12)
            rendered = list(yaml.safe_load_all(resolved.read_text(encoding="utf-8")))
            self.assertEqual({job["spec"]["template"]["spec"]["nodeSelector"]["kubernetes.io/hostname"]: next(row["value"] for row in job["spec"]["template"]["spec"]["containers"][0]["env"] if row["name"] == "DCGM_URL") for job in rendered}, {"iccl-s3-251230": "http://10.42.0.1:9400", "mirc516-20250605": "http://10.42.0.2:9400"})

    def test_dcgm_lookup_rejects_missing_node_exporter(self):
        plan, _, _ = DRYRUN.prepare(CONFIG, run_id="test-run-001", worker_commit=COMMIT)
        with tempfile.TemporaryDirectory() as raw, patch.object(DRYRUN, "_invoke", return_value=json.dumps({"items": []})):
            with self.assertRaisesRegex(RuntimeError, "expected exactly one DCGM exporter Pod"):
                DRYRUN._resolve_dcgm_urls(plan, "test-context", Path(raw))

    def test_dcgm_lookup_rejects_not_ready_exporter(self):
        plan, _, _ = DRYRUN.prepare(CONFIG, run_id="test-run-001", worker_commit=COMMIT)
        pod = {"spec": {"nodeName": plan["candidates"][0]["node"]}, "status": {"phase": "Running", "podIP": "10.42.0.1", "conditions": [{"type": "Ready", "status": "False"}]}}
        with tempfile.TemporaryDirectory() as raw, patch.object(DRYRUN, "_invoke", return_value=json.dumps({"items": [pod]})):
            with self.assertRaisesRegex(RuntimeError, "not Running/Ready"):
                DRYRUN._resolve_dcgm_urls(plan, "test-context", Path(raw))

    def test_smoke_pods_use_two_bound_nodes_without_training(self):
        compile(DRYRUN.SMOKE_PROGRAM, "<smoke-pod>", "exec")
        plan, jobs, _ = DRYRUN.prepare(CONFIG, run_id="test-run-001", worker_commit=COMMIT)
        for job in jobs:
            env = {item["name"]: item for item in job["spec"]["template"]["spec"]["containers"][0]["env"]}
            env["DCGM_URL"]["value"] = "http://10.42.0.1:9400"
        pods = DRYRUN._smoke_pods(plan, jobs)
        self.assertEqual(len(pods), 2)
        for pod in pods:
            spec = pod["spec"]
            self.assertEqual(spec["runtimeClassName"], "nvidia")
            self.assertEqual(spec["activeDeadlineSeconds"], 300)
            self.assertEqual(spec["containers"][0]["resources"]["requests"]["nvidia.com/gpu.shared"], "1")
            self.assertIn("nvidia-smi", spec["containers"][0]["command"][2])
            self.assertNotIn("yolo detect train", spec["containers"][0]["command"][2])
            self.assertEqual({volume["name"] for volume in spec["volumes"]}, {"artifacts", "nsys"})
        self.assertEqual({pod["spec"]["nodeSelector"]["kubernetes.io/hostname"] for pod in pods}, {item["node"] for item in plan["candidates"]})

    def test_smoke_pods_are_deleted_after_success(self):
        plan, jobs, _ = DRYRUN.prepare(CONFIG, run_id="test-run-001", worker_commit=COMMIT)
        for job in jobs:
            env = {item["name"]: item for item in job["spec"]["template"]["spec"]["containers"][0]["env"]}
            env["DCGM_URL"]["value"] = "http://10.42.0.1:9400"
        pods = DRYRUN._smoke_pods(plan, jobs)
        queries = 0

        def fake_kubectl(command, **_kwargs):
            nonlocal queries
            if "get" in command and "pods" in command:
                queries += 1
                if queries == 1:
                    return json.dumps({"items": []})
                return json.dumps({"items": [{"metadata": {"name": pod["metadata"]["name"]}, "status": {"phase": "Succeeded"}} for pod in pods]})
            return ""

        def fake_logs(command, **_kwargs):
            name = command[command.index("logs") + 1]
            candidate = next(item for item in plan["candidates"] if item["node"] in name)
            payload = {"schema_version": "pre6g.cross-node-smoke-result/v1", "passed": True, "run_id": plan["run_id"], "node": candidate["node"], "gpu_uuid": candidate["gpu_uuid"], "nfs_peer_read": True, "github_access": True}
            return SimpleNamespace(stdout="PRE6G_SMOKE_RESULT=" + json.dumps(payload) + "\n", stderr="", returncode=0)

        with tempfile.TemporaryDirectory() as raw, patch.object(DRYRUN, "_invoke", side_effect=fake_kubectl) as invoke, patch.object(DRYRUN.subprocess, "run", side_effect=fake_logs):
            DRYRUN._run_smoke(plan, jobs, "test-context", Path(raw))
            result = json.loads((Path(raw) / "smoke-result.json").read_text(encoding="utf-8"))
            self.assertTrue(result["passed"])
            self.assertEqual(len(result["results"]), 2)
            self.assertEqual(sum("delete" in call.args[0] for call in invoke.call_args_list), 2)

    def test_target_wrapper_records_process_window(self):
        with tempfile.TemporaryDirectory() as raw:
            marker = Path(raw) / "application-window.json"
            result = subprocess.run([
                sys.executable, str(ROOT / "scripts/run_command_with_end_marker.py"),
                "--output", str(marker), "--", sys.executable, "-c", "print('trained')",
            ], capture_output=True, text=True, check=False)
            self.assertEqual(result.returncode, 0)
            window = json.loads(marker.read_text(encoding="utf-8"))
            self.assertLess(window["started_at_unix_ns"], window["finished_at_unix_ns"])
            self.assertEqual(window["returncode"], 0)

    def test_telemetry_crop_requires_full_target_coverage(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            csv_path = root / "aligned.csv"
            start = 1_000_000_000_000
            csv_path.write_text("timestamp_ns,power\n" + "".join(f"{start + i * 1_000_000_000},{100 + i}\n" for i in range(12)), encoding="utf-8")
            window = root / "window.json"
            window.write_text(json.dumps({
                "schema_version": "pre6g.application-window/v1",
                "command": ["yolo", "detect", "train"],
                "started_at_unix_ns": start,
                "finished_at_unix_ns": start + 11_000_000_000,
            }), encoding="utf-8")
            crop = DRYRUN._crop_telemetry(csv_path, window, root / "cropped.csv")
            self.assertEqual(crop["sample_count"], 12)
            self.assertTrue((root / "cropped.csv").is_file())
            window.write_text(json.dumps({
                "schema_version": "pre6g.application-window/v1",
                "command": ["yolo", "detect", "train"],
                "started_at_unix_ns": start - 3_000_000_000,
                "finished_at_unix_ns": start + 11_000_000_000,
            }), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "does not cover"):
                DRYRUN._crop_telemetry(csv_path, window, root / "bad.csv")

    def test_wait_requires_all_candidate_jobs(self):
        plan, _, _ = DRYRUN.prepare(CONFIG, run_id="test-run-001", worker_commit=COMMIT)
        jobs = [{"metadata": {"name": row["job_name"]}, "status": {"succeeded": 1}} for row in plan["candidates"]]
        with tempfile.TemporaryDirectory() as raw, patch.object(DRYRUN, "_invoke", return_value=json.dumps({"items": jobs})):
            DRYRUN._wait_jobs(plan, "test-context", Path(raw), 10)
            status = json.loads((Path(raw) / "dryrun-jobs-status.json").read_text(encoding="utf-8"))
            self.assertEqual(len(status["items"]), 2)
        jobs[1]["status"] = {"failed": 1}
        with tempfile.TemporaryDirectory() as raw, patch.object(DRYRUN, "_invoke", return_value=json.dumps({"items": jobs})):
            with self.assertRaisesRegex(RuntimeError, "dry-run Job failed"):
                DRYRUN._wait_jobs(plan, "test-context", Path(raw), 10)

    def test_master_assembles_and_ranks_collected_predictions(self):
        plan, _, _ = DRYRUN.prepare(CONFIG, run_id="test-run-001", worker_commit=COMMIT)
        real_invoke = DRYRUN._invoke

        def fake_inference(command, **kwargs):
            script_name = Path(command[1]).name
            if script_name in {"discover_work.py", "provisional_rank_nodes.py"}:
                return real_invoke(command, **kwargs)
            output = Path(command[command.index("--output") + 1]) if "--output" in command else Path(command[command.index("--output-summary") + 1])
            node = next(row["node"] for row in plan["candidates"] if row["node"] in str(output))
            if script_name == "predict_runtime.py":
                payload = {"model_id": f"runtime-{node}", "predicted_runtime_ms": 50}
            elif script_name == "aggregate_runtime.py":
                payload = {"node": node, "device_id": next(row["device_id"] for row in plan["candidates"] if row["node"] == node), "model_id": f"runtime-{node}", "work_unit": "training_iteration", "total_work_units": 960, "predicted_runtime_ms_per_work_unit": 50 if node == plan["candidates"][0]["node"] else 100}
            else:
                candidate = next(row for row in plan["candidates"] if row["node"] == node)
                payload = {"bound_node": node, "bound_gpu_uuid": candidate["gpu_uuid"], "target_semantics": "node-total-power", "target_unit": "W", "model_id": f"power-{node}", "status": "validation_required", "range_exceeded": True, "range_warnings": ["reference range exceeded"], "observed_profile_window": {"time_weighted_mean_predicted_power_w": 100 if node == plan["candidates"][0]["node"] else 200}}
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(json.dumps(payload), encoding="utf-8")
            return ""

        with tempfile.TemporaryDirectory() as raw:
            output = Path(raw)
            for item in plan["candidates"]:
                node = item["node"]
                artifacts = output / "artifacts" / node
                telemetry = artifacts / "telemetry"
                telemetry.mkdir(parents=True)
                features = {"schema_version": "pre6g.runtime-features/v1", "node": node, "device_id": item["device_id"]}
                profile = {"schema_version": "pre6g.profile-result/v1", "task_id": plan["run_id"], "node": node, "device_id": item["device_id"], "status": "ready-for-control-side-inference", "detector": {"complete_cycles": 5}, "runtime_features": features}
                (artifacts / "runtime-features.json").write_text(json.dumps(features), encoding="utf-8")
                (artifacts / "profile-result.json").write_text(json.dumps(profile), encoding="utf-8")
                (telemetry / "alignment-quality.json").write_text(json.dumps({"pass": True}), encoding="utf-8")
                start = 1_000_000_000_000
                (telemetry / "application-window.json").write_text(json.dumps({"schema_version": "pre6g.application-window/v1", "command": ["yolo", "detect", "train"], "started_at_unix_ns": start, "finished_at_unix_ns": start + 11_000_000_000}), encoding="utf-8")
                (telemetry / "aligned-telemetry.csv").write_text("timestamp_ns,power\n" + "".join(f"{start + i * 1_000_000_000},100\n" for i in range(12)), encoding="utf-8")
            with patch.object(DRYRUN, "_invoke", side_effect=fake_inference):
                DRYRUN._predict(plan, output)
            ranking_input = json.loads((output / "ranking-input.json").read_text(encoding="utf-8"))
            ranking = json.loads((output / "provisional-ranking.json").read_text(encoding="utf-8"))
            self.assertEqual(len(ranking_input["candidates"]), 2)
            self.assertEqual(ranking["selected_node"], plan["candidates"][0]["node"])


if __name__ == "__main__":
    unittest.main()
