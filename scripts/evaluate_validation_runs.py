from __future__ import annotations

import argparse
import csv
import json
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


FIVE_MINUTES = timedelta(minutes=5)


def timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"timestamp must include timezone offset: {value!r}")
    return parsed.astimezone(timezone.utc)


def pod_intervals(pods: dict[str, Any], plan: dict[str, Any]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    expected = {row["node"]: row["job_name"] for row in plan["jobs"]}
    for pod in pods.get("items", []):
        labels = pod.get("metadata", {}).get("labels", {})
        node = labels.get("pre6g.io/candidate-node")
        if node not in expected or labels.get("pre6g.io/validation-id") != plan["validation_id"]:
            continue
        if labels.get("job-name") != expected[node]:
            continue
        if node in result:
            raise ValueError(f"multiple Pods for {node}; retries require manual review")
        statuses = pod.get("status", {}).get("containerStatuses", [])
        trainer = next((item for item in statuses if item.get("name") == "trainer"), None)
        terminated = (trainer or {}).get("state", {}).get("terminated", {})
        if not terminated.get("startedAt") or not terminated.get("finishedAt"):
            raise ValueError(f"{node}: trainer has no completed container timestamps")
        start = timestamp(terminated["startedAt"])
        end = timestamp(terminated["finishedAt"])
        if end <= start:
            raise ValueError(f"{node}: invalid trainer interval")
        result[node] = {
            "pod_name": pod["metadata"]["name"],
            "started_at": start.isoformat().replace("+00:00", "Z"),
            "finished_at": end.isoformat().replace("+00:00", "Z"),
            "elapsed_s": (end - start).total_seconds(),
            "exit_code": terminated.get("exitCode"),
            "succeeded": terminated.get("exitCode") == 0 and pod.get("status", {}).get("phase") == "Succeeded",
        }
    missing = set(expected) - set(result)
    if missing:
        raise ValueError(f"missing completed Pod intervals for: {', '.join(sorted(missing))}")
    return result


def pdu_energy(csv_path: Path, *, start: datetime, end: datetime, timestamp_column: str, power_column: str, interval_position: str) -> dict[str, Any]:
    if interval_position not in {"start", "end"}:
        raise ValueError("PDU interval position must be start or end")
    rows = []
    with csv_path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        if timestamp_column not in (reader.fieldnames or []) or power_column not in (reader.fieldnames or []):
            raise ValueError(f"{csv_path}: missing timestamp or power column")
        for row in reader:
            point = timestamp(row[timestamp_column])
            watts = float(row[power_column])
            if not math.isfinite(watts) or watts < 0:
                raise ValueError(f"{csv_path}: invalid non-negative watt value")
            left = point if interval_position == "start" else point - FIVE_MINUTES
            rows.append((left, left + FIVE_MINUTES, watts))
    rows.sort(key=lambda item: item[0])
    if not rows:
        raise ValueError(f"{csv_path}: no PDU samples")
    cursor = start
    energy_j = 0.0
    used = 0
    for left, right, watts in rows:
        if right <= start or left >= end:
            continue
        if left > cursor:
            raise ValueError(f"{csv_path}: PDU coverage gap from {cursor.isoformat()} to {left.isoformat()}")
        if left < cursor and used:
            raise ValueError(f"{csv_path}: overlapping or duplicate PDU intervals")
        overlap_start = max(left, start)
        overlap_end = min(right, end)
        if overlap_end > overlap_start:
            energy_j += watts * (overlap_end - overlap_start).total_seconds()
            cursor = overlap_end
            used += 1
    if cursor < end:
        raise ValueError(f"{csv_path}: PDU data ends before trainer finishes")
    return {
        "gross_energy_wh": energy_j / 3600,
        "mean_power_w": energy_j / (end - start).total_seconds(),
        "sample_count_used": used,
        "sample_period_s": 300,
        "interval_position": interval_position,
        "source": str(csv_path),
    }


def evaluate(plan: dict[str, Any], pods: dict[str, Any], pdu_paths: dict[str, Path], *, timestamp_column: str, power_column: str, interval_position: str) -> dict[str, Any]:
    intervals = pod_intervals(pods, plan)
    rows = []
    for predicted in plan["jobs"]:
        node = predicted["node"]
        actual = intervals[node]
        row = {**predicted, **actual}
        row["runtime_proxy_error_percent"] = 100 * (predicted["predicted_steady_runtime_s"] - actual["elapsed_s"]) / actual["elapsed_s"]
        eta = predicted.get("predicted_total_job_runtime_s")
        row["full_job_runtime_error_percent"] = (
            100 * (eta - actual["elapsed_s"]) / actual["elapsed_s"]
            if predicted.get("total_job_runtime_status") == "ready" and eta is not None and actual["succeeded"]
            else None
        )
        if node in pdu_paths:
            row["pdu"] = pdu_energy(
                pdu_paths[node],
                start=timestamp(actual["started_at"]),
                end=timestamp(actual["finished_at"]),
                timestamp_column=timestamp_column,
                power_column=power_column,
                interval_position=interval_position,
            )
            measured_wh = row["pdu"]["gross_energy_wh"]
            row["energy_proxy_error_percent"] = (
                100 * (predicted["predicted_steady_gross_energy_wh"] - measured_wh) / measured_wh
                if measured_wh > 0 else None
            )
        rows.append(row)
    complete = all(row.get("succeeded") and "pdu" in row for row in rows)
    actual_winner = min(rows, key=lambda row: row["pdu"]["gross_energy_wh"])["node"] if complete else None
    return {
        "schema_version": "pre6g.validation-result/v1",
        "validation_id": plan["validation_id"],
        "selected_node": plan["selected_node"],
        "actual_lowest_gross_energy_node": actual_winner,
        "selection_match": actual_winner == plan["selected_node"] if complete else None,
        "evaluation_complete": complete,
        "nodes": rows,
        "limitations": [
            "PDU values are treated as five-minute interval-average whole-node watts; boundary intervals are overlap-weighted.",
            "Steady runtime proxy errors compare steady training against the full trainer container. Full-job errors are reported separately only for ready ETA and successful runs.",
            "Synthetic data supports workflow and runtime/energy validation, not object-detection accuracy claims.",
            "Background node load and GPU sharing may affect gross PDU energy; record occupancy and idle baseline separately.",
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Compare frozen selection with all-node trainer timing and optional 5-minute PDU CSV exports.")
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--pods-json", type=Path, required=True)
    parser.add_argument("--pdu", action="append", default=[], help="NODE=CSV_PATH; repeat for each candidate")
    parser.add_argument("--timestamp-column", default="timestamp")
    parser.add_argument("--power-column", default="power_w")
    parser.add_argument("--pdu-interval", choices=("start", "end"), help="Whether each average-power timestamp marks the five-minute interval start or end")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.pdu and not args.pdu_interval:
        parser.error("--pdu-interval is required when PDU data is provided")
    pdu_paths = {}
    for item in args.pdu:
        node, separator, path = item.partition("=")
        if not separator or not node or not path or node in pdu_paths:
            parser.error("--pdu must be a unique NODE=CSV_PATH")
        pdu_paths[node] = Path(path)
    plan = json.loads(args.plan.read_text(encoding="utf-8"))
    expected = {row["node"] for row in plan["jobs"]}
    if not set(pdu_paths).issubset(expected):
        parser.error("PDU node names must match the validation plan")
    pods = json.loads(args.pods_json.read_text(encoding="utf-8"))
    result = evaluate(plan, pods, pdu_paths, timestamp_column=args.timestamp_column, power_column=args.power_column, interval_position=args.pdu_interval or "end")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    if args.output.exists():
        raise ValueError(f"refusing to overwrite existing validation result: {args.output}")
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
