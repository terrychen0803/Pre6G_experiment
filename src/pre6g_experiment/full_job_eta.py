"""Optional full trainer ETA on top of the trace model's steady work-unit time.

The dry-run detector supplies only steady work-unit runtime. Other durations
come from application telemetry or prior naturally completed training jobs.
"""

from __future__ import annotations

import math
from typing import Any


PHASES = (
    "startup_s", "warmup_work_unit_s", "steady_work_unit_s",
    "validation_s", "checkpoint_s", "finalization_s",
)
PLAN_FIELDS = (
    "total_work_units", "warmup_work_units", "validation_runs", "checkpoint_writes",
)


def _nonnegative_int(value: Any, name: str) -> int:
    if isinstance(value, bool) or isinstance(value, float) and not value.is_integer():
        raise ValueError(f"{name} must be a nonnegative integer")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a nonnegative integer") from exc
    if parsed < 0:
        raise ValueError(f"{name} must be a nonnegative integer")
    return parsed


def _duration(value: Any, name: str) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a finite nonnegative duration") from exc
    if not math.isfinite(parsed) or parsed < 0:
        raise ValueError(f"{name} must be a finite nonnegative duration")
    return parsed


def estimate_full_job_eta(
    total_work_units: int,
    steady_runtime_ms_per_work_unit: float,
    eta_input: dict[str, Any] | None,
    node: str,
) -> dict[str, Any]:
    """Compute ETA or explain exactly which plan/duration inputs are absent."""
    if eta_input is None:
        return {"status": "incomplete", "predicted_total_job_runtime_s": None,
                "missing_plan_fields": list(PLAN_FIELDS[1:]), "missing_phases": [],
                "components_s": {}, "phase_sources": {}}
    if eta_input.get("schema_version") != "pre6g.full-job-eta-input/v1":
        raise ValueError("unsupported full_job_eta schema_version")
    if eta_input.get("work_unit") != "training_iteration":
        raise ValueError("full_job_eta currently supports training_iteration only")
    plan = eta_input.get("plan") or {}
    if not isinstance(plan, dict):
        raise ValueError("full_job_eta.plan must be an object")
    missing_plan = [name for name in PLAN_FIELDS if plan.get(name) is None]
    if missing_plan:
        return {"status": "incomplete", "predicted_total_job_runtime_s": None,
                "missing_plan_fields": missing_plan, "missing_phases": [],
                "components_s": {}, "phase_sources": {}}
    counts = {name: _nonnegative_int(plan[name], name) for name in PLAN_FIELDS}
    if counts["total_work_units"] != total_work_units:
        raise ValueError("full_job_eta total_work_units differs from ranking input")
    if counts["total_work_units"] == 0 or counts["warmup_work_units"] > total_work_units:
        raise ValueError("invalid full_job_eta total/warmup work units")
    nodes = eta_input.get("nodes") or {}
    if not isinstance(nodes, dict):
        raise ValueError("full_job_eta.nodes must be an object")
    node_data = nodes.get(node) or {}
    if not isinstance(node_data, dict):
        raise ValueError(f"full_job_eta.nodes[{node}] must be an object")
    observed = node_data.get("dry_run") or {}
    calibration = node_data.get("calibration") or {}
    if not isinstance(observed, dict) or not isinstance(calibration, dict):
        raise ValueError(f"{node}: dry_run and calibration must be objects")
    if calibration:
        sources = node_data.get("calibration_source_runs")
        if not isinstance(sources, list) or not sources or any(
            not isinstance(row, dict) or not row.get("run_id")
            or row.get("completed_naturally") is not True for row in sources
        ):
            raise ValueError(f"{node}: calibration requires naturally completed source run IDs")
    phase_counts = {
        "startup_s": 1,
        "warmup_work_unit_s": counts["warmup_work_units"],
        "steady_work_unit_s": total_work_units - counts["warmup_work_units"],
        "validation_s": counts["validation_runs"],
        "checkpoint_s": counts["checkpoint_writes"],
        "finalization_s": 1,
    }
    components: dict[str, float] = {}
    phase_sources: dict[str, str] = {}
    missing_phases: list[str] = []
    for phase in PHASES:
        if phase_counts[phase] == 0:
            components[phase], phase_sources[phase] = 0.0, "not_scheduled"
            continue
        if phase == "steady_work_unit_s":
            value = _duration(steady_runtime_ms_per_work_unit, phase) / 1000
            source = "trace_runtime_model"
        elif observed.get(phase) is not None:
            value, source = _duration(observed[phase], phase), "dry_run"
        elif calibration.get(phase) is not None:
            value, source = _duration(calibration[phase], phase), "prior_full_jobs"
        else:
            missing_phases.append(phase)
            continue
        components[phase] = phase_counts[phase] * value
        phase_sources[phase] = source
    total_known = sum(components.values())
    return {
        "status": "ready" if not missing_phases else "incomplete",
        "predicted_total_job_runtime_s": total_known if not missing_phases else None,
        "known_component_s": total_known,
        "missing_plan_fields": [], "missing_phases": missing_phases,
        "phase_counts": phase_counts,
        "components_s": components, "phase_sources": phase_sources,
    }
