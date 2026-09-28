from __future__ import annotations

import collections
import math
import sqlite3
import statistics
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class DetectorProfile:
    name: str
    horizons_seconds: tuple[int, ...]
    deployment_horizons: tuple[int, ...]
    tail_fraction: float
    min_occurrences: int
    max_occurrences: int
    min_period_ms: float
    max_period_ms: float
    cluster_tolerance: float
    max_cluster_members: int
    deployment_min_confidence: float
    deployment_stability_tolerance: float
    harmonic_policy: str


YOLO_V1 = DetectorProfile(
    name="yolo-v1",
    horizons_seconds=(5, 7, 9, 12, 15, 20, 30),
    deployment_horizons=(7, 9, 12, 15, 20, 30),
    tail_fraction=0.60,
    min_occurrences=8,
    max_occurrences=800,
    min_period_ms=20.0,
    max_period_ms=2000.0,
    cluster_tolerance=0.08,
    max_cluster_members=12,
    deployment_min_confidence=0.60,
    deployment_stability_tolerance=0.10,
    harmonic_policy="supported-2x-yolo",
)

PROFILES = {YOLO_V1.name: YOLO_V1}


def detector_profile(name: str) -> DetectorProfile:
    try:
        return PROFILES[name]
    except KeyError as exc:
        raise ValueError(f"Unknown detector profile: {name!r}") from exc


def median(values: list[float]) -> float:
    return float(statistics.median(values))


def median_absolute_deviation(values: list[float]) -> float:
    center = median(values)
    return median([abs(value - center) for value in values])


def relative_difference(left: float, right: float) -> float:
    return abs(left - right) / max(left, right)


def list_cuda_groups(connection: sqlite3.Connection) -> list[dict[str, int]]:
    rows = connection.execute(
        """
        SELECT
            globalPid,
            contextId,
            COUNT(*) AS n,
            MIN(start) AS first_start,
            MAX(start) AS last_start
        FROM CUPTI_ACTIVITY_KIND_KERNEL
        GROUP BY globalPid, contextId
        ORDER BY n DESC
        """
    ).fetchall()
    return [
        {
            "global_pid": int(global_pid),
            "context_id": int(context_id),
            "kernel_count": int(count),
            "first_start_ns": int(first_start),
            "last_start_ns": int(last_start),
        }
        for global_pid, context_id, count, first_start, last_start in rows
    ]


def select_cuda_group(
    connection: sqlite3.Connection,
    global_pid: int | None = None,
    context_id: int | None = None,
) -> dict[str, int]:
    groups = list_cuda_groups(connection)
    if not groups:
        raise ValueError("Trace contains no CUDA kernel process/context groups")

    if (global_pid is None) != (context_id is None):
        raise ValueError("Specify both global_pid and context_id together")

    if global_pid is not None and context_id is not None:
        matches = [
            group
            for group in groups
            if group["global_pid"] == global_pid
            and group["context_id"] == context_id
        ]
        if len(matches) != 1:
            raise ValueError(
                "Requested CUDA process/context was not found exactly once: "
                f"globalPid={global_pid}, contextId={context_id}"
            )
        return matches[0]

    if len(groups) != 1:
        summary = ", ".join(
            f"({item['global_pid']},{item['context_id']}):{item['kernel_count']}"
            for item in groups[:10]
        )
        raise ValueError(
            "Multiple CUDA process/context groups are present. "
            "Failing closed to avoid mixing co-tenant activity. "
            "Specify --global-pid and --context-id explicitly. "
            f"Top groups: {summary}"
        )

    return groups[0]


def load_trace(
    database: Path,
    *,
    global_pid: int | None = None,
    context_id: int | None = None,
    include_nvtx_audit: bool = False,
) -> dict[str, Any]:
    connection = sqlite3.connect(f"file:{database.resolve()}?mode=ro", uri=True)
    try:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        required = {"CUPTI_ACTIVITY_KIND_KERNEL", "StringIds"}
        missing = sorted(required - tables)
        if missing:
            raise ValueError(f"Missing required Nsight tables: {missing}")

        target = select_cuda_group(connection, global_pid, context_id)
        events = [
            (int(start), int(short_name))
            for start, short_name in connection.execute(
                """
                SELECT start, shortName
                FROM CUPTI_ACTIVITY_KIND_KERNEL
                WHERE globalPid=? AND contextId=?
                ORDER BY start
                """,
                (target["global_pid"], target["context_id"]),
            )
        ]
        names = {
            int(identifier): str(value)
            for identifier, value in connection.execute(
                "SELECT id, value FROM StringIds"
            )
        }

        nvtx: list[tuple[str, int]] = []
        if include_nvtx_audit and "NVTX_EVENTS" in tables:
            nvtx = [
                (str(text), int(start))
                for text, start in connection.execute(
                    """
                    SELECT text, start
                    FROM NVTX_EVENTS
                    WHERE text LIKE 'TRAIN_ITER_%'
                    ORDER BY start
                    """
                )
            ]

        diagnostic = (
            "\n".join(
                str(row[0])
                for row in connection.execute(
                    "SELECT text FROM DIAGNOSTIC_EVENT"
                )
                if row[0]
            )
            if "DIAGNOSTIC_EVENT" in tables
            else ""
        )
        all_groups = list_cuda_groups(connection)
    finally:
        connection.close()

    if not events:
        raise ValueError("Selected CUDA process/context contains no kernel events")

    return {
        "events": events,
        "names": names,
        "target": target,
        "groups": all_groups,
        "nvtx": nvtx,
        "nvtx_available": "NVTX_EVENTS" in tables,
        "hardware_trace": "Hardware tracing used for CUDA tracing" in diagnostic,
        "nvtx_warning": "Not all NVTX events might have been collected" in diagnostic,
    }


def build_candidates(
    events: list[tuple[int, int]],
    horizon_seconds: int,
    profile: DetectorProfile,
) -> tuple[list[dict[str, Any]], int, int]:
    capture_start = events[0][0]
    capture_end = capture_start + int(horizon_seconds * 1e9)

    occurrences: dict[int, list[int]] = collections.defaultdict(list)
    for timestamp, token in events:
        if timestamp > capture_end:
            break
        occurrences[token].append(timestamp)

    candidates: list[dict[str, Any]] = []
    for token, timestamps in occurrences.items():
        count = len(timestamps)
        if not profile.min_occurrences <= count <= profile.max_occurrences:
            continue

        gaps_ms = [
            (right - left) / 1e6
            for left, right in zip(timestamps, timestamps[1:])
        ]
        if not gaps_ms:
            continue

        all_period = median(gaps_ms)
        tail_start = int(len(gaps_ms) * (1.0 - profile.tail_fraction))
        tail_gaps = gaps_ms[tail_start:]
        period = median(tail_gaps)

        if not (profile.min_period_ms <= all_period <= profile.max_period_ms):
            continue
        if not (profile.min_period_ms <= period <= profile.max_period_ms):
            continue

        robust_cv = median_absolute_deviation(tail_gaps) / period
        ordinary_cv = (
            statistics.stdev(gaps_ms) / statistics.mean(gaps_ms)
            if len(gaps_ms) > 1
            else math.inf
        )
        coverage = (
            (timestamps[-1] - timestamps[0])
            / max(1, capture_end - capture_start)
        )
        score = (
            math.log(count)
            * coverage
            / (1.0 + 2.0 * robust_cv + 0.5 * ordinary_cv)
        )

        candidates.append(
            {
                "token": token,
                "count": count,
                "period_ms": period,
                "all_period_ms": all_period,
                "robust_cv": robust_cv,
                "ordinary_cv": ordinary_cv,
                "coverage": coverage,
                "score": score,
            }
        )

    candidates.sort(key=lambda item: item["score"], reverse=True)
    return candidates, capture_start, capture_end


def cluster_candidates(
    candidates: list[dict[str, Any]],
    profile: DetectorProfile,
) -> list[dict[str, Any]]:
    clusters: list[dict[str, Any]] = []
    used: set[int] = set()

    for seed in candidates:
        if seed["token"] in used:
            continue

        members = [
            item
            for item in candidates
            if item["token"] not in used
            and relative_difference(item["period_ms"], seed["period_ms"])
            <= profile.cluster_tolerance
        ]
        used.update(item["token"] for item in members)
        members = sorted(
            members,
            key=lambda item: item["score"],
            reverse=True,
        )[: profile.max_cluster_members]

        clusters.append(
            {
                "period_ms": median(
                    [item["period_ms"] for item in members]
                ),
                "score": sum(item["score"] for item in members),
                "member_count": len(members),
                "leader": members[0],
                "members": members,
            }
        )

    return sorted(
        clusters,
        key=lambda item: item["score"],
        reverse=True,
    )


def select_cluster(
    clusters: list[dict[str, Any]],
    profile: DetectorProfile,
) -> tuple[dict[str, Any] | None, bool]:
    if not clusters:
        return None, False

    selected = clusters[0]

    if profile.harmonic_policy != "supported-2x-yolo":
        return selected, False

    # YOLO-validated policy inherited from the reference detector:
    # repeated loader/sort anchors can occur roughly twice per semantic
    # training iteration. This policy is NOT a generic workload rule.
    higher = [
        item
        for item in clusters[1:]
        if 1.7 <= item["period_ms"] / selected["period_ms"] <= 2.3
        and item["score"] >= 0.30 * selected["score"]
        and item["leader"]["robust_cv"] <= 0.60
    ]
    if higher:
        return max(higher, key=lambda item: item["score"]), True

    return selected, False


def detect(
    events: list[tuple[int, int]],
    names: dict[int, str],
    horizon_seconds: int,
    profile: DetectorProfile = YOLO_V1,
) -> dict[str, Any]:
    candidates, capture_start, capture_end = build_candidates(
        events,
        horizon_seconds,
        profile,
    )
    clusters = cluster_candidates(candidates, profile)
    selected, harmonic_corrected = select_cluster(clusters, profile)

    if selected is None:
        return {
            "accepted": False,
            "detected_unit": "execution_cycle",
            "detector_profile": profile.name,
            "rejection_reason": "no recurrent anchor candidates",
            "capture_start_ns": capture_start,
            "capture_end_ns": capture_end,
            "candidate_count": 0,
            "complete_cycles": 0,
        }

    leader = selected["leader"]
    accepted = (
        selected["member_count"] >= 2
        and leader["coverage"] >= 0.25
        and leader["robust_cv"] <= 0.60
    )
    reasons: list[str] = []

    if selected["member_count"] < 2:
        reasons.append("no anchor consensus")
    if leader["coverage"] < 0.25:
        reasons.append("insufficient temporal coverage")
    if leader["robust_cv"] > 0.60:
        reasons.append("unstable anchor intervals")

    confidence = min(
        1.0,
        0.35 * min(1.0, selected["member_count"] / 6.0)
        + 0.30 * min(1.0, leader["coverage"] / 0.75)
        + 0.35 * max(0.0, 1.0 - leader["robust_cv"] / 0.60),
    )

    period_ns = selected["period_ms"] * 1e6
    complete_cycles = int(
        max(0, capture_end - capture_start) // max(1.0, period_ns)
    )

    return {
        "accepted": accepted,
        "detected_unit": "execution_cycle",
        "detector_profile": profile.name,
        "rejection_reason": " | ".join(reasons),
        "detected_period_ms": selected["period_ms"],
        "confidence": confidence,
        "harmonic_corrected": harmonic_corrected,
        "harmonic_policy": profile.harmonic_policy,
        "anchor_name": names.get(
            leader["token"],
            str(leader["token"]),
        ),
        "anchor_token": leader["token"],
        "anchor_occurrences": leader["count"],
        "anchor_coverage": leader["coverage"],
        "anchor_robust_cv": leader["robust_cv"],
        "cluster_member_count": selected["member_count"],
        "cluster_score": selected["score"],
        "candidate_count": len(candidates),
        "capture_start_ns": capture_start,
        "capture_end_ns": capture_end,
        "complete_cycles": complete_cycles,
    }


def select_adaptive(
    rows: list[dict[str, Any]],
    profile: DetectorProfile = YOLO_V1,
) -> dict[str, Any]:
    ordered = sorted(rows, key=lambda item: int(item["horizon_seconds"]))
    previous: dict[str, Any] | None = None

    for row in ordered:
        if int(row["horizon_seconds"]) not in profile.deployment_horizons:
            continue

        if (
            row.get("accepted")
            and float(row.get("confidence", 0.0))
            >= profile.deployment_min_confidence
        ):
            if previous is not None and relative_difference(
                float(row["detected_period_ms"]),
                float(previous["detected_period_ms"]),
            ) <= profile.deployment_stability_tolerance:
                chosen = dict(row)
                chosen["selection_reason"] = "two-window stability"
                chosen["two_window_stability_pass"] = True
                chosen["previous_horizon_seconds"] = previous["horizon_seconds"]
                chosen["previous_period_ms"] = previous["detected_period_ms"]
                return chosen

            previous = row
        else:
            previous = None

    accepted = [row for row in ordered if row.get("accepted")]
    chosen = dict(accepted[-1] if accepted else ordered[-1])
    chosen["selection_reason"] = (
        "latest accepted fallback" if accepted else "rejected"
    )
    chosen["two_window_stability_pass"] = False
    return chosen


def oracle_nvtx_metrics(
    nvtx: list[tuple[str, int]],
    capture_end_ns: int,
    *,
    tail_fraction: float = 0.60,
) -> dict[str, float | int | None]:
    """Optional post-detection audit. Never required for production detection."""
    if not nvtx:
        return {
            "observed_iteration_count": 0,
            "prefix_oracle_period_ms": None,
            "full_steady_oracle_period_ms": None,
        }

    all_starts = [start for _, start in nvtx]
    steady_starts = [
        start
        for text, start in nvtx
        if "TRAIN_ITER_0021" <= text <= "TRAIN_ITER_0128"
    ]

    full_gaps = [
        (right - left) / 1e6
        for left, right in zip(steady_starts, steady_starts[1:])
    ]
    prefix_starts = [
        start for start in all_starts if start <= capture_end_ns
    ]
    prefix_gaps = [
        (right - left) / 1e6
        for left, right in zip(prefix_starts, prefix_starts[1:])
    ]
    tail_start = int(len(prefix_gaps) * (1.0 - tail_fraction))
    prefix_tail = prefix_gaps[tail_start:]

    return {
        "observed_iteration_count": len(prefix_starts),
        "prefix_oracle_period_ms": (
            median(prefix_tail) if prefix_tail else None
        ),
        "full_steady_oracle_period_ms": (
            median(full_gaps) if full_gaps else None
        ),
    }
