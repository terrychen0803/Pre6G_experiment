from __future__ import annotations

import argparse
import csv
import gzip
import json
import sqlite3
import statistics
from collections import Counter
from pathlib import Path

from pre6g_experiment.marker_free import (
    list_cuda_groups,
    select_cuda_group,
)


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Extract marker-free target-process CUDA kernel events from "
            "an Nsight Systems SQLite export."
        )
    )
    parser.add_argument("--sqlite", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--summary-output", type=Path, required=True)
    parser.add_argument("--global-pid", type=int)
    parser.add_argument("--context-id", type=int)
    args = parser.parse_args()

    connection = sqlite3.connect(
        f"file:{args.sqlite.resolve()}?mode=ro",
        uri=True,
    )
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
            raise SystemExit(
                f"missing required Nsight tables: {missing}"
            )

        groups = list_cuda_groups(connection)
        target = select_cuda_group(
            connection,
            args.global_pid,
            args.context_id,
        )

        rows = connection.execute(
            """
            SELECT
                start,
                end,
                shortName,
                globalPid,
                contextId,
                streamId
            FROM CUPTI_ACTIVITY_KIND_KERNEL
            WHERE globalPid=?
              AND contextId=?
            ORDER BY start
            """,
            (
                target["global_pid"],
                target["context_id"],
            ),
        )
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.summary_output.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        fieldnames = [
            "event_id",
            "start_ns",
            "end_ns",
            "duration_ns",
            "relative_start_ns",
            "inter_arrival_ns",
            "short_name_id",
            "global_pid",
            "context_id",
            "stream_id",
        ]

        first_start = None
        previous_start = None
        last_start = None
        event_count = 0
        durations: list[int] = []
        inter_arrivals: list[int] = []
        streams: Counter[int] = Counter()
        kernels: Counter[int] = Counter()

        opener = (
            gzip.open
            if args.output.suffix == ".gz"
            else open
        )
        with opener(
            args.output,
            "wt",
            newline="",
            encoding="utf-8",
        ) as handle:
            writer = csv.DictWriter(
                handle,
                fieldnames=fieldnames,
            )
            writer.writeheader()

            for (
                start,
                end,
                short_name,
                global_pid,
                context_id,
                stream_id,
            ) in rows:
                start = int(start)
                end = int(end)
                if first_start is None:
                    first_start = start

                duration_ns = end - start
                inter_arrival_ns = (
                    None
                    if previous_start is None
                    else start - previous_start
                )

                writer.writerow(
                    {
                        "event_id": event_count,
                        "start_ns": start,
                        "end_ns": end,
                        "duration_ns": duration_ns,
                        "relative_start_ns": (
                            start - first_start
                        ),
                        "inter_arrival_ns": (
                            ""
                            if inter_arrival_ns is None
                            else inter_arrival_ns
                        ),
                        "short_name_id": short_name,
                        "global_pid": global_pid,
                        "context_id": context_id,
                        "stream_id": stream_id,
                    }
                )

                durations.append(duration_ns)
                if inter_arrival_ns is not None:
                    inter_arrivals.append(
                        inter_arrival_ns
                    )
                streams[int(stream_id)] += 1
                kernels[int(short_name)] += 1
                previous_start = start
                last_start = start
                event_count += 1
    finally:
        connection.close()

    if not event_count or first_start is None or last_start is None:
        raise SystemExit("selected target contains no CUDA kernel events")

    span_s = (last_start - first_start) / 1e9

    summary = {
        "schema_version": "pre6g.marker-free-extraction/v1",
        "input": {
            "sqlite": str(args.sqlite),
        },
        "target": target,
        "cuda_groups": groups,
        "selection_policy": (
            "explicit"
            if args.global_pid is not None
            else "single-group-auto"
        ),
        "events": {
            "count": event_count,
            "first_start_ns": first_start,
            "last_start_ns": last_start,
            "start_span_s": span_s,
            "event_rate_hz": (
                event_count / span_s
                if span_s > 0
                else None
            ),
        },
        "duration_ns": {
            "median": statistics.median(durations),
            "mean": statistics.mean(durations),
        },
        "inter_arrival_ns": {
            "median": (
                statistics.median(inter_arrivals)
                if inter_arrivals
                else None
            ),
            "mean": (
                statistics.mean(inter_arrivals)
                if inter_arrivals
                else None
            ),
        },
        "streams": {
            "count": len(streams),
            "top10": streams.most_common(10),
        },
        "kernel_short_names": {
            "unique": len(kernels),
            "top20": kernels.most_common(20),
        },
        "production_input_policy": {
            "uses_iterations_csv": False,
            "uses_nvtx": False,
            "uses_callbacks": False,
            "uses_epoch_labels": False,
            "uses_batch_labels": False,
        },
    }

    args.summary_output.write_text(
        json.dumps(
            summary,
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    print(
        json.dumps(
            summary,
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
