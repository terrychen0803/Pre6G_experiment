from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]


@dataclass
class Stage:
    name: str
    command: list[str]
    outputs: list[Path] = field(default_factory=list)
    required: bool = True


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def relative(path: Path, base: Path) -> str:
    try:
        return str(path.resolve().relative_to(base.resolve())).replace("\\", "/")
    except ValueError:
        return str(path.resolve()).replace("\\", "/")


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def command_text(command: list[str]) -> str:
    return subprocess.list2cmdline(command)


class PipelineRunner:
    def __init__(self, output_dir: Path, *, resume: bool = False) -> None:
        self.output_dir = output_dir.resolve()
        self.resume = resume
        self.records: list[dict[str, Any]] = []
        self.output_dir.mkdir(parents=True, exist_ok=True)

        env = os.environ.copy()
        source = str(ROOT / "src")
        existing = env.get("PYTHONPATH")
        env["PYTHONPATH"] = source if not existing else source + os.pathsep + existing
        self.env = env

    def run(self, stage: Stage) -> None:
        started = time.monotonic()
        record: dict[str, Any] = {
            "name": stage.name,
            "required": stage.required,
            "command": command_text(stage.command),
            "outputs": [relative(path, self.output_dir) for path in stage.outputs],
            "started_at": utc_now(),
        }

        if self.resume and stage.outputs and all(path.exists() for path in stage.outputs):
            record.update(
                {
                    "status": "skipped-existing",
                    "duration_s": 0.0,
                    "finished_at": utc_now(),
                }
            )
            self.records.append(record)
            print(f"[skip] {stage.name}")
            return

        print(f"[run ] {stage.name}")
        completed = subprocess.run(
            stage.command,
            cwd=ROOT,
            env=self.env,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            check=False,
        )
        duration = round(time.monotonic() - started, 3)
        log_path = self.output_dir / "logs" / f"{stage.name}.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text(
            "$ "
            + command_text(stage.command)
            + "\n\n--- stdout ---\n"
            + completed.stdout
            + "\n--- stderr ---\n"
            + completed.stderr,
            encoding="utf-8",
        )

        record.update(
            {
                "status": "passed" if completed.returncode == 0 else "failed",
                "exit_code": completed.returncode,
                "duration_s": duration,
                "finished_at": utc_now(),
                "log": relative(log_path, self.output_dir),
            }
        )
        self.records.append(record)

        if completed.returncode != 0:
            message = completed.stderr.strip() or completed.stdout.strip()
            raise RuntimeError(f"stage {stage.name!r} failed: {message}")


def py_script(name: str, *arguments: str | Path) -> list[str]:
    return [sys.executable, str(ROOT / "scripts" / name), *(str(value) for value in arguments)]


def module(*arguments: str | Path) -> list[str]:
    return [sys.executable, "-m", "pre6g_experiment", *(str(value) for value in arguments)]


def build_stages(args: argparse.Namespace, output: Path) -> list[Stage]:
    if args.mode == "validation-evaluate":
        result = output / "validation-result.json"
        command = py_script(
            "evaluate_validation_runs.py",
            "--plan", args.validation_plan,
            "--pods-json", args.pods_json,
            "--timestamp-column", args.timestamp_column,
            "--power-column", args.power_column,
            "--output", result,
        )
        if args.pdu_interval:
            command.extend(["--pdu-interval", args.pdu_interval])
        for item in args.pdu:
            command.extend(["--pdu", item])
        return [Stage("01-evaluate-all-node-runs", command, [result])]

    discovery = output / "workload-discovery.json"
    stages = [
        Stage(
            "01-discover-work",
            py_script(
                "discover_work.py",
                "--job",
                args.job,
                "--output",
                discovery,
            ),
            [discovery],
        )
    ]

    if args.mode == "demo":
        selected_job = output / "production-job.yaml"
        decision_report = output / "decision-report.json"
        stages.append(
            Stage(
                "02-rank-and-render",
                module(
                    "decide",
                    "--job",
                    args.job,
                    "--results",
                    args.node_results,
                    "--output",
                    selected_job,
                    "--report",
                    decision_report,
                    "--allow-synthetic",
                ),
                [selected_job, decision_report],
            )
        )
        return stages

    if args.mode == "validation":
        ranking = output / "provisional-ranking.json"
        plan = output / "validation-plan.json"
        jobs = output / "validation-jobs.yaml"
        commands = output / "deploy-commands.txt"
        stages.extend(
            [
                Stage(
                    "02-provisional-rank",
                    py_script(
                        "provisional_rank_nodes.py",
                        "--input",
                        args.ranking_input,
                        "--output",
                        ranking,
                    ),
                    [ranking],
                ),
                Stage(
                    "03-plan-all-node-training",
                    py_script(
                        "plan_validation_runs.py",
                        "--job",
                        args.job,
                        "--ranking",
                        ranking,
                        "--discovery",
                        discovery,
                        "--validation-id",
                        args.validation_id,
                        "--target-minutes",
                        str(args.target_minutes),
                        "--output-dir",
                        output,
                    ),
                    [plan, jobs, commands],
                ),
            ]
        )
        return stages

    detector_dir = output / "marker-free"
    detection = detector_dir / "marker-free-discovery.json"
    features = output / "runtime-features.json"
    profile_result = output / "profile-result.json"
    runtime_prediction = output / "runtime-prediction.json"
    semantic_runtime = output / "semantic-runtime.json"

    detect_command = py_script(
        "evaluate_trace_event_periods.py",
        "--sqlite",
        args.sqlite,
        "--output-dir",
        detector_dir,
        "--detector-profile",
        args.detector_profile,
    )
    if args.global_pid is not None:
        detect_command.extend(["--global-pid", str(args.global_pid)])
    if args.context_id is not None:
        detect_command.extend(["--context-id", str(args.context_id)])
    if args.audit_nvtx:
        detect_command.append("--audit-nvtx")

    stages.extend(
        [
            Stage(
                "02-detect-execution-cycle",
                detect_command,
                [detection, detector_dir / "horizons.csv"],
            ),
            Stage(
                "03-build-runtime-features",
                py_script(
                    "build_runtime_features.py",
                    "--sqlite",
                    args.sqlite,
                    "--detection-json",
                    detection,
                    "--output",
                    features,
                    "--node",
                    args.node,
                    "--device-id",
                    args.device_id,
                    "--workload-id",
                    args.workload_id,
                ),
                [features],
            ),
            Stage(
                "04-package-profile-result",
                py_script(
                    "package_profile_result.py",
                    "--task-id",
                    args.task_id,
                    "--node",
                    args.node,
                    "--device-id",
                    args.device_id,
                    "--detection-json",
                    detection,
                    "--runtime-features-json",
                    features,
                    "--output",
                    profile_result,
                ),
                [profile_result],
            ),
            Stage(
                "05-predict-runtime",
                py_script(
                    "predict_runtime.py",
                    "--model",
                    args.runtime_model,
                    "--features",
                    features,
                    "--output",
                    runtime_prediction,
                ),
                [runtime_prediction],
            ),
            Stage(
                "06-aggregate-runtime",
                py_script(
                    "aggregate_runtime.py",
                    "--runtime-prediction",
                    runtime_prediction,
                    "--work-discovery",
                    discovery,
                    "--output",
                    semantic_runtime,
                ),
                [semantic_runtime],
            ),
        ]
    )

    aligned_telemetry = args.aligned_telemetry
    alignment_quality = args.alignment_quality
    if args.netdata_telemetry is not None:
        aligned_telemetry = output / "aligned-telemetry.csv"
        alignment_quality = output / "alignment-quality.json"
        stages.append(
            Stage(
                "07-align-telemetry",
                py_script(
                    "align_telemetry.py",
                    "--netdata",
                    args.netdata_telemetry,
                    "--dcgm",
                    args.dcgm_telemetry,
                    "--output",
                    aligned_telemetry,
                    "--quality-output",
                    alignment_quality,
                ),
                [aligned_telemetry, alignment_quality],
            )
        )

    if aligned_telemetry is not None:
        series = output / "predicted-power-series.json"
        summary = output / "power-prediction-summary.json"
        power_command = py_script(
            "predict_power_from_aligned_telemetry.py",
            "--aligned-telemetry",
            aligned_telemetry,
            "--bundle-dir",
            args.power_bundle,
            "--output-series",
            series,
            "--output-summary",
            summary,
        )
        if alignment_quality is not None:
            power_command.extend(["--alignment-quality", str(alignment_quality)])
        stages.append(Stage("08-predict-power", power_command, [series, summary]))

    if args.node_results is not None:
        selected_job = output / "production-job.yaml"
        decision_report = output / "decision-report.json"
        decision = module(
            "decide",
            "--job",
            args.job,
            "--results",
            args.node_results,
            "--output",
            selected_job,
            "--report",
            decision_report,
        )
        if args.allow_synthetic:
            decision.append("--allow-synthetic")
        stages.append(
            Stage(
                "09-rank-and-render",
                decision,
                [selected_job, decision_report],
            )
        )

    return stages


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(
        description=(
            "Run the Pre6G experiment stages as one resumable workflow and "
            "write a machine-readable run summary."
        )
    )
    root.add_argument("--mode", choices=("demo", "profile", "validation", "validation-evaluate"), default="demo")
    root.add_argument("--job", type=Path)
    root.add_argument("--output-dir", type=Path, required=True)
    root.add_argument("--resume", action="store_true")
    root.add_argument("--node-results", type=Path)
    root.add_argument("--allow-synthetic", action="store_true")
    root.add_argument("--ranking-input", type=Path)
    root.add_argument("--validation-id")
    root.add_argument("--target-minutes", type=float, default=40.0)
    root.add_argument("--validation-plan", type=Path)
    root.add_argument("--pods-json", type=Path)
    root.add_argument("--pdu", action="append", default=[])
    root.add_argument("--pdu-interval", choices=("start", "end"))
    root.add_argument("--timestamp-column", default="timestamp")
    root.add_argument("--power-column", default="power_w")

    root.add_argument("--sqlite", type=Path)
    root.add_argument("--node")
    root.add_argument("--device-id")
    root.add_argument("--runtime-model", type=Path)
    root.add_argument("--task-id", default="pre6g-profile-run")
    root.add_argument("--workload-id", default="user-workload")
    root.add_argument("--detector-profile", default="yolo-v1", choices=("yolo-v1",))
    root.add_argument("--global-pid", type=int)
    root.add_argument("--context-id", type=int)
    root.add_argument("--audit-nvtx", action="store_true")

    root.add_argument("--aligned-telemetry", type=Path)
    root.add_argument("--alignment-quality", type=Path)
    root.add_argument("--netdata-telemetry", type=Path)
    root.add_argument("--dcgm-telemetry", type=Path)
    root.add_argument("--power-bundle", type=Path)
    return root


def validate(args: argparse.Namespace) -> None:
    if args.mode == "validation-evaluate":
        if args.validation_plan is None or args.pods_json is None:
            raise ValueError("validation-evaluate requires --validation-plan and --pods-json")
        if args.pdu and not args.pdu_interval:
            raise ValueError("--pdu-interval is required when --pdu is provided")
        if args.resume:
            raise ValueError("validation-evaluate does not support --resume")
        return
    if args.job is None:
        raise ValueError(f"{args.mode} mode requires --job")
    if args.mode == "demo" and args.node_results is None:
        args.node_results = ROOT / "examples" / "yolo26" / "synthetic-node-results.json"

    if args.mode == "profile":
        missing = [
            name
            for name in ("sqlite", "node", "device_id", "runtime_model")
            if getattr(args, name) in (None, "")
        ]
        if missing:
            raise ValueError("profile mode requires: " + ", ".join(missing))

    if args.mode == "validation":
        if args.ranking_input is None or not args.validation_id:
            raise ValueError("validation mode requires --ranking-input and --validation-id")
        if not 30 <= args.target_minutes <= 50:
            raise ValueError("--target-minutes must be between 30 and 50")
        if args.resume:
            raise ValueError("validation mode does not support --resume; use a new output directory")

    raw_values = (args.netdata_telemetry, args.dcgm_telemetry)
    if any(value is not None for value in raw_values) and not all(
        value is not None for value in raw_values
    ):
        raise ValueError(
            "--netdata-telemetry and --dcgm-telemetry must be provided together"
        )
    if args.aligned_telemetry is not None and args.netdata_telemetry is not None:
        raise ValueError(
            "use either --aligned-telemetry or raw Netdata/DCGM telemetry, not both"
        )

    has_telemetry = args.aligned_telemetry is not None or all(
        value is not None for value in raw_values
    )
    if has_telemetry and args.power_bundle is None:
        raise ValueError("power telemetry input requires --power-bundle")
    if args.power_bundle is not None and not has_telemetry:
        raise ValueError(
            "--power-bundle requires --aligned-telemetry or raw Netdata/DCGM telemetry"
        )


def main() -> int:
    args = parser().parse_args()
    for name in (
        "job",
        "node_results",
        "sqlite",
        "runtime_model",
        "aligned_telemetry",
        "alignment_quality",
        "netdata_telemetry",
        "dcgm_telemetry",
        "power_bundle",
        "ranking_input",
        "validation_plan",
        "pods_json",
    ):
        value = getattr(args, name, None)
        if value is not None:
            setattr(args, name, value.resolve())
    output = args.output_dir.resolve()
    summary_path = output / "run-summary.json"
    started_at = utc_now()
    runner = PipelineRunner(output, resume=args.resume)

    try:
        validate(args)
        stages = build_stages(args, output)
        for stage in stages:
            runner.run(stage)
    except (OSError, ValueError, RuntimeError) as exc:
        write_json(
            summary_path,
            {
                "schema_version": "pre6g.run-summary/v1",
                "mode": args.mode,
                "status": "failed",
                "started_at": started_at,
                "finished_at": utc_now(),
                "error": str(exc),
                "stages": runner.records,
            },
        )
        print(f"error: {exc}", file=sys.stderr)
        return 2

    write_json(
        summary_path,
        {
            "schema_version": "pre6g.run-summary/v1",
            "mode": args.mode,
            "status": "completed",
            "started_at": started_at,
            "finished_at": utc_now(),
            "job": relative(args.job, ROOT) if args.job is not None else None,
            "output_dir": str(output).replace("\\", "/"),
            "stages": runner.records,
        },
    )
    print(f"[done] {summary_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
