from __future__ import annotations

import argparse
import json
import math
import urllib.request
from pathlib import Path
from typing import Any


REQUIRED_CHARTS = ("system.cpu", "system.load", "system.ram")


def parse_node(text: str) -> tuple[str, str]:
    if "=" not in text:
        raise argparse.ArgumentTypeError("Use NODE=http://host:19999[/host/<hostname>]")
    node, url = text.split("=", 1)
    if not node or not url.startswith(("http://", "https://")):
        raise argparse.ArgumentTypeError("Use NODE=http://host:19999[/host/<hostname>]")
    return node, url.rstrip("/")


def finite_dimensions(chart: dict[str, Any]) -> int:
    count = 0
    for dimension in (chart.get("dimensions") or {}).values():
        try:
            if math.isfinite(float(dimension.get("value"))):
                count += 1
        except (TypeError, ValueError):
            pass
    return count


def audit(node: str, base_url: str) -> dict[str, Any]:
    url = base_url + "/api/v1/allmetrics?format=json"
    with urllib.request.urlopen(url, timeout=5) as response:
        metrics = json.load(response)

    checks: dict[str, bool] = {}
    for chart_id in REQUIRED_CHARTS:
        checks[chart_id] = chart_id in metrics and finite_dimensions(metrics[chart_id]) > 0

    checks["cpu_temperature"] = any(
        chart_id.startswith("sensors.temperature_") and finite_dimensions(chart) > 0
        for chart_id, chart in metrics.items()
    )
    checks["top_cpu"] = any(
        chart_id.startswith("app.")
        and chart_id.endswith("_cpu_utilization")
        and finite_dimensions(chart) > 0
        for chart_id, chart in metrics.items()
    )

    missing = sorted(key for key, ready in checks.items() if not ready)
    return {
        "node": node,
        "url": base_url,
        "ready": not missing,
        "checks": checks,
        "missing": missing,
        "scope": "netdata-system-cpu",
        "note": (
            "NVIDIA GPU device telemetry is audited separately through DCGM Exporter. "
            "Top1/Top2 per-process GPU telemetry is a model-dependent extension."
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Audit Netdata system/CPU features for candidate nodes"
    )
    parser.add_argument("--node", action="append", type=parse_node, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    reports = []
    for node, url in args.node:
        try:
            reports.append(audit(node, url))
        except Exception as exc:
            reports.append(
                {
                    "node": node,
                    "url": url,
                    "ready": False,
                    "error": str(exc),
                    "missing": ["connection"],
                    "scope": "netdata-system-cpu",
                }
            )

    result = {
        "schema_version": "pre6g.netdata-audit/v2",
        "nodes": reports,
    }
    text_out = json.dumps(result, ensure_ascii=False, indent=2)
    print(text_out)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text_out + "\n", encoding="utf-8")

    raise SystemExit(0 if all(item["ready"] for item in reports) else 1)


if __name__ == "__main__":
    main()
