from __future__ import annotations

import argparse
import csv
import json
import math
import statistics
from pathlib import Path

from pre6g_experiment.runtime_features import (
    TRACE_FEATURE_NAMES,
    extract_trace_features,
    summarize_telemetry_window,
)


RIDGE_ALPHAS = (0.1, 1.0, 10.0)
SOFT_GATE_TEMPERATURES = (0.25, 0.5, 1.0, 2.0)

# Keep the original reference feature family, but source it from the canonical
# aligned telemetry file and timestamps.json rather than iterations.csv.
TELEMETRY_COLUMNS = (
    "GPU Util%",
    "GPU Mem Used(MB)",
    "GPU Power(W)",
    "CPU User%",
    "CPU System%",
    "Load 1min",
)


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    if not rows:
        raise ValueError(f"No rows in {path}")
    return rows


def write_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fields = sorted({key for row in rows for key in row})
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def percentile(values: list[float], quantile: float) -> float:
    ordered = sorted(values)
    position = (len(ordered) - 1) * quantile
    low = math.floor(position)
    high = math.ceil(position)
    if low == high:
        return float(ordered[low])
    fraction = position - low
    return float(
        ordered[low] * (1.0 - fraction)
        + ordered[high] * fraction
    )


def finite(value: float) -> bool:
    return math.isfinite(value)


def solve(matrix: list[list[float]], vector: list[float]) -> list[float]:
    size = len(vector)
    augmented = [
        list(matrix[index]) + [vector[index]]
        for index in range(size)
    ]
    for column in range(size):
        pivot = max(
            range(column, size),
            key=lambda row: abs(augmented[row][column]),
        )
        augmented[column], augmented[pivot] = (
            augmented[pivot],
            augmented[column],
        )
        divisor = augmented[column][column]
        if abs(divisor) < 1e-12:
            raise RuntimeError("singular ridge system")
        augmented[column] = [
            value / divisor
            for value in augmented[column]
        ]
        for row in range(size):
            if row == column:
                continue
            factor = augmented[row][column]
            augmented[row] = [
                augmented[row][index]
                - factor * augmented[column][index]
                for index in range(size + 1)
            ]
    return [augmented[index][-1] for index in range(size)]


def ridge_predict(
    train_features: list[list[float]],
    train_targets: list[float],
    test_features: list[float],
    alpha: float,
) -> float:
    feature_count = len(test_features)
    means = [
        statistics.mean(row[column] for row in train_features)
        for column in range(feature_count)
    ]
    scales = []
    for column in range(feature_count):
        values = [row[column] for row in train_features]
        scale = (
            statistics.stdev(values)
            if len(values) > 1
            else 1.0
        )
        scales.append(scale if scale > 1e-12 else 1.0)

    design = [
        [1.0]
        + [
            (row[column] - means[column]) / scales[column]
            for column in range(feature_count)
        ]
        for row in train_features
    ]
    width = feature_count + 1
    gram = [
        [
            sum(
                row[left] * row[right]
                for row in design
            )
            for right in range(width)
        ]
        for left in range(width)
    ]
    projected = [
        sum(
            row[column] * target
            for row, target in zip(design, train_targets)
        )
        for column in range(width)
    ]
    for column in range(1, width):
        gram[column][column] += alpha

    coefficients = solve(gram, projected)
    transformed = [1.0] + [
        (test_features[column] - means[column]) / scales[column]
        for column in range(feature_count)
    ]
    return sum(
        value * coefficient
        for value, coefficient in zip(
            transformed,
            coefficients,
        )
    )


def impute(
    train: list[list[float]],
    test: list[float],
) -> tuple[list[list[float]], list[float]]:
    width = len(test)
    medians: list[float] = []
    missing_columns: list[bool] = []

    for column in range(width):
        observed = [
            row[column]
            for row in train
            if finite(row[column])
        ]
        medians.append(
            statistics.median(observed)
            if observed
            else 0.0
        )
        missing_columns.append(
            any(
                not finite(row[column])
                for row in train
            )
        )

    transformed_train = []
    for row in train:
        values = [
            row[column]
            if finite(row[column])
            else medians[column]
            for column in range(width)
        ]
        values.extend(
            1.0 if not finite(row[column]) else 0.0
            for column in range(width)
            if missing_columns[column]
        )
        transformed_train.append(values)

    transformed_test = [
        test[column]
        if finite(test[column])
        else medians[column]
        for column in range(width)
    ]
    transformed_test.extend(
        1.0 if not finite(test[column]) else 0.0
        for column in range(width)
        if missing_columns[column]
    )
    return transformed_train, transformed_test


def resolve_path(base: Path, raw: str | None) -> Path | None:
    if raw in (None, ""):
        return None
    path = Path(raw)
    return path if path.is_absolute() else (base / path).resolve()


def load_sample(row: dict[str, str], manifest_dir: Path) -> dict:
    sqlite_path = resolve_path(manifest_dir, row.get("sqlite_path"))
    detection_path = resolve_path(
        manifest_dir,
        row.get("detection_json"),
    )
    if sqlite_path is None or detection_path is None:
        raise ValueError(
            "manifest requires sqlite_path and detection_json"
        )

    detection = json.loads(
        detection_path.read_text(encoding="utf-8")
    )
    if not detection.get("accepted", detection.get("status") == "detected"):
        raise ValueError(
            f"{row.get('workload_id')}: detection is not accepted"
        )

    global_pid = int(
        row.get("global_pid")
        or detection["target_global_pid"]
    )
    context_id = int(
        row.get("context_id")
        or detection["target_context_id"]
    )

    detection_for_features = dict(detection)
    detection_for_features["horizon_seconds"] = float(
        detection.get(
            "horizon_seconds",
            detection.get("input_window_seconds"),
        )
    )
    trace = extract_trace_features(
        sqlite_path,
        detection_for_features,
        global_pid=global_pid,
        context_id=context_id,
    )

    telemetry_values: list[float] = [
        math.nan for _ in TELEMETRY_COLUMNS
    ]
    telemetry_sample_count = 0

    telemetry_path = resolve_path(
        manifest_dir,
        row.get("telemetry_csv"),
    )
    timestamps_path = resolve_path(
        manifest_dir,
        row.get("timestamps_json"),
    )

    if telemetry_path is None or timestamps_path is None:
        raise ValueError(
            "Every clean/high-load sample must provide telemetry_csv and "
            "timestamps_json. The deployment reference does not use "
            "missing-telemetry condition identity as a model signal."
        )

    summary = summarize_telemetry_window(
        telemetry_path,
        timestamps_path,
        seconds=float(row.get("pre_window_seconds") or 5.0),
        columns=TELEMETRY_COLUMNS,
    )
    telemetry_sample_count = int(summary["sample_count"])
    telemetry_values = []
    for column in TELEMETRY_COLUMNS:
        key = (
            "pre_"
            + column.lower()
            .replace("%", "pct")
            .replace(" ", "_")
            .replace("(", "")
            .replace(")", "")
            .replace("/", "_")
        )
        value = summary["features"].get(key)
        telemetry_values.append(
            float(value)
            if value is not None
            else math.nan
        )

    condition = str(row["condition"])
    target_runtime_ms = float(row["target_runtime_ms"])
    trace_runtime_ms = float(
        row.get("trace_runtime_ms")
        or detection["detected_period_ms"]
    )
    repeat_cv = float(row.get("baseline_repeat_cv_percent") or 0.0)

    sample = {
        "condition": condition,
        "load_flag": 0.0 if condition == "clean" else 1.0,
        "workload_id": str(row["workload_id"]),
        "device_id": str(row.get("device_id") or "unknown"),
        "target_runtime_ms": target_runtime_ms,
        "trace_runtime_ms": trace_runtime_ms,
        "observed_overhead_percent": (
            (trace_runtime_ms - target_runtime_ms)
            / target_runtime_ms
            * 100.0
        ),
        "baseline_repeat_cv_percent": repeat_cv,
        "detected_period_ms": float(detection["detected_period_ms"]),
        "emission_horizon_seconds": float(
            detection_for_features["horizon_seconds"]
        ),
        "kernel_event_rate_hz": trace["kernel_event_rate_hz"],
        "unique_kernel_count": trace["unique_kernel_count"],
        "median_kernel_duration_us": trace[
            "median_kernel_duration_us"
        ],
        "mean_kernel_duration_us": trace[
            "mean_kernel_duration_us"
        ],
        "kernel_busy_fraction": trace["kernel_busy_fraction"],
        "anchor_robust_cv": float(detection["anchor_robust_cv"]),
        "telemetry_sample_count": telemetry_sample_count,
        "_trace_features": trace["feature_vector"],
        "_telemetry_features": telemetry_values,
    }
    sample.update(trace["features"])
    return sample


def raw_features(sample: dict, model: str) -> list[float]:
    values = list(sample["_trace_features"])
    if model == "trace_plus_load_flag":
        values.append(float(sample["load_flag"]))
    elif model == "trace_plus_load_interactions":
        flag = float(sample["load_flag"])
        values.extend(
            [flag]
            + [
                flag * value
                for value in sample["_trace_features"]
            ]
        )
    elif model == "trace_plus_pre_telemetry":
        values.extend(sample["_telemetry_features"])
    return values


def predict_one(
    train: list[dict],
    test: dict,
    model: str,
    alpha: float,
) -> float:
    train_raw = [
        raw_features(sample, model)
        for sample in train
    ]
    test_raw = raw_features(test, model)
    train_features, test_features = impute(
        train_raw,
        test_raw,
    )
    targets = [
        math.log(sample["target_runtime_ms"])
        for sample in train
    ]
    return math.exp(
        ridge_predict(
            train_features,
            targets,
            test_features,
            alpha,
        )
    )


def choose_alpha(
    train: list[dict],
    model: str,
) -> tuple[float, list[dict]]:
    workloads = sorted(
        {sample["workload_id"] for sample in train}
    )
    trials = []
    for alpha in RIDGE_ALPHAS:
        errors = []
        for workload_id in workloads:
            inner_train = [
                sample
                for sample in train
                if sample["workload_id"] != workload_id
            ]
            validation = [
                sample
                for sample in train
                if sample["workload_id"] == workload_id
            ]
            if not inner_train:
                continue
            for sample in validation:
                prediction = predict_one(
                    inner_train,
                    sample,
                    model,
                    alpha,
                )
                errors.append(
                    abs(
                        prediction
                        - sample["target_runtime_ms"]
                    )
                    / sample["target_runtime_ms"]
                    * 100.0
                )
        if not errors:
            raise ValueError(
                "Not enough workloads for nested alpha selection"
            )
        trials.append(
            {
                "alpha": alpha,
                "inner_mean_ape_percent": statistics.mean(errors),
                "inner_median_ape_percent": statistics.median(errors),
            }
        )

    winner = min(
        trials,
        key=lambda item: (
            item["inner_mean_ape_percent"],
            item["inner_median_ape_percent"],
            item["alpha"],
        ),
    )
    return float(winner["alpha"]), trials


def evaluate_scenario(
    samples: list[dict],
    experiment: str,
    model: str,
    train_condition: str | None,
    test_conditions: tuple[str, ...],
) -> tuple[list[dict], list[dict]]:
    predictions = []
    tuning = []

    for held_out in sorted(
        {sample["workload_id"] for sample in samples}
    ):
        train = [
            sample
            for sample in samples
            if sample["workload_id"] != held_out
        ]
        if train_condition is not None:
            train = [
                sample
                for sample in train
                if sample["condition"] == train_condition
            ]

        tests = [
            sample
            for sample in samples
            if sample["workload_id"] == held_out
            and sample["condition"] in test_conditions
        ]
        if not tests:
            continue

        alpha, trials = choose_alpha(train, model)
        for trial in trials:
            tuning.append(
                {
                    "experiment": experiment,
                    "model": model,
                    "held_out_workload_id": held_out,
                    **trial,
                }
            )

        for test in tests:
            prediction = predict_one(
                train,
                test,
                model,
                alpha,
            )
            target = test["target_runtime_ms"]
            predicted_overhead = (
                (test["trace_runtime_ms"] - prediction)
                / prediction
                * 100.0
            )
            predictions.append(
                {
                    "experiment": experiment,
                    "model": model,
                    "held_out_workload_id": held_out,
                    "test_condition": test["condition"],
                    "selected_alpha": alpha,
                    "actual_runtime_ms": target,
                    "predicted_runtime_ms": prediction,
                    "runtime_ape_percent": (
                        abs(prediction - target)
                        / target
                        * 100.0
                    ),
                    "actual_overhead_percent": test[
                        "observed_overhead_percent"
                    ],
                    "predicted_overhead_percent": predicted_overhead,
                    "detected_period_ms": test["detected_period_ms"],
                    "emission_horizon_seconds": test[
                        "emission_horizon_seconds"
                    ],
                }
            )

    return predictions, tuning


def infer_condition(train: list[dict], test: dict) -> str:
    width = len(test["_trace_features"])
    means = [
        statistics.mean(
            sample["_trace_features"][column]
            for sample in train
        )
        for column in range(width)
    ]
    scales = []
    for column in range(width):
        values = [
            sample["_trace_features"][column]
            for sample in train
        ]
        scale = statistics.stdev(values)
        scales.append(scale if scale > 1e-12 else 1.0)

    conditions = sorted(
        {sample["condition"] for sample in train}
    )
    centroids = {}
    for condition in conditions:
        condition_rows = [
            sample
            for sample in train
            if sample["condition"] == condition
        ]
        centroids[condition] = [
            statistics.mean(
                (
                    sample["_trace_features"][column]
                    - means[column]
                )
                / scales[column]
                for sample in condition_rows
            )
            for column in range(width)
        ]

    point = [
        (
            test["_trace_features"][column]
            - means[column]
        )
        / scales[column]
        for column in range(width)
    ]
    return min(
        centroids,
        key=lambda condition: sum(
            (value - center) ** 2
            for value, center in zip(
                point,
                centroids[condition],
            )
        ),
    )


def evaluate_regime_moe(
    samples: list[dict],
) -> tuple[list[dict], list[dict]]:
    predictions = []
    tuning = []
    conditions = sorted(
        {sample["condition"] for sample in samples}
    )

    for held_out in sorted(
        {sample["workload_id"] for sample in samples}
    ):
        outer_train = [
            sample
            for sample in samples
            if sample["workload_id"] != held_out
        ]
        tests = [
            sample
            for sample in samples
            if sample["workload_id"] == held_out
        ]

        experts = {}
        for condition in conditions:
            expert_train = [
                sample
                for sample in outer_train
                if sample["condition"] == condition
            ]
            alpha, trials = choose_alpha(
                expert_train,
                "trace_only",
            )
            experts[condition] = (
                expert_train,
                alpha,
            )
            for trial in trials:
                tuning.append(
                    {
                        "experiment": "mixed_grouped_loow",
                        "model": "trace_regime_moe",
                        "held_out_workload_id": held_out,
                        "expert_condition": condition,
                        **trial,
                    }
                )

        for test in tests:
            inferred = infer_condition(
                outer_train,
                test,
            )
            expert_train, alpha = experts[inferred]
            prediction = predict_one(
                expert_train,
                test,
                "trace_only",
                alpha,
            )
            target = test["target_runtime_ms"]
            predictions.append(
                {
                    "experiment": "mixed_grouped_loow",
                    "model": "trace_regime_moe",
                    "held_out_workload_id": held_out,
                    "test_condition": test["condition"],
                    "inferred_condition": inferred,
                    "condition_classification_correct": (
                        inferred == test["condition"]
                    ),
                    "selected_alpha": alpha,
                    "actual_runtime_ms": target,
                    "predicted_runtime_ms": prediction,
                    "runtime_ape_percent": (
                        abs(prediction - target)
                        / target
                        * 100.0
                    ),
                }
            )

    return predictions, tuning


def summarize(predictions: list[dict]) -> list[dict]:
    keys = sorted(
        {
            (
                row["experiment"],
                row["model"],
                row["test_condition"],
            )
            for row in predictions
        }
    )
    rows = []
    for experiment, model, condition in keys:
        group = [
            row
            for row in predictions
            if row["experiment"] == experiment
            and row["model"] == model
            and row["test_condition"] == condition
        ]
        errors = [
            row["runtime_ape_percent"]
            for row in group
        ]
        summary = {
            "experiment": experiment,
            "model": model,
            "test_condition": condition,
            "n": len(group),
            "mape_percent": statistics.mean(errors),
            "median_ape_percent": statistics.median(errors),
            "p90_ape_percent": percentile(errors, 0.90),
            "max_ape_percent": max(errors),
            "within_10_percent": (
                sum(error <= 10.0 for error in errors)
                / len(errors)
            ),
        }
        classified = [
            row
            for row in group
            if "condition_classification_correct" in row
        ]
        if classified:
            summary["condition_classification_accuracy"] = (
                sum(
                    bool(
                        row[
                            "condition_classification_correct"
                        ]
                    )
                    for row in classified
                )
                / len(classified)
            )
        rows.append(summary)

    for model in (
        "trace_only",
        "trace_plus_load_flag",
        "trace_plus_load_interactions",
        "trace_plus_pre_telemetry",
        "trace_regime_moe",
    ):
        group = [
            row
            for row in predictions
            if row["experiment"] == "mixed_grouped_loow"
            and row["model"] == model
        ]
        if not group:
            continue
        errors = [
            row["runtime_ape_percent"]
            for row in group
        ]
        summary = {
            "experiment": "mixed_grouped_loow",
            "model": model,
            "test_condition": "both",
            "n": len(group),
            "mape_percent": statistics.mean(errors),
            "median_ape_percent": statistics.median(errors),
            "p90_ape_percent": percentile(errors, 0.90),
            "max_ape_percent": max(errors),
            "within_10_percent": (
                sum(error <= 10.0 for error in errors)
                / len(errors)
            ),
        }
        classified = [
            row
            for row in group
            if "condition_classification_correct" in row
        ]
        if classified:
            summary["condition_classification_accuracy"] = (
                sum(
                    bool(
                        row[
                            "condition_classification_correct"
                        ]
                    )
                    for row in classified
                )
                / len(classified)
            )
        rows.append(summary)

    return rows


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Offline clean/high-load marker-free runtime-model reference "
            "runner. Pre-run telemetry is anchored by timestamps.json; "
            "iterations.csv is never read."
        )
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        required=True,
        help=(
            "CSV columns: condition,workload_id,device_id,sqlite_path,"
            "detection_json,target_runtime_ms,trace_runtime_ms(optional),"
            "timestamps_json,telemetry_csv,"
            "baseline_repeat_cv_percent(optional)"
        ),
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        required=True,
    )
    args = parser.parse_args()

    manifest = read_csv(args.manifest)
    samples = [
        load_sample(row, args.manifest.resolve().parent)
        for row in manifest
    ]

    conditions = sorted(
        {sample["condition"] for sample in samples}
    )
    if len(conditions) != 2:
        raise ValueError(
            "This reference runner expects exactly two conditions "
            "(for example clean and high_load_01)"
        )
    clean_condition = (
        "clean"
        if "clean" in conditions
        else conditions[0]
    )
    high_condition = next(
        item
        for item in conditions
        if item != clean_condition
    )

    scenarios = [
        (
            "clean_to_clean_loow",
            "trace_only",
            clean_condition,
            (clean_condition,),
        ),
        (
            "clean_to_high_strict",
            "trace_only",
            clean_condition,
            (high_condition,),
        ),
        (
            "high_to_clean_strict",
            "trace_only",
            high_condition,
            (clean_condition,),
        ),
        (
            "mixed_grouped_loow",
            "trace_only",
            None,
            tuple(conditions),
        ),
        (
            "mixed_grouped_loow",
            "trace_plus_load_flag",
            None,
            tuple(conditions),
        ),
        (
            "mixed_grouped_loow",
            "trace_plus_load_interactions",
            None,
            tuple(conditions),
        ),
        (
            "mixed_grouped_loow",
            "trace_plus_pre_telemetry",
            None,
            tuple(conditions),
        ),
    ]

    predictions = []
    tuning = []
    for (
        experiment,
        model,
        train_condition,
        test_conditions,
    ) in scenarios:
        scenario_predictions, scenario_tuning = (
            evaluate_scenario(
                samples,
                experiment,
                model,
                train_condition,
                test_conditions,
            )
        )
        predictions.extend(scenario_predictions)
        tuning.extend(scenario_tuning)

    regime_predictions, regime_tuning = (
        evaluate_regime_moe(samples)
    )
    predictions.extend(regime_predictions)
    tuning.extend(regime_tuning)

    metrics = summarize(predictions)
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)

    public_samples = [
        {
            key: value
            for key, value in sample.items()
            if not key.startswith("_")
        }
        for sample in samples
    ]
    write_csv(output / "samples.csv", public_samples)
    write_csv(output / "predictions.csv", predictions)
    write_csv(output / "metrics.csv", metrics)
    write_csv(
        output / "inner_alpha_selection.csv",
        tuning,
    )

    contract = {
        "schema_version": "pre6g.runtime-model-evaluation/v1",
        "program": Path(__file__).name,
        "role": (
            "offline model-development/reference runner; "
            "not production single-request inference"
        ),
        "conditions": conditions,
        "trace_features": TRACE_FEATURE_NAMES,
        "pre_run_telemetry_columns": TELEMETRY_COLUMNS,
        "pre_run_telemetry_anchor": (
            "timestamps.json application_start_ns, "
            "fallback profile_start_ns"
        ),
        "telemetry_collection_policy": (
            "required for every clean/high-load sample; no condition-specific "
            "missing-telemetry shortcut"
        ),
        "forbidden_features": [
            "NVTX",
            "iterations.csv",
            "training callbacks",
            "workload ID as a feature",
            "batch size",
            "image size",
            "AMP",
            "during-run telemetry as a pre-run feature",
        ],
        "target": (
            "manifest-provided unprofiled runtime target "
            "for the semantic execution unit"
        ),
        "outer_split": (
            "leave one workload out; both condition rows excluded "
            "for mixed experiments"
        ),
        "ridge_alphas": RIDGE_ALPHAS,
    }
    (output / "experiment_contract.json").write_text(
        json.dumps(
            contract,
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    print(
        json.dumps(
            {
                "conditions": conditions,
                "profiles": len(samples),
                "metrics": metrics,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
