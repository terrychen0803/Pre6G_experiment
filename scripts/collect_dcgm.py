from __future__ import annotations

import argparse
import csv
import math
import re
import signal
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


_RUNNING = True


def _stop(_signum, _frame) -> None:
    global _RUNNING
    _RUNNING = False


signal.signal(signal.SIGTERM, _stop)
signal.signal(signal.SIGINT, _stop)


METRICS = {
    "DCGM_FI_DEV_GPU_UTIL": "GPU Util%",
    "DCGM_FI_DEV_FB_USED": "GPU Mem Used(MB)",
    "DCGM_FI_DEV_GPU_TEMP": "GPU Temp(°C)",
    "DCGM_FI_DEV_POWER_USAGE": "GPU Power(W)",
}

LINE_RE = re.compile(r"^(?P<name>[A-Z0-9_]+)(?:\{(?P<labels>.*)\})?\s+(?P<value>[-+0-9.eE]+)$")
LABEL_RE = re.compile(r'([A-Za-z_][A-Za-z0-9_]*)="((?:\\.|[^"])*)"')


def parse_labels(text: str) -> dict[str, str]:
    return {key: value.replace(r'\"', '"') for key, value in LABEL_RE.findall(text)}


def parse_metric_text(text: str, gpu_uuid: str | None) -> tuple[dict[str, float], dict[str, str]]:
    matches: dict[str, list[tuple[float, dict[str, str]]]] = {name: [] for name in METRICS}

    for raw_line in text.splitlines():
        if not raw_line or raw_line.startswith("#"):
            continue
        match = LINE_RE.match(raw_line.strip())
        if not match:
            continue

        metric_name = match.group("name")
        if metric_name not in METRICS:
            continue

        labels = parse_labels(match.group("labels") or "")
        if gpu_uuid and labels.get("UUID") != gpu_uuid:
            continue

        value = float(match.group("value"))
        if not math.isfinite(value):
            continue
        matches[metric_name].append((value, labels))

    values: dict[str, float] = {}
    identity: dict[str, str] = {}

    for metric_name, column_name in METRICS.items():
        candidates = matches[metric_name]
        if not candidates:
            raise ValueError(f"Missing DCGM metric: {metric_name}")
        if len(candidates) > 1 and not gpu_uuid:
            raise ValueError(
                f"Multiple samples found for {metric_name}; pass --gpu-uuid to select one GPU"
            )

        value, labels = candidates[0]
        values[column_name] = value

        if not identity:
            identity = {
                "gpu_uuid": labels.get("UUID", ""),
                "hostname": labels.get("Hostname", ""),
                "gpu_model": labels.get("modelName", ""),
                "driver_version": labels.get("DCGM_FI_DRIVER_VERSION", ""),
                "gpu_index": labels.get("gpu", ""),
            }

    return values, identity


def fetch_text(url: str, timeout: float) -> tuple[str, int, int]:
    start_ns = time.time_ns()
    with urllib.request.urlopen(url, timeout=timeout) as response:
        body = response.read().decode("utf-8")
    end_ns = time.time_ns()
    return body, start_ns, end_ns


def utc_iso(timestamp_ns: int) -> str:
    dt = datetime.fromtimestamp(timestamp_ns / 1e9, tz=timezone.utc)
    return dt.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def collect(args: argparse.Namespace) -> dict[str, Any]:
    global _RUNNING
    _RUNNING = True

    interval_s = args.interval_ms / 1000.0
    sample_count = 0

    args.output.parent.mkdir(parents=True, exist_ok=True)

    columns = [
        "timestamp_ns",
        "timestamp_utc",
        "request_start_ns",
        "request_end_ns",
        "node",
        "gpu_uuid",
        "gpu_model",
        "driver_version",
        "gpu_index",
        "GPU Util%",
        "GPU Mem Used(MB)",
        "GPU Temp(°C)",
        "GPU Power(W)",
    ]

    first_timestamp_ns: int | None = None
    last_timestamp_ns: int | None = None
    monotonic_origin = time.monotonic()

    with args.output.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()

        index = 0
        while _RUNNING:
            if args.duration_s > 0 and index * interval_s >= args.duration_s:
                break

            target = monotonic_origin + index * interval_s
            delay = target - time.monotonic()
            if delay > 0:
                time.sleep(delay)
            if not _RUNNING:
                break

            body, request_start_ns, request_end_ns = fetch_text(args.url, args.timeout_s)
            timestamp_ns = (request_start_ns + request_end_ns) // 2
            values, identity = parse_metric_text(body, args.gpu_uuid)

            writer.writerow(
                {
                    "timestamp_ns": timestamp_ns,
                    "timestamp_utc": utc_iso(timestamp_ns),
                    "request_start_ns": request_start_ns,
                    "request_end_ns": request_end_ns,
                    "node": args.node or identity.get("hostname", ""),
                    **identity,
                    **values,
                }
            )
            handle.flush()

            first_timestamp_ns = first_timestamp_ns or timestamp_ns
            last_timestamp_ns = timestamp_ns
            sample_count += 1
            index += 1

    return {
        "schema_version": "pre6g.dcgm-collection/v1",
        "output": str(args.output),
        "url": args.url,
        "node": args.node,
        "gpu_uuid": args.gpu_uuid,
        "configured_interval_ms": args.interval_ms,
        "configured_duration_s": args.duration_s,
        "samples": sample_count,
        "first_timestamp_ns": first_timestamp_ns,
        "last_timestamp_ns": last_timestamp_ns,
    }


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Collect timestamped DCGM Exporter GPU telemetry"
    )
    p.add_argument("--url", required=True, help="DCGM /metrics endpoint")
    p.add_argument("--node", help="Expected Kubernetes node name")
    p.add_argument("--gpu-uuid", help="Expected GPU UUID; required on multi-GPU endpoints")
    p.add_argument(
        "--duration-s",
        type=float,
        default=120.0,
        help="Seconds to collect; 0 means until SIGINT/SIGTERM.",
    )
    p.add_argument("--interval-ms", type=int, default=1000)
    p.add_argument("--timeout-s", type=float, default=3.0)
    p.add_argument("--output", type=Path, required=True)
    return p


def main() -> None:
    args = parser().parse_args()
    if args.duration_s < 0:
        raise SystemExit("--duration-s must be >= 0")
    if args.interval_ms <= 0:
        raise SystemExit("--interval-ms must be positive")

    result = collect(args)
    import json

    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
