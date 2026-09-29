from __future__ import annotations

import argparse
import csv
import json
import os
import statistics
import subprocess
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DETECTOR_SCRIPT = PROJECT_ROOT / "scripts" / "evaluate_trace_event_periods.py"
FEATURE_SCRIPT = PROJECT_ROOT / "scripts" / "build_runtime_features.py"

FEATURE_NAMES = [
    "log_detected_period_ms",
    "log_kernel_event_rate_hz",
    "log_unique_kernel_count",
    "log_median_kernel_duration_us",
    "log_mean_kernel_duration_us",
    "log_kernel_busy_fraction",
    "anchor_robust_cv",
]


def parse_workloads(text: str) -> list[str]:
    result = [item.strip() for item in text.split(",") if item.strip()]
    if not result:
        raise ValueError("No workloads specified")
    return result


def baseline_label(workload_dir: Path) -> tuple[int, float]:
    values: list[float] = []
    for path in sorted(workload_dir.glob("baseline_*/summary.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        value = payload.get("steady_window_mean_iter_ms")
        if value is not None:
            values.append(float(value))
    if not values:
        raise ValueError(f"No baseline steady_window_mean_iter_ms under {workload_dir}")
    return len(values), float(statistics.median(values))


def run(command: list[str], *, env: dict[str, str] | None = None) -> None:
    print("+", " ".join(command), flush=True)
    subprocess.run(command, check=True, env=env)


def write_csv(path: Path, rows: list[dict], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Reprocess historical YOLO nsys_trace_01 reports with the current "
            "marker-free detector and build same-schema runtime training samples."
        )
    )
    parser.add_argument("--runs-root", type=Path, required=True)
    parser.add_argument("--nsys", type=Path, required=True)
    parser.add_argument("--device-id", required=True)
    parser.add_argument("--node", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument(
        "--workloads",
        default=",".join(f"C{i:02d}" for i in range(1, 25)),
    )
    parser.add_argument("--keep-sqlite", action="store_true")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    runs_root = args.runs_root.resolve()
    nsys = args.nsys.resolve()
    output = args.output_dir.resolve()
    workloads = parse_workloads(args.workloads)

    if not nsys.exists():
        raise FileNotFoundError(f"nsys not found: {nsys}")

    output.mkdir(parents=True, exist_ok=True)

    env = os.environ.copy()
    src = str(PROJECT_ROOT / "src")
    env["PYTHONPATH"] = (
        src
        if not env.get("PYTHONPATH")
        else src + os.pathsep + env["PYTHONPATH"]
    )

    fields = [
        "device_id",
        "node",
        "workload_id",
        "baseline_repeat_count",
        "target_runtime_ms",
        "accepted",
        "detector_status",
        "detected_unit",
        "detector_profile",
        "detected_period_ms",
        "detector_confidence",
        "horizon_seconds",
        "complete_cycles",
        "selection_reason",
        "two_window_stability_pass",
        "harmonic_corrected",
        *FEATURE_NAMES,
    ]
    failure_fields = ["workload_id", "stage", "error"]

    rows: list[dict] = []
    failures: list[dict] = []

    for workload_id in workloads:
        print()
        print("=" * 72)
        print(f"[{workload_id}] historical runtime sample")
        print("=" * 72)

        workload_dir = runs_root / workload_id
        report = workload_dir / "nsys_trace_01" / "profile.nsys-rep"
        sample_dir = output / workload_id
        sqlite_path = sample_dir / "profile.sqlite"
        detector_dir = sample_dir / "detector"
        detection_path = detector_dir / "marker-free-discovery.json"
        feature_path = sample_dir / "runtime-features.json"

        stage = "preflight"
        try:
            if not report.is_file():
                raise FileNotFoundError(f"missing report: {report}")

            repeat_count, target_runtime_ms = baseline_label(workload_dir)
            sample_dir.mkdir(parents=True, exist_ok=True)

            if args.force or not (detection_path.is_file() and feature_path.is_file()):
                stage = "nsys-export"
                sqlite_path.unlink(missing_ok=True)
                run(
                    [
                        str(nsys),
                        "export",
                        "--type=sqlite",
                        f"--output={sqlite_path}",
                        str(report),
                    ]
                )
                if not sqlite_path.is_file() or sqlite_path.stat().st_size == 0:
                    raise RuntimeError("Nsight export did not create a non-empty SQLite")

                stage = "marker-free-detector"
                if detector_dir.exists():
                    for child in detector_dir.iterdir():
                        if child.is_file():
                            child.unlink()
                detector_dir.mkdir(parents=True, exist_ok=True)
                run(
                    [
                        sys.executable,
                        str(DETECTOR_SCRIPT),
                        "--sqlite",
                        str(sqlite_path),
                        "--output-dir",
                        str(detector_dir),
                        "--detector-profile",
                        "yolo-v1",
                    ],
                    env=env,
                )

                stage = "runtime-features"
                run(
                    [
                        sys.executable,
                        str(FEATURE_SCRIPT),
                        "--sqlite",
                        str(sqlite_path),
                        "--detection-json",
                        str(detection_path),
                        "--output",
                        str(feature_path),
                        "--node",
                        args.node,
                        "--device-id",
                        args.device_id,
                        "--workload-id",
                        workload_id,
                    ],
                    env=env,
                )

            stage = "assemble-sample"
            detection = json.loads(detection_path.read_text(encoding="utf-8"))
            features = json.loads(feature_path.read_text(encoding="utf-8"))
            feature_map = features["trace_features"]

            missing = [name for name in FEATURE_NAMES if name not in feature_map]
            if missing:
                raise ValueError(f"runtime feature artifact missing: {missing}")

            policy = features.get("production_input_policy", {})
            forbidden = [
                "uses_iterations_csv",
                "uses_nvtx",
                "uses_callbacks",
                "uses_epoch_labels",
                "uses_batch_labels",
            ]
            if any(bool(policy.get(name)) for name in forbidden):
                raise ValueError("historical sample violates production input policy")

            row = {
                "device_id": args.device_id,
                "node": args.node,
                "workload_id": workload_id,
                "baseline_repeat_count": repeat_count,
                "target_runtime_ms": target_runtime_ms,
                "accepted": bool(detection.get("accepted")),
                "detector_status": detection.get("status"),
                "detected_unit": detection.get("detected_unit"),
                "detector_profile": detection.get("detector_profile"),
                "detected_period_ms": detection.get("detected_period_ms"),
                "detector_confidence": detection.get("confidence"),
                "horizon_seconds": detection.get("horizon_seconds"),
                "complete_cycles": detection.get("complete_cycles"),
                "selection_reason": detection.get("selection_reason"),
                "two_window_stability_pass": bool(
                    detection.get("two_window_stability_pass", False)
                ),
                "harmonic_corrected": bool(
                    detection.get("harmonic_corrected", False)
                ),
            }
            row.update({name: float(feature_map[name]) for name in FEATURE_NAMES})
            rows.append(row)

            print(
                "[OK] "
                f"period={float(row['detected_period_ms']):.6f} ms "
                f"confidence={float(row['detector_confidence']):.3f} "
                f"target={target_runtime_ms:.6f} ms "
                f"selection={row['selection_reason']}"
            )

        except Exception as exc:
            failures.append(
                {
                    "workload_id": workload_id,
                    "stage": stage,
                    "error": str(exc),
                }
            )
            print(f"[FAILED] stage={stage}: {exc}", file=sys.stderr)

        finally:
            if not args.keep_sqlite:
                sqlite_path.unlink(missing_ok=True)

        write_csv(output / "samples.csv", rows, fields)
        write_csv(output / "failures.csv", failures, failure_fields)

    accepted = sum(bool(row["accepted"]) for row in rows)
    stable = sum(bool(row["two_window_stability_pass"]) for row in rows)

    print()
    print("=" * 72)
    print("HISTORICAL RUNTIME SAMPLE BUILD COMPLETE")
    print(f"requested workloads : {len(workloads)}")
    print(f"samples built       : {len(rows)}")
    print(f"detector accepted   : {accepted}")
    print(f"two-window stable   : {stable}")
    print(f"failures            : {len(failures)}")
    print(f"samples.csv         : {output / 'samples.csv'}")
    print(f"failures.csv        : {output / 'failures.csv'}")
    print("=" * 72)

    if failures:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
