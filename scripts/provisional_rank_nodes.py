from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def rank_candidates(payload: dict[str, Any]) -> dict[str, Any]:
    total_work_units = int(payload["total_work_units"])
    if total_work_units <= 0:
        raise ValueError("total_work_units must be positive")

    candidates = payload.get("candidates") or []
    if len(candidates) < 2:
        raise ValueError("at least two candidates are required")

    ranked: list[dict[str, Any]] = []

    for candidate in candidates:
        node = str(candidate["node"])
        runtime = candidate["runtime"]
        power = candidate["power"]

        runtime_ms = float(runtime["predicted_runtime_ms_per_work_unit"])
        steady_power_w = float(power["predicted_node_total_steady_power_w"])

        if runtime_ms <= 0:
            raise ValueError(f"{node}: runtime prediction must be positive")
        if steady_power_w < 0:
            raise ValueError(f"{node}: power prediction must be non-negative")

        runtime_s = runtime_ms * total_work_units / 1000.0
        energy_per_work_unit_j = steady_power_w * runtime_ms / 1000.0
        steady_energy_j = steady_power_w * runtime_s

        ranked.append(
            {
                "node": node,
                "device_id": candidate.get("device_id"),
                "runtime_model_id": runtime.get("model_id"),
                "power_model_id": power.get("model_id"),
                "predicted_runtime_ms_per_work_unit": runtime_ms,
                "total_work_units": total_work_units,
                "predicted_steady_runtime_s": runtime_s,
                "predicted_node_total_steady_power_w": steady_power_w,
                "energy_j_per_work_unit": energy_per_work_unit_j,
                "predicted_steady_gross_energy_j": steady_energy_j,
                "predicted_steady_gross_energy_kj": steady_energy_j / 1000.0,
                "predicted_steady_gross_energy_wh": steady_energy_j / 3600.0,
                "power_status": power.get("status"),
                "power_range_exceeded": bool(power.get("range_exceeded", False)),
                "power_range_detail": power.get("range_detail"),
            }
        )

    ranked.sort(
        key=lambda item: (
            item["predicted_steady_gross_energy_j"],
            item["node"],
        )
    )

    for index, item in enumerate(ranked, start=1):
        item["rank"] = index

    selected = ranked[0]
    runner_up = ranked[1]
    reduction_percent = (
        (
            runner_up["predicted_steady_gross_energy_j"]
            - selected["predicted_steady_gross_energy_j"]
        )
        / runner_up["predicted_steady_gross_energy_j"]
        * 100.0
    )

    return {
        "schema_version": "pre6g.provisional-ranking-result/v1",
        "task_id": payload.get("task_id"),
        "ranking_mode": "research-provisional-model-output",
        "production_ready": False,
        "selection_basis": "minimum predicted steady gross node energy",
        "energy_formula": (
            "predicted_node_total_steady_power_w * "
            "predicted_runtime_ms_per_work_unit / 1000 * total_work_units"
        ),
        "work_unit": payload.get("work_unit"),
        "total_work_units": total_work_units,
        "selected_node": selected["node"],
        "selected_device_id": selected.get("device_id"),
        "selected_predicted_steady_gross_energy_j": selected[
            "predicted_steady_gross_energy_j"
        ],
        "energy_reduction_vs_runner_up_percent": reduction_percent,
        "ranked": ranked,
        "limitations": [
            "This path intentionally uses the current model predictions even when power bundles are validation_required.",
            "Power scaler-range warnings are preserved as diagnostics and do not block this ranking test.",
            "predicted steady runtime is not whole-job runtime.",
            "predicted steady gross energy is not whole-job energy.",
            "The strict production readiness gate in decision.py is not modified.",
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Research-only cross-node energy ranking using the current model "
            "outputs without overriding the strict production gate."
        )
    )
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    payload = json.loads(args.input.read_text(encoding="utf-8"))
    result = rank_candidates(payload)
    rendered = json.dumps(result, ensure_ascii=False, indent=2) + "\n"

    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")

    print(rendered, end="")


if __name__ == "__main__":
    main()
