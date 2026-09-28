from __future__ import annotations

import argparse
import json
from pathlib import Path


def load_json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Package worker-side marker-free artifacts into one "
            "ProfileResult for controller/artifact-store transport."
        )
    )
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--node", required=True)
    parser.add_argument("--device-id", required=True)
    parser.add_argument(
        "--detection-json",
        type=Path,
        required=True,
    )
    parser.add_argument(
        "--runtime-features-json",
        type=Path,
        required=True,
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    detection = load_json(args.detection_json)
    features = load_json(args.runtime_features_json)

    if detection.get("status") != "detected":
        raise SystemExit("cannot package a rejected detector result")

    if features.get("schema_version") != "pre6g.runtime-features/v1":
        raise SystemExit("unexpected runtime feature schema")

    if features.get("node") != args.node:
        raise SystemExit("runtime feature node does not match --node")

    if features.get("device_id") != args.device_id:
        raise SystemExit(
            "runtime feature device_id does not match --device-id"
        )

    payload = {
        "schema_version": "pre6g.profile-result/v1",
        "task_id": args.task_id,
        "node": args.node,
        "device_id": args.device_id,
        "status": "ready-for-control-side-inference",
        "detector": {
            "detected_unit": detection["detected_unit"],
            "detector_profile": detection["detector_profile"],
            "detected_period_ms": detection["detected_period_ms"],
            "confidence": detection["confidence"],
            "complete_cycles": detection["complete_cycles"],
            "target_global_pid": detection["target_global_pid"],
            "target_context_id": detection["target_context_id"],
        },
        "runtime_features": features,
        "transport_contract": {
            "preferred": "shared-artifact-store",
            "scp": "smoke-test-only",
            "path_template": (
                "results/<task_id>/<node>/profile-result.json"
            ),
        },
    }

    args.output.parent.mkdir(
        parents=True,
        exist_ok=True,
    )
    args.output.write_text(
        json.dumps(
            payload,
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    print(
        json.dumps(
            payload,
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
