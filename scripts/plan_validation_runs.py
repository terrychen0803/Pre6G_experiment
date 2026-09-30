from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import re
from pathlib import Path
from typing import Any

import yaml


def _json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def _safe_name(value: str) -> str:
    if not re.fullmatch(r"[a-z0-9]([-a-z0-9]*[a-z0-9])?", value):
        raise ValueError(f"invalid Kubernetes name: {value!r}")
    return value


def _set_epochs(container: dict[str, Any], epochs: int) -> None:
    args = container.get("args")
    if not isinstance(args, list):
        raise ValueError("trainer container must have an args list")
    matches = [i for i, item in enumerate(args) if isinstance(item, str) and item.startswith("epochs=")]
    if len(matches) != 1:
        raise ValueError("expected exactly one YOLO epochs=N argument")
    args[matches[0]] = f"epochs={epochs}"
    if "patience=0" not in args:
        raise ValueError("training must set patience=0 to prevent early stopping")
    if any(isinstance(item, str) and item.startswith("time=") for item in args):
        raise ValueError("time= would stop the job before the planned work completes")


def plan(job: dict[str, Any], ranking: dict[str, Any], discovery: dict[str, Any], *, validation_id: str, target_minutes: float) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    _safe_name(validation_id)
    if job.get("apiVersion") != "batch/v1" or job.get("kind") != "Job":
        raise ValueError("source must be a batch/v1 Job")
    if ranking.get("schema_version") != "pre6g.provisional-ranking-result/v1":
        raise ValueError("ranking must be a provisional-ranking-result/v1")
    ranked = ranking.get("ranked") or []
    nodes = [str(item["node"]) for item in ranked]
    if len(nodes) < 2 or len(nodes) != len(set(nodes)):
        raise ValueError("ranking must have at least two distinct nodes")
    selected = ranking["selected_node"]
    if selected not in nodes:
        raise ValueError("selected node is absent from ranking")
    if discovery.get("adapter") != "yolo" or discovery.get("work", {}).get("unit") != "training_iteration":
        raise ValueError("validation requires YOLO training_iteration discovery")
    steps = discovery["work"].get("steps_per_epoch")
    if not isinstance(steps, int) or steps <= 0:
        raise ValueError("validation requires known positive steps_per_epoch")
    source_units = discovery["work"].get("total_units")
    if source_units != ranking.get("total_work_units"):
        raise ValueError("ranking total_work_units must match source Job discovery")
    template = job["spec"]["template"]
    pod_spec = template["spec"]
    containers = pod_spec.get("containers") or []
    app_name = job.get("metadata", {}).get("annotations", {}).get("pre6g.io/application-container")
    if len(containers) != 1 or containers[0].get("name") != app_name:
        raise ValueError("source must have one declared application container")
    image = str(containers[0].get("image", ""))
    if not image or "REPLACE" in image or "example.edu" in image:
        raise ValueError("source Job image is a placeholder")
    if pod_spec.get("nodeSelector", {}).get("kubernetes.io/hostname"):
        raise ValueError("source Job must not already pin a hostname")
    if job["spec"].get("parallelism", 1) != 1 or job["spec"].get("completions", 1) != 1:
        raise ValueError("validation requires one Pod per Job")
    if not 30 <= target_minutes <= 50:
        raise ValueError("target_minutes must be between 30 and 50")
    selected_row = next(item for item in ranked if item["node"] == selected)
    selected_ms = float(selected_row["predicted_runtime_ms_per_work_unit"])
    if not math.isfinite(selected_ms) or selected_ms <= 0:
        raise ValueError("selected runtime prediction must be finite and positive")
    epochs = max(1, math.ceil(target_minutes * 60_000 / (steps * selected_ms)))
    total_units = epochs * steps
    run_hash = hashlib.sha256(f"{validation_id}:{selected}:{total_units}".encode()).hexdigest()[:8]
    jobs: list[dict[str, Any]] = []
    predicted: list[dict[str, Any]] = []
    for row in ranked:
        node = str(row["node"])
        _safe_name(node)
        name = f"pre6g-{validation_id}-{run_hash}-{node}"
        if len(name) > 63:
            raise ValueError(f"generated Job name exceeds 63 characters: {name}")
        rendered = copy.deepcopy(job)
        rendered["metadata"]["name"] = name
        rendered["metadata"].setdefault("labels", {})["pre6g.io/validation-id"] = validation_id
        rendered["metadata"]["labels"]["pre6g.io/candidate-node"] = node
        rendered["spec"]["template"].setdefault("metadata", {}).setdefault("labels", {}).update({
            "pre6g.io/validation-id": validation_id,
            "pre6g.io/candidate-node": node,
        })
        spec = rendered["spec"]["template"]["spec"]
        spec.setdefault("nodeSelector", {})["kubernetes.io/hostname"] = node
        _set_epochs(spec["containers"][0], epochs)
        rendered["metadata"]["annotations"]["pre6g.io/validation-work-units"] = str(total_units)
        jobs.append(rendered)
        runtime_ms = float(row["predicted_runtime_ms_per_work_unit"])
        power_w = float(row["predicted_node_total_steady_power_w"])
        duration_s = runtime_ms * total_units / 1000
        predicted.append({
            "node": node,
            "job_name": name,
            "predicted_steady_runtime_s": duration_s,
            "predicted_steady_gross_energy_wh": power_w * duration_s / 3600,
            "predicted_steady_power_w": power_w,
            "power_status": row.get("power_status"),
            "power_range_exceeded": row.get("power_range_exceeded"),
        })
    result = {
        "schema_version": "pre6g.validation-plan/v1",
        "validation_id": validation_id,
        "ranking_mode": ranking["ranking_mode"],
        "production_ready": False,
        "selected_node": selected,
        "namespace": job["metadata"].get("namespace", "default"),
        "source_job_image": image,
        "source_total_work_units": source_units,
        "steps_per_epoch": steps,
        "planned_epochs": epochs,
        "planned_total_work_units": total_units,
        "target_selected_node_minutes": target_minutes,
        "jobs": predicted,
        "warnings": [
            "Research-only ranking; current power bundles are validation_required.",
            "Duration uses steady per-iteration estimates; setup, validation and checkpoint overhead are excluded.",
            "The same workload can exceed 50 minutes on slower nodes.",
            "All-node training uses the same dataset and hyperparameters; only node pinning and Job identity differ.",
            "Whole-job PDU energy is not directly identical to predicted steady-window energy.",
        ],
    }
    return result, jobs


def main() -> None:
    parser = argparse.ArgumentParser(description="Freeze provisional selection and prepare identical YOLO26 full runs on every candidate node; never deploys.")
    parser.add_argument("--job", type=Path, required=True)
    parser.add_argument("--ranking", type=Path, required=True)
    parser.add_argument("--discovery", type=Path, required=True)
    parser.add_argument("--validation-id", required=True)
    parser.add_argument("--target-minutes", type=float, default=40)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    job = yaml.safe_load(args.job.read_text(encoding="utf-8"))
    result, jobs = plan(job, _json(args.ranking), _json(args.discovery), validation_id=args.validation_id, target_minutes=args.target_minutes)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    for name in ("validation-plan.json", "validation-jobs.yaml", "deploy-commands.txt"):
        if (args.output_dir / name).exists():
            raise ValueError(f"refusing to overwrite existing validation artifact: {name}")
    result["input_sha256"] = {
        "source_job": hashlib.sha256(args.job.read_bytes()).hexdigest(),
        "ranking_result": hashlib.sha256(args.ranking.read_bytes()).hexdigest(),
        "work_discovery": hashlib.sha256(args.discovery.read_bytes()).hexdigest(),
    }
    (args.output_dir / "validation-plan.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (args.output_dir / "validation-jobs.yaml").write_text(yaml.safe_dump_all(jobs, sort_keys=False, allow_unicode=True), encoding="utf-8")
    namespace = result["namespace"]
    commands = [
        "# Review validation-plan.json and validation-jobs.yaml before applying.",
        "kubectl config current-context",
        f"kubectl apply --dry-run=server -n {namespace} -f validation-jobs.yaml",
        f"kubectl apply -n {namespace} -f validation-jobs.yaml",
        f"kubectl wait -n {namespace} --for=condition=complete job -l pre6g.io/validation-id={args.validation_id} --timeout=3h",
        f"kubectl get pods -n {namespace} -l pre6g.io/validation-id={args.validation_id} -o json > validation-pods.json",
        f"kubectl get jobs -n {namespace} -l pre6g.io/validation-id={args.validation_id} -o wide",
    ]
    (args.output_dir / "deploy-commands.txt").write_text("\n".join(commands) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
