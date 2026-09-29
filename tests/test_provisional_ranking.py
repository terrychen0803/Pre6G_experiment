import json
import unittest
from pathlib import Path
import importlib.util


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "provisional_rank_nodes.py"
SPEC = importlib.util.spec_from_file_location("provisional_rank_nodes", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


class ProvisionalRankingTests(unittest.TestCase):
    def test_formal_cross_node_selection(self):
        payload = json.loads(
            (
                ROOT
                / "docs"
                / "evidence"
                / "formal-cross-node-ranking-input.json"
            ).read_text(encoding="utf-8")
        )

        result = MODULE.rank_candidates(payload)

        self.assertFalse(result["production_ready"])
        self.assertEqual(result["selected_node"], "iccl-s3-251230")
        self.assertEqual(result["selected_device_id"], "RTX4090")

        ranked = result["ranked"]
        self.assertEqual([row["rank"] for row in ranked], [1, 2])
        self.assertEqual(ranked[0]["node"], "iccl-s3-251230")
        self.assertEqual(ranked[1]["node"], "mirc516-20250605")

        self.assertAlmostEqual(
            ranked[0]["predicted_steady_gross_energy_j"],
            21538.59388767719,
            places=6,
        )
        self.assertAlmostEqual(
            ranked[1]["predicted_steady_gross_energy_j"],
            57148.153785304705,
            places=6,
        )
        self.assertAlmostEqual(
            result["energy_reduction_vs_runner_up_percent"],
            62.31095414106675,
            places=6,
        )

        self.assertTrue(ranked[0]["power_ood"])
        self.assertTrue(ranked[1]["power_ood"])


if __name__ == "__main__":
    unittest.main()
