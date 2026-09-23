from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class WorkEstimate:
    status: str
    total_iterations: int | None
    source: str
    assumptions: list[str]
    missing: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


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


def application_container(job: dict[str, Any]) -> dict[str, Any]:
    metadata = job.get("metadata") or {}
    annotations = metadata.get("annotations") or {}
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
            raise ValueError(f"Application container {requested!r} was not found exactly once")
        return matches[0]
    if len(containers) != 1:
        raise ValueError(
            "Set metadata.annotations['pre6g.io/application-container'] when the Job "
            "does not contain exactly one container"
        )
    return containers[0]


def estimate_work(job: dict[str, Any]) -> WorkEstimate:
    metadata = job.get("metadata") or {}
    annotations = metadata.get("annotations") or {}
    explicit = _positive_int(annotations.get("pre6g.io/total-iterations"))
    if explicit:
        return WorkEstimate(
            status="declared",
            total_iterations=explicit,
            source="metadata annotation pre6g.io/total-iterations",
            assumptions=[],
            missing=[],
        )

    container = application_container(job)
    tokens = [str(item) for item in (container.get("command") or [])]
    tokens += [str(item) for item in (container.get("args") or [])]
    epochs = _positive_int(_option(tokens, ("--epochs", "epochs")))
    batch = _positive_int(_option(tokens, ("--batch", "--batch-size", "batch")))
    samples = _positive_int(annotations.get("pre6g.io/dataset-train-samples"))
    family = str(annotations.get("pre6g.io/workload-family", "")).lower()

    if "yolo" in family and epochs and batch and samples:
        steps = math.ceil(samples / batch)
        assumptions_text = annotations.get("pre6g.io/work-estimate-assumptions", "")
        assumptions = [item.strip() for item in assumptions_text.split(",") if item.strip()]
        assumptions += ["steps_per_epoch=ceil(training_samples/batch)"]
        return WorkEstimate(
            status="estimated",
            total_iterations=epochs * steps,
            source="yolo epochs * ceil(dataset samples / batch)",
            assumptions=assumptions,
            missing=[],
        )

    missing = []
    if not epochs:
        missing.append("epochs")
    if not batch:
        missing.append("batch")
    if not samples:
        missing.append("dataset training sample count")
    return WorkEstimate(
        status="unknown",
        total_iterations=None,
        source="insufficient static Job metadata; use runtime discovery",
        assumptions=[],
        missing=missing,
    )

