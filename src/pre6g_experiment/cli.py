from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import yaml

from .decision import production_job, rank_nodes
from .work import execution_contract, estimate_work


def load_yaml(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        value = yaml.safe_load(handle)
    if not isinstance(value, dict):
        raise ValueError("Expected one YAML object")
    if value.get("apiVersion") != "batch/v1" or value.get("kind") != "Job":
        raise ValueError("Only batch/v1 Job is supported")
    return value


def inspect_command(args: argparse.Namespace) -> int:
    job = load_yaml(args.job)
    workload = estimate_work(job)
    report = {
        "apiVersion": job.get("apiVersion"),
        "kind": job.get("kind"),
        "name": (job.get("metadata") or {}).get("name"),
        "namespace": (job.get("metadata") or {}).get("namespace", "default"),
        "execution_contract": execution_contract(job),
        "workload_spec": workload.to_dict(),
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


def decide_command(args: argparse.Namespace) -> int:
    job = load_yaml(args.job)
    workload = estimate_work(job)

    if (
        workload.total_work_units is None
        and not args.allow_per_work_unit_ranking
    ):
        raise ValueError(
            "Total work units are unknown. Profiling/per-work-unit analysis may "
            "continue, but automatic production placement stays profile-only. "
            "Declare/discover total work or pass --allow-per-work-unit-ranking "
            "only when that policy is intentional."
        )

    with args.results.open("r", encoding="utf-8") as handle:
        results = json.load(handle)
    if results.get("synthetic") and not args.allow_synthetic:
        raise ValueError("Synthetic results require --allow-synthetic")

    ranked, rejected = rank_nodes(
        results,
        workload.total_work_units,
        args.min_confidence,
        work_unit=workload.work_unit,
    )
    if not ranked:
        raise ValueError(f"No eligible nodes; rejection reasons: {rejected}")

    if len(ranked) < args.min_ready_nodes:
        raise ValueError(
            f"Only {len(ranked)} comparable node(s) passed the gate; "
            f"{args.min_ready_nodes} required for automatic placement. "
            f"Rejection reasons: {rejected}"
        )

    selected = ranked[0]
    output_job = production_job(job, selected.node)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8", newline="\n") as handle:
        yaml.safe_dump(output_job, handle, sort_keys=False, allow_unicode=True)

    report = {
        "synthetic": bool(results.get("synthetic")),
        "workload_spec": workload.to_dict(),
        "selected_node": selected.node,
        "ranked": [item.__dict__ for item in ranked],
        "rejected": rejected,
        "production_job": str(args.output),
    }
    if args.report is not None:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description="Pre6G experiment prototype")
    commands = root.add_subparsers(dest="command", required=True)

    inspect_parser = commands.add_parser(
        "inspect",
        help="inspect an opaque source Job and discover workload semantics",
    )
    inspect_parser.add_argument("--job", type=Path, required=True)
    inspect_parser.set_defaults(handler=inspect_command)

    decide_parser = commands.add_parser(
        "decide",
        help="rank nodes and render a production Job",
    )
    decide_parser.add_argument("--job", type=Path, required=True)
    decide_parser.add_argument("--results", type=Path, required=True)
    decide_parser.add_argument("--output", type=Path, required=True)
    decide_parser.add_argument(
        "--report",
        type=Path,
        help="Optional path for the JSON decision report.",
    )
    decide_parser.add_argument("--min-confidence", type=float, default=0.8)
    decide_parser.add_argument(
        "--min-ready-nodes",
        type=int,
        default=2,
        help="Minimum comparable nodes required for automatic placement.",
    )
    decide_parser.add_argument(
        "--allow-per-work-unit-ranking",
        action="store_true",
        help=(
            "Allow automatic placement when total work units are unknown. "
            "Use only when per-work-unit ranking is an intentional policy."
        ),
    )
    decide_parser.add_argument("--allow-synthetic", action="store_true")
    decide_parser.set_defaults(handler=decide_command)
    return root


def main() -> None:
    args = parser().parse_args()
    try:
        if getattr(args, "min_ready_nodes", 1) < 1:
            raise ValueError("--min-ready-nodes must be >= 1")
        status = args.handler(args)
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc
    raise SystemExit(status)


if __name__ == "__main__":
    main()
