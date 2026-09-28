from __future__ import annotations

import argparse
import json
from pathlib import Path

from pre6g_experiment.runtime_aggregation import (
    RuntimeAggregationError,
    aggregate_runtime,
)


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Combine a frozen per-execution-cycle runtime prediction with "
            "automatic workload discovery and a validated semantic binding."
        )
    )
    parser.add_argument(
        "--runtime-prediction",
        type=Path,
        required=True,
    )
    parser.add_argument(
        "--work-discovery",
        type=Path,
        required=True,
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    runtime_prediction = json.loads(
        args.runtime_prediction.read_text(encoding="utf-8")
    )
    workload_discovery = json.loads(
        args.work_discovery.read_text(encoding="utf-8")
    )

    try:
        result = aggregate_runtime(
            runtime_prediction,
            workload_discovery,
        )
    except RuntimeAggregationError as exc:
        raise SystemExit(str(exc)) from exc

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
