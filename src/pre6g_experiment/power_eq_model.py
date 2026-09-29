from __future__ import annotations

import csv
import json
import math
import warnings
from pathlib import Path
from typing import Any, Iterable


TEMPERATURE_ALIASES = {
    "GPU Temp(簞C)": ("GPU Temp(°C)", "GPU Temp(℃)"),
    "CPU Temp(簞C)": ("CPU Temp(°C)", "CPU Temp(℃)"),
}


def load_records(path: Path) -> list[dict[str, Any]]:
    """Load a list of telemetry records from JSON or CSV."""
    if path.suffix.lower() == ".csv":
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            return list(csv.DictReader(handle))

    with path.open("r", encoding="utf-8-sig") as handle:
        value = json.load(handle)
    if isinstance(value, dict) and isinstance(value.get("records"), list):
        value = value["records"]
    if not isinstance(value, list) or not all(isinstance(row, dict) for row in value):
        raise ValueError("JSON input must be an array of objects or {'records': [...]}")
    return value


def load_scaler(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8-sig") as handle:
        scaler = json.load(handle)
    required = {
        "feature_cols",
        "x_mins",
        "x_data_ranges",
        "core_indices",
        "direct_indices",
        "other_indices",
        "y_min",
        "y_data_range",
    }
    missing = sorted(required - scaler.keys())
    if missing:
        raise ValueError(f"Scaler is missing keys: {', '.join(missing)}")
    if not (
        len(scaler["feature_cols"])
        == len(scaler["x_mins"])
        == len(scaler["x_data_ranges"])
    ):
        raise ValueError("Scaler feature, minimum, and range lengths do not match")
    return scaler


def _value(row: dict[str, Any], configured_name: str) -> float:
    candidates = (configured_name, *TEMPERATURE_ALIASES.get(configured_name, ()))
    for name in candidates:
        if name in row and row[name] not in (None, ""):
            try:
                value = float(row[name])
            except (TypeError, ValueError) as exc:
                raise ValueError(f"Feature {name!r} is not numeric: {row[name]!r}") from exc
            if not math.isfinite(value):
                raise ValueError(f"Feature {name!r} must be finite")
            return value
    raise ValueError(
        f"Missing feature {configured_name!r}; accepted names: {', '.join(candidates)}"
    )


def prepare_inputs(
    records: Iterable[dict[str, Any]],
    scaler: dict[str, Any],
    *,
    reject_out_of_domain: bool = False,
) -> tuple[dict[str, Any], list[str]]:
    """Min-max scale records and split them into the ONNX model's three inputs."""
    try:
        import numpy as np
    except ImportError as exc:  # pragma: no cover - depends on optional runtime
        raise RuntimeError(
            "Power-model inference requires NumPy; install requirements-power-model.txt"
        ) from exc

    rows = list(records)
    if not rows:
        raise ValueError("Input contains no telemetry records")

    columns: dict[str, list[float]] = {}
    out_of_domain: list[str] = []
    for index, (name, minimum, data_range) in enumerate(
        zip(scaler["feature_cols"], scaler["x_mins"], scaler["x_data_ranges"])
    ):
        minimum = float(minimum)
        data_range = float(data_range)
        if data_range <= 0:
            raise ValueError(f"Scaler range for {name!r} must be positive")
        scaled_values = []
        for row_number, row in enumerate(rows):
            raw = _value(row, name)
            scaled = (raw - minimum) / data_range
            if scaled < 0.0 or scaled > 1.0:
                out_of_domain.append(
                    f"row {row_number}: {name}={raw:g} outside "
                    f"[{minimum:g}, {minimum + data_range:g}]"
                )
            scaled_values.append(scaled)
        columns[name] = scaled_values

    if out_of_domain and reject_out_of_domain:
        raise ValueError("Out-of-domain telemetry: " + "; ".join(out_of_domain))

    def matrix(names: list[str]):
        return np.asarray(
            [[columns[name][row] for name in names] for row in range(len(rows))],
            dtype=np.float32,
        )

    return (
        {
            "x_core": matrix(scaler["core_indices"]),
            "x_direct": matrix(scaler["direct_indices"]),
            "x_other": matrix(scaler["other_indices"]),
        },
        out_of_domain,
    )


def predict_records(
    records: list[dict[str, Any]],
    model_path: Path,
    scaler_path: Path,
    *,
    reject_out_of_domain: bool = False,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Run ONNX inference and append PREDICTED_POWER_W to each input record."""
    try:
        import onnxruntime as ort
    except ImportError as exc:  # pragma: no cover - depends on optional runtime
        raise RuntimeError(
            "Power-model inference requires ONNX Runtime; install "
            "requirements-power-model.txt"
        ) from exc

    scaler = load_scaler(scaler_path)
    inputs, out_of_domain = prepare_inputs(
        records, scaler, reject_out_of_domain=reject_out_of_domain
    )
    if out_of_domain:
        warnings.warn(
            f"{len(out_of_domain)} feature value(s) are outside the recorded training range",
            stacklevel=2,
        )

    session = ort.InferenceSession(str(model_path), providers=["CPUExecutionProvider"])
    expected = {item.name for item in session.get_inputs()}
    if expected != set(inputs):
        raise ValueError(
            f"Unexpected ONNX inputs {sorted(expected)}; expected {sorted(inputs)}"
        )
    scaled_predictions = session.run(None, inputs)[0].reshape(-1)
    if len(scaled_predictions) != len(records):
        raise ValueError("ONNX output row count does not match input row count")

    y_min = float(scaler["y_min"])
    y_range = float(scaler["y_data_range"])
    predictions = scaled_predictions * y_range + y_min
    return (
        [
            {**row, "PREDICTED_POWER_W": float(prediction)}
            for row, prediction in zip(records, predictions)
        ],
        out_of_domain,
    )


def write_records(path: Path, records: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.suffix.lower() == ".csv":
        fieldnames = list(dict.fromkeys(key for row in records for key in row))
        with path.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(records)
        return
    with path.open("w", encoding="utf-8") as handle:
        json.dump(records, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
