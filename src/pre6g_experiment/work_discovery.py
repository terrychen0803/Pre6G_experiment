from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import yaml

from .work import estimate_work


class WorkDiscoveryError(ValueError):
    pass


IMAGE_SUFFIXES = {
    ".jpg",
    ".jpeg",
    ".png",
    ".bmp",
    ".webp",
    ".tif",
    ".tiff",
}


def _count_images(path: Path) -> int:
    if path.is_dir():
        return sum(
            1
            for item in path.rglob("*")
            if item.is_file() and item.suffix.lower() in IMAGE_SUFFIXES
        )

    if path.is_file() and path.suffix.lower() == ".txt":
        return sum(
            1
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        )

    if path.is_file() and path.suffix.lower() in IMAGE_SUFFIXES:
        return 1

    raise WorkDiscoveryError(
        f"unsupported or missing YOLO train path: {path}"
    )


def _resolve_dataset_root(data_yaml: Path, payload: dict[str, Any]) -> Path:
    raw_root = payload.get("path")
    if raw_root in (None, ""):
        return data_yaml.parent.resolve()

    root = Path(str(raw_root))
    if root.is_absolute():
        return root.resolve()
    return (data_yaml.parent / root).resolve()


def _discover_yolo_dataset(
    data_value: str,
) -> tuple[int, dict[str, Any]]:
    data_yaml = Path(data_value).expanduser()

    if not data_yaml.is_absolute():
        data_yaml = data_yaml.resolve()

    if not data_yaml.is_file():
        raise WorkDiscoveryError(
            "YOLO dataset YAML is not visible at the original application "
            f"path: {data_yaml}. Run work discovery in a Pod/container with "
            "the same dataset volume mounts as the user workload."
        )

    payload = yaml.safe_load(data_yaml.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise WorkDiscoveryError(
            f"YOLO dataset YAML must contain a mapping: {data_yaml}"
        )

    train_spec = payload.get("train")
    if train_spec in (None, ""):
        raise WorkDiscoveryError(
            f"YOLO dataset YAML has no train entry: {data_yaml}"
        )

    dataset_root = _resolve_dataset_root(data_yaml, payload)
    items = train_spec if isinstance(train_spec, list) else [train_spec]

    resolved_paths: list[str] = []
    total = 0
    for raw_item in items:
        path = Path(str(raw_item)).expanduser()
        if not path.is_absolute():
            path = dataset_root / path
        path = path.resolve()
        resolved_paths.append(str(path))
        total += _count_images(path)

    if total <= 0:
        raise WorkDiscoveryError(
            f"no training samples discovered from {data_yaml}"
        )

    return total, {
        "status": "discovered",
        "data_yaml": str(data_yaml),
        "train_spec": train_spec,
        "resolved_train_paths": resolved_paths,
        "dataset_train_samples": total,
    }


def _policy() -> dict[str, bool]:
    return {
        "uses_iterations_csv": False,
        "uses_nvtx": False,
        "uses_callbacks": False,
        "uses_epoch_timestamps": False,
        "uses_batch_timestamps": False,
    }


def discover_work(job: dict[str, Any]) -> dict[str, Any]:
    """
    Resolve total work without requiring runtime iteration markers.

    The existing workload adapter remains the semantic source of truth for
    workload family, parameters, and candidate work unit. This layer only
    enriches that semantic view with information that can be discovered from
    resources already mounted for the original application, such as YOLO
    dataset cardinality.
    """
    estimate = estimate_work(job)
    parameters = dict(estimate.parameters)

    if estimate.total_work_units is not None:
        steps_per_epoch = None
        epochs = parameters.get("epochs")
        if (
            estimate.adapter == "yolo"
            and epochs
            and estimate.total_work_units % int(epochs) == 0
        ):
            steps_per_epoch = estimate.total_work_units // int(epochs)

        return {
            "schema_version": "pre6g.work-discovery/v1",
            "workload_family": estimate.workload_family,
            "adapter": estimate.adapter,
            "profileable": estimate.profileable,
            "discovery_mode": "static-semantic-contract",
            "parameters": parameters,
            "dataset": {
                "status": "not-required-for-total-work",
            },
            "work": {
                "status": estimate.status,
                "unit": estimate.work_unit,
                "steps_per_epoch": steps_per_epoch,
                "total_units": estimate.total_work_units,
                "source": estimate.source,
                "assumptions": estimate.assumptions,
                "missing": estimate.missing,
            },
            "production_input_policy": _policy(),
        }

    if estimate.adapter != "yolo":
        return {
            "schema_version": "pre6g.work-discovery/v1",
            "workload_family": estimate.workload_family,
            "adapter": estimate.adapter,
            "profileable": estimate.profileable,
            "discovery_mode": "unresolved",
            "parameters": parameters,
            "dataset": {
                "status": "not-supported-by-current-discovery-adapter",
            },
            "work": {
                "status": "unknown",
                "unit": estimate.work_unit,
                "steps_per_epoch": None,
                "total_units": None,
                "source": estimate.source,
                "assumptions": estimate.assumptions,
                "missing": estimate.missing,
            },
            "production_input_policy": _policy(),
        }

    epochs = parameters.get("epochs")
    batch_size = parameters.get("batch_size")
    data_value = parameters.get("data")

    missing = []
    if not epochs:
        missing.append("epochs")
    if not batch_size:
        missing.append("batch size")
    if not data_value:
        missing.append("YOLO data path")

    if missing:
        return {
            "schema_version": "pre6g.work-discovery/v1",
            "workload_family": estimate.workload_family,
            "adapter": estimate.adapter,
            "profileable": estimate.profileable,
            "discovery_mode": "unresolved",
            "parameters": parameters,
            "dataset": {
                "status": "not-discovered",
            },
            "work": {
                "status": "unknown",
                "unit": estimate.work_unit or "training_iteration",
                "steps_per_epoch": None,
                "total_units": None,
                "source": "YOLO work discovery lacks required static inputs",
                "assumptions": estimate.assumptions,
                "missing": missing,
            },
            "production_input_policy": _policy(),
        }

    sample_count, dataset = _discover_yolo_dataset(str(data_value))
    epochs_i = int(epochs)
    batch_i = int(batch_size)
    steps_per_epoch = math.ceil(sample_count / batch_i)
    total_units = epochs_i * steps_per_epoch

    parameters["dataset_train_samples"] = sample_count

    assumptions = list(estimate.assumptions)
    formula = "steps_per_epoch=ceil(training_samples/batch)"
    if formula not in assumptions:
        assumptions.append(formula)

    return {
        "schema_version": "pre6g.work-discovery/v1",
        "workload_family": estimate.workload_family,
        "adapter": estimate.adapter,
        "profileable": estimate.profileable,
        "discovery_mode": "mounted-dataset",
        "parameters": parameters,
        "dataset": dataset,
        "work": {
            "status": "estimated",
            "unit": estimate.work_unit or "training_iteration",
            "steps_per_epoch": steps_per_epoch,
            "total_units": total_units,
            "source": (
                "adapter:yolo argv/config + mounted dataset cardinality"
            ),
            "assumptions": assumptions,
            "missing": [],
        },
        "production_input_policy": _policy(),
    }
