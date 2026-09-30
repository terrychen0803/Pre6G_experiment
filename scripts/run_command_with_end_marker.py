from __future__ import annotations

import argparse
import json
import signal
import subprocess
import sys
import time
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the original training command unchanged and persist its wall-clock execution window for telemetry cropping.")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command[1:] if args.command and args.command[0] == "--" else args.command
    if not command:
        parser.error("a command is required after --")
    process: subprocess.Popen | None = None
    forwarded_signal: int | None = None

    def forward(signum: int, _frame: object) -> None:
        nonlocal forwarded_signal
        forwarded_signal = signum
        if process is not None and process.poll() is None:
            process.send_signal(signum)

    signal.signal(signal.SIGTERM, forward)
    signal.signal(signal.SIGINT, forward)
    started = time.time_ns()
    returncode: int | None = None
    try:
        process = subprocess.Popen(command)
        returncode = process.wait()
    finally:
        finished = time.time_ns()
        payload = {
            "schema_version": "pre6g.application-window/v1",
            "started_at_unix_ns": started,
            "finished_at_unix_ns": finished,
            "command": command,
            "returncode": returncode,
            "forwarded_signal": forwarded_signal,
        }
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if forwarded_signal is not None:
        return 128 + forwarded_signal
    if returncode is None:
        return 1
    return 128 - returncode if returncode < 0 else returncode


if __name__ == "__main__":
    raise SystemExit(main())
