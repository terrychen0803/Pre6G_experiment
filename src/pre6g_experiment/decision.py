from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class RankedNode:
    node: str
    runtime_ms_per_iteration: float
    energy_j_per_iteration: float
    total_runtime_s: float | None
    total_energy_j: float | None
    score: float


def _reject_reason(item: dict[str, Any], min_confidence: float) -> str | None:
    runtime = item.get("runtime") or {}
    power = item.get("power") or {}
    quality = item.get("quality") or {}
    if not item.get("eligible", False):
        return "node marked ineligible"
    if not item.get("exclusive_gpu", False):
        return "GPU is not exclusive"
    if runtime.get("status") != "ready" or power.get("status") != "ready":
        return "runtime or power model is not ready"
    if runtime.get("ood", True) or power.get("ood", True):
        return "runtime or power model rejected the sample as OOD"
    if float(runtime.get("confidence", 0)) < min_confidence:
        return "runtime confidence below threshold"
    if float(power.get("confidence", 0)) < min_confidence:
        return "power confidence below threshold"
    if power.get("missing_features"):
        return "power input has missing required features"
    if int(quality.get("complete_cycles", 0)) < 3:
        return "fewer than three complete cycles"
    if int(quality.get("netdata_samples", 0)) < 10:
        return "fewer than ten Netdata samples"
    if float(quality.get("max_netdata_gap_s", float("inf"))) > 2.0:
        return "Netdata gap exceeds two seconds"
    if float(runtime.get("predicted_runtime_ms_per_iteration", 0)) <= 0:
        return "invalid runtime prediction"
    if float(power.get("predicted_power_w", -1)) < 0:
        return "invalid power prediction"
    return None


def rank_nodes(
    results: dict[str, Any],
    total_iterations: int | None,
    min_confidence: float = 0.8,
) -> tuple[list[RankedNode], dict[str, str]]:
    ranked: list[RankedNode] = []
    rejected: dict[str, str] = {}
    for item in results.get("nodes") or []:
        node = str(item.get("node", "<unknown>"))
        reason = _reject_reason(item, min_confidence)
        if reason:
            rejected[node] = reason
            continue
        runtime_ms = float(item["runtime"]["predicted_runtime_ms_per_iteration"])
        predicted_power = float(item["power"]["predicted_power_w"])
        idle_power = float(item["power"].get("idle_power_w", 0))
        incremental_power = max(0.0, predicted_power - idle_power)
        energy_per_iteration = incremental_power * runtime_ms / 1000.0
        confidence = min(
            float(item["runtime"]["confidence"]),
            float(item["power"]["confidence"]),
        )
        uncertainty_penalty = 1.0 + (1.0 - confidence)
        total_runtime = (
            runtime_ms * total_iterations / 1000.0
            if total_iterations is not None
            else None
        )
        total_energy = (
            energy_per_iteration * total_iterations
            if total_iterations is not None
            else None
        )
        base_score = total_energy if total_energy is not None else energy_per_iteration
        ranked.append(
            RankedNode(
                node=node,
                runtime_ms_per_iteration=runtime_ms,
                energy_j_per_iteration=energy_per_iteration,
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
    for key in ("uid", "resourceVersion", "generation", "creationTimestamp", "managedFields"):
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

