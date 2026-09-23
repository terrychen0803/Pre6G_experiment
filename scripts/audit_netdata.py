from __future__ import annotations

import argparse
import json
import math
import urllib.request
from pathlib import Path
from typing import Any


REQUIRED_CHARTS = ("system.cpu", "system.load", "system.ram")
REQUIRED_GPU_CONTEXTS = (
    "nvidia_smi.gpu_utilization",
    "nvidia_smi.gpu_frame_buffer_memory_usage",
    "nvidia_smi.gpu_temperature",
    "nvidia_smi.gpu_power_draw",
)


def parse_node(text: str) -> tuple[str, str]:
    if "=" not in text:
        raise argparse.ArgumentTypeError("Use NODE=http://host:19999")
    node, url = text.split("=", 1)
    if not node or not url.startswith(("http://", "https://")):
        raise argparse.ArgumentTypeError("Use NODE=http://host:19999")
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
    contexts: dict[str, list[dict[str, Any]]] = {}
    for chart_id, chart in metrics.items():
        contexts.setdefault(str(chart.get("context", "")), []).append(
            {"chart": chart_id, "finite_dimensions": finite_dimensions(chart)}
        )
    checks: dict[str, bool] = {}
    for chart_id in REQUIRED_CHARTS:
        checks[chart_id] = chart_id in metrics and finite_dimensions(metrics[chart_id]) > 0
    for context in REQUIRED_GPU_CONTEXTS:
        checks[context] = any(
            item["finite_dimensions"] > 0 for item in contexts.get(context, [])
        )
    checks["cpu_temperature"] = any(
        chart_id.startswith("sensors.temperature_") and finite_dimensions(chart) > 0
        for chart_id, chart in metrics.items()
    )
    checks["top_cpu"] = any(
        chart_id.startswith("app.") and chart_id.endswith("_cpu_utilization")
        and finite_dimensions(chart) > 0
        for chart_id, chart in metrics.items()
    )
    # Stock per-GPU nvidia_smi charts do not prove per-process Top GPU availability.
    checks["top_gpu"] = any("process" in key and "gpu" in key for key in contexts)
    missing = sorted(key for key, ready in checks.items() if not ready)
    return {
        "node": node,
        "url": base_url,
        "ready": not missing,
        "checks": checks,
        "missing": missing,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Audit Netdata features on candidate nodes")
    parser.add_argument("--node", action="append", type=parse_node, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    reports = []
    for node, url in args.node:
        try:
            reports.append(audit(node, url))
        except Exception as exc:
            reports.append(
                {"node": node, "url": url, "ready": False, "error": str(exc), "missing": ["connection"]}
            )
    result = {"schema_version": "pre6g.netdata-audit/v1", "nodes": reports}
    text = json.dumps(result, ensure_ascii=False, indent=2)
    print(text)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n", encoding="utf-8")
    raise SystemExit(0 if all(item["ready"] for item in reports) else 1)


if __name__ == "__main__":
    main()

