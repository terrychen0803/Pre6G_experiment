from __future__ import annotations

import argparse
import csv
import json
import math
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


def _finite(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _dim(chart: dict[str, Any] | None, name: str) -> float | None:
    if not chart:
        return None
    value = ((chart.get("dimensions") or {}).get(name) or {}).get("value")
    return _finite(value)


def _cpu_temperature(metrics: dict[str, Any]) -> float | None:
    preferred: list[float] = []
    fallback: list[float] = []
    for chart_id, chart in metrics.items():
        lowered = chart_id.lower()
        if not (
            lowered.startswith("sensors.temperature_")
            and lowered.endswith("_input")
        ):
            continue
        value = _dim(chart, "input")
        if value is None:
            continue
        if "k10temp" in lowered and "_tctl_input" in lowered:
            preferred.append(value)
        elif "k10temp" in lowered:
            fallback.append(value)
        else:
            fallback.append(value)
    if preferred:
        return preferred[0]
    if fallback:
        return max(fallback)
    return None


def _top_cpu(metrics: dict[str, Any]) -> list[float]:
    usage: list[float] = []
    for chart_id, chart in metrics.items():
        if not (
            chart_id.startswith("app.")
            and chart_id.endswith("_cpu_utilization")
        ):
            continue
        user = _dim(chart, "user") or 0.0
        system = _dim(chart, "system") or 0.0
        usage.append(user + system)
    usage.sort(reverse=True)
    while len(usage) < 3:
        usage.append(0.0)
    return usage[:3]


def fetch_allmetrics(base_url: str, timeout_s: float) -> tuple[dict[str, Any], int, int]:
    url = base_url.rstrip("/") + "/api/v1/allmetrics?format=json"
    start_ns = time.time_ns()
    with urllib.request.urlopen(url, timeout=timeout_s) as response:
        payload = json.load(response)
    end_ns = time.time_ns()
    if not isinstance(payload, dict):
        raise ValueError("Netdata allmetrics response must be a JSON object")
    return payload, start_ns, end_ns


def utc_iso(timestamp_ns: int) -> str:
    dt = datetime.fromtimestamp(timestamp_ns / 1e9, tz=timezone.utc)
    return dt.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def canonical_row(
    metrics: dict[str, Any],
    *,
    node: str,
    request_start_ns: int,
    request_end_ns: int,
) -> dict[str, Any]:
    cpu = metrics.get("system.cpu") or {}
    load = metrics.get("system.load") or {}
    ram = metrics.get("system.ram") or {}
    top_cpu = _top_cpu(metrics)
    timestamp_ns = (request_start_ns + request_end_ns) // 2

    return {
        "timestamp_ns": timestamp_ns,
        "timestamp_utc": utc_iso(timestamp_ns),
        "request_start_ns": request_start_ns,
        "request_end_ns": request_end_ns,
        "node": node,
        "CPU User%": _dim(cpu, "user"),
        "CPU System%": _dim(cpu, "system"),
        "CPU IOWait%": _dim(cpu, "iowait"),
        "Load 1min": _dim(load, "load1"),
        "Load 5min": _dim(load, "load5"),
        "Load 15min": _dim(load, "load15"),
        "Mem Used(MB)": _dim(ram, "used"),
        "Mem Free(MB)": _dim(ram, "free"),
        "CPU Temp(°C)": _cpu_temperature(metrics),
        "Top1 CPU%": top_cpu[0],
        "Top2 CPU%": top_cpu[1],
        "Top3 CPU%": top_cpu[2],
    }


def collect(args: argparse.Namespace) -> dict[str, Any]:
    global _RUNNING
    _RUNNING = True

    interval_s = args.interval_ms / 1000.0
    args.output.parent.mkdir(parents=True, exist_ok=True)

    columns = [
        "timestamp_ns",
        "timestamp_utc",
        "request_start_ns",
        "request_end_ns",
        "node",
        "CPU User%",
        "CPU System%",
        "CPU IOWait%",
        "Load 1min",
        "Load 5min",
        "Load 15min",
        "Mem Used(MB)",
        "Mem Free(MB)",
        "CPU Temp(°C)",
        "Top1 CPU%",
        "Top2 CPU%",
        "Top3 CPU%",
    ]

    sample_count = 0
    first_timestamp_ns: int | None = None
    last_timestamp_ns: int | None = None
    monotonic_origin = time.monotonic()

    with args.output.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        handle.flush()

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

            metrics, request_start_ns, request_end_ns = fetch_allmetrics(
                args.url, args.timeout_s
            )
            row = canonical_row(
                metrics,
                node=args.node,
                request_start_ns=request_start_ns,
                request_end_ns=request_end_ns,
            )
            writer.writerow(row)
            handle.flush()

            timestamp_ns = int(row["timestamp_ns"])
            first_timestamp_ns = first_timestamp_ns or timestamp_ns
            last_timestamp_ns = timestamp_ns
            sample_count += 1
            index += 1

    return {
        "schema_version": "pre6g.netdata-collection/v1",
        "output": str(args.output),
        "url": args.url,
        "node": args.node,
        "configured_interval_ms": args.interval_ms,
        "configured_duration_s": args.duration_s,
        "samples": sample_count,
        "first_timestamp_ns": first_timestamp_ns,
        "last_timestamp_ns": last_timestamp_ns,
    }


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description=(
            "Diagnostic live-polling collector for canonical Netdata CPU/system "
            "telemetry. Formal Profile Jobs use query_netdata_window.py after "
            "the run so Netdata remains continuously monitored by Agent/Parent."
        )
    )
    p.add_argument("--url", required=True)
    p.add_argument("--node", required=True)
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

    # Fail before creating a long-running collector if the endpoint is unusable.
    metrics, start_ns, end_ns = fetch_allmetrics(args.url, args.timeout_s)
    preflight = canonical_row(
        metrics,
        node=args.node,
        request_start_ns=start_ns,
        request_end_ns=end_ns,
    )
    required = (
        "CPU User%",
        "Mem Used(MB)",
        "Mem Free(MB)",
        "Top1 CPU%",
    )
    missing = [name for name in required if preflight.get(name) is None]
    if missing:
        raise SystemExit(
            "Netdata endpoint is missing required canonical features: "
            + ", ".join(missing)
        )

    result = collect(args)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
