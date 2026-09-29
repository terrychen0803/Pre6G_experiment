from __future__ import annotations

import argparse
import csv
import json
import math
from pathlib import Path

import numpy as np


FEATURE_NAMES = [
    "log_detected_period_ms",
    "log_kernel_event_rate_hz",
    "log_unique_kernel_count",
    "log_median_kernel_duration_us",
    "log_mean_kernel_duration_us",
    "log_kernel_busy_fraction",
    "anchor_robust_cv",
]

ALPHAS = [0.01, 0.1, 1.0, 10.0, 100.0]


def parse_bool(value: str) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "y"}


def load_rows(
    path: Path,
    *,
    device_id: str,
    require_two_window_stability: bool,
) -> list[dict[str, str]]:
    rows = list(csv.DictReader(path.open(newline="", encoding="utf-8")))
    selected: list[dict[str, str]] = []

    for row in rows:
        if str(row.get("device_id", "")) != device_id:
            continue
        if not parse_bool(row.get("accepted", "")):
            continue
        if str(row.get("detector_status", "")) != "detected":
            continue
        if str(row.get("detected_unit", "")) != "execution_cycle":
            continue
        if str(row.get("detector_profile", "")) != "yolo-v1":
            continue
        if require_two_window_stability and not parse_bool(
            row.get("two_window_stability_pass", "")
        ):
            continue

        missing = [
            name
            for name in [*FEATURE_NAMES, "target_runtime_ms", "workload_id"]
            if row.get(name, "") == ""
        ]
        if missing:
            raise ValueError(
                f"row {row.get('workload_id')} is missing required fields: {missing}"
            )
        selected.append(row)

    if len(selected) < 8:
        raise ValueError(
            f"too few usable samples after filtering: {len(selected)}"
        )
    return selected


def matrices(rows: list[dict[str, str]]) -> tuple[np.ndarray, np.ndarray]:
    x = np.asarray(
        [[float(row[name]) for name in FEATURE_NAMES] for row in rows],
        dtype=float,
    )
    y = np.log(
        np.asarray([float(row["target_runtime_ms"]) for row in rows], dtype=float)
    )
    if not np.isfinite(x).all() or not np.isfinite(y).all():
        raise ValueError("training data contains non-finite values")
    return x, y


def standardize_fit(x: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    means = x.mean(axis=0)
    scales = x.std(axis=0, ddof=0)
    scales = np.where(scales > 0.0, scales, 1.0)
    z = (x - means) / scales
    return z, means, scales


def fit_ridge(x: np.ndarray, y: np.ndarray, alpha: float) -> dict[str, np.ndarray | float]:
    z, means, scales = standardize_fit(x)
    intercept = float(y.mean())
    centered = y - intercept
    gram = z.T @ z
    coef = np.linalg.solve(
        gram + float(alpha) * np.eye(z.shape[1]),
        z.T @ centered,
    )
    return {
        "means": means,
        "scales": scales,
        "intercept": intercept,
        "coefficients": coef,
    }


def predict(model: dict[str, np.ndarray | float], x: np.ndarray) -> np.ndarray:
    means = np.asarray(model["means"], dtype=float)
    scales = np.asarray(model["scales"], dtype=float)
    coef = np.asarray(model["coefficients"], dtype=float)
    intercept = float(model["intercept"])
    z = (x - means) / scales
    return np.exp(intercept + z @ coef)


def mape(actual: np.ndarray, predicted: np.ndarray) -> float:
    return float(np.mean(np.abs(predicted - actual) / actual) * 100.0)


def loow_predictions(
    rows: list[dict[str, str]],
    alpha: float,
) -> tuple[np.ndarray, np.ndarray]:
    actual: list[float] = []
    predicted: list[float] = []

    for held_index in range(len(rows)):
        train = [row for index, row in enumerate(rows) if index != held_index]
        test = [rows[held_index]]

        x_train, y_train = matrices(train)
        x_test, _ = matrices(test)

        model = fit_ridge(x_train, y_train, alpha)
        pred = float(predict(model, x_test)[0])

        actual.append(float(test[0]["target_runtime_ms"]))
        predicted.append(pred)

    return np.asarray(actual), np.asarray(predicted)


def metric_record(alpha: float, actual: np.ndarray, predicted: np.ndarray) -> dict:
    ape = np.abs(predicted - actual) / actual * 100.0
    errors = predicted - actual
    return {
        "alpha": float(alpha),
        "n": int(len(actual)),
        "mape_percent": float(np.mean(ape)),
        "median_ape_percent": float(np.median(ape)),
        "p90_ape_percent": float(np.quantile(ape, 0.9)),
        "max_ape_percent": float(np.max(ape)),
        "mae_ms": float(np.mean(np.abs(errors))),
        "rmse_ms": float(np.sqrt(np.mean(errors**2))),
        "bias_ms": float(np.mean(errors)),
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Fit a device-specific YOLO trace-only runtime model from the "
            "current seven-feature marker-free sample schema."
        )
    )
    parser.add_argument("--samples", type=Path, required=True)
    parser.add_argument("--device-id", required=True)
    parser.add_argument("--output-model", type=Path, required=True)
    parser.add_argument("--output-metrics", type=Path, required=True)
    parser.add_argument(
        "--require-two-window-stability",
        action="store_true",
    )
    parser.add_argument(
        "--role",
        default="deployment-smoke",
    )
    args = parser.parse_args()

    rows = load_rows(
        args.samples,
        device_id=args.device_id,
        require_two_window_stability=args.require_two_window_stability,
    )

    trials = []
    for alpha in ALPHAS:
        actual, predicted = loow_predictions(rows, alpha)
        metrics = metric_record(alpha, actual, predicted)
        trials.append(metrics)

    best = min(
        enumerate(trials),
        key=lambda item: (item[1]["mape_percent"], item[0]),
    )[1]
    selected_alpha = float(best["alpha"])

    x, y = matrices(rows)
    final = fit_ridge(x, y, selected_alpha)

    stable_count = sum(
        parse_bool(row.get("two_window_stability_pass", ""))
        for row in rows
    )
    fallback_count = sum(
        row.get("selection_reason") == "latest accepted fallback"
        for row in rows
    )

    model_id = f"{args.device_id}_yolo_trace_only_v1"
    source_mode = (
        "two-window-stable-only"
        if args.require_two_window_stability
        else "accepted-historical"
    )

    model_payload = {
        "schema_version": "pre6g.runtime-model/v1",
        "model_id": model_id,
        "model_type": "ridge_log_runtime",
        "role": args.role,
        "device_id": args.device_id,
        "workload_family": "YOLO26",
        "detector_profile": "yolo-v1",
        "detected_unit": "execution_cycle",
        "target": "unprofiled_runtime_ms_per_execution_unit",
        "target_transform": "log",
        "feature_names": FEATURE_NAMES,
        "preprocessing": {
            "type": "standardize",
            "means": [float(value) for value in final["means"]],
            "scales": [float(value) for value in final["scales"]],
        },
        "model": {
            "alpha": selected_alpha,
            "intercept": float(final["intercept"]),
            "coefficients": [
                float(value) for value in final["coefficients"]
            ],
        },
        "training": {
            "samples": len(rows),
            "workloads": len({row["workload_id"] for row in rows}),
            "conditions": ["historical-clean"],
            "source_mode": source_mode,
            "two_window_stable_samples": stable_count,
            "fallback_samples": fallback_count,
            "alpha_policy": (
                "selected by leave-one-workload-out MAPE from fixed candidates "
                + str(ALPHAS)
            ),
            "source_file": str(args.samples),
        },
        "validation_reference": {
            "evaluation": "leave-one-workload-out on historical accepted samples",
            "mape_percent": float(best["mape_percent"]),
            "median_ape_percent": float(best["median_ape_percent"]),
            "p90_ape_percent": float(best["p90_ape_percent"]),
            "max_ape_percent": float(best["max_ape_percent"]),
            "mae_ms": float(best["mae_ms"]),
            "rmse_ms": float(best["rmse_ms"]),
        },
        "limitations": [
            f"{args.device_id} only",
            "YOLO26 validation workload family",
            "historical clean-condition traces only",
            "trace-only deployment-smoke model",
            (
                "training set includes accepted fallback detector samples"
                if fallback_count
                else "training set uses only two-window-stable detector samples"
            ),
            "not a generic cross-workload production model",
        ],
    }

    metrics_payload = {
        "schema_version": "pre6g.runtime-model-fit-report/v1",
        "device_id": args.device_id,
        "input_samples": len(rows),
        "two_window_stable_samples": stable_count,
        "fallback_samples": fallback_count,
        "selected_alpha": selected_alpha,
        "selected_metrics": best,
        "all_alpha_trials": trials,
        "workloads": [row["workload_id"] for row in rows],
    }

    args.output_model.parent.mkdir(parents=True, exist_ok=True)
    args.output_metrics.parent.mkdir(parents=True, exist_ok=True)

    args.output_model.write_text(
        json.dumps(model_payload, indent=2) + "\n",
        encoding="utf-8",
    )
    args.output_metrics.write_text(
        json.dumps(metrics_payload, indent=2) + "\n",
        encoding="utf-8",
    )

    print(json.dumps(metrics_payload, indent=2))
    print()
    print(f"model={args.output_model}")
    print(f"metrics={args.output_metrics}")


if __name__ == "__main__":
    main()
