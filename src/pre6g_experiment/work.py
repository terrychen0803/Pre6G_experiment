from __future__ import annotations

import json
import math
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class WorkEstimate:
    workload_family: str
    adapter: str
    parameters: dict[str, Any]
    status: str
    work_unit: str | None
    total_work_units: int | None
    source: str
    assumptions: list[str]
    missing: list[str]
    profileable: bool = True

    @property
    def total_iterations(self) -> int | None:
        """Backward-compatible alias for older iteration-oriented callers."""
        if self.work_unit in {"iteration", "training_iteration"}:
            return self.total_work_units
        return None

    def to_dict(self) -> dict[str, Any]:
        return {
            "workload_family": self.workload_family,
            "adapter": self.adapter,
            "profileable": self.profileable,
            "parameters": self.parameters,
            "work": {
                "status": self.status,
                "unit": self.work_unit,
                "total_units": self.total_work_units,
                "source": self.source,
                "assumptions": self.assumptions,
                "missing": self.missing,
                "runtime_discovery_required": self.status == "unknown",
            },
        }


def _annotations(job: dict[str, Any]) -> dict[str, str]:
    metadata = job.get("metadata") or {}
    raw = metadata.get("annotations") or {}
    return {str(key): str(value) for key, value in raw.items()}


def _option(tokens: list[str], names: tuple[str, ...]) -> str | None:
    for index, token in enumerate(tokens):
        for name in names:
            if token == name and index + 1 < len(tokens):
                return tokens[index + 1]
            prefix = name + "="
            if token.startswith(prefix):
                return token[len(prefix) :]
    return None


def _positive_int(value: Any) -> int | None:
    try:
        parsed = int(str(value))
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _optional_bool(value: str | None) -> bool | None:
    if value is None:
        return None
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    return None


def _declared_parameters(annotations: dict[str, str]) -> dict[str, Any]:
    raw = annotations.get("pre6g.io/workload-parameters-json")
    if not raw:
        return {}
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(
            "metadata annotation pre6g.io/workload-parameters-json must be valid JSON"
        ) from exc
    if not isinstance(value, dict):
        raise ValueError(
            "metadata annotation pre6g.io/workload-parameters-json must contain a JSON object"
        )
    return value


def application_container(job: dict[str, Any]) -> dict[str, Any]:
    annotations = _annotations(job)
    requested = annotations.get("pre6g.io/application-container")
    containers = (
        (((job.get("spec") or {}).get("template") or {}).get("spec") or {}).get(
            "containers"
        )
        or []
    )
    if requested:
        matches = [item for item in containers if item.get("name") == requested]
        if len(matches) != 1:
            raise ValueError(
                f"Application container {requested!r} was not found exactly once"
            )
        return matches[0]
    if len(containers) != 1:
        raise ValueError(
            "Set metadata.annotations['pre6g.io/application-container'] when the Job "
            "does not contain exactly one container"
        )
    return containers[0]


def execution_contract(job: dict[str, Any]) -> dict[str, Any]:
    """Return the workload execution shape without interpreting argument semantics."""
    container = application_container(job)
    pod_spec = (((job.get("spec") or {}).get("template") or {}).get("spec") or {})

    volume_mounts = []
    for item in container.get("volumeMounts") or []:
        volume_mounts.append(
            {
                "name": item.get("name"),
                "mount_path": item.get("mountPath"),
                "read_only": bool(item.get("readOnly", False)),
            }
        )

    volume_names = [
        str(item.get("name"))
        for item in (pod_spec.get("volumes") or [])
        if item.get("name")
    ]

    env_names = [
        str(item.get("name"))
        for item in (container.get("env") or [])
        if item.get("name")
    ]

    return {
        "application_container": container.get("name"),
        "image": container.get("image"),
        "command": [str(item) for item in (container.get("command") or [])],
        "args": [str(item) for item in (container.get("args") or [])],
        "env_names": env_names,
        "resources": container.get("resources") or {},
        "volume_mounts": volume_mounts,
        "pod_volumes": volume_names,
        "runtime_class_name": pod_spec.get("runtimeClassName"),
        "restart_policy": pod_spec.get("restartPolicy"),
    }


def _command_tokens(job: dict[str, Any]) -> list[str]:
    container = application_container(job)
    tokens = [str(item) for item in (container.get("command") or [])]
    tokens += [str(item) for item in (container.get("args") or [])]
    return tokens


def _select_adapter(
    annotations: dict[str, str],
    workload_family: str,
) -> str:
    requested = annotations.get("pre6g.io/workload-adapter", "").strip().lower()
    if requested:
        return requested
    if "yolo" in workload_family.lower():
        return "yolo"
    return "generic"


def _yolo_adapter(
    job: dict[str, Any],
    annotations: dict[str, str],
) -> tuple[dict[str, Any], int | None, list[str], list[str]]:
    tokens = _command_tokens(job)

    model = _option(tokens, ("--model", "model"))
    epochs = _positive_int(_option(tokens, ("--epochs", "epochs")))
    batch = _positive_int(
        _option(tokens, ("--batch", "--batch-size", "batch", "batch-size"))
    )
    imgsz = _positive_int(
        _option(tokens, ("--imgsz", "--img-size", "imgsz", "img-size"))
    )
    amp = _optional_bool(_option(tokens, ("--amp", "amp")))
    samples = _positive_int(annotations.get("pre6g.io/dataset-train-samples"))

    parameters: dict[str, Any] = {}
    if model:
        parameters["model"] = model
    if epochs:
        parameters["epochs"] = epochs
    if batch:
        parameters["batch_size"] = batch
    if imgsz:
        parameters["input_size"] = imgsz
    if amp is not None:
        parameters["amp"] = amp
    if samples:
        parameters["dataset_train_samples"] = samples

    missing = []
    if not epochs:
        missing.append("epochs")
    if not batch:
        missing.append("batch size")
    if not samples:
        missing.append("dataset training sample count")

    assumptions_text = annotations.get("pre6g.io/work-estimate-assumptions", "")
    assumptions = [
        item.strip()
        for item in assumptions_text.split(",")
        if item.strip()
    ]

    total_units = None
    if epochs and batch and samples:
        steps_per_epoch = math.ceil(samples / batch)
        total_units = epochs * steps_per_epoch
        assumptions.append("steps_per_epoch=ceil(training_samples/batch)")

    return parameters, total_units, assumptions, missing


def estimate_work(job: dict[str, Any]) -> WorkEstimate:
    """
    Build a canonical workload-semantic view.

    Profiling does not depend on successful work estimation.  Unknown workloads
    remain profileable; they simply cannot be extrapolated to total runtime or
    total energy until explicit metadata, an adapter, or runtime discovery
    resolves the amount of work.
    """
    annotations = _annotations(job)
    workload_family = annotations.get("pre6g.io/workload-family", "unknown").strip()
    workload_family = workload_family or "unknown"
    adapter = _select_adapter(annotations, workload_family)

    declared_parameters = _declared_parameters(annotations)
    adapter_parameters: dict[str, Any] = {}
    adapter_total_units: int | None = None
    adapter_assumptions: list[str] = []
    adapter_missing: list[str] = []

    if adapter == "yolo":
        (
            adapter_parameters,
            adapter_total_units,
            adapter_assumptions,
            adapter_missing,
        ) = _yolo_adapter(job, annotations)

    # User-declared canonical parameters take precedence over adapter guesses.
    parameters = {**adapter_parameters, **declared_parameters}

    declared_unit = annotations.get("pre6g.io/work-unit")
    declared_total = _positive_int(annotations.get("pre6g.io/total-work-units"))
    legacy_total = _positive_int(annotations.get("pre6g.io/total-iterations"))

    if declared_total and legacy_total and declared_total != legacy_total:
        raise ValueError(
            "pre6g.io/total-work-units conflicts with legacy "
            "pre6g.io/total-iterations"
        )

    if declared_total is not None:
        if not declared_unit:
            return WorkEstimate(
                workload_family=workload_family,
                adapter=adapter,
                parameters=parameters,
                status="unknown",
                work_unit=None,
                total_work_units=None,
                source=(
                    "pre6g.io/total-work-units was declared without "
                    "pre6g.io/work-unit"
                ),
                assumptions=[],
                missing=["work unit"],
            )
        return WorkEstimate(
            workload_family=workload_family,
            adapter=adapter,
            parameters=parameters,
            status="declared",
            work_unit=declared_unit,
            total_work_units=declared_total,
            source="metadata annotations pre6g.io/work-unit + pre6g.io/total-work-units",
            assumptions=[],
            missing=[],
        )

    # Backward compatibility for the original prototype annotation.
    if legacy_total is not None:
        unit = declared_unit or "training_iteration"
        return WorkEstimate(
            workload_family=workload_family,
            adapter=adapter,
            parameters=parameters,
            status="declared",
            work_unit=unit,
            total_work_units=legacy_total,
            source="legacy metadata annotation pre6g.io/total-iterations",
            assumptions=["legacy total-iterations compatibility path"],
            missing=[],
        )

    if adapter == "yolo" and adapter_total_units is not None:
        return WorkEstimate(
            workload_family=workload_family,
            adapter=adapter,
            parameters=parameters,
            status="estimated",
            work_unit=declared_unit or "training_iteration",
            total_work_units=adapter_total_units,
            source="adapter:yolo epochs * ceil(dataset samples / batch)",
            assumptions=adapter_assumptions,
            missing=[],
        )

    missing: list[str] = []
    if not declared_unit:
        missing.append("work unit")
    missing.append("total work units")

    if adapter == "yolo":
        missing.extend(
            item for item in adapter_missing if item not in missing
        )
        source = "YOLO adapter lacks sufficient metadata; use runtime discovery"
    elif adapter != "generic":
        missing.append(f"registered adapter implementation for {adapter!r}")
        source = (
            f"requested workload adapter {adapter!r} is not implemented; "
            "use explicit metadata or runtime discovery"
        )
    else:
        source = (
            "no declared work amount and no registered adapter estimate; "
            "use runtime discovery"
        )

    return WorkEstimate(
        workload_family=workload_family,
        adapter=adapter,
        parameters=parameters,
        status="unknown",
        work_unit=declared_unit,
        total_work_units=None,
        source=source,
        assumptions=adapter_assumptions,
        missing=missing,
    )
