from __future__ import annotations

import importlib.util
import json
import unittest
from pathlib import Path

from pre6g_experiment.full_job_eta import estimate_full_job_eta


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("provisional_rank_nodes", ROOT / "scripts/provisional_rank_nodes.py")
assert SPEC and SPEC.loader
RANKER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(RANKER)


def eta_input() -> dict:
    return {
        "schema_version": "pre6g.full-job-eta-input/v1",
        "work_unit": "training_iteration",
        "plan": {"total_work_units": 10, "warmup_work_units": 2,
                 "validation_runs": 0, "checkpoint_writes": 0},
        "nodes": {"gpu-a": {
            "dry_run": {"startup_s": 2, "warmup_work_unit_s": 0.2,
                        "steady_work_unit_s": 999},
            "calibration": {"finalization_s": 0.3},
            "calibration_source_runs": [{"run_id": "previous-full-001", "completed_naturally": True}],
        }},
    }


class FullJobEtaTests(unittest.TestCase):
    def test_training_only_complete_eta_uses_trace_steady(self):
        result = estimate_full_job_eta(10, 100, eta_input(), "gpu-a")
        self.assertEqual(result["status"], "ready")
        self.assertAlmostEqual(result["predicted_total_job_runtime_s"], 3.5)
        self.assertEqual(result["phase_sources"]["steady_work_unit_s"], "trace_runtime_model")
        self.assertEqual(result["phase_sources"]["validation_s"], "not_scheduled")

    def test_missing_phase_is_explicit(self):
        inputs = eta_input()
        del inputs["nodes"]["gpu-a"]["calibration"]
        result = estimate_full_job_eta(10, 100, inputs, "gpu-a")
        self.assertEqual(result["status"], "incomplete")
        self.assertIsNone(result["predicted_total_job_runtime_s"])
        self.assertEqual(result["missing_phases"], ["finalization_s"])

    def test_plan_and_calibration_provenance_are_checked(self):
        inputs = eta_input()
        inputs["plan"]["total_work_units"] = 11
        with self.assertRaisesRegex(ValueError, "differs"):
            estimate_full_job_eta(10, 100, inputs, "gpu-a")
        inputs = eta_input()
        inputs["nodes"]["gpu-a"]["calibration_source_runs"][0]["completed_naturally"] = False
        with self.assertRaisesRegex(ValueError, "naturally completed"):
            estimate_full_job_eta(10, 100, inputs, "gpu-a")

    def test_current_provisional_ranker_emits_eta_without_changing_energy_selection(self):
        payload = json.loads((ROOT / "docs/evidence/formal-cross-node-ranking-input.json").read_text(encoding="utf-8"))
        baseline = RANKER.rank_candidates(payload)
        self.assertEqual(baseline["ranked"][0]["total_job_runtime_status"], "incomplete")
        self.assertIsNone(baseline["ranked"][0]["predicted_total_job_runtime_s"])
        inputs = eta_input()
        inputs["plan"]["total_work_units"] = payload["total_work_units"]
        inputs["nodes"] = {
            row["node"]: {"dry_run": {"startup_s": 2, "warmup_work_unit_s": 0.2},
                          "calibration": {"finalization_s": 0.3},
                          "calibration_source_runs": [{"run_id": "older-full-job", "completed_naturally": True}]}
            for row in payload["candidates"]
        }
        payload["full_job_eta"] = inputs
        ranked = RANKER.rank_candidates(payload)
        self.assertEqual(ranked["selected_node"], baseline["selected_node"])
        self.assertEqual(ranked["ranked"][0]["total_job_runtime_status"], "ready")
        self.assertGreater(ranked["ranked"][0]["predicted_total_job_runtime_s"], 0)


if __name__ == "__main__":
    unittest.main()
