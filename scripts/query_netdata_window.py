from __future__ import annotations

import argparse
import csv
import json
import math
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SYSTEM_CHARTS = {
    "system.cpu": ("user", "system", "iowait"),
    "system.load": ("load1", "load5", "load15"),
    "system.ram": ("used", "free"),
}

COLUMNS = [
    "timestamp_ns",
    "timestamp_utc",
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


def _finite(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _fetch_json(url: str, timeout_s: float, retries: int) -> Any:
    last_error: Exception | None = None
    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(url, timeout=timeout_s) as response:
                return json.load(response)
        except Exception as exc:
            last_error = exc
            if attempt >= retries:
                break
            time.sleep(min(2.0, 0.25 * (2**attempt)))
    assert last_error is not None
    raise last_error


def _allmetrics_url(base_url: str) -> str:
    return base_url.rstrip("/") + "/api/v1/allmetrics?format=json"


def _data_url(
    base_url: str,
    chart: str,
    after_s: int,
    before_s: int,
) -> str:
    params = {
        "chart": chart,
        "after": str(after_s),
        "before": str(before_s),
        "format": "json",
        "options": "seconds,flip,unaligned,objectrows",
    }
    return (
        base_url.rstrip("/")
        + "/api/v1/data?"
        + urllib.parse.urlencode(params)
    )


def _temperature_chart(metrics: dict[str, Any]) -> str | None:
    preferred: list[str] = []
    fallback: list[str] = []

    for chart_id, chart in metrics.items():
        lowered = chart_id.lower()
        if not (
            lowered.startswith("sensors.temperature_")
            and lowered.endswith("_input")
        ):
            continue

        value = (
            ((chart.get("dimensions") or {}).get("input") or {}).get("value")
        )
        if _finite(value) is None:
            continue

        if "k10temp" in lowered and "_tctl_input" in lowered:
            preferred.append(chart_id)
        elif "k10temp" in lowered:
            fallback.append(chart_id)
        else:
            fallback.append(chart_id)

    if preferred:
        return sorted(preferred)[0]
    if fallback:
        return sorted(fallback)[0]
    return None


def _app_cpu_charts(metrics: dict[str, Any]) -> list[str]:
    return sorted(
        chart_id
        for chart_id in metrics
        if chart_id.startswith("app.")
        and chart_id.endswith("_cpu_utilization")
    )


def _parse_data_payload(payload: Any) -> dict[int, dict[str, float | None]]:
    if not isinstance(payload, dict):
        raise ValueError("Netdata data response must be a JSON object")

    labels = payload.get("labels")
    data = payload.get("data")
    if not isinstance(labels, list) or not isinstance(data, list):
        raise ValueError("Netdata data response is missing labels/data")

    parsed: dict[int, dict[str, float | None]] = {}

    for raw in data:
        if isinstance(raw, dict):
            raw_time = raw.get("time")
            values = {
                str(key): _finite(value)
                for key, value in raw.items()
                if key != "time"
            }
        elif isinstance(raw, list):
            if not raw:
                continue
            raw_time = raw[0]
            values = {
                str(label): _finite(value)
                for label, value in zip(labels[1:], raw[1:])
            }
        else:
            continue

        timestamp = _finite(raw_time)
        if timestamp is None:
            continue

        parsed[int(timestamp)] = values

    return parsed


def _query_chart(
    base_url: str,
    chart: str,
    after_s: int,
    before_s: int,
    timeout_s: float,
    retries: int,
) -> dict[int, dict[str, float | None]]:
    payload = _fetch_json(
        _data_url(base_url, chart, after_s, before_s),
        timeout_s,
        retries,
    )
    return _parse_data_payload(payload)


def _nearest(
    series: dict[int, dict[str, float | None]],
    timestamp_s: int,
    tolerance_s: int = 1,
) -> dict[str, float | None] | None:
    if timestamp_s in series:
        return series[timestamp_s]

    candidates = [
        candidate
        for candidate in (timestamp_s - 1, timestamp_s + 1)
        if candidate in series
        and abs(candidate - timestamp_s) <= tolerance_s
    ]
    if not candidates:
        return None

    return series[min(candidates, key=lambda value: abs(value - timestamp_s))]


def _value(
    series: dict[int, dict[str, float | None]],
    timestamp_s: int,
    dimension: str,
) -> float | None:
    row = _nearest(series, timestamp_s)
    if row is None:
        return None
    return _finite(row.get(dimension))


def _top_cpu_at(
    app_series: list[dict[int, dict[str, float | None]]],
    timestamp_s: int,
) -> list[float | None]:
    values: list[float] = []

    for series in app_series:
        row = _nearest(series, timestamp_s)
        if row is None:
            continue

        user = _finite(row.get("user"))
        system = _finite(row.get("system"))
        if user is None or system is None:
            continue
        values.append(user + system)

    values.sort(reverse=True)
    if len(values) < 3:
        return [None, None, None]
    return values[:3]


def utc_iso(timestamp_ns: int) -> str:
    dt = datetime.fromtimestamp(timestamp_ns / 1e9, tz=timezone.utc)
    return dt.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def query_window(
    *,
    base_url: str,
    node: str,
    after_ns: int,
    before_ns: int,
    timeout_s: float,
    retries: int,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if after_ns >= before_ns:
        raise ValueError("--after-ns must be earlier than --before-ns")

    current = _fetch_json(_allmetrics_url(base_url), timeout_s, retries)
    if not isinstance(current, dict):
        raise ValueError("Netdata allmetrics response must be a JSON object")

    missing_system = [
        chart_id for chart_id in SYSTEM_CHARTS if chart_id not in current
    ]
    if missing_system:
        raise ValueError(
            "Netdata endpoint is missing required charts: "
            + ", ".join(missing_system)
        )

    temp_chart = _temperature_chart(current)
    if temp_chart is None:
        raise ValueError("Netdata endpoint has no usable CPU temperature chart")

    app_charts = _app_cpu_charts(current)
    if len(app_charts) < 3:
        raise ValueError(
            "Netdata endpoint exposes fewer than three app CPU charts"
        )

    # /api/v1/data treats after as exclusive and before as inclusive.
    # Pad by one second and then crop locally to the requested ns window.
    after_s = after_ns // 1_000_000_000 - 1
    before_s = (before_ns + 999_999_999) // 1_000_000_000 + 1

    cpu = _query_chart(
        base_url, "system.cpu", after_s, before_s, timeout_s, retries
    )
    load = _query_chart(
        base_url, "system.load", after_s, before_s, timeout_s, retries
    )
    ram = _query_chart(
        base_url, "system.ram", after_s, before_s, timeout_s, retries
    )
    temp = _query_chart(
        base_url, temp_chart, after_s, before_s, timeout_s, retries
    )

    app_series: list[dict[int, dict[str, float | None]]] = []
    queried_app_charts: list[str] = []
    unavailable_app_charts: list[str] = []

    for chart_id in app_charts:
        try:
            series = _query_chart(
                base_url,
                chart_id,
                after_s,
                before_s,
                timeout_s,
                retries,
            )
        except Exception:
            # app.* charts are process-group time series and can be
            # transient. A chart visible "now" may not have existed in the
            # historical dry-run window (or its historical series may already
            # have expired). This is not, by itself, a window-level failure.
            unavailable_app_charts.append(chart_id)
            continue

        if not series:
            unavailable_app_charts.append(chart_id)
            continue

        queried_app_charts.append(chart_id)
        app_series.append(series)

    if len(app_series) < 3:
        raise RuntimeError(
            "Netdata historical window exposes fewer than three usable "
            "app CPU series; cannot derive Top1/Top2/Top3 CPU%"
        )

    rows: list[dict[str, Any]] = []
    for timestamp_s in sorted(cpu):
        timestamp_ns = timestamp_s * 1_000_000_000
        if timestamp_ns < after_ns or timestamp_ns > before_ns:
            continue

        top_cpu = _top_cpu_at(app_series, timestamp_s)

        rows.append(
            {
                "timestamp_ns": timestamp_ns,
                "timestamp_utc": utc_iso(timestamp_ns),
                "node": node,
                "CPU User%": _value(cpu, timestamp_s, "user"),
                "CPU System%": _value(cpu, timestamp_s, "system"),
                "CPU IOWait%": _value(cpu, timestamp_s, "iowait"),
                "Load 1min": _value(load, timestamp_s, "load1"),
                "Load 5min": _value(load, timestamp_s, "load5"),
                "Load 15min": _value(load, timestamp_s, "load15"),
                "Mem Used(MB)": _value(ram, timestamp_s, "used"),
                "Mem Free(MB)": _value(ram, timestamp_s, "free"),
                "CPU Temp(°C)": _value(temp, timestamp_s, "input"),
                "Top1 CPU%": top_cpu[0],
                "Top2 CPU%": top_cpu[1],
                "Top3 CPU%": top_cpu[2],
            }
        )

    if not rows:
        raise RuntimeError(
            "Netdata historical query returned no system.cpu samples "
            "inside the requested window"
        )

    required = (
        "CPU User%",
        "Mem Used(MB)",
        "Mem Free(MB)",
        "Top1 CPU%",
        "Top2 CPU%",
        "Top3 CPU%",
    )
    missing_by_feature = {
        feature: sum(row.get(feature) is None for row in rows)
        for feature in required
    }
    if any(missing_by_feature.values()):
        raise RuntimeError(
            "Netdata historical window has missing required canonical "
            f"features: {missing_by_feature}"
        )

    metadata = {
        "schema_version": "pre6g.netdata-historical-query/v1",
        "node": node,
        "url": base_url,
        "after_ns": after_ns,
        "before_ns": before_ns,
        "samples": len(rows),
        "first_timestamp_ns": rows[0]["timestamp_ns"],
        "last_timestamp_ns": rows[-1]["timestamp_ns"],
        "temperature_chart": temp_chart,
        "app_cpu_chart_count_discovered": len(app_charts),
        "app_cpu_chart_count_queried": len(queried_app_charts),
        "app_cpu_chart_count_unavailable": len(unavailable_app_charts),
        "unavailable_app_cpu_charts": unavailable_app_charts,
        "query_mode": "historical",
        "timestamp_source": "netdata-database",
    }
    return rows, metadata


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Query an absolute historical Netdata Parent window and emit the "
            "canonical system/CPU telemetry CSV used by Pre6G."
        )
    )
    parser.add_argument(
        "--url",
        required=True,
        help="Netdata Parent per-host base URL ending in /host/<hostname>.",
    )
    parser.add_argument("--node", required=True)
    parser.add_argument("--after-ns", type=int, required=True)
    parser.add_argument("--before-ns", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--timeout-s", type=float, default=8.0)
    parser.add_argument("--retries", type=int, default=3)
    parser.add_argument(
        "--settle-s",
        type=float,
        default=1.0,
        help="Wait before querying so the child->Parent stream can catch up.",
    )
    args = parser.parse_args()

    if args.timeout_s <= 0:
        raise SystemExit("--timeout-s must be positive")
    if args.retries < 0:
        raise SystemExit("--retries must be >= 0")
    if args.settle_s < 0:
        raise SystemExit("--settle-s must be >= 0")

    if args.settle_s:
        time.sleep(args.settle_s)

    rows, metadata = query_window(
        base_url=args.url,
        node=args.node,
        after_ns=args.after_ns,
        before_ns=args.before_ns,
        timeout_s=args.timeout_s,
        retries=args.retries,
    )

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(rows)

    print(json.dumps(metadata, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
