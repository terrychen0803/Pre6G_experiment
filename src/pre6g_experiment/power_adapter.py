from __future__ import annotations

import json
from pathlib import Path
from typing import Any


FEATURE_SOURCES = {
    "CPU User%": "netdata",
    "Top1 CPU%": "netdata",
    "Top2 CPU%": "netdata",
    "Top3 CPU%": "netdata",
    "Mem Used(MB)": "netdata",
    "Mem Free(MB)": "netdata",
    "CPU Temp(°C)": "netdata",
    "GPU Mem Used(MB)": "dcgm",
    "GPU Power(W)": "dcgm",
    "GPU Temp(°C)": "dcgm",
}


def load_bundle_manifest(path: Path) -> dict[str, Any]:
    try:
        import yaml
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("Power bundle manifest loading requires PyYAML") from exc

    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Power bundle manifest must be a YAML object")
    return payload


def feature_source_map(required_features: list[str]) -> dict[str, str]:
    result: dict[str, str] = {}
    missing: list[str] = []
    for name in required_features:
        source = FEATURE_SOURCES.get(name)
        if source is None:
            missing.append(name)
        else:
            result[name] = source
    if missing:
        raise ValueError(
            "No validated telemetry source mapping for: " + ", ".join(missing)
        )
    return result


def summarize_predicted_series(
    rows: list[dict[str, Any]],
) -> dict[str, float | int | None]:
    if not rows:
        raise ValueError("Power prediction series is empty")

    powers = [float(row["PREDICTED_POWER_W"]) for row in rows]
    if any(value < 0 for value in powers):
        raise ValueError("Predicted power must be non-negative")

    timestamps: list[int] = []
    for row in rows:
        value = row.get("timestamp_ns")
        if value in (None, ""):
            timestamps = []
            break
        timestamps.append(int(value))

    arithmetic_mean = sum(powers) / len(powers)
    summary: dict[str, float | int | None] = {
        "samples": len(rows),
        "mean_predicted_power_w": arithmetic_mean,
        "min_predicted_power_w": min(powers),
        "max_predicted_power_w": max(powers),
        "observed_window_s": None,
        "observed_window_energy_j": None,
        "time_weighted_mean_predicted_power_w": None,
    }

    if len(timestamps) >= 2:
        paired = sorted(zip(timestamps, powers), key=lambda item: item[0])
        energy_j = 0.0
        for (left_t, left_p), (right_t, right_p) in zip(paired, paired[1:]):
            delta_s = (right_t - left_t) / 1e9
            if delta_s <= 0:
                raise ValueError("Power sample timestamps must be strictly increasing")
            energy_j += (left_p + right_p) * 0.5 * delta_s
        window_s = (paired[-1][0] - paired[0][0]) / 1e9
        summary["observed_window_s"] = window_s
        summary["observed_window_energy_j"] = energy_j
        summary["time_weighted_mean_predicted_power_w"] = (
            energy_j / window_s if window_s > 0 else None
        )

    return summary


def build_power_smoke_result(
    *,
    manifest: dict[str, Any],
    required_features: list[str],
    predicted_rows: list[dict[str, Any]],
    ood_messages: list[str],
    alignment_quality: dict[str, Any] | None = None,
) -> dict[str, Any]:
    node_binding = manifest.get("node_binding") or {}
    target = manifest.get("target") or {}

    blockers: list[str] = []
    if manifest.get("status") != "ready":
        blockers.append("bundle manifest status is not ready")
    if not node_binding.get("kubernetes_node"):
        blockers.append("Kubernetes node binding is missing")
    if not node_binding.get("gpu_uuid"):
        blockers.append("physical GPU UUID binding is missing")
    if not bool(target.get("semantics_verified")):
        blockers.append("power target semantics are not verified")
    idle_power = manifest.get("idle_power_w")
    if idle_power is None:
        blockers.append("node idle power is missing")
    if ood_messages:
        blockers.append("telemetry contains out-of-domain feature values")
    if alignment_quality is not None and not bool(alignment_quality.get("pass")):
        blockers.append("Netdata/DCGM alignment quality did not pass")

    source_map = feature_source_map(required_features)
    series_summary = summarize_predicted_series(predicted_rows)

    return {
        "schema_version": "pre6g.power-prediction-smoke/v1",
        "status": "ready" if not blockers else "validation_required",
        "ranking_eligible": not blockers,
        "model_id": manifest.get("model_id"),
        "model_version": manifest.get("model_version"),
        "model_format": manifest.get("model_format"),
        "bound_node": node_binding.get("kubernetes_node"),
        "bound_gpu_uuid": node_binding.get("gpu_uuid"),
        "bound_gpu_model": node_binding.get("gpu_model"),
        "required_features": required_features,
        "feature_sources": source_map,
        "target_source_field": target.get("source_field"),
        "target_semantics": (
            target.get("semantics")
            or target.get("inferred_semantics")
        ),
        "target_semantics_verified": bool(target.get("semantics_verified")),
        "target_unit": target.get("unit"),
        "idle_power_w": idle_power,
        "ood": bool(ood_messages),
        "ood_messages": list(ood_messages),
        "alignment_quality": alignment_quality,
        "observed_profile_window": series_summary,
        "blockers": blockers,
        "notes": [
            (
                "Observed-profile-window power is diagnostic only until node binding, "
                "target semantics, idle power, and deployment policy are frozen."
            ),
            (
                "Do not multiply this profiled-window power by predicted training time "
                "for automatic ranking while ranking_eligible is false."
            ),
        ],
    }
