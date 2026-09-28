from __future__ import annotations

import csv
import json
import math
import sqlite3
import statistics
from pathlib import Path
from typing import Any


TRACE_FEATURE_NAMES = (
    "log_detected_period_ms",
    "log_kernel_event_rate_hz",
    "log_unique_kernel_count",
    "log_median_kernel_duration_us",
    "log_mean_kernel_duration_us",
    "log_kernel_busy_fraction",
    "anchor_robust_cv",
)


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    if not rows:
        raise ValueError(f"No rows in {path}")
    return rows


def read_timestamps(path: Path) -> dict[str, int]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("timestamps.json must contain one JSON object")

    parsed: dict[str, int] = {}
    for key, value in payload.items():
        if value is None:
            continue
        try:
            parsed[str(key)] = int(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"timestamps.json field {key!r} must be an integer nanosecond timestamp"
            ) from exc
    return parsed


def choose_pre_run_anchor_ns(
    timestamps: dict[str, int],
    preferred: tuple[str, ...] = (
        "application_start_ns",
        "profile_start_ns",
    ),
) -> tuple[str, int]:
    for key in preferred:
        if key in timestamps:
            return key, timestamps[key]
    raise ValueError(
        "timestamps.json must contain application_start_ns or profile_start_ns"
    )


def pre_run_window(
    timestamps: dict[str, int],
    seconds: float = 5.0,
) -> tuple[str, int, int]:
    if seconds <= 0:
        raise ValueError("pre-run window seconds must be positive")

    anchor_name, anchor_ns = choose_pre_run_anchor_ns(timestamps)
    lower_ns = anchor_ns - int(seconds * 1e9)
    return anchor_name, lower_ns, anchor_ns


def _parse_float(value: str | None) -> float | None:
    if value in (None, ""):
        return None
    try:
        parsed = float(value)
    except ValueError:
        return None
    return parsed if math.isfinite(parsed) else None


def summarize_telemetry_window(
    telemetry_csv: Path,
    timestamps_json: Path,
    *,
    seconds: float = 5.0,
    columns: tuple[str, ...] | None = None,
) -> dict[str, Any]:
    rows = read_csv(telemetry_csv)
    if "timestamp_ns" not in rows[0]:
        raise ValueError(
            f"{telemetry_csv} must contain canonical timestamp_ns"
        )

    timestamps = read_timestamps(timestamps_json)
    anchor_name, lower_ns, upper_ns = pre_run_window(
        timestamps,
        seconds,
    )

    selected = [
        row
        for row in rows
        if lower_ns <= int(row["timestamp_ns"]) < upper_ns
    ]

    if columns is None:
        excluded = {
            "timestamp_ns",
            "dcgm_timestamp_ns",
            "netdata_timestamp_ns",
            "alignment_delta_ms",
        }
        columns = tuple(
            key
            for key in rows[0]
            if key not in excluded
        )

    features: dict[str, float | None] = {}
    for column in columns:
        values = [
            parsed
            for parsed in (
                _parse_float(row.get(column))
                for row in selected
            )
            if parsed is not None
        ]
        key = (
            "pre_"
            + column.lower()
            .replace("%", "pct")
            .replace(" ", "_")
            .replace("(", "")
            .replace(")", "")
            .replace("/", "_")
        )
        features[key] = statistics.mean(values) if values else None

    return {
        "schema_version": "pre6g.pre-run-telemetry/v1",
        "anchor": anchor_name,
        "window_start_ns": lower_ns,
        "window_end_ns": upper_ns,
        "window_seconds": seconds,
        "sample_count": len(selected),
        "features": features,
    }


def extract_trace_features(
    sqlite_path: Path,
    detection: dict[str, Any],
    *,
    global_pid: int,
    context_id: int,
) -> dict[str, Any]:
    capture_start = int(detection["capture_start_ns"])
    capture_end = int(detection["capture_end_ns"])
    horizon_seconds = float(detection["horizon_seconds"])

    connection = sqlite3.connect(
        f"file:{sqlite_path.resolve()}?mode=ro",
        uri=True,
    )
    try:
        rows = connection.execute(
            """
            SELECT end-start, shortName
            FROM CUPTI_ACTIVITY_KIND_KERNEL
            WHERE globalPid=?
              AND contextId=?
              AND start BETWEEN ? AND ?
            """,
            (
                int(global_pid),
                int(context_id),
                capture_start,
                capture_end,
            ),
        ).fetchall()
    finally:
        connection.close()

    if not rows:
        raise ValueError(
            "No CUDA kernels found in the selected target/window"
        )

    durations_us = [
        float(duration_ns) / 1000.0
        for duration_ns, _ in rows
    ]
    event_rate = len(rows) / horizon_seconds
    unique_kernel_count = len(
        {short_name for _, short_name in rows}
    )
    busy_fraction = (
        sum(durations_us) / 1_000_000.0
    ) / horizon_seconds

    values = [
        math.log(float(detection["detected_period_ms"])),
        math.log(event_rate),
        math.log(unique_kernel_count),
        math.log(statistics.median(durations_us)),
        math.log(statistics.mean(durations_us)),
        math.log(max(busy_fraction, 1e-12)),
        float(detection["anchor_robust_cv"]),
    ]

    return {
        "kernel_event_count": len(rows),
        "kernel_event_rate_hz": event_rate,
        "unique_kernel_count": unique_kernel_count,
        "median_kernel_duration_us": statistics.median(durations_us),
        "mean_kernel_duration_us": statistics.mean(durations_us),
        "kernel_busy_fraction": busy_fraction,
        "features": dict(zip(TRACE_FEATURE_NAMES, values)),
        "feature_vector": values,
    }
