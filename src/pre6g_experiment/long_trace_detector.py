"""Versioned long-trace CUDA period trajectory, independent of training markers.

This is the yolo-v2-long diagnostic detector for 120–180 second captures.
Its 15-second windows reuse the validated yolo-v1 anchor detector. The
trajectory gate is deliberately separate from the frozen yolo-v1 runtime model.
"""

from __future__ import annotations

import bisect
import math
import statistics
from typing import Any

from .marker_free import YOLO_V1, detect


PROFILE = "yolo-v2-long"
WINDOW_SECONDS = 15
SKIP_SECONDS = 10
MIN_WINDOWS = 6
MIN_COVERAGE = 0.80
MIN_CONFIDENCE = 0.60
MAX_DRIFT = 0.10
MAX_WINDOW_CV = 0.10


def _median(values: list[float]) -> float:
    return float(statistics.median(values))


def _percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    lower = math.floor(position)
    upper = math.ceil(position)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def _harmonic_conflict(periods: list[float]) -> bool:
    for index, left in enumerate(periods):
        for right in periods[index + 1:]:
            ratio = max(left, right) / min(left, right)
            if abs(ratio - 2.0) <= 0.16:
                return True
    return False


def analyze_long_trace(
    events: list[tuple[int, int]], names: dict[int, str], *,
    window_seconds: int = WINDOW_SECONDS,
    skip_seconds: int = SKIP_SECONDS,
    min_windows: int = MIN_WINDOWS,
    min_coverage: float = MIN_COVERAGE,
    min_confidence: float = MIN_CONFIDENCE,
    max_drift: float = MAX_DRIFT,
    max_window_cv: float = MAX_WINDOW_CV,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    if not events:
        raise ValueError("trace contains no CUDA kernel events")
    if window_seconds <= 0 or skip_seconds < 0 or min_windows < 1:
        raise ValueError("invalid long-detector window configuration")
    if not 0 < min_coverage <= 1 or not 0 <= min_confidence <= 1:
        raise ValueError("invalid long-detector coverage/confidence threshold")
    if max_drift < 0 or max_window_cv < 0:
        raise ValueError("long-detector drift/CV thresholds must be nonnegative")
    if any(right[0] < left[0] for left, right in zip(events, events[1:])):
        raise ValueError("kernel events must be sorted")
    first, last = events[0][0], events[-1][0]
    window_ns = round(window_seconds * 1e9)
    analysis_start = first + round(skip_seconds * 1e9)
    count = max(0, (last - analysis_start) // window_ns)
    starts = [event[0] for event in events]
    windows: list[dict[str, Any]] = []
    for index in range(count):
        start = analysis_start + index * window_ns
        end = start + window_ns
        subset = events[bisect.bisect_left(starts, start):bisect.bisect_left(starts, end)]
        detected = (
            detect(subset, names, window_seconds, YOLO_V1)
            if subset else {"accepted": False, "rejection_reason": "no CUDA kernel events"}
        )
        accepted = bool(detected.get("accepted")) and float(detected.get("confidence", 0)) >= min_confidence
        windows.append({
            "window_index": index,
            "start_seconds": (start - first) / 1e9,
            "end_seconds": (end - first) / 1e9,
            "kernel_count": len(subset),
            "accepted": accepted,
            "detected_period_ms": detected.get("detected_period_ms"),
            "confidence": detected.get("confidence"),
            "anchor_name": detected.get("anchor_name"),
            "harmonic_corrected": detected.get("harmonic_corrected"),
            "rejection_reason": (
                "confidence below threshold" if detected.get("accepted") and not accepted
                else detected.get("rejection_reason", "")
            ),
        })
    good = [row for row in windows if row["accepted"]]
    periods = [float(row["detected_period_ms"]) for row in good]
    coverage = len(good) / len(windows) if windows else 0.0
    observed = len(periods) / sum(1 / period for period in periods) if periods else None
    midpoint = len(windows) // 2
    early = [float(row["detected_period_ms"]) for row in windows[:midpoint] if row["accepted"]]
    late = [float(row["detected_period_ms"]) for row in windows[midpoint:] if row["accepted"]]
    early_period = _median(early) if early else None
    late_period = _median(late) if late else None
    drift = (late_period - early_period) / early_period if early_period is not None and late_period is not None else None
    cv = statistics.stdev(periods) / statistics.mean(periods) if len(periods) >= 2 else None
    reasons = []
    if len(windows) < min_windows:
        reasons.append("too_few_complete_windows")
    if coverage < min_coverage:
        reasons.append("insufficient_accepted_window_coverage")
    if not windows or not windows[-1]["accepted"]:
        reasons.append("latest_window_unreliable")
    if drift is None:
        reasons.append("cannot_compare_early_and_late_windows")
    elif abs(drift) > max_drift:
        reasons.append("observed_period_drift")
    if cv is not None and cv > max_window_cv:
        reasons.append("high_window_period_variation")
    if _harmonic_conflict(periods):
        reasons.append("possible_cross_window_harmonic_alias")
    status = "stable_observed" if not reasons else "unreliable_for_extrapolation"
    return {
        "schema_version": "pre6g.long-trace-detector/v1",
        "detector_profile": PROFILE,
        "base_window_detector_profile": YOLO_V1.name,
        "status": status,
        "reasons": reasons,
        "trace_duration_seconds": (last - first) / 1e9,
        "analysis_start_seconds": skip_seconds,
        "window_seconds": window_seconds,
        "complete_windows": len(windows),
        "accepted_windows": len(good),
        "accepted_window_coverage": coverage,
        "observed_throughput_period_ms": observed,
        "recent_period_ms": _median(periods[-max(2, math.ceil(len(periods) / 3)):]) if periods else None,
        "early_period_ms": early_period,
        "late_period_ms": late_period,
        "early_to_late_drift_fraction": drift,
        "window_period_cv": cv,
        "window_period_p10_ms": _percentile(periods, 0.10),
        "window_period_p90_ms": _percentile(periods, 0.90),
        "median_detection_confidence": _median([float(row["confidence"]) for row in good]) if good else None,
        "recommended_period_ms": observed if status == "stable_observed" else None,
        "used_as_runtime_model_input": False,
        "future_load_forecast_validated": False,
        "production_input_policy": {"uses_nvtx": False, "uses_iterations_csv": False,
                                    "uses_callbacks": False},
    }, windows
