# Marker-Free Workload Discovery

## Goal

The production Pre6G path must work with an opaque user application without requiring source-code modification or framework-specific instrumentation.

The marker-free discovery path therefore accepts only:

- the original Kubernetes Job execution contract;
- static metadata that already exists outside the application process, such as argv, mounted config, annotations, image metadata, or dataset metadata;
- a target-process CUDA trace collected by Nsight Systems;
- node telemetry collected independently through Netdata and DCGM.

The production detector must not depend on:

- iterations.csv;
- NVTX iteration markers;
- training callbacks;
- user-added timestamps;
- source-code instrumentation;
- framework-specific marker injection.

These artifacts may exist in research fixtures, but they are validation-only ground truth.

## Why marker-free discovery is separate from workload semantics

A CUDA trace can reveal recurring execution behavior without knowing the user's application semantics.

For example, a detector may observe a stable recurring sequence and estimate:

~~~text
detected_unit = execution_cycle
period_ms = 31.2
confidence = 0.96
~~~

This does not by itself prove:

~~~text
execution_cycle == training_iteration
~~~

A training iteration may contain multiple CUDA sub-cycles, or several detected cycles may compose one optimizer step. Therefore the platform separates:

~~~text
marker-free execution discovery
        |
        v
execution_cycle

static/application semantics
        |
        v
candidate work_unit

execution_cycle + candidate work_unit
        |
        v
semantic binding gate
~~~

Until the binding is validated, the detector output remains an execution_cycle.

## Production data path

~~~text
Opaque User Job
      |
      +--> static semantic extraction
      |      argv / config / annotations / dataset metadata
      |
      +--> short marker-free dry-run
               |
               v
        target-process CUDA trace
               |
               v
        target process/context isolation
               |
               v
        recurring-pattern detection
               |
               v
        execution_cycle period/confidence
               |
               v
        semantic binding gate
               |
        +------+------+
        |             |
        v             v
   bound unit      unbound
        |             |
        v             v
runtime/work unit  cycle latency /
total extrapolation slowdown only
~~~

## Trace input contract

The validated Nsight baseline is:

~~~text
Nsight Systems 2026.4.1.191-264138605071v0
trace = cuda,nvtx,osrt
sample = none
cpuctxsw = none
~~~

NVTX may be present in a trace for research audit, but marker-free extraction must ignore it.

For the marker-free runtime detector, the minimum useful CUDA kernel fields are:

~~~text
start
end
shortName
globalPid
contextId
streamId
~~~

StringIds is used only to resolve the kernel short name. Device-wide GPU Metrics is not a period-detection fallback.

## Target process and context

The detector first isolates the target CUDA process/context.

Preferred evidence:

1. Profile Job launches the original application directly under Nsight.
2. CUPTI kernel activity contains globalPid/contextId.
3. The dominant application process/context can be identified without using NVTX.

If multiple target processes or contexts are legitimate, the profile/model contract must explicitly support that topology. Do not silently merge unrelated co-tenant activity.

## Event representation

The first marker-free representation should preserve:

~~~text
start_ns
end_ns
duration_ns
short_name_id
global_pid
context_id
stream_id
~~~

A normalized event sequence may additionally contain:

~~~text
relative_start_ns
inter_arrival_ns
kernel_token
~~~

Do not add iteration_id, epoch, batch_in_epoch, or any label derived from instrumentation.

## Period detection target

The desired timing target is a recurring start-to-start cadence, not merely the sum of CUDA kernel durations.

~~~text
cycle_start[n] -> cycle_start[n+1]
                   |
                   v
               period
~~~

This distinction matters because wall-clock work-unit latency may include:

- gaps between kernels;
- CUDA API synchronization;
- CPU-side launch delay;
- data movement;
- scheduler/contention delay.

Summing GPU kernel busy time alone can underestimate end-to-end work-unit latency.

## Detector output

A marker-free detector should first output execution-cycle semantics:

~~~json
{
  "schema_version": "pre6g.marker-free-discovery/v1",
  "status": "detected",
  "detected_unit": "execution_cycle",
  "period_ms": 31.2,
  "confidence": 0.96,
  "complete_cycles": 87,
  "target_process_identified": true,
  "target_global_pid": "...",
  "target_context_id": 1,
  "input_window_seconds": 15.0,
  "stability": {
    "two_window_pass": true
  }
}
~~~

The detector must not write training_iteration merely because the test fixture happens to be a training job.

## Semantic binding

Semantic binding is a separate gate.

Possible states:

~~~text
bound
unbound
conflict
insufficient_evidence
~~~

Example:

~~~json
{
  "detected_unit": "execution_cycle",
  "semantic_binding": {
    "status": "bound",
    "work_unit": "training_iteration",
    "cycles_per_work_unit": 1,
    "source": "validated-workload-adapter"
  }
}
~~~

The binding may use:

- explicit user/upstream metadata;
- a validated framework adapter;
- a standard framework config;
- offline validation evidence collected during platform development.

Production binding must not use hidden iterations.csv or NVTX labels from the current run.

## Semantic-aware and opaque modes

### Semantic-aware mode

Required:

~~~text
work_unit known
total_work_units known
execution_cycle -> work_unit binding validated
~~~

Then:

~~~text
runtime_per_work_unit
        x
total_work_units
        =
predicted total runtime
~~~

This mode can support total-runtime and total-energy placement once the runtime and power models are also ready.

### Opaque mode

If the application remains semantically unknown:

~~~text
detected_unit = execution_cycle
total_work_units = unknown
~~~

The platform may still report:

- execution-cycle latency;
- predicted slowdown under current load;
- relative local performance;
- trace stability and OOD evidence.

It must not claim an exact total job runtime or total energy without an independent total-work signal.

## Research validation versus production input

Instrumented fixtures remain useful for evaluating the marker-free method.

Correct evaluation order:

~~~text
1. Hide instrumentation-derived ground truth.
2. Run marker-free detector using only allowed production inputs.
3. Freeze detector output.
4. Reveal instrumented ground truth.
5. Compute error.
~~~

This prevents answer leakage into the detector.

For the current YOLO C03 validation fixture, an existing instrumented baseline reports:

~~~text
batch = 16
imgsz = 320
observed 32 batches per epoch
steady-window mean iteration = 31.141946287 ms
GPU-event mean = 27.943115252 ms
~~~

These values are validation-only. They are not detector inputs.

## RTX5090 C03 marker-free preflight evidence

A marker-free trace preflight has been validated on the RTX5090 C03 trace using Nsight Systems 2026.4.1.191.

Observed SQLite evidence:

~~~text
CUPTI_ACTIVITY_KIND_KERNEL present
StringIds present
kernel_count = 653976
kernel start span = 17.980515013 s
usable columns:
  start
  end
  shortName
  globalPid
  deviceId
  contextId
  streamId
~~~

The trace contained one dominant CUDA process/context group for the kernel table:

~~~text
globalPid = 327417436569600
contextId = 1
kernel_count = 653976
~~~

This is sufficient to proceed to marker-free event extraction. It does not yet prove that a stable execution-cycle period can be recovered.

## Phase 05B acceptance criteria

Phase 05B PASS requires:

- no source-code modification;
- no callback dependency;
- no iterations.csv input;
- no NVTX dependency;
- target CUDA process/context identifiable;
- recurring execution pattern detectable;
- at least three complete cycles;
- period stable across independent windows;
- detector emits execution_cycle before semantic binding;
- instrumented ground truth revealed only after detector output is frozen.

The next implementation step is marker-free CUDA event extraction from the Nsight SQLite export.
