from __future__ import annotations

import argparse
import json
from pathlib import Path

from pre6g_experiment.runtime_features import extract_trace_features


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Build the production runtime-features.json artifact from "
            "a marker-free detection and target-process Nsight SQLite."
        )
    )
    parser.add_argument("--sqlite", type=Path, required=True)
    parser.add_argument(
        "--detection-json",
        type=Path,
        required=True,
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--node", required=True)
    parser.add_argument("--device-id", required=True)
    parser.add_argument("--workload-id")
    args = parser.parse_args()

    detection = json.loads(
        args.detection_json.read_text(encoding="utf-8")
    )

    if detection.get("status") != "detected":
        raise SystemExit(
            "marker-free detection status must be 'detected'"
        )

    if not detection.get("target_process_identified"):
        raise SystemExit(
            "target CUDA process/context was not identified"
        )

    if detection.get("production_input_policy", {}).get(
        "uses_iterations_csv"
    ):
        raise SystemExit(
            "production runtime features may not depend on iterations.csv"
        )

    if detection.get("production_input_policy", {}).get(
        "uses_nvtx"
    ):
        raise SystemExit(
            "production runtime features may not depend on NVTX"
        )

    feature_input = dict(detection)
    feature_input["horizon_seconds"] = float(
        detection["horizon_seconds"]
    )

    features = extract_trace_features(
        args.sqlite,
        feature_input,
        global_pid=int(detection["target_global_pid"]),
        context_id=int(detection["target_context_id"]),
    )

    artifact = {
        "schema_version": "pre6g.runtime-features/v1",
        "node": args.node,
        "device_id": args.device_id,
        "workload_id": args.workload_id,
        "detected_unit": detection["detected_unit"],
        "detector_profile": detection["detector_profile"],
        "detected_period_ms": detection["detected_period_ms"],
        "detector_confidence": detection["confidence"],
        "target_global_pid": detection["target_global_pid"],
        "target_context_id": detection["target_context_id"],
        "feature_names": list(features["features"].keys()),
        "feature_vector": features["feature_vector"],
        "trace_features": features["features"],
        "trace_summary": {
            "kernel_event_count": features["kernel_event_count"],
            "kernel_event_rate_hz": features["kernel_event_rate_hz"],
            "unique_kernel_count": features["unique_kernel_count"],
            "median_kernel_duration_us": features[
                "median_kernel_duration_us"
            ],
            "mean_kernel_duration_us": features[
                "mean_kernel_duration_us"
            ],
            "kernel_busy_fraction": features[
                "kernel_busy_fraction"
            ],
        },
        "production_input_policy": {
            "uses_iterations_csv": False,
            "uses_nvtx": False,
            "uses_callbacks": False,
            "uses_epoch_labels": False,
            "uses_batch_labels": False,
        },
    }

    args.output.parent.mkdir(
        parents=True,
        exist_ok=True,
    )
    args.output.write_text(
        json.dumps(
            artifact,
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    print(
        json.dumps(
            artifact,
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
