"""Produce the yolo-v2-long diagnostic artifact from a marker-free Nsight trace."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

from pre6g_experiment.long_trace_detector import analyze_long_trace
from pre6g_experiment.marker_free import load_trace


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sqlite", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--global-pid", type=int)
    parser.add_argument("--context-id", type=int)
    args = parser.parse_args()
    trace = load_trace(args.sqlite, global_pid=args.global_pid, context_id=args.context_id)
    summary, windows = analyze_long_trace(trace["events"], trace["names"])
    summary["target_global_pid"] = trace["target"]["global_pid"]
    summary["target_context_id"] = trace["target"]["context_id"]
    summary["target_process_identified"] = True
    summary["hardware_trace"] = bool(trace["hardware_trace"])
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "trajectory.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    with (args.output_dir / "windows.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(windows[0]) if windows else [
            "window_index", "start_seconds", "end_seconds", "kernel_count", "accepted",
            "detected_period_ms", "confidence", "anchor_name", "harmonic_corrected", "rejection_reason",
        ])
        writer.writeheader()
        writer.writerows(windows)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
