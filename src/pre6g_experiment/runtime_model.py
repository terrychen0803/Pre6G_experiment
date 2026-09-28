from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class RuntimeModelError(ValueError):
    pass


@dataclass(frozen=True)
class RuntimePrediction:
    model_id: str
    device_id: str
    detected_unit: str
    predicted_runtime_ms: float
    feature_count: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "pre6g.runtime-prediction/v1",
            "model_id": self.model_id,
            "device_id": self.device_id,
            "detected_unit": self.detected_unit,
            "predicted_runtime_ms": self.predicted_runtime_ms,
            "feature_count": self.feature_count,
        }


def load_model(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))

    required = {
        "schema_version",
        "model_id",
        "model_type",
        "device_id",
        "detected_unit",
        "feature_names",
        "preprocessing",
        "model",
    }
    missing = sorted(required - set(payload))
    if missing:
        raise RuntimeModelError(
            f"runtime model is missing required fields: {missing}"
        )

    if payload["schema_version"] != "pre6g.runtime-model/v1":
        raise RuntimeModelError(
            f"unsupported runtime model schema: {payload['schema_version']}"
        )

    if payload["model_type"] != "ridge_log_runtime":
        raise RuntimeModelError(
            f"unsupported runtime model type: {payload['model_type']}"
        )

    names = payload["feature_names"]
    means = payload["preprocessing"].get("means", [])
    scales = payload["preprocessing"].get("scales", [])
    coefficients = payload["model"].get("coefficients", [])

    width = len(names)
    if not (len(means) == len(scales) == len(coefficients) == width):
        raise RuntimeModelError(
            "runtime model feature_names/means/scales/coefficients length mismatch"
        )

    if any(float(scale) <= 0.0 for scale in scales):
        raise RuntimeModelError("runtime model contains non-positive scale")

    return payload


def load_feature_artifact(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))

    if payload.get("schema_version") != "pre6g.runtime-features/v1":
        raise RuntimeModelError(
            "unsupported runtime feature artifact schema"
        )

    if not isinstance(payload.get("trace_features"), dict):
        raise RuntimeModelError(
            "runtime feature artifact must contain trace_features"
        )

    return payload


def predict_runtime(
    model: dict[str, Any],
    feature_artifact: dict[str, Any],
) -> RuntimePrediction:
    model_device = str(model["device_id"])
    sample_device = str(feature_artifact.get("device_id", ""))

    if sample_device != model_device:
        raise RuntimeModelError(
            f"device mismatch: model={model_device}, sample={sample_device}"
        )

    model_unit = str(model["detected_unit"])
    sample_unit = str(feature_artifact.get("detected_unit", ""))

    if sample_unit != model_unit:
        raise RuntimeModelError(
            f"detected_unit mismatch: model={model_unit}, sample={sample_unit}"
        )

    detector_profile = model.get("detector_profile")
    sample_profile = feature_artifact.get("detector_profile")
    if detector_profile and sample_profile != detector_profile:
        raise RuntimeModelError(
            "detector profile mismatch: "
            f"model={detector_profile}, sample={sample_profile}"
        )

    feature_map = feature_artifact["trace_features"]
    names = list(model["feature_names"])

    missing = [name for name in names if name not in feature_map]
    if missing:
        raise RuntimeModelError(
            f"missing runtime features: {missing}"
        )

    values = [float(feature_map[name]) for name in names]
    means = [float(value) for value in model["preprocessing"]["means"]]
    scales = [float(value) for value in model["preprocessing"]["scales"]]

    standardized = [
        (value - means[index]) / scales[index]
        for index, value in enumerate(values)
    ]

    intercept = float(model["model"]["intercept"])
    coefficients = [
        float(value)
        for value in model["model"]["coefficients"]
    ]

    log_runtime = intercept + sum(
        coefficient * value
        for coefficient, value in zip(
            coefficients,
            standardized,
        )
    )
    prediction = math.exp(log_runtime)

    if not math.isfinite(prediction) or prediction <= 0.0:
        raise RuntimeModelError(
            f"invalid runtime prediction: {prediction}"
        )

    return RuntimePrediction(
        model_id=str(model["model_id"]),
        device_id=model_device,
        detected_unit=model_unit,
        predicted_runtime_ms=prediction,
        feature_count=len(names),
    )
