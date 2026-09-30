from __future__ import annotations

import argparse
import csv
import hashlib
import ipaddress
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
SMOKE_PROGRAM = r'''
import json
import os
import subprocess
import time
import urllib.request
from pathlib import Path

run_id = os.environ["RUN_ID"]
node = os.environ["NODE_NAME"]
peer = os.environ["PEER_NODE"]
gpu_uuid = os.environ["GPU_UUID"]

def command(*args):
    result = subprocess.run(args, check=True, capture_output=True, text=True, timeout=45)
    return (result.stdout + result.stderr).strip()

visible = command("nvidia-smi", "--query-gpu=uuid", "--format=csv,noheader").splitlines()
if gpu_uuid not in [value.strip() for value in visible]:
    raise RuntimeError("expected GPU UUID is not visible inside this Pod")
nsight_version = command("/opt/pre6g/nsight/bin/nsys", "--version")
command("git", "ls-remote", "https://github.com/terrychen0803/Pre6G_experiment.git", "HEAD")

with urllib.request.urlopen(os.environ["DCGM_URL"].rstrip("/") + "/metrics", timeout=15) as response:
    dcgm = response.read().decode("utf-8")
required = (
    "DCGM_FI_DEV_GPU_UTIL", "DCGM_FI_DEV_FB_USED",
    "DCGM_FI_DEV_GPU_TEMP", "DCGM_FI_DEV_POWER_USAGE",
)
missing = [metric for metric in required if not any(
    line.startswith(metric + "{") and ('UUID="' + gpu_uuid + '"') in line
    for line in dcgm.splitlines()
)]
if missing:
    raise RuntimeError("DCGM endpoint lacks metrics for expected GPU UUID: " + ", ".join(missing))

with urllib.request.urlopen(os.environ["NETDATA_URL"].rstrip("/") + "/api/v1/allmetrics?format=json", timeout=15) as response:
    netdata = json.load(response)
if not isinstance(netdata, dict) or not netdata:
    raise RuntimeError("Netdata did not return nonempty host metrics")

root = Path("/shared/smoke") / run_id
os.umask(0o022)
own = root / node
own.mkdir(parents=True, exist_ok=True)
marker = own / "marker.json"
marker.write_text(json.dumps({"run_id": run_id, "node": node, "gpu_uuid": gpu_uuid}) + "\n", encoding="utf-8")
deadline = time.monotonic() + 90
peer_marker = root / peer / "marker.json"
while not peer_marker.is_file() and time.monotonic() < deadline:
    time.sleep(1)
if not peer_marker.is_file():
    raise RuntimeError("peer marker was not visible across the NFS PVC")
other = json.loads(peer_marker.read_text(encoding="utf-8"))
if other.get("run_id") != run_id or other.get("node") != peer:
    raise RuntimeError("peer NFS marker identity mismatch")

print("PRE6G_SMOKE_RESULT=" + json.dumps({
    "schema_version": "pre6g.cross-node-smoke-result/v1",
    "passed": True,
    "run_id": run_id,
    "node": node,
    "gpu_uuid": gpu_uuid,
    "peer_node": peer,
    "nfs_peer_read": True,
    "nsight_version": nsight_version,
    "dcgm_metrics": list(required),
    "netdata_chart_count": len(netdata),
    "github_access": True,
}), flush=True)
'''


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
    exporter = config.get("dcgm_exporter") or {}
    exporter_namespace = _name(str(exporter.get("namespace", "gpu-monitoring")), "DCGM exporter namespace")
    exporter_selector = str(exporter.get("label_selector", "app.kubernetes.io/name=dcgm-exporter"))
    exporter_port = int(exporter.get("port", 9400))
    if not re.fullmatch(r"[A-Za-z0-9./_-]+=[A-Za-z0-9._-]+", exporter_selector) or not 1 <= exporter_port <= 65535:
        raise ValueError("DCGM exporter selector or port is invalid")
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
        if spec.get("runtimeClassName") != "nvidia":
            raise ValueError(f"{node}: profile template must use the validated nvidia RuntimeClass")
        requests = container.get("resources", {}).get("requests", {})
        limits = container.get("resources", {}).get("limits", {})
        if requests.get("nvidia.com/gpu.shared") != "1" or limits.get("nvidia.com/gpu.shared") != "1":
            raise ValueError(f"{node}: profile template must request one validated shared GPU")
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
        # An old exporter Pod IP in the formal template must never be submitted.
        # The live endpoint is resolved against the target node at preflight.
        values["DCGM_URL"]["value"] = "DCGM_ENDPOINT_UNRESOLVED"
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
        "dcgm_exporter": {"namespace": exporter_namespace, "label_selector": exporter_selector, "port": exporter_port},
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


def _resolve_dcgm_urls(plan: dict[str, Any], context: str, output: Path) -> dict[str, str]:
    exporter = plan["dcgm_exporter"]
    raw = _invoke(
        _kubectl(context, "-n", exporter["namespace"], "get", "pods", "-l", exporter["label_selector"], "-o", "json"),
        cwd=output, log=output / "logs" / "kubectl.log",
    )
    pods = json.loads(raw).get("items", [])
    urls: dict[str, str] = {}
    for item in plan["candidates"]:
        node = item["node"]
        matches = [pod for pod in pods if pod.get("spec", {}).get("nodeName") == node]
        if len(matches) != 1:
            raise RuntimeError(f"{node}: expected exactly one DCGM exporter Pod, found {len(matches)}")
        pod = matches[0]
        status = pod.get("status", {})
        ready = any(c.get("type") == "Ready" and c.get("status") == "True" for c in status.get("conditions", []))
        if status.get("phase") != "Running" or not ready or not status.get("podIP"):
            raise RuntimeError(f"{node}: DCGM exporter Pod is not Running/Ready with a Pod IP")
        address = ipaddress.ip_address(status["podIP"])
        host = f"[{address}]" if address.version == 6 else str(address)
        urls[node] = f"http://{host}:{exporter['port']}"
    return urls


def _preflight(plan: dict[str, Any], jobs: list[dict[str, Any]], context: str, output: Path) -> Path:
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
    _invoke(_kubectl(context, "get", "namespace", namespace, "-o", "json"), cwd=output, log=log)
    _invoke(_kubectl(context, "get", "runtimeclass", "nvidia", "-o", "json"), cwd=output, log=log)
    pvc = json.loads(_invoke(_kubectl(context, "-n", namespace, "get", "pvc", plan["artifact_pvc"], "-o", "json"), cwd=output, log=log))
    if pvc.get("status", {}).get("phase") != "Bound" or "ReadWriteMany" not in pvc.get("spec", {}).get("accessModes", []):
        raise RuntimeError("artifact PVC must be Bound and ReadWriteMany for simultaneous cross-node profiles")
    volume_name = pvc.get("spec", {}).get("volumeName")
    if not volume_name:
        raise RuntimeError("artifact PVC is Bound but has no backing PV name")
    pv = json.loads(_invoke(_kubectl(context, "get", "pv", volume_name, "-o", "json"), cwd=output, log=log))
    pv_spec = pv.get("spec", {})
    claim = pv_spec.get("claimRef", {})
    nfs = pv_spec.get("nfs", {})
    if (pv.get("status", {}).get("phase") != "Bound" or "ReadWriteMany" not in pv_spec.get("accessModes", [])
            or claim.get("namespace") != namespace or claim.get("name") != plan["artifact_pvc"]
            or not nfs.get("server") or not nfs.get("path")):
        raise RuntimeError("artifact PV must be a Bound RWX NFS volume claimed by this namespace/PVC")
    existing_jobs = json.loads(_invoke(_kubectl(context, "-n", namespace, "get", "jobs", "-o", "json"), cwd=output, log=log))
    existing_names = {item.get("metadata", {}).get("name") for item in existing_jobs.get("items", [])}
    for item in plan["candidates"]:
        node = json.loads(_invoke(_kubectl(context, "get", "node", item["node"], "-o", "json"), cwd=output, log=log))
        ready = any(c.get("type") == "Ready" and c.get("status") == "True" for c in node.get("status", {}).get("conditions", []))
        if not ready or int(node.get("status", {}).get("allocatable", {}).get("nvidia.com/gpu.shared", "0")) < 1:
            raise RuntimeError(f"{item['node']}: node is not Ready or has no allocatable shared GPU")
        if any(c.get("type") == "DiskPressure" and c.get("status") == "True" for c in node.get("status", {}).get("conditions", [])):
            raise RuntimeError(f"{item['node']}: node reports DiskPressure")
        if item["job_name"] in existing_names:
            raise RuntimeError(f"Job already exists; refusing to reuse or overwrite: {item['job_name']}")
    urls = _resolve_dcgm_urls(plan, context, output)
    for job in jobs:
        node = job["spec"]["template"]["spec"]["nodeSelector"]["kubernetes.io/hostname"]
        values = _env(job["spec"]["template"]["spec"]["containers"][0])
        values["DCGM_URL"]["value"] = urls[node]
    resolved = output / "dryrun-jobs-resolved.yaml"
    resolved.write_text(yaml.safe_dump_all(jobs, sort_keys=False, allow_unicode=True), encoding="utf-8")
    _write_json(output / "dcgm-endpoints.json", {"schema_version": "pre6g.dcgm-endpoints/v1", "run_id": plan["run_id"], "urls_by_node": urls})
    _invoke(_kubectl(context, "-n", namespace, "apply", "--dry-run=server", "-f", str(resolved)), cwd=output, log=log)
    _invoke(_kubectl(context, "-n", namespace, "apply", "--dry-run=server", "-f", str(output / "collector-pod.yaml")), cwd=output, log=log)
    return resolved


def _smoke_pods(plan: dict[str, Any], jobs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    nodes = [item["node"] for item in plan["candidates"]]
    pods: list[dict[str, Any]] = []
    for index, job in enumerate(jobs):
        source_spec = job["spec"]["template"]["spec"]
        node = source_spec["nodeSelector"]["kubernetes.io/hostname"]
        source_container = source_spec["containers"][0]
        values = _env(source_container)
        if values["DCGM_URL"].get("value") == "DCGM_ENDPOINT_UNRESOLVED":
            raise ValueError(f"{node}: DCGM endpoint must be resolved before smoke testing")
        volumes = {volume["name"]: volume for volume in source_spec.get("volumes", [])}
        if volumes.get("artifacts", {}).get("persistentVolumeClaim", {}).get("claimName") != plan["artifact_pvc"]:
            raise ValueError(f"{node}: artifact PVC mount is missing")
        if volumes.get("nsys", {}).get("hostPath", {}).get("path") != "/opt/nvidia/nsight-systems-cli/2026.4.1":
            raise ValueError(f"{node}: Nsight hostPath differs from validated path")
        peer = nodes[(index + 1) % len(nodes)]
        pod = {
            "apiVersion": "v1", "kind": "Pod",
            "metadata": {
                "name": _name(f"pre6g-smoke-{plan['run_id']}-{node}", "smoke Pod name"),
                "namespace": plan["namespace"],
                "labels": {"pre6g.io/smoke-id": plan["run_id"], "pre6g.io/candidate-node": node},
            },
            "spec": {
                "restartPolicy": "Never", "activeDeadlineSeconds": 300,
                "runtimeClassName": source_spec["runtimeClassName"],
                "nodeSelector": {"kubernetes.io/hostname": node},
                "containers": [{
                    "name": "smoke", "image": source_container["image"],
                    "imagePullPolicy": source_container.get("imagePullPolicy", "IfNotPresent"),
                    "securityContext": source_container.get("securityContext", {}),
                    "command": ["python3", "-c", SMOKE_PROGRAM],
                    "env": [
                        {"name": "RUN_ID", "value": plan["run_id"]},
                        {"name": "NODE_NAME", "value": node},
                        {"name": "PEER_NODE", "value": peer},
                        *[{"name": name, "value": values[name]["value"]} for name in ("GPU_UUID", "NETDATA_URL", "DCGM_URL")],
                    ],
                    "resources": {
                        "requests": {"cpu": "250m", "memory": "512Mi", "nvidia.com/gpu.shared": "1"},
                        "limits": {"cpu": "1", "memory": "1Gi", "nvidia.com/gpu.shared": "1"},
                    },
                    "volumeMounts": [
                        {"name": "artifacts", "mountPath": "/shared"},
                        {"name": "nsys", "mountPath": "/opt/pre6g/nsight", "readOnly": True},
                    ],
                }],
                "volumes": [volumes["artifacts"], volumes["nsys"]],
            },
        }
        pods.append(pod)
    return pods


def _parse_smoke_log(log: str, *, run_id: str, node: str, gpu_uuid: str) -> dict[str, Any]:
    markers = [line.removeprefix("PRE6G_SMOKE_RESULT=") for line in log.splitlines() if line.startswith("PRE6G_SMOKE_RESULT=")]
    if len(markers) != 1:
        raise ValueError(f"{node}: expected exactly one smoke result in Pod logs")
    result = json.loads(markers[0])
    if (result.get("schema_version") != "pre6g.cross-node-smoke-result/v1" or result.get("passed") is not True
            or result.get("run_id") != run_id or result.get("node") != node or result.get("gpu_uuid") != gpu_uuid
            or result.get("nfs_peer_read") is not True or result.get("github_access") is not True):
        raise ValueError(f"{node}: smoke result identity or checks failed")
    return result


def _run_smoke(plan: dict[str, Any], jobs: list[dict[str, Any]], context: str, output: Path) -> None:
    namespace = plan["namespace"]
    log = output / "logs" / "kubectl.log"
    pods = _smoke_pods(plan, jobs)
    existing = json.loads(_invoke(_kubectl(context, "-n", namespace, "get", "pods", "-l", f"pre6g.io/smoke-id={plan['run_id']}", "-o", "json"), cwd=output, log=log))
    if existing.get("items"):
        raise RuntimeError("smoke Pods already exist for this run ID; use a new run ID")
    manifest = output / "smoke-pods.yaml"
    manifest.write_text(yaml.safe_dump_all(pods, sort_keys=False, allow_unicode=True), encoding="utf-8")
    _invoke(_kubectl(context, "-n", namespace, "apply", "--dry-run=server", "-f", str(manifest)), cwd=output, log=log)
    created: list[dict[str, Any]] = []
    cleanup_errors: list[str] = []
    try:
        for pod in pods:
            name = pod["metadata"]["name"]
            single = output / f"{name}.yaml"
            single.write_text(yaml.safe_dump(pod, sort_keys=False, allow_unicode=True), encoding="utf-8")
            _invoke(_kubectl(context, "-n", namespace, "create", "-f", str(single)), cwd=output, log=log)
            created.append(pod)
        expected = {pod["metadata"]["name"] for pod in pods}
        deadline = time.monotonic() + 360
        while time.monotonic() < deadline:
            raw = _invoke(_kubectl(context, "-n", namespace, "get", "pods", "-l", f"pre6g.io/smoke-id={plan['run_id']}", "-o", "json"), cwd=output, log=log)
            statuses = {row["metadata"]["name"]: row.get("status", {}) for row in json.loads(raw).get("items", [])}
            if any(statuses.get(name, {}).get("phase") == "Failed" for name in expected):
                raise RuntimeError("a smoke Pod failed; inspect the saved Pod logs")
            if all(statuses.get(name, {}).get("phase") == "Succeeded" for name in expected):
                break
            time.sleep(5)
        else:
            raise RuntimeError("smoke Pods did not all succeed within six minutes")
    finally:
        for pod in created:
            name = pod["metadata"]["name"]
            result = subprocess.run(_kubectl(context, "-n", namespace, "logs", name, "-c", "smoke"), capture_output=True, text=True, encoding="utf-8", errors="replace", check=False)
            path = output / "logs" / f"{name}.log"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(result.stdout + result.stderr, encoding="utf-8")
        for pod in created:
            name = pod["metadata"]["name"]
            try:
                _invoke(_kubectl(context, "-n", namespace, "delete", "pod", name, "--wait=true", "--timeout=60s"), cwd=output, log=log)
            except RuntimeError as exc:
                cleanup_errors.append(f"{name}: {exc}")
        if cleanup_errors:
            raise RuntimeError("smoke Pod cleanup failed: " + "; ".join(cleanup_errors))
    results = []
    for item in plan["candidates"]:
        name = _name(f"pre6g-smoke-{plan['run_id']}-{item['node']}", "smoke Pod name")
        saved = (output / "logs" / f"{name}.log").read_text(encoding="utf-8")
        results.append(_parse_smoke_log(saved, run_id=plan["run_id"], node=item["node"], gpu_uuid=item["gpu_uuid"]))
    _write_json(output / "smoke-result.json", {
        "schema_version": "pre6g.cross-node-smoke-summary/v1", "run_id": plan["run_id"],
        "passed": True, "results": results, "pvc_relative_marker_path": f"smoke/{plan['run_id']}",
        "pods_deleted": True,
    })


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
    parser = argparse.ArgumentParser(description="Plan, smoke-test, or execute parallel YOLO26 dry-run Jobs, then rank nodes on the master.")
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--worker-commit", required=True, help="Full pushed Git SHA checked out inside each worker Job")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--preflight-only", action="store_true", help="Check live cluster prerequisites and render resolved Jobs without creating resources")
    parser.add_argument("--smoke-only", action="store_true", help="Run short non-training Pods on candidate nodes, then delete them")
    parser.add_argument("--execute", action="store_true", help="Actually create all dry-run Jobs in Kubernetes")
    parser.add_argument("--kube-context", help="Required with --preflight-only, --smoke-only, or --execute; must equal current kube context")
    parser.add_argument("--timeout-seconds", type=int, default=900)
    args = parser.parse_args()
    if sum((args.preflight_only, args.smoke_only, args.execute)) > 1:
        parser.error("--preflight-only, --smoke-only, and --execute are mutually exclusive")
    if (args.execute or args.preflight_only or args.smoke_only) and not args.kube_context:
        parser.error("--preflight-only, --smoke-only, and --execute require --kube-context")
    if args.timeout_seconds <= 0:
        parser.error("--timeout-seconds must be positive")
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    summary = output / "cross-node-run-summary.json"
    if summary.exists() and _json(summary).get("status") == "completed":
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
        if not args.execute and not args.preflight_only and not args.smoke_only:
            _write_json(summary, {"schema_version": "pre6g.cross-node-run-summary/v1", "status": "planned", "started_at": started_at, "run_id": args.run_id, "plan": str(plan_path)})
            print(f"[planned] {plan_path}; no Kubernetes Job was created")
            return 0
        phase = "preflight"
        resolved_jobs_path = _preflight(plan, jobs, args.kube_context, output)
        if args.preflight_only:
            _write_json(summary, {"schema_version": "pre6g.cross-node-run-summary/v1", "status": "preflight_passed", "started_at": started_at, "finished_at": datetime.now(timezone.utc).isoformat(), "run_id": args.run_id, "resolved_jobs": str(resolved_jobs_path)})
            print(f"[preflight passed] {resolved_jobs_path}; no Kubernetes resource was created")
            return 0
        if args.smoke_only:
            phase = "smoke"
            _run_smoke(plan, jobs, args.kube_context, output)
            _write_json(summary, {"schema_version": "pre6g.cross-node-run-summary/v1", "status": "smoke_passed", "started_at": started_at, "finished_at": datetime.now(timezone.utc).isoformat(), "run_id": args.run_id, "smoke_result": str(output / "smoke-result.json")})
            print(f"[smoke passed] {output / 'smoke-result.json'}; no YOLO training Job was created")
            return 0
        phase = "submit"
        _invoke(_kubectl(args.kube_context, "-n", plan["namespace"], "create", "-f", str(resolved_jobs_path)), cwd=output, log=output / "logs" / "kubectl.log")
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
