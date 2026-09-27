from __future__ import annotations

import argparse
import csv
import json
import statistics
from bisect import bisect_left
from pathlib import Path
from typing import Any


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    if not rows:
        raise ValueError(f"No rows in {path}")
    if "timestamp_ns" not in rows[0]:
        raise ValueError(f"{path} must contain timestamp_ns")
    return rows


def max_gap_s(rows: list[dict[str, str]]) -> float:
    timestamps = sorted(int(row["timestamp_ns"]) for row in rows)
    if len(timestamps) < 2:
        return float("inf")
    return max((right - left) / 1e9 for left, right in zip(timestamps, timestamps[1:]))


def nearest_row(
    timestamp_ns: int,
    rows: list[dict[str, str]],
    timestamps: list[int],
) -> tuple[dict[str, str], int] | None:
    position = bisect_left(timestamps, timestamp_ns)
    candidates: list[tuple[dict[str, str], int]] = []

    if position < len(rows):
        candidates.append((rows[position], timestamps[position]))
    if position > 0:
        candidates.append((rows[position - 1], timestamps[position - 1]))

    if not candidates:
        return None

    row, matched_timestamp_ns = min(
        candidates,
        key=lambda pair: abs(pair[1] - timestamp_ns),
    )
    return row, matched_timestamp_ns


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Nearest-neighbor alignment for Netdata and DCGM telemetry"
    )
    parser.add_argument("--netdata", type=Path, required=True)
    parser.add_argument("--dcgm", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--quality-output", type=Path)
    parser.add_argument("--tolerance-ms", type=float, default=750.0)
    parser.add_argument("--min-coverage", type=float, default=0.90)
    parser.add_argument("--max-gap-s", type=float, default=2.0)
    args = parser.parse_args()

    netdata_rows = read_csv(args.netdata)
    dcgm_rows = read_csv(args.dcgm)

    netdata_rows = sorted(netdata_rows, key=lambda row: int(row["timestamp_ns"]))
    dcgm_rows = sorted(dcgm_rows, key=lambda row: int(row["timestamp_ns"]))
    netdata_timestamps = [int(row["timestamp_ns"]) for row in netdata_rows]

    tolerance_ns = int(args.tolerance_ms * 1_000_000)
    aligned: list[dict[str, Any]] = []
    deltas_ms: list[float] = []

    for dcgm_row in dcgm_rows:
        dcgm_timestamp_ns = int(dcgm_row["timestamp_ns"])
        match = nearest_row(dcgm_timestamp_ns, netdata_rows, netdata_timestamps)
        if match is None:
            continue

        netdata_row, netdata_timestamp_ns = match
        delta_ns = abs(netdata_timestamp_ns - dcgm_timestamp_ns)
        if delta_ns > tolerance_ns:
            continue

        row: dict[str, Any] = {
            "timestamp_ns": dcgm_timestamp_ns,
            "dcgm_timestamp_ns": dcgm_timestamp_ns,
            "netdata_timestamp_ns": netdata_timestamp_ns,
            "alignment_delta_ms": delta_ns / 1e6,
        }

        for key, value in netdata_row.items():
            if key == "timestamp_ns":
                continue
            row[key] = value

        for key, value in dcgm_row.items():
            if key in {
                "timestamp_ns",
                "request_start_ns",
                "request_end_ns",
                "timestamp_utc",
            }:
                continue
            row[key] = value

        aligned.append(row)
        deltas_ms.append(delta_ns / 1e6)

    coverage = len(aligned) / len(dcgm_rows)
    netdata_gap_s = max_gap_s(netdata_rows)
    dcgm_gap_s = max_gap_s(dcgm_rows)

    quality = {
        "schema_version": "pre6g.telemetry-alignment-quality/v1",
        "netdata_samples": len(netdata_rows),
        "dcgm_samples": len(dcgm_rows),
        "aligned_samples": len(aligned),
        "alignment_coverage": coverage,
        "tolerance_ms": args.tolerance_ms,
        "median_alignment_delta_ms": (
            statistics.median(deltas_ms) if deltas_ms else None
        ),
        "max_alignment_delta_ms": max(deltas_ms) if deltas_ms else None,
        "max_netdata_gap_s": netdata_gap_s,
        "max_dcgm_gap_s": dcgm_gap_s,
        "pass": (
            coverage >= args.min_coverage
            and netdata_gap_s <= args.max_gap_s
            and dcgm_gap_s <= args.max_gap_s
            and bool(aligned)
        ),
    }

    args.output.parent.mkdir(parents=True, exist_ok=True)
    if not aligned:
        args.output.write_text("", encoding="utf-8")
    else:
        fieldnames = list(aligned[0].keys())
        with args.output.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(aligned)

    quality_text = json.dumps(quality, ensure_ascii=False, indent=2)
    print(quality_text)

    if args.quality_output:
        args.quality_output.parent.mkdir(parents=True, exist_ok=True)
        args.quality_output.write_text(quality_text + "\n", encoding="utf-8")

    raise SystemExit(0 if quality["pass"] else 1)


if __name__ == "__main__":
    main()
