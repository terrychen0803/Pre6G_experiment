from __future__ import annotations

import argparse
import json
import signal
import subprocess
import sys
import time
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
NETDATA_SCRIPT = REPOSITORY_ROOT / "scripts" / "collect_netdata.py"
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


def _wait_for_samples(path: Path, minimum_rows: int, timeout_s: float) -> None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if path.is_file():
            try:
                lines = sum(1 for _ in path.open("r", encoding="utf-8"))
            except OSError:
                lines = 0
            if lines >= minimum_rows + 1:
                return
        time.sleep(0.25)
    raise RuntimeError(
        f"{path} did not produce {minimum_rows} telemetry row(s) within {timeout_s}s"
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Run a command while collecting Netdata and DCGM telemetry in the "
            "same wall-clock window, then align both streams by Unix-ns timestamp."
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

    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)

    netdata_csv = output / "netdata.csv"
    dcgm_csv = output / "dcgm.csv"
    aligned_csv = output / "aligned-telemetry.csv"
    quality_json = output / "alignment-quality.json"
    timestamps_json = output / "timestamps.json"
    netdata_log_path = output / "netdata.log"
    dcgm_log_path = output / "dcgm.log"

    netdata_log = netdata_log_path.open("w", encoding="utf-8")
    dcgm_log = dcgm_log_path.open("w", encoding="utf-8")

    timestamps = {
        "schema_version": "pre6g.profile-wall-clock/v1",
        "pre_window_start_ns": time.time_ns(),
        "profile_start_ns": None,
        "profile_end_ns": None,
        "post_window_end_ns": None,
    }

    netdata_process = subprocess.Popen(
        [
            sys.executable,
            str(NETDATA_SCRIPT),
            "--url",
            args.netdata_url,
            "--node",
            args.node,
            "--duration-s",
            "0",
            "--interval-ms",
            str(args.interval_ms),
            "--output",
            str(netdata_csv),
        ],
        stdout=netdata_log,
        stderr=subprocess.STDOUT,
    )
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
    alignment_returncode: int | None = None

    try:
        minimum_pre_samples = max(
            1,
            int(args.pre_roll_s * 1000 / args.interval_ms),
        )
        startup_timeout = max(10.0, args.pre_roll_s + 8.0)
        _wait_for_samples(netdata_csv, minimum_pre_samples, startup_timeout)
        _wait_for_samples(dcgm_csv, minimum_pre_samples, startup_timeout)

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

        netdata_rc = _terminate(netdata_process)
        dcgm_rc = _terminate(dcgm_process)
        netdata_log.close()
        dcgm_log.close()

    if netdata_rc != 0:
        raise SystemExit(
            f"Netdata collector exited with {netdata_rc}; inspect {netdata_log_path}"
        )
    if dcgm_rc != 0:
        raise SystemExit(
            f"DCGM collector exited with {dcgm_rc}; inspect {dcgm_log_path}"
        )

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

    result = {
        "schema_version": "pre6g.profile-telemetry-window/v1",
        "node": args.node,
        "gpu_uuid": args.gpu_uuid,
        "command": command,
        "command_returncode": command_returncode,
        "alignment_returncode": alignment_returncode,
        "artifacts": {
            "timestamps": str(timestamps_json),
            "netdata": str(netdata_csv),
            "dcgm": str(dcgm_csv),
            "aligned_telemetry": str(aligned_csv),
            "alignment_quality": str(quality_json),
        },
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))

    if command_returncode:
        raise SystemExit(command_returncode)
    if alignment_returncode:
        raise SystemExit(alignment_returncode)


if __name__ == "__main__":
    main()
