from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from pre6g_experiment.work import estimate_work  # noqa: E402


NAME = re.compile(r"[a-z0-9](?:[-a-z0-9]*[a-z0-9])?\Z")
COMMIT = re.compile(r"[0-9a-f]{40}\Z")
CHECKOUT = re.compile(r"(\bgit checkout\s*\\\s*)([0-9a-f]{40})")
YOLO_TARGET = re.compile(r"(?m)^([ \t]*)yolo detect train \\$")
ARTIFACTS = (
    "profile-result.json",
    "runtime-features.json",
    "marker-free-discovery.json",
    "telemetry/aligned-telemetry.csv",
    "telemetry/alignment-quality.json",
    "telemetry/application-window.json",
)


def _name(value: str, label: str) -> str:
    if not NAME.fullmatch(value) or len(value) > 63:
        raise ValueError(f"{label} must be a Kubernetes DNS label of at most 63 characters")
    return value


def _path(value: str) -> Path:
    path = Path(value)
    return (path if path.is_absolute() else ROOT / path).resolve()


def _json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _environment() -> dict[str, str]:
    env = os.environ.copy()
    source = str(ROOT / "src")
    env["PYTHONPATH"] = source + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    return env


def _command_text(job: dict[str, Any]) -> str:
    containers = job["spec"]["template"]["spec"]["containers"]
    if len(containers) != 1 or containers[0].get("name") != "profiler":
        raise ValueError("profile template must have exactly one profiler container")
    container = containers[0]
    command = container.get("command") or []
    if len(command) != 3 or command[:2] != ["/bin/bash", "-lc"] or container.get("args"):
        raise ValueError("profile template must use the validated bash command shape")
    return command[2]


def _env(container: dict[str, Any]) -> dict[str, dict[str, Any]]:
    values = {item["name"]: item for item in container.get("env") or []}
    required = {"TASK_ID", "NODE_NAME", "DEVICE_ID", "GPU_UUID", "NETDATA_URL", "DCGM_URL"}
    if not required.issubset(values):
        raise ValueError(f"profile template is missing env vars: {sorted(required - set(values))}")
    return values


def prepare(config_path: Path, *, run_id: str, worker_commit: str) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any]]:
    _name(run_id, "run_id")
    if not COMMIT.fullmatch(worker_commit):
        raise ValueError("worker_commit must be a full lowercase 40-character Git SHA")
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    if not isinstance(config, dict) or config.get("schema_version") != "pre6g.cross-node-dryrun-config/v1":
        raise ValueError("unsupported cross-node dry-run config schema")
    namespace = _name(str(config["namespace"]), "namespace")
    pvc = _name(str(config["artifact_pvc"]), "artifact_pvc")
    source_job = _path(str(config["source_job"]))
    source = yaml.safe_load(source_job.read_text(encoding="utf-8"))
    if source.get("apiVersion") != "batch/v1" or source.get("kind") != "Job":
        raise ValueError("source_job must be batch/v1 Job")
    source_work = estimate_work(source)
    if source_work.adapter != "yolo" or source_work.work_unit != "training_iteration" or source_work.total_work_units is None:
        raise ValueError("source_job must expose a known YOLO training iteration count")
    parameters = source_work.parameters
    expected_training = {
        "model": "yolo26n.yaml", "epochs": 30, "batch_size": 16,
        "input_size": 320, "amp": False, "dataset_train_samples": 512,
    }
    if any(parameters.get(key) != value for key, value in expected_training.items()):
        raise ValueError("source_job settings differ from the validated YOLO26 dry-run fixture")
    candidates = config.get("candidates") or []
    if len(candidates) < 2:
        raise ValueError("at least two candidate nodes are required")
    nodes: set[str] = set()
    rendered: list[dict[str, Any]] = []
    plan_candidates: list[dict[str, Any]] = []
    workload_commands: set[str] = set()
    for item in candidates:
        node = _name(str(item["node"]), "candidate node")
        if node in nodes:
            raise ValueError(f"duplicate candidate node: {node}")
        nodes.add(node)
        template_path = _path(str(item["profile_job_template"]))
        runtime_model = _path(str(item["runtime_model"]))
        power_bundle = _path(str(item["power_bundle"]))
        for path in (template_path, runtime_model, power_bundle / "manifest.yaml", power_bundle / "model.onnx", power_bundle / "scaler.json"):
            if not path.is_file():
                raise ValueError(f"required input is missing: {path}")
        job = yaml.safe_load(template_path.read_text(encoding="utf-8"))
        if job.get("apiVersion") != "batch/v1" or job.get("kind") != "Job":
            raise ValueError(f"{template_path}: expected batch/v1 Job")
        if job["metadata"].get("namespace") != namespace:
            raise ValueError(f"{node}: profile template namespace differs from config")
        spec = job["spec"]["template"]["spec"]
        if spec.get("nodeSelector", {}).get("kubernetes.io/hostname") != node:
            raise ValueError(f"{node}: profile template nodeSelector differs from config")
        claims = [v.get("persistentVolumeClaim", {}).get("claimName") for v in spec.get("volumes") or []]
        if pvc not in claims:
            raise ValueError(f"{node}: profile template does not mount artifact PVC {pvc}")
        container = spec["containers"][0]
        values = _env(container)
        if values["NODE_NAME"].get("value") != node or values["DEVICE_ID"].get("value") != item["device_id"]:
            raise ValueError(f"{node}: profile template identity differs from config")
        original = _command_text(job)
        if len(CHECKOUT.findall(original)) != 1:
            raise ValueError(f"{node}: template must contain one pinned git checkout SHA")
        if "SHARED=/shared/results/${TASK_ID}/${NODE_NAME}" not in original:
            raise ValueError(f"{node}: template lacks the shared artifact path contract")
        if "--duration=120" not in original or "model=yolo26n.yaml" not in original:
            raise ValueError(f"{node}: template is not the validated YOLO26 120-second profile")
        for option in ("epochs=30", "imgsz=320", "batch=16", "amp=False"):
            if option not in original:
                raise ValueError(f"{node}: dry-run workload differs from source Job: {option}")
        if not re.search(r'\("train",\s*512\)', original):
            raise ValueError(f"{node}: dry-run synthetic dataset count differs from source Job")
        # The two node templates may differ only in node-bound env, not in the
        # profiled workload/capture command. The old checkout SHA is normalized.
        normalized = CHECKOUT.sub(r"\g<1><commit>", original)
        workload_commands.add(normalized)
        if len(YOLO_TARGET.findall(original)) != 1:
            raise ValueError(f"{node}: cannot locate the original YOLO target command")
        wrapped = YOLO_TARGET.sub(
            lambda match: match.group(1) + 'python3 scripts/run_command_with_end_marker.py --output "$LOCAL/telemetry/application-window.json" -- yolo detect train \\',
            original,
        )
        container["command"][2] = CHECKOUT.sub(lambda match: match.group(1) + worker_commit, wrapped)
        values["TASK_ID"]["value"] = run_id
        manifest = yaml.safe_load((power_bundle / "manifest.yaml").read_text(encoding="utf-8"))
        if manifest.get("node_binding", {}).get("kubernetes_node") != node:
            raise ValueError(f"{node}: power bundle is bound to another node")
        if manifest.get("node_binding", {}).get("gpu_uuid") != values["GPU_UUID"].get("value"):
            raise ValueError(f"{node}: power bundle GPU UUID differs from profile Job")
        model = _json(runtime_model)
        if model.get("device_id") != item["device_id"]:
            raise ValueError(f"{node}: runtime model device differs from profile Job")
        job_name = _name(f"pre6g-dryrun-{run_id}-{node}", "generated Job name")
        job["metadata"]["name"] = job_name
        job["metadata"].setdefault("labels", {}).update({"pre6g.io/dryrun-id": run_id, "pre6g.io/candidate-node": node})
        job["spec"]["template"].setdefault("metadata", {}).setdefault("labels", {}).update({"pre6g.io/dryrun-id": run_id, "pre6g.io/candidate-node": node})
        rendered.append(job)
        plan_candidates.append({
            "node": node,
            "device_id": item["device_id"],
            "job_name": job_name,
            "profile_job_template": str(template_path),
            "template_sha256": _digest(template_path),
            "runtime_model": str(runtime_model),
            "runtime_model_sha256": _digest(runtime_model),
            "power_bundle": str(power_bundle),
            "power_manifest_sha256": _digest(power_bundle / "manifest.yaml"),
            "gpu_uuid": values["GPU_UUID"]["value"],
        })
    if len(workload_commands) != 1:
        raise ValueError("candidate templates have different profiling commands or workloads")
    collector_name = _name(f"pre6g-collector-{run_id}", "collector Pod name")
    collector_image = str(config.get("collector_image", "busybox:1.36"))
    if not collector_image or "REPLACE" in collector_image:
        raise ValueError("collector image is missing or a placeholder")
    collector = {
        "apiVersion": "v1", "kind": "Pod",
        "metadata": {"name": collector_name, "namespace": namespace, "labels": {"pre6g.io/dryrun-id": run_id}},
        "spec": {
            "restartPolicy": "Never", "activeDeadlineSeconds": 1800,
            "containers": [{
                "name": "collector", "image": collector_image,
                "command": ["sh", "-c", "sleep 1800"],
                "resources": {"requests": {"cpu": "50m", "memory": "64Mi"}, "limits": {"cpu": "200m", "memory": "128Mi"}},
                "volumeMounts": [{"name": "artifacts", "mountPath": "/shared", "readOnly": True}],
            }],
            "volumes": [{"name": "artifacts", "persistentVolumeClaim": {"claimName": pvc, "readOnly": True}}],
        },
    }
    plan = {
        "schema_version": "pre6g.cross-node-dryrun-plan/v1",
        "run_id": run_id,
        "namespace": namespace,
        "worker_commit": worker_commit,
        "source_job": str(source_job),
        "source_job_sha256": _digest(source_job),
        "source_total_work_units": source_work.total_work_units,
        "config_sha256": _digest(config_path),
        "artifact_pvc": pvc,
        "collector_pod": collector_name,
        "collector_image": collector_image,
        "candidates": plan_candidates,
        "ranking_mode": "research-provisional-model-output",
        "production_ready": False,
        "deployment_policy": "plan-only unless --execute and explicit --kube-context are provided",
    }
    return plan, rendered, collector


def _invoke(command: list[str], *, cwd: Path, log: Path, env: dict[str, str] | None = None) -> str:
    result = subprocess.run(command, cwd=cwd, env=env, capture_output=True, text=True, encoding="utf-8", errors="replace", check=False)
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("a", encoding="utf-8") as handle:
        handle.write("$ " + subprocess.list2cmdline(command) + "\n" + result.stdout + result.stderr + "\n")
    if result.returncode:
        detail = (result.stderr or result.stdout).strip()[-1200:]
        raise RuntimeError(f"command failed ({result.returncode}): {detail}")
    return result.stdout


def _kubectl(context: str, *arguments: str) -> list[str]:
    return ["kubectl", "--context", context, *arguments]


def _preflight(plan: dict[str, Any], context: str, output: Path) -> None:
    worker_check = subprocess.run(
        ["git", "cat-file", "-e", f"{plan['worker_commit']}:scripts/run_command_with_end_marker.py"],
        cwd=ROOT, capture_output=True, text=True, check=False,
    )
    if worker_check.returncode:
        raise RuntimeError("worker_commit does not contain the required application-window wrapper; use a pushed commit containing this feature")
    if shutil.which("kubectl") is None:
        raise RuntimeError("kubectl is unavailable; plan files were generated, but no Job was submitted")
    log = output / "logs" / "kubectl.log"
    current = _invoke(["kubectl", "config", "current-context"], cwd=output, log=log).strip()
    if current != context:
        raise RuntimeError(f"current kube context {current!r} differs from required {context!r}")
    namespace = plan["namespace"]
    pvc = json.loads(_invoke(_kubectl(context, "-n", namespace, "get", "pvc", plan["artifact_pvc"], "-o", "json"), cwd=output, log=log))
    if pvc.get("status", {}).get("phase") != "Bound" or "ReadWriteMany" not in pvc.get("spec", {}).get("accessModes", []):
        raise RuntimeError("artifact PVC must be Bound and ReadWriteMany for simultaneous cross-node profiles")
    for item in plan["candidates"]:
        node = json.loads(_invoke(_kubectl(context, "get", "node", item["node"], "-o", "json"), cwd=output, log=log))
        ready = any(c.get("type") == "Ready" and c.get("status") == "True" for c in node.get("status", {}).get("conditions", []))
        if not ready or int(node.get("status", {}).get("allocatable", {}).get("nvidia.com/gpu.shared", "0")) < 1:
            raise RuntimeError(f"{item['node']}: node is not Ready or has no allocatable shared GPU")
        existing = subprocess.run(_kubectl(context, "-n", namespace, "get", "job", item["job_name"], "-o", "name"), capture_output=True, text=True, check=False)
        if existing.returncode == 0:
            raise RuntimeError(f"Job already exists; refusing to reuse or overwrite: {item['job_name']}")
    _invoke(_kubectl(context, "-n", namespace, "apply", "--dry-run=server", "-f", str(output / "dryrun-jobs.yaml")), cwd=output, log=log)
    _invoke(_kubectl(context, "-n", namespace, "apply", "--dry-run=server", "-f", str(output / "collector-pod.yaml")), cwd=output, log=log)


def _wait_jobs(plan: dict[str, Any], context: str, output: Path, timeout_s: int) -> None:
    expected = {item["job_name"] for item in plan["candidates"]}
    deadline = time.monotonic() + timeout_s
    log = output / "logs" / "kubectl.log"
    while time.monotonic() < deadline:
        raw = _invoke(_kubectl(context, "-n", plan["namespace"], "get", "jobs", "-l", f"pre6g.io/dryrun-id={plan['run_id']}", "-o", "json"), cwd=output, log=log)
        jobs = {item["metadata"]["name"]: item for item in json.loads(raw).get("items", [])}
        if not expected.issubset(jobs):
            raise RuntimeError("not all submitted dry-run Jobs are visible in Kubernetes")
        for name in expected:
            status = jobs[name].get("status", {})
            if status.get("failed", 0) or any(c.get("type") == "Failed" and c.get("status") == "True" for c in status.get("conditions", [])):
                raise RuntimeError(f"dry-run Job failed: {name}; inspect kubectl logs and events")
        complete = sum(bool(jobs[name].get("status", {}).get("succeeded", 0)) for name in expected)
        print(f"[dry-run] {complete}/{len(expected)} Jobs complete", flush=True)
        if complete == len(expected):
            _write_json(output / "dryrun-jobs-status.json", {"items": [jobs[name] for name in sorted(expected)]})
            return
        time.sleep(5)
    raise RuntimeError(f"dry-run Jobs did not all complete within {timeout_s} seconds")


def _collect(plan: dict[str, Any], context: str, output: Path) -> None:
    namespace = plan["namespace"]
    collector = plan["collector_pod"]
    log = output / "logs" / "kubectl.log"
    created = False
    try:
        _invoke(_kubectl(context, "-n", namespace, "create", "-f", str(output / "collector-pod.yaml")), cwd=output, log=log)
        created = True
        _invoke(_kubectl(context, "-n", namespace, "wait", f"pod/{collector}", "--for=condition=Ready", "--timeout=5m"), cwd=output, log=log)
        for item in plan["candidates"]:
            target = output / "artifacts" / item["node"]
            for relative in ARTIFACTS:
                destination = target / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                source = f"{collector}:/shared/results/{plan['run_id']}/{item['node']}/{relative}"
                # Relative local paths avoid Windows drive-letter colons being
                # mistaken for a second Kubernetes pod:path argument.
                local = destination.relative_to(output).as_posix()
                _invoke(_kubectl(context, "-n", namespace, "cp", source, local, "-c", "collector"), cwd=output, log=log)
    finally:
        if created:
            _invoke(_kubectl(context, "-n", namespace, "delete", "pod", collector, "--wait=false"), cwd=output, log=log)


def _crop_telemetry(source: Path, window_path: Path, destination: Path) -> dict[str, Any]:
    window = _json(window_path)
    if window.get("schema_version") != "pre6g.application-window/v1":
        raise ValueError("missing validated application-window schema")
    if (window.get("command") or [])[:3] != ["yolo", "detect", "train"]:
        raise ValueError("application window was not recorded for the YOLO training command")
    start = int(window["started_at_unix_ns"])
    end = int(window["finished_at_unix_ns"])
    if end <= start or end - start < 10_000_000_000:
        raise ValueError("application execution window is invalid or under 10 seconds")
    with source.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        names = list(reader.fieldnames or [])
        if "timestamp_ns" not in names:
            raise ValueError("aligned telemetry lacks timestamp_ns")
        all_rows = list(reader)
    selected = [row for row in all_rows if start <= int(row["timestamp_ns"]) <= end]
    if len(selected) < 10:
        raise ValueError("fewer than ten aligned telemetry samples in application window")
    selected.sort(key=lambda row: int(row["timestamp_ns"]))
    first = int(selected[0]["timestamp_ns"])
    last = int(selected[-1]["timestamp_ns"])
    if first - start > 2_000_000_000 or end - last > 2_000_000_000:
        raise ValueError("aligned telemetry does not cover both application-window boundaries within two seconds")
    max_gap = max(int(right["timestamp_ns"]) - int(left["timestamp_ns"]) for left, right in zip(selected, selected[1:]))
    if max_gap > 2_000_000_000:
        raise ValueError("aligned telemetry has a gap over two seconds inside application window")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=names)
        writer.writeheader()
        writer.writerows(selected)
    return {
        "schema_version": "pre6g.power-window-crop/v1",
        "application_start_unix_ns": start,
        "application_end_unix_ns": end,
        "sample_count": len(selected),
        "first_sample_unix_ns": first,
        "last_sample_unix_ns": last,
        "max_gap_s": max_gap / 1e9,
        "window_semantics": "original YOLO target process execution, excluding Nsight finalization",
    }


def _predict(plan: dict[str, Any], output: Path) -> None:
    discovery = output / "workload-discovery.json"
    _invoke([sys.executable, str(ROOT / "scripts" / "discover_work.py"), "--job", plan["source_job"], "--output", str(discovery)], cwd=ROOT, log=output / "logs" / "prediction.log", env=_environment())
    work = _json(discovery)["work"]
    if work.get("unit") != "training_iteration" or not isinstance(work.get("total_units"), int) or work["total_units"] <= 0:
        raise ValueError("source Job must resolve a positive YOLO training_iteration count")
    candidates: list[dict[str, Any]] = []
    for item in plan["candidates"]:
        node = item["node"]
        artifacts = output / "artifacts" / node
        profile = _json(artifacts / "profile-result.json")
        features = _json(artifacts / "runtime-features.json")
        quality = _json(artifacts / "telemetry" / "alignment-quality.json")
        if profile.get("schema_version") != "pre6g.profile-result/v1" or profile.get("task_id") != plan["run_id"] or profile.get("node") != node or profile.get("device_id") != item["device_id"] or profile.get("status") != "ready-for-control-side-inference":
            raise ValueError(f"{node}: ProfileResult identity or schema mismatch")
        if features.get("schema_version") != "pre6g.runtime-features/v1" or features.get("node") != node or features.get("device_id") != item["device_id"]:
            raise ValueError(f"{node}: runtime features identity or schema mismatch")
        if profile.get("runtime_features") != features or int(profile.get("detector", {}).get("complete_cycles", 0)) < 3:
            raise ValueError(f"{node}: packaged features mismatch or too few complete cycles")
        if not quality.get("pass"):
            raise ValueError(f"{node}: telemetry alignment quality did not pass")
        result = output / "predictions" / node
        result.mkdir(parents=True, exist_ok=True)
        cropped = result / "application-aligned-telemetry.csv"
        crop = _crop_telemetry(artifacts / "telemetry" / "aligned-telemetry.csv", artifacts / "telemetry" / "application-window.json", cropped)
        _write_json(result / "power-window-crop.json", crop)
        log = output / "logs" / f"predict-{node}.log"
        commands = [
            ("predict_runtime.py", ["--model", item["runtime_model"], "--features", str(artifacts / "runtime-features.json"), "--output", str(result / "runtime-prediction.json")]),
            ("aggregate_runtime.py", ["--runtime-prediction", str(result / "runtime-prediction.json"), "--work-discovery", str(discovery), "--output", str(result / "semantic-runtime.json")]),
            ("predict_power_from_aligned_telemetry.py", ["--aligned-telemetry", str(cropped), "--alignment-quality", str(artifacts / "telemetry" / "alignment-quality.json"), "--bundle-dir", item["power_bundle"], "--output-series", str(result / "power-series.csv"), "--output-summary", str(result / "power-summary.json")]),
        ]
        for script, arguments in commands:
            _invoke([sys.executable, str(ROOT / "scripts" / script), *arguments], cwd=ROOT, log=log, env=_environment())
        runtime = _json(result / "semantic-runtime.json")
        power = _json(result / "power-summary.json")
        if runtime.get("node") != node or runtime.get("total_work_units") != work["total_units"] or runtime.get("work_unit") != work["unit"]:
            raise ValueError(f"{node}: semantic runtime is not comparable to source Job")
        if power.get("bound_node") != node or power.get("bound_gpu_uuid") != item["gpu_uuid"] or power.get("target_semantics") != "node-total-power" or power.get("target_unit") != "W":
            raise ValueError(f"{node}: node-bound power target does not match candidate")
        representative = power.get("observed_profile_window", {}).get("time_weighted_mean_predicted_power_w")
        runtime_ms = float(runtime["predicted_runtime_ms_per_work_unit"])
        watts = float(representative)
        if not all(math.isfinite(v) and v > 0 for v in (runtime_ms, watts)):
            raise ValueError(f"{node}: runtime or power prediction is not positive and finite")
        candidates.append({
            "node": node, "device_id": item["device_id"],
            "runtime": {"model_id": runtime["model_id"], "predicted_runtime_ms_per_work_unit": runtime_ms},
            "power": {
                "model_id": power["model_id"], "predicted_node_total_steady_power_w": watts,
                "status": power["status"], "range_exceeded": power.get("range_exceeded", False),
                "range_detail": "; ".join(power.get("range_warnings") or []),
            },
        })
    ranking_input = {
        "schema_version": "pre6g.provisional-ranking-input/v1",
        "task_id": plan["run_id"], "ranking_mode": "research-provisional-model-output",
        "work_unit": work["unit"], "total_work_units": work["total_units"],
        "energy_objective": "steady-gross-node-energy", "candidates": candidates,
    }
    ranking_path = output / "ranking-input.json"
    _write_json(ranking_path, ranking_input)
    _invoke([sys.executable, str(ROOT / "scripts" / "provisional_rank_nodes.py"), "--input", str(ranking_path), "--output", str(output / "provisional-ranking.json")], cwd=ROOT, log=output / "logs" / "prediction.log", env=_environment())


def main() -> int:
    parser = argparse.ArgumentParser(description="Plan or execute parallel YOLO26 dry-run Jobs, collect ProfileResults through the shared PVC, and rank nodes on the master.")
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--worker-commit", required=True, help="Full pushed Git SHA checked out inside each worker Job")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--execute", action="store_true", help="Actually create all dry-run Jobs in Kubernetes")
    parser.add_argument("--kube-context", help="Required with --execute; must equal current kube context")
    parser.add_argument("--timeout-seconds", type=int, default=900)
    args = parser.parse_args()
    if args.execute and not args.kube_context:
        parser.error("--execute requires --kube-context")
    if args.timeout_seconds <= 0:
        parser.error("--timeout-seconds must be positive")
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    summary = output / "cross-node-run-summary.json"
    if args.execute and summary.exists() and _json(summary).get("status") == "completed":
        parser.error("this run already completed; use a new run ID and output directory")
    started_at = datetime.now(timezone.utc).isoformat()
    phase = "prepare"
    try:
        plan, jobs, collector = prepare(args.config.resolve(), run_id=args.run_id, worker_commit=args.worker_commit)
        plan_path = output / "cross-node-plan.json"
        jobs_path = output / "dryrun-jobs.yaml"
        collector_path = output / "collector-pod.yaml"
        if plan_path.exists():
            if _json(plan_path) != plan:
                raise ValueError("existing plan differs from current inputs; use a new output directory")
        else:
            if jobs_path.exists() or collector_path.exists():
                raise ValueError("partial plan files exist; use a new output directory")
            _write_json(plan_path, plan)
            jobs_path.write_text(yaml.safe_dump_all(jobs, sort_keys=False, allow_unicode=True), encoding="utf-8")
            collector_path.write_text(yaml.safe_dump(collector, sort_keys=False, allow_unicode=True), encoding="utf-8")
        if not args.execute:
            _write_json(summary, {"schema_version": "pre6g.cross-node-run-summary/v1", "status": "planned", "started_at": started_at, "run_id": args.run_id, "plan": str(plan_path)})
            print(f"[planned] {plan_path}; no Kubernetes Job was created")
            return 0
        phase = "preflight"
        _preflight(plan, args.kube_context, output)
        phase = "submit"
        _invoke(_kubectl(args.kube_context, "-n", plan["namespace"], "create", "-f", str(jobs_path)), cwd=output, log=output / "logs" / "kubectl.log")
        phase = "wait"
        _wait_jobs(plan, args.kube_context, output, args.timeout_seconds)
        phase = "collect"
        _collect(plan, args.kube_context, output)
        phase = "predict-and-rank"
        _predict(plan, output)
        _write_json(summary, {"schema_version": "pre6g.cross-node-run-summary/v1", "status": "completed", "started_at": started_at, "finished_at": datetime.now(timezone.utc).isoformat(), "run_id": args.run_id, "selected_node": _json(output / "provisional-ranking.json")["selected_node"], "ranking_result": str(output / "provisional-ranking.json")})
        print(f"[done] {output / 'provisional-ranking.json'}")
        return 0
    except (OSError, ValueError, RuntimeError, KeyError, TypeError) as exc:
        _write_json(summary, {"schema_version": "pre6g.cross-node-run-summary/v1", "status": "failed", "phase": phase, "started_at": started_at, "finished_at": datetime.now(timezone.utc).isoformat(), "run_id": args.run_id, "error": str(exc)})
        print(f"error in {phase}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
