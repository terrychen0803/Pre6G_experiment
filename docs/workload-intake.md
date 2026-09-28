# Generic workload intake and marker-free discovery

## Goal

Pre6G_experiment must accept arbitrary Kubernetes batch/v1 Jobs. YOLO is only the first integration fixture; the platform core must not require YOLO-specific flags, training callbacks, NVTX markers, or source-code instrumentation.

The intake path is split into three contracts:

~~~text
User Job
   |
   +--> Execution Contract
   |      image / command / args / env / resources / volumes
   |
   +--> Static Semantic Contract
   |      explicit metadata / known argv / config / dataset metadata
   |
   +--> Marker-Free Runtime Discovery
          target-process CUDA trace
          recurring execution cycle
          period / confidence / stability
~~~

A workload can be profileable even when its total work amount or semantic work unit is unknown.

## Execution contract

The controller identifies the primary application container through:

~~~yaml
metadata:
  annotations:
    pre6g.io/application-container: app
~~~

If a Job contains exactly one container, the annotation may be omitted.

The execution contract preserves:

- image
- command
- args
- environment configuration
- resources
- volume mounts
- runtimeClassName
- restart policy

The platform does not need to understand argument semantics to launch a dry-run.

## Static semantic contract

Static semantic discovery may use only information already available outside the application process:

- explicit Pre6G annotations;
- known command-line arguments;
- mounted framework configuration;
- dataset manifest / sample-count metadata;
- image metadata when explicitly defined by an adapter.

It must not require modifying the user application.

Canonical semantic schema:

~~~text
schemas/workload-spec.schema.json
~~~

Example:

~~~json
{
  "workload_family": "vision-training",
  "adapter": "yolo",
  "profileable": true,
  "parameters": {
    "model": "yolo26n.yaml",
    "batch_size": 16,
    "input_size": 640,
    "epochs": 20
  },
  "work": {
    "status": "estimated",
    "unit": "training_iteration",
    "total_units": 640,
    "source": "adapter:yolo epochs * ceil(dataset samples / batch)",
    "runtime_discovery_required": false
  }
}
~~~

This spec expresses application semantics. It does not prove that one detected CUDA recurring cycle equals one semantic work unit.

## Static discovery priority

### 1. Explicit canonical metadata

~~~yaml
metadata:
  annotations:
    pre6g.io/workload-family: video-encoding
    pre6g.io/work-unit: frame
    pre6g.io/total-work-units: "18000"
    pre6g.io/workload-parameters-json: >-
      {"codec":"h265","resolution":"3840x2160","preset":"slow"}
~~~

### 2. Registered workload adapter

An adapter understands one known workload family and translates existing Job/config fields into the canonical schema.

Current prototype adapter:

~~~text
YOLO
~~~

Possible future adapters:

~~~text
Hugging Face / LLM training
FFmpeg
FAISS
custom domain adapters
~~~

Adding an adapter must not change the core execution path.

### 3. Unknown

If no trustworthy static semantics exist:

~~~json
{
  "workload_family": "unknown",
  "adapter": "generic",
  "profileable": true,
  "parameters": {},
  "work": {
    "status": "unknown",
    "unit": null,
    "total_units": null,
    "runtime_discovery_required": true
  }
}
~~~

Unknown semantics do not block profiling.

## Marker-free runtime discovery

Production runtime discovery does not depend on user instrumentation.

Allowed runtime evidence:

~~~text
target-process CUDA trace
kernel timestamps
kernel short-name IDs
process/context IDs
stream IDs
~~~

Forbidden production dependencies:

~~~text
iterations.csv
NVTX iteration markers
training callbacks
epoch/batch labels
summary.json iteration timing
source-code instrumentation
~~~

The first detector output is intentionally semantic-neutral:

~~~json
{
  "detected_unit": "execution_cycle",
  "period_ms": 31.2,
  "confidence": 0.96,
  "complete_cycles": 87
}
~~~

The detector must not call this a training_iteration merely because the test fixture is a training application.

See [Marker-Free Workload Discovery](marker-free-workload-discovery.md).

## Semantic binding

Static semantics and marker-free execution behavior are joined only after detection.

~~~text
static semantics:
  candidate work_unit = training_iteration

marker-free detector:
  detected_unit = execution_cycle

                 |
                 v

semantic binding:
  execution_cycle -> training_iteration ?
~~~

Binding states:

~~~text
bound
unbound
conflict
insufficient_evidence
~~~

If bound:

~~~text
cycles_per_work_unit
work_unit
runtime_per_work_unit
~~~

can be used for total-work extrapolation.

If unbound, the system reports execution-cycle latency / slowdown only.

## Why this separation matters

A CUDA recurring cycle is not automatically an application iteration.

Possible cases:

~~~text
1 execution_cycle = 1 training_iteration
2 execution_cycles = 1 optimizer_step
1 training_iteration contains multiple CUDA sub-cycles
~~~

Therefore:

~~~text
period detection
!=
semantic interpretation
~~~

The detector and workload adapter must remain separate.

## Research validation policy

Instrumented benchmark artifacts may be used only after marker-free output is frozen.

Correct order:

~~~text
1. Hide iterations.csv / NVTX / callback-derived truth.
2. Run marker-free detector.
3. Freeze predicted cycle period.
4. Reveal hidden ground truth.
5. Compute validation error.
~~~

This prevents label leakage into the production method.

## Current YOLO C03 validation status

Static/instrumented validation evidence:

~~~text
batch = 16
imgsz = 320
32 batches / epoch
steady-window mean iteration = 31.141946287 ms
GPU-event mean = 27.943115252 ms
~~~

Marker-free trace preflight on RTX5090:

~~~text
Nsight Systems 2026.4.1.191
CUPTI_ACTIVITY_KIND_KERNEL present
StringIds present
kernel_count = 653976
kernel-start span = 17.980515013 s
single dominant process/context
~~~

This proves the trace is suitable for marker-free event extraction. It does not yet prove that the correct execution-cycle period can be recovered.

## Work-unit examples

| Workload | semantic work_unit |
|---|---|
| Vision training | training_iteration |
| LLM fine-tuning | optimizer_step |
| LLM inference | generated_token |
| Video encoding | frame |
| FAISS build | vector_insert |
| Opaque cyclic CUDA workload | unknown until binding |

The detector-level unit is always execution_cycle until binding.

## Backward compatibility

The original prototype annotation remains accepted:

~~~yaml
pre6g.io/total-iterations: "640"
~~~

It maps to:

~~~text
work_unit = training_iteration
total_work_units = 640
~~~

New integrations should use:

~~~text
pre6g.io/work-unit
pre6g.io/total-work-units
~~~

## Phase 05 acceptance criteria

Phase 05 PASS requires:

1. arbitrary batch/v1 Job can be inspected;
2. original image/command/args/resources are captured without semantic rewriting;
3. static semantic discovery produces a canonical workload spec or explicit unknown state;
4. unknown semantics do not block profiling;
5. marker-free extraction uses only target-process CUDA trace;
6. detector does not depend on iterations.csv, NVTX, callbacks, or user code changes;
7. detector emits execution_cycle before semantic binding;
8. semantic binding is explicit and can remain unbound;
9. instrumented ground truth is validation-only.
