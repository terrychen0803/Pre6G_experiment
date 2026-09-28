from __future__ import annotations

from typing import Any


class SemanticBindingError(ValueError):
    pass


def resolve_semantic_binding(
    workload: dict[str, Any],
    runtime_prediction: dict[str, Any],
) -> dict[str, Any]:
    """
    Resolve detector-level execution_cycle semantics to an application work unit.

    This registry is intentionally fail-closed. The only production binding
    currently validated is the YOLO adapter with detector profile yolo-v1:
    one detected execution_cycle corresponds to one training_iteration.
    """
    adapter = str(workload.get("adapter", ""))
    workload_family = str(workload.get("workload_family", ""))
    work = workload.get("work") or {}
    work_unit = work.get("unit")
    detector_profile = runtime_prediction.get("detector_profile")
    detected_unit = runtime_prediction.get("detected_unit")

    if detected_unit != "execution_cycle":
        raise SemanticBindingError(
            f"unsupported detected unit: {detected_unit!r}"
        )

    if (
        adapter == "yolo"
        and work_unit == "training_iteration"
        and detector_profile == "yolo-v1"
    ):
        return {
            "schema_version": "pre6g.semantic-binding/v1",
            "status": "bound",
            "workload_family": workload_family,
            "adapter": adapter,
            "detector_profile": detector_profile,
            "detected_unit": "execution_cycle",
            "work_unit": "training_iteration",
            "cycles_per_work_unit": 1.0,
            "source": "validated-yolo-v1-offline-evidence",
            "limitations": [
                "validated YOLO26 fixture family only",
                "must not be reused for other workload adapters",
            ],
        }

    raise SemanticBindingError(
        "no validated semantic binding for "
        f"adapter={adapter!r}, work_unit={work_unit!r}, "
        f"detector_profile={detector_profile!r}"
    )
