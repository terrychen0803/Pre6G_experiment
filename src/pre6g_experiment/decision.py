from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class RankedNode:
    node: str
    work_unit: str | None
    runtime_ms_per_work_unit: float
    energy_j_per_work_unit: float
    total_runtime_s: float | None
    total_energy_j: float | None
    score: float


SHARED_STRATEGIES = {"time-slicing", "mps", "mig-time-slicing"}
SUPPORTED_POWER_TARGET_SEMANTICS = {"node-total-power"}


def integrate_power_samples(samples: list[dict[str, Any]]) -> float:
    """Integrate timestamped instantaneous power predictions with trapezoids."""
    ordered = sorted(samples, key=lambda row: int(row["timestamp_unix_ns"]))
    if len(ordered) < 2:
        raise ValueError("At least two timestamped power samples are required")
    energy_j = 0.0
    for left, right in zip(ordered, ordered[1:]):
        delta_s = (
            int(right["timestamp_unix_ns"]) - int(left["timestamp_unix_ns"])
        ) / 1e9
        if delta_s <= 0:
            raise ValueError("Power sample timestamps must be strictly increasing")
        left_w = float(left["predicted_power_w"])
        right_w = float(right["predicted_power_w"])
        if left_w < 0 or right_w < 0:
            raise ValueError("Power predictions must be non-negative")
        energy_j += (left_w + right_w) * 0.5 * delta_s
    return energy_j


def _runtime_prediction(runtime: dict[str, Any]) -> tuple[float, str | None]:
    if "predicted_runtime_ms_per_work_unit" in runtime:
        value = float(runtime["predicted_runtime_ms_per_work_unit"])
        return value, runtime.get("work_unit")

    if "predicted_runtime_ms_per_iteration" in runtime:
        value = float(runtime["predicted_runtime_ms_per_iteration"])
        return value, runtime.get("work_unit", "training_iteration")

    return -1.0, runtime.get("work_unit")


def _reject_reason(
    item: dict[str, Any],
    min_confidence: float,
    expected_work_unit: str | None,
) -> str | None:
    node = str(item.get("node", "<unknown>"))
    runtime = item.get("runtime") or {}
    power = item.get("power") or {}
    quality = item.get("quality") or {}
    sharing = item.get("gpu_sharing") or {}

    if not item.get("eligible", False):
        return "node marked ineligible"

    if runtime.get("status") != "ready" or power.get("status") != "ready":
        return "runtime or power model is not ready"

    strategy = sharing.get("strategy")
    if strategy not in {"none", *SHARED_STRATEGIES}:
        return "GPU sharing strategy is missing or unsupported"

    if strategy in SHARED_STRATEGIES:
        supported = set(runtime.get("supported_sharing_strategies") or [])
        if runtime.get("backend") != "target-process-cuda-trace":
            return "shared GPU requires target-process CUDA trace runtime backend"
        if strategy not in supported:
            return "runtime model does not declare support for this sharing strategy"
        if not quality.get("target_process_identified", False):
            return "target CUDA process was not identified"
        if not quality.get("hardware_trace", False):
            return "shared GPU trace is not a CUDA hardware trace"

    if runtime.get("ood", True) or power.get("ood", True):
        return "runtime or power model rejected the sample as OOD"

    if float(runtime.get("confidence", 0)) < min_confidence:
        return "runtime confidence below threshold"
    if float(power.get("confidence", 0)) < min_confidence:
        return "power confidence below threshold"

    if power.get("model_scope") != "node-bound":
        return "power model must declare model_scope=node-bound"

    if power.get("bound_node") != node:
        return "power model node binding does not match candidate node"

    physical_gpu_uuid = sharing.get("physical_gpu_uuid")
    if not physical_gpu_uuid:
        return "candidate node is missing physical GPU UUID"

    if power.get("bound_gpu_uuid") != physical_gpu_uuid:
        return "power model GPU UUID binding does not match candidate GPU"

    if power.get("target_semantics") not in SUPPORTED_POWER_TARGET_SEMANTICS:
        return "power target semantics are not comparable for automatic ranking"

    if power.get("target_unit") != "W":
        return "power target unit must be W"

    if not power.get("model_id") or not power.get("model_version"):
        return "power model identity/version is missing"

    if power.get("missing_features"):
        return "power input has missing required features"

    if int(quality.get("complete_cycles", 0)) < 3:
        return "fewer than three complete cycles"

    if not quality.get("clock_synchronized", False):
        return "node clock synchronization preflight did not pass"

    if int(quality.get("netdata_samples", 0)) < 10:
        return "fewer than ten Netdata samples"
    if float(quality.get("max_netdata_gap_s", float("inf"))) > 2.0:
        return "Netdata gap exceeds two seconds"

    if int(quality.get("dcgm_samples", 0)) < 10:
        return "fewer than ten DCGM samples"
    if float(quality.get("max_dcgm_gap_s", float("inf"))) > 2.0:
        return "DCGM gap exceeds two seconds"

    if float(quality.get("alignment_coverage", 0.0)) < 0.90:
        return "Netdata/DCGM alignment coverage below 90 percent"
    if float(quality.get("max_alignment_delta_ms", float("inf"))) > 750.0:
        return "Netdata/DCGM alignment delta exceeds 750 ms"

    runtime_ms, runtime_work_unit = _runtime_prediction(runtime)
    if runtime_ms <= 0:
        return "invalid runtime prediction"

    if expected_work_unit is not None:
        if runtime_work_unit is None:
            return "runtime prediction does not declare its work unit"
        if runtime_work_unit != expected_work_unit:
            return "runtime prediction work unit does not match workload work unit"

    steady_power = power.get("steady_power_w", power.get("predicted_power_w", -1))
    if float(steady_power) < 0:
        return "invalid power prediction"

    if "idle_power_w" not in power or float(power["idle_power_w"]) < 0:
        return "node-total-power ranking requires non-negative idle_power_w"

    return None


def rank_nodes(
    results: dict[str, Any],
    total_work_units: int | None,
    min_confidence: float = 0.8,
    *,
    work_unit: str | None = None,
) -> tuple[list[RankedNode], dict[str, str]]:
    ranked: list[RankedNode] = []
    rejected: dict[str, str] = {}

    for item in results.get("nodes") or []:
        node = str(item.get("node", "<unknown>"))
        reason = _reject_reason(item, min_confidence, work_unit)
        if reason:
            rejected[node] = reason
            continue

        runtime_ms, runtime_work_unit = _runtime_prediction(item["runtime"])
        effective_work_unit = work_unit or runtime_work_unit

        predicted_power = float(
            item["power"].get(
                "steady_power_w", item["power"].get("predicted_power_w")
            )
        )
        idle_power = float(item["power"]["idle_power_w"])

        # The first production contract accepts node-total-power models only.
        # Ranking uses incremental power so nodes with different idle baselines
        # remain comparable.
        incremental_power = max(0.0, predicted_power - idle_power)
        energy_per_work_unit = incremental_power * runtime_ms / 1000.0

        confidence = min(
            float(item["runtime"]["confidence"]),
            float(item["power"]["confidence"]),
        )
        uncertainty_penalty = 1.0 + (1.0 - confidence)

        total_runtime = (
            runtime_ms * total_work_units / 1000.0
            if total_work_units is not None
            else None
        )
        total_energy = (
            energy_per_work_unit * total_work_units
            if total_work_units is not None
            else None
        )
        base_score = (
            total_energy
            if total_energy is not None
            else energy_per_work_unit
        )

        ranked.append(
            RankedNode(
                node=node,
                work_unit=effective_work_unit,
                runtime_ms_per_work_unit=runtime_ms,
                energy_j_per_work_unit=energy_per_work_unit,
                total_runtime_s=total_runtime,
                total_energy_j=total_energy,
                score=base_score * uncertainty_penalty,
            )
        )

    ranked.sort(key=lambda item: (item.score, item.node))
    return ranked, rejected


def production_job(source: dict[str, Any], selected_node: str) -> dict[str, Any]:
    result = copy.deepcopy(source)
    metadata = result.setdefault("metadata", {})
    source_name = str(metadata.get("name", "workload"))
    metadata["name"] = f"{source_name}-energy-selected"

    for key in (
        "uid",
        "resourceVersion",
        "generation",
        "creationTimestamp",
        "managedFields",
    ):
        metadata.pop(key, None)
    metadata.pop("ownerReferences", None)

    result.pop("status", None)
    spec = result.setdefault("spec", {})
    spec.pop("selector", None)
    spec["manualSelector"] = False

    template = spec.setdefault("template", {})
    template_metadata = template.setdefault("metadata", {})
    template_metadata.pop("creationTimestamp", None)
    template_metadata.setdefault("labels", {})["pre6g.io/placement"] = "energy-selected"

    pod_spec = template.setdefault("spec", {})
    selector = pod_spec.setdefault("nodeSelector", {})
    existing = selector.get("kubernetes.io/hostname")
    if existing and existing != selected_node:
        raise ValueError(
            f"Source Job already pins hostname {existing!r}, cannot select {selected_node!r}"
        )
    selector["kubernetes.io/hostname"] = selected_node
    return result
