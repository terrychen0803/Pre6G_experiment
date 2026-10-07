from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]


def script(name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


PLANNER = script("plan_validation_runs")
EVALUATOR = script("evaluate_validation_runs")
RANKER = script("provisional_rank_nodes")


class ValidationRunTests(unittest.TestCase):
    def test_fixed_plan_carries_full_eta_and_evaluator_scores_completed_trainer(self):
        job = yaml.safe_load((ROOT / "examples/yolo26/formal-40min-source-job.yaml").read_text(encoding="utf-8"))
        ranking = json.loads(json.dumps(self.ranking))
        ranking["total_work_units"] = 52448
        for row in ranking["ranked"]:
            row["predicted_total_job_runtime_s"] = 600.0
            row["total_job_runtime_status"] = "ready"
            row["full_job_eta"] = {"status": "ready", "phase_counts": {"validation_s": 0, "checkpoint_s": 0}}
        discovery = {"adapter": "yolo", "work": {"unit": "training_iteration", "steps_per_epoch": 32, "total_units": 52448}}
        plan, _ = PLANNER.plan(job, ranking, discovery, validation_id="formal-eta-001", target_minutes=40)
        self.assertTrue(all(row["predicted_total_job_runtime_s"] == 600 for row in plan["jobs"]))
        pods = {"items": [{
            "metadata": {"name": f"pod-{row['node']}", "labels": {
                "pre6g.io/validation-id": "formal-eta-001",
                "pre6g.io/candidate-node": row["node"], "job-name": row["job_name"]}},
            "status": {"phase": "Succeeded", "containerStatuses": [{"name": "trainer", "state": {
                "terminated": {"startedAt": "2026-10-01T00:00:00Z", "finishedAt": "2026-10-01T00:10:00Z", "exitCode": 0}}}]},
        } for row in plan["jobs"]]}
        result = EVALUATOR.evaluate(plan, pods, {}, timestamp_column="timestamp", power_column="power_w", interval_position="end")
        self.assertTrue(all(row["full_job_runtime_error_percent"] == 0 for row in result["nodes"]))

    def setUp(self):
        self.job = yaml.safe_load((ROOT / "examples/yolo26/validation-source-job.yaml").read_text(encoding="utf-8"))
        ranking_input = json.loads((ROOT / "docs/evidence/formal-cross-node-ranking-input.json").read_text(encoding="utf-8"))
        self.ranking = RANKER.rank_candidates(ranking_input)
        self.discovery = {
            "adapter": "yolo",
            "work": {"unit": "training_iteration", "steps_per_epoch": 32, "total_units": 960},
        }

    def test_plan_preserves_workload_and_pins_all_candidates(self):
        result, jobs = PLANNER.plan(self.job, self.ranking, self.discovery, validation_id="test-001", target_minutes=40)
        self.assertEqual(result["selected_node"], "iccl-s3-251230")
        self.assertEqual(len(jobs), 2)
        self.assertGreater(result["planned_epochs"], 30)
        for row, job in zip(result["jobs"], jobs):
            self.assertEqual(job["spec"]["template"]["spec"]["nodeSelector"]["kubernetes.io/hostname"], row["node"])
            self.assertIn(f"epochs={result['planned_epochs']}", job["spec"]["template"]["spec"]["containers"][0]["args"])
            self.assertEqual(job["spec"]["template"]["spec"]["containers"][0]["image"], "ultralytics/ultralytics:8.4.104")
        self.assertNotIn("nodeSelector", self.job["spec"]["template"]["spec"])

    def test_fixed_formal_workload_is_not_rescaled_after_prediction(self):
        job = yaml.safe_load((ROOT / "examples/yolo26/formal-40min-source-job.yaml").read_text(encoding="utf-8"))
        ranking = json.loads(json.dumps(self.ranking))
        ranking["total_work_units"] = 52448
        discovery = {
            "adapter": "yolo",
            "work": {"unit": "training_iteration", "steps_per_epoch": 32, "total_units": 52448},
        }
        result, jobs = PLANNER.plan(job, ranking, discovery, validation_id="formal-test-001", target_minutes=40)
        self.assertEqual(result["planning_mode"], "fixed-source-workload")
        self.assertTrue(result["source_workload_fixed"])
        self.assertEqual(result["planned_epochs"], 1639)
        self.assertEqual(result["planned_total_work_units"], 52448)
        for rendered in jobs:
            args = rendered["spec"]["template"]["spec"]["containers"][0]["args"]
            self.assertIn("epochs=1639", args)
            self.assertIn("val=False", args)
            self.assertIn("save=False", args)
            self.assertIn("patience=0", args)

    def test_discovery_mismatch_fails(self):
        self.discovery["work"]["total_units"] = 100
        with self.assertRaisesRegex(ValueError, "must match"):
            PLANNER.plan(self.job, self.ranking, self.discovery, validation_id="test-001", target_minutes=40)

    def test_five_minute_average_uses_partial_boundaries(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "pdu.csv"
            path.write_text("timestamp,power_w\n2026-10-01T00:05:00Z,100\n2026-10-01T00:10:00Z,200\n", encoding="utf-8")
            result = EVALUATOR.pdu_energy(
                path,
                start=EVALUATOR.timestamp("2026-10-01T00:02:00Z"),
                end=EVALUATOR.timestamp("2026-10-01T00:08:00Z"),
                timestamp_column="timestamp",
                power_column="power_w",
                interval_position="end",
            )
            self.assertAlmostEqual(result["gross_energy_wh"], 15.0)
            self.assertEqual(result["sample_count_used"], 2)

    def test_pdu_gap_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "pdu.csv"
            path.write_text("timestamp,power_w\n2026-10-01T00:05:00Z,100\n2026-10-01T00:15:00Z,200\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "gap"):
                EVALUATOR.pdu_energy(
                    path,
                    start=EVALUATOR.timestamp("2026-10-01T00:00:00Z"),
                    end=EVALUATOR.timestamp("2026-10-01T00:12:00Z"),
                    timestamp_column="timestamp",
                    power_column="power_w",
                    interval_position="end",
                )

    def test_evaluation_compares_all_successful_nodes(self):
        plan, _ = PLANNER.plan(self.job, self.ranking, self.discovery, validation_id="test-001", target_minutes=40)
        pods = {"items": []}
        with tempfile.TemporaryDirectory() as tmp:
            paths = {}
            for row, watts in zip(plan["jobs"], (100, 200)):
                node = row["node"]
                pods["items"].append({
                    "metadata": {"name": f"pod-{node}", "labels": {
                        "pre6g.io/validation-id": "test-001",
                        "pre6g.io/candidate-node": node,
                        "job-name": row["job_name"],
                    }},
                    "status": {"phase": "Succeeded", "containerStatuses": [{
                        "name": "trainer",
                        "state": {"terminated": {"startedAt": "2026-10-01T00:00:00Z", "finishedAt": "2026-10-01T00:10:00Z", "exitCode": 0}},
                    }]},
                })
                path = Path(tmp) / f"{node}.csv"
                path.write_text(f"timestamp,power_w\n2026-10-01T00:05:00Z,{watts}\n2026-10-01T00:10:00Z,{watts}\n", encoding="utf-8")
                paths[node] = path
            result = EVALUATOR.evaluate(plan, pods, paths, timestamp_column="timestamp", power_column="power_w", interval_position="end")
            self.assertTrue(result["evaluation_complete"])
            self.assertEqual(result["actual_lowest_gross_energy_node"], plan["selected_node"])
            self.assertTrue(result["selection_match"])


if __name__ == "__main__":
    unittest.main()
