from __future__ import annotations

import argparse
import json
from pathlib import Path

import yaml

from pre6g_experiment.work_discovery import (
    WorkDiscoveryError,
    discover_work,
)


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Discover total workload amount from the original Job/config and "
            "mounted dataset without iteration markers."
        )
    )
    parser.add_argument("--job", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    payload = yaml.safe_load(args.job.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise SystemExit("Job YAML must contain one mapping")
    if payload.get("apiVersion") != "batch/v1" or payload.get("kind") != "Job":
        raise SystemExit("Only batch/v1 Job is supported")

    try:
        result = discover_work(payload)
    except WorkDiscoveryError as exc:
        raise SystemExit(str(exc)) from exc

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
