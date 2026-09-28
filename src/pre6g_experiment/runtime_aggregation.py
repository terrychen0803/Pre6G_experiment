from __future__ import annotations

import math
from typing import Any

from .semantic_binding import (
    SemanticBindingError,
    resolve_semantic_binding,
)


class RuntimeAggregationError(ValueError):
    pass


def aggregate_runtime(
    runtime_prediction: dict[str, Any],
    workload_discovery: dict[str, Any],
) -> dict[str, Any]:
    work = workload_discovery.get("work") or {}
    total_units = work.get("total_units")
    if total_units is None:
        raise RuntimeAggregationError(
            "total work units are unknown; total runtime extrapolation is not allowed"
        )

    try:
        total_units_i = int(total_units)
    except (TypeError, ValueError) as exc:
        raise RuntimeAggregationError(
            f"invalid total work units: {total_units!r}"
        ) from exc

    if total_units_i <= 0:
        raise RuntimeAggregationError(
            f"total work units must be positive: {total_units_i}"
        )

    try:
        binding = resolve_semantic_binding(
            workload_discovery,
            runtime_prediction,
        )
    except SemanticBindingError as exc:
        raise RuntimeAggregationError(str(exc)) from exc

    try:
        runtime_ms_per_cycle = float(
            runtime_prediction["predicted_runtime_ms"]
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise RuntimeAggregationError(
            "runtime prediction is missing a valid predicted_runtime_ms"
        ) from exc

    if (
        not math.isfinite(runtime_ms_per_cycle)
        or runtime_ms_per_cycle <= 0.0
    ):
        raise RuntimeAggregationError(
            f"invalid per-cycle runtime prediction: {runtime_ms_per_cycle}"
        )

    cycles_per_work_unit = float(binding["cycles_per_work_unit"])
    runtime_ms_per_work_unit = (
        runtime_ms_per_cycle * cycles_per_work_unit
    )
    steady_runtime_s = (
        runtime_ms_per_work_unit * total_units_i / 1000.0
    )

    return {
        "schema_version": "pre6g.semantic-runtime/v1",
        "node": runtime_prediction.get("node"),
        "device_id": runtime_prediction.get("device_id"),
        "model_id": runtime_prediction.get("model_id"),
        "model_role": runtime_prediction.get("model_role"),
        "detector_profile": runtime_prediction.get("detector_profile"),
        "detector_confidence": runtime_prediction.get(
            "detector_confidence"
        ),
        "detected_unit": runtime_prediction.get("detected_unit"),
        "predicted_runtime_ms_per_execution_cycle": (
            runtime_ms_per_cycle
        ),
        "semantic_binding": binding,
        "workload_family": workload_discovery.get("workload_family"),
        "work_unit": binding["work_unit"],
        "predicted_runtime_ms_per_work_unit": (
            runtime_ms_per_work_unit
        ),
        "total_work_units": total_units_i,
        "predicted_steady_runtime_s": steady_runtime_s,
        "predicted_total_job_runtime_s": None,
        "total_job_runtime_status": (
            "pending-non-steady-overhead-model"
        ),
        "aggregation_scope": "steady-work-only",
    }
