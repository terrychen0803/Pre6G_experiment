from __future__ import annotations

import argparse
import csv
import json
import math
from pathlib import Path

from pre6g_experiment.marker_free import (
    detector_profile,
    detect,
    load_trace,
    oracle_nvtx_metrics,
    select_adaptive,
)


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


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Production-style marker-free CUDA event period detection. "
            "NVTX is optional post-detection audit only."
        )
    )
    parser.add_argument("--sqlite", type=Path, required=True)
    parser.add_argument(
        "--output-dir",
        type=Path,
        required=True,
    )
    parser.add_argument(
        "--detector-profile",
        default="yolo-v1",
        choices=("yolo-v1",),
    )
    parser.add_argument("--global-pid", type=int)
    parser.add_argument("--context-id", type=int)
    parser.add_argument(
        "--audit-nvtx",
        action="store_true",
        help=(
            "Optionally join TRAIN_ITER_* NVTX after detection for audit. "
            "NVTX is never required or used by the detector."
        ),
    )
    args = parser.parse_args()

    profile = detector_profile(args.detector_profile)
    trace = load_trace(
        args.sqlite,
        global_pid=args.global_pid,
        context_id=args.context_id,
        include_nvtx_audit=args.audit_nvtx,
    )

    rows: list[dict] = []
    for horizon in profile.horizons_seconds:
        row = detect(
            trace["events"],
            trace["names"],
            horizon,
            profile,
        )
        row["horizon_seconds"] = horizon
        row["target_global_pid"] = trace["target"]["global_pid"]
        row["target_context_id"] = trace["target"]["context_id"]
        row["target_process_identified"] = True
        row["hardware_trace"] = bool(trace["hardware_trace"])
        rows.append(row)

    selected = select_adaptive(rows, profile)
    selected["schema_version"] = "pre6g.marker-free-discovery/v1"
    selected["status"] = (
        "detected"
        if selected.get("accepted")
        else "rejected"
    )
    selected["target_process_identified"] = True
    selected["target_global_pid"] = trace["target"]["global_pid"]
    selected["target_context_id"] = trace["target"]["context_id"]
    selected["hardware_trace"] = bool(trace["hardware_trace"])
    selected["nvtx_required"] = False
    selected["nvtx_available"] = bool(trace["nvtx_available"])
    selected["production_input_policy"] = {
        "uses_iterations_csv": False,
        "uses_nvtx": False,
        "uses_callbacks": False,
        "uses_epoch_labels": False,
        "uses_batch_labels": False,
    }

    if args.audit_nvtx:
        selected["nvtx_audit"] = oracle_nvtx_metrics(
            trace["nvtx"],
            int(selected["capture_end_ns"]),
            tail_fraction=profile.tail_fraction,
        )
        if (
            selected.get("accepted")
            and selected["nvtx_audit"].get("prefix_oracle_period_ms")
        ):
            oracle = float(
                selected["nvtx_audit"]["prefix_oracle_period_ms"]
            )
            predicted = float(selected["detected_period_ms"])
            selected["nvtx_audit"]["prefix_period_ape_percent"] = (
                abs(predicted - oracle) / oracle * 100.0
            )

    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)

    write_csv(output / "horizons.csv", rows)
    (output / "marker-free-discovery.json").write_text(
        json.dumps(
            selected,
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    contract = {
        "schema_version": "pre6g.marker-free-detector-contract/v1",
        "detector_profile": profile.name,
        "detected_unit": "execution_cycle",
        "required_tables": [
            "CUPTI_ACTIVITY_KIND_KERNEL",
            "StringIds",
        ],
        "optional_audit_tables": ["NVTX_EVENTS"],
        "period_range_ms": [
            profile.min_period_ms,
            profile.max_period_ms,
        ],
        "harmonic_policy": profile.harmonic_policy,
        "harmonic_policy_scope": "YOLO-validated; not generic",
        "evaluation_horizons_seconds": list(
            profile.horizons_seconds
        ),
        "deployment_horizons_seconds": list(
            profile.deployment_horizons
        ),
        "deployment_min_confidence": (
            profile.deployment_min_confidence
        ),
        "deployment_stability_tolerance": (
            profile.deployment_stability_tolerance
        ),
        "target_process_policy": (
            "single process/context auto-select; "
            "multiple groups require explicit globalPid/contextId"
        ),
        "forbidden_production_inputs": [
            "iterations.csv",
            "NVTX iteration labels",
            "training callbacks",
            "epoch labels",
            "batch labels",
        ],
    }
    (output / "detector-contract.json").write_text(
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
            selected,
            ensure_ascii=False,
            indent=2,
        )
    )

    if not selected.get("accepted"):
        raise SystemExit(2)
    if int(selected.get("complete_cycles", 0)) < 3:
        raise SystemExit(
            "detector accepted a period but fewer than three complete cycles were observed"
        )


if __name__ == "__main__":
    main()
