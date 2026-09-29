from __future__ import annotations

import argparse
import json
import signal
import subprocess
import sys
import time
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
NETDATA_QUERY_SCRIPT = REPOSITORY_ROOT / "scripts" / "query_netdata_window.py"
DCGM_SCRIPT = REPOSITORY_ROOT / "scripts" / "collect_dcgm.py"
ALIGN_SCRIPT = REPOSITORY_ROOT / "scripts" / "align_telemetry.py"


def _terminate(process: subprocess.Popen, timeout_s: float = 10.0) -> int:
    if process.poll() is not None:
        return int(process.returncode)
    process.send_signal(signal.SIGINT)
    try:
        return int(process.wait(timeout=timeout_s))
    except subprocess.TimeoutExpired:
        process.kill()
        return int(process.wait())


def _wait_for_samples(
    path: Path,
    minimum_rows: int,
    timeout_s: float,
    process: subprocess.Popen | None = None,
    log_path: Path | None = None,
) -> None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if process is not None and process.poll() is not None:
            suffix = f"; inspect {log_path}" if log_path is not None else ""
            raise RuntimeError(
                f"collector for {path} exited early with code "
                f"{process.returncode}{suffix}"
            )
        if path.is_file():
            try:
                lines = sum(1 for _ in path.open("r", encoding="utf-8"))
            except OSError:
                lines = 0
            if lines >= minimum_rows + 1:
                return
        time.sleep(0.25)
    raise RuntimeError(
        f"{path} did not produce {minimum_rows} telemetry row(s) "
        f"within {timeout_s}s"
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Run a profiled command while actively polling DCGM. Netdata is "
            "not polled during the workload; after the run this wrapper queries "
            "the continuously collected Netdata Parent history for the recorded "
            "absolute wall-clock window, then aligns both streams."
        )
    )
    parser.add_argument("--netdata-url", required=True)
    parser.add_argument("--dcgm-url", required=True)
    parser.add_argument("--node", required=True)
    parser.add_argument("--gpu-uuid", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--pre-roll-s", type=float, default=5.0)
    parser.add_argument("--post-roll-s", type=float, default=2.0)
    parser.add_argument("--interval-ms", type=int, default=1000)
    parser.add_argument("--tolerance-ms", type=float, default=750.0)
    parser.add_argument("--min-coverage", type=float, default=0.90)
    parser.add_argument("--max-gap-s", type=float, default=2.0)
    parser.add_argument("--netdata-timeout-s", type=float, default=8.0)
    parser.add_argument("--netdata-retries", type=int, default=3)
    parser.add_argument("--netdata-settle-s", type=float, default=1.0)
    parser.add_argument(
        "--require-alignment-pass",
        action="store_true",
        help=(
            "Make telemetry readiness a hard wrapper failure. By default, "
            "runtime profiling may continue when Netdata/DCGM/alignment is "
            "invalid; downstream power/ranking gates must reject it."
        ),
    )
    parser.add_argument(
        "--accept-command-returncode",
        type=int,
        action="append",
        default=[],
        help=(
            "Explicitly accept a non-zero wrapped-command return code. "
            "Repeat this option for multiple codes. This is intended for "
            "known profiler termination semantics such as Nsight "
            "--duration with --kill=sigterm returning 143; non-zero codes "
            "remain failures unless explicitly listed."
        ),
    )
    parser.add_argument(
        "command",
        nargs=argparse.REMAINDER,
        help="Command to run after --.",
    )
    args = parser.parse_args()

    command = list(args.command)
    if command and command[0] == "--":
        command = command[1:]
    if not command:
        raise SystemExit("A command is required after --")
    if args.pre_roll_s < 0 or args.post_roll_s < 0:
        raise SystemExit("pre/post roll must be >= 0")
    if args.interval_ms <= 0:
        raise SystemExit("--interval-ms must be positive")
    if args.netdata_timeout_s <= 0:
        raise SystemExit("--netdata-timeout-s must be positive")
    if args.netdata_retries < 0:
        raise SystemExit("--netdata-retries must be >= 0")
    if args.netdata_settle_s < 0:
        raise SystemExit("--netdata-settle-s must be >= 0")

    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)

    netdata_csv = output / "netdata.csv"
    dcgm_csv = output / "dcgm.csv"
    aligned_csv = output / "aligned-telemetry.csv"
    quality_json = output / "alignment-quality.json"
    timestamps_json = output / "timestamps.json"
    netdata_log_path = output / "netdata.log"
    dcgm_log_path = output / "dcgm.log"

    dcgm_log = dcgm_log_path.open("w", encoding="utf-8")

    timestamps = {
        "schema_version": "pre6g.profile-wall-clock/v2",
        "pre_window_start_ns": time.time_ns(),
        "application_start_ns": None,
        "profile_start_ns": None,
        "steady_window_start_ns": None,
        "steady_window_end_ns": None,
        "profile_end_ns": None,
        "application_end_ns": None,
        "post_window_end_ns": None,
        "notes": {
            "profile_boundary": (
                "outer Nsight/workload command boundary; no user "
                "instrumentation required"
            ),
            "application_boundary": (
                "unknown unless recoverable without modifying user code"
            ),
            "steady_boundary": (
                "unknown here; marker-free/runtime analysis may derive a "
                "separate accepted steady window"
            ),
        },
    }

    dcgm_process = subprocess.Popen(
        [
            sys.executable,
            str(DCGM_SCRIPT),
            "--url",
            args.dcgm_url,
            "--node",
            args.node,
            "--gpu-uuid",
            args.gpu_uuid,
            "--duration-s",
            "0",
            "--interval-ms",
            str(args.interval_ms),
            "--output",
            str(dcgm_csv),
        ],
        stdout=dcgm_log,
        stderr=subprocess.STDOUT,
    )

    command_returncode: int | None = None
    dcgm_preflight_error: str | None = None
    dcgm_returncode: int | None = None
    netdata_query_returncode: int | None = None
    alignment_returncode: int | None = None

    try:
        minimum_pre_samples = max(
            1,
            int(args.pre_roll_s * 1000 / args.interval_ms),
        )
        startup_timeout = max(10.0, args.pre_roll_s + 8.0)
        try:
            _wait_for_samples(
                dcgm_csv,
                minimum_pre_samples,
                startup_timeout,
                process=dcgm_process,
                log_path=dcgm_log_path,
            )
        except RuntimeError as exc:
            dcgm_preflight_error = str(exc)
            print(
                f"WARNING: DCGM telemetry preflight failed: {exc}",
                file=sys.stderr,
            )
            if args.require_alignment_pass:
                raise

        timestamps["profile_start_ns"] = time.time_ns()
        completed = subprocess.run(command, check=False)
        command_returncode = int(completed.returncode)
        timestamps["profile_end_ns"] = time.time_ns()

        if args.post_roll_s:
            time.sleep(args.post_roll_s)
    finally:
        timestamps["post_window_end_ns"] = time.time_ns()
        timestamps_json.write_text(
            json.dumps(timestamps, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

        dcgm_returncode = _terminate(dcgm_process)
        dcgm_log.close()

    with netdata_log_path.open("w", encoding="utf-8") as netdata_log:
        netdata_query = subprocess.run(
            [
                sys.executable,
                str(NETDATA_QUERY_SCRIPT),
                "--url",
                args.netdata_url,
                "--node",
                args.node,
                "--after-ns",
                str(timestamps["pre_window_start_ns"]),
                "--before-ns",
                str(timestamps["post_window_end_ns"]),
                "--timeout-s",
                str(args.netdata_timeout_s),
                "--retries",
                str(args.netdata_retries),
                "--settle-s",
                str(args.netdata_settle_s),
                "--output",
                str(netdata_csv),
            ],
            stdout=netdata_log,
            stderr=subprocess.STDOUT,
            check=False,
        )
    netdata_query_returncode = int(netdata_query.returncode)

    telemetry_sources_ready = (
        dcgm_preflight_error is None
        and dcgm_returncode == 0
        and netdata_query_returncode == 0
        and netdata_csv.is_file()
        and dcgm_csv.is_file()
    )

    if telemetry_sources_ready:
        alignment = subprocess.run(
            [
                sys.executable,
                str(ALIGN_SCRIPT),
                "--netdata",
                str(netdata_csv),
                "--dcgm",
                str(dcgm_csv),
                "--output",
                str(aligned_csv),
                "--quality-output",
                str(quality_json),
                "--tolerance-ms",
                str(args.tolerance_ms),
                "--min-coverage",
                str(args.min_coverage),
                "--max-gap-s",
                str(args.max_gap_s),
            ],
            check=False,
        )
        alignment_returncode = int(alignment.returncode)
    else:
        print(
            "WARNING: telemetry sources are incomplete; skipping alignment. "
            f"DCGM preflight error={dcgm_preflight_error!r}, "
            f"DCGM rc={dcgm_returncode}, "
            f"Netdata query rc={netdata_query_returncode}",
            file=sys.stderr,
        )

    telemetry_ready = (
        telemetry_sources_ready
        and alignment_returncode == 0
        and quality_json.is_file()
    )

    accepted_command_returncodes = sorted(
        set(int(value) for value in args.accept_command_returncode)
    )
    command_returncode_accepted = (
        command_returncode == 0
        or command_returncode in accepted_command_returncodes
    )

    result = {
        "schema_version": "pre6g.profile-telemetry-window/v2",
        "node": args.node,
        "gpu_uuid": args.gpu_uuid,
        "command": command,
        "command_returncode": command_returncode,
        "accepted_command_returncodes": accepted_command_returncodes,
        "command_returncode_accepted": command_returncode_accepted,
        "telemetry": {
            "netdata_mode": "historical-query",
            "netdata_query_returncode": netdata_query_returncode,
            "dcgm_mode": "active-poll",
            "dcgm_preflight_error": dcgm_preflight_error,
            "dcgm_returncode": dcgm_returncode,
            "alignment_returncode": alignment_returncode,
            "alignment_required": bool(args.require_alignment_pass),
            "ready_for_power": telemetry_ready,
        },
        "artifacts": {
            "timestamps": str(timestamps_json),
            "netdata": str(netdata_csv),
            "netdata_log": str(netdata_log_path),
            "dcgm": str(dcgm_csv),
            "dcgm_log": str(dcgm_log_path),
            "aligned_telemetry": str(aligned_csv),
            "alignment_quality": str(quality_json),
        },
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))

    if not command_returncode_accepted:
        raise SystemExit(command_returncode if command_returncode is not None else 1)

    if args.require_alignment_pass and not telemetry_ready:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
