import sqlite3
import tempfile
import unittest
from pathlib import Path

from pre6g_experiment.marker_free import (
    YOLO_V1,
    detect,
    load_trace,
)


def build_sqlite(
    path: Path,
    *,
    groups: list[tuple[int, int]] | None = None,
    include_nvtx: bool = False,
) -> None:
    groups = groups or [(1001, 1)]
    connection = sqlite3.connect(path)
    try:
        connection.execute(
            """
            CREATE TABLE CUPTI_ACTIVITY_KIND_KERNEL (
                start INTEGER,
                end INTEGER,
                shortName INTEGER,
                globalPid INTEGER,
                contextId INTEGER,
                streamId INTEGER
            )
            """
        )
        connection.execute(
            "CREATE TABLE StringIds (id INTEGER, value TEXT)"
        )
        connection.executemany(
            "INSERT INTO StringIds VALUES (?, ?)",
            [(11, "kernel_a"), (12, "kernel_b")],
        )

        for group_index, (pid, context_id) in enumerate(groups):
            base = group_index * 5_000_000
            for cycle in range(75):
                cycle_start = base + cycle * 100_000_000
                connection.execute(
                    """
                    INSERT INTO CUPTI_ACTIVITY_KIND_KERNEL
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        cycle_start,
                        cycle_start + 50_000,
                        11,
                        pid,
                        context_id,
                        7,
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO CUPTI_ACTIVITY_KIND_KERNEL
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        cycle_start + 20_000_000,
                        cycle_start + 20_050_000,
                        12,
                        pid,
                        context_id,
                        7,
                    ),
                )

        if include_nvtx:
            connection.execute(
                "CREATE TABLE NVTX_EVENTS (text TEXT, start INTEGER)"
            )
            for cycle in range(75):
                connection.execute(
                    "INSERT INTO NVTX_EVENTS VALUES (?, ?)",
                    (
                        f"TRAIN_ITER_{cycle + 1:04d}",
                        cycle * 100_000_000,
                    ),
                )
        connection.commit()
    finally:
        connection.close()


class MarkerFreeTests(unittest.TestCase):
    def test_nvtx_is_not_required(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "trace.sqlite"
            build_sqlite(path, include_nvtx=False)

            trace = load_trace(path)

            self.assertFalse(trace["nvtx_available"])
            self.assertEqual(trace["target"]["global_pid"], 1001)
            self.assertEqual(trace["target"]["context_id"], 1)
            self.assertGreater(len(trace["events"]), 0)

    def test_multiple_groups_fail_closed_without_explicit_target(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "trace.sqlite"
            build_sqlite(
                path,
                groups=[(1001, 1), (2002, 2)],
            )

            with self.assertRaisesRegex(
                ValueError,
                "Multiple CUDA process/context groups",
            ):
                load_trace(path)

            trace = load_trace(
                path,
                global_pid=2002,
                context_id=2,
            )
            self.assertEqual(trace["target"]["global_pid"], 2002)
            self.assertEqual(trace["target"]["context_id"], 2)

    def test_yolo_v1_detects_execution_cycle(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "trace.sqlite"
            build_sqlite(path)

            trace = load_trace(path)
            result = detect(
                trace["events"],
                trace["names"],
                7,
                YOLO_V1,
            )

            self.assertTrue(result["accepted"])
            self.assertEqual(
                result["detected_unit"],
                "execution_cycle",
            )
            self.assertEqual(
                result["detector_profile"],
                "yolo-v1",
            )
            self.assertAlmostEqual(
                result["detected_period_ms"],
                100.0,
                delta=1.0,
            )
            self.assertGreaterEqual(result["complete_cycles"], 3)

    def test_optional_nvtx_audit_is_loaded_only_when_requested(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "trace.sqlite"
            build_sqlite(path, include_nvtx=True)

            production = load_trace(path)
            audited = load_trace(
                path,
                include_nvtx_audit=True,
            )

            self.assertEqual(production["nvtx"], [])
            self.assertGreater(len(audited["nvtx"]), 0)


if __name__ == "__main__":
    unittest.main()
