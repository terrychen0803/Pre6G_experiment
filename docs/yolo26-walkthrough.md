# YOLO26 integration fixture

YOLO26 is the first workload used to exercise the generic Pre6G_experiment intake and profiling pipeline. It is not the platform schema and it must not introduce YOLO-specific instrumentation requirements into the controller core.

## 1. Source Job

Example:

~~~text
examples/yolo26/user-job.yaml
~~~

The platform reads two independent static views:

~~~text
Execution Contract
  application container
  image
  command
  args
  resources
  volumes

Static Semantic Contract
  workload_family = vision-training
  adapter = yolo
~~~

The original argv remains the execution source of truth.

## 2. Static semantic estimate

Current example:

~~~text
dataset_train_samples = 512
epochs = 20
batch = 16
~~~

YOLO adapter estimate:

~~~text
steps_per_epoch = ceil(512 / 16) = 32
total_work_units = 20 × 32 = 640
candidate work_unit = training_iteration
~~~

This is a static semantic estimate. It does not prove that one recurring CUDA cycle equals one training iteration.

## 3. Instrumented benchmark artifacts are validation-only

The existing yolo26_runtime_prediction project can generate:

~~~text
iterations.csv
summary.json
metadata.json
NVTX TRAIN_ITER markers
callback-derived timing
~~~

These artifacts are useful for research validation, but they are not production inputs to Pre6G marker-free profiling.

Formal detector input must not depend on:

~~~text
iterations.csv
NVTX iteration labels
Ultralytics callbacks
epoch/batch IDs
summary.json iteration mean
source-code instrumentation
~~~

Correct research workflow:

~~~text
1. Hide instrumented ground truth.
2. Run marker-free detector.
3. Freeze detector output.
4. Reveal instrumented ground truth.
5. Compute error.
~~~

## 4. Current C03 hidden ground truth

For the current RTX5090 C03 validation fixture:

~~~text
model = yolo26n.yaml
batch = 16
imgsz = 320
amp = false
max_iters = 128
warmup_iters = 20

observed:
32 batches / epoch
4 observed epochs
128 recorded iterations
steady-window valid iterations = 108
steady-window mean iteration = 31.141946287 ms
GPU-event mean = 27.943115252 ms
~~~

These values must remain hidden from the marker-free detector.

The steady-window mean iteration and GPU-event mean are different quantities. The marker-free detector should target recurring start-to-start cadence rather than simply summing kernel durations.

## 5. Current marker-free trace preflight

RTX5090 C03 trace:

~~~text
Nsight Systems = 2026.4.1.191-264138605071v0
profile.nsys-rep successfully exported to SQLite
CUPTI_ACTIVITY_KIND_KERNEL present
StringIds present
kernel_count = 653976
kernel-start span = 17.980515013 s
~~~

Usable kernel columns:

~~~text
start
end
shortName
globalPid
deviceId
contextId
streamId
~~~

Current kernel table contains one dominant process/context group:

~~~text
globalPid = 327417436569600
contextId = 1
kernel_count = 653976
~~~

This is sufficient to proceed to marker-free event extraction.

It does not yet prove that the correct training-iteration cadence can be recovered.

## 6. Marker-free extraction target

The next artifact should be:

~~~text
marker-free-events.csv
~~~

Fields:

~~~text
start_ns
end_ns
duration_ns
short_name_id
global_pid
context_id
stream_id
relative_start_ns
inter_arrival_ns
~~~

Do not include:

~~~text
iteration_id
epoch
batch_in_epoch
NVTX label
callback-derived label
instrumented ground truth
~~~

## 7. Detector output

The detector should first report:

~~~json
{
  "detected_unit": "execution_cycle",
  "period_ms": "...",
  "confidence": "...",
  "complete_cycles": "...",
  "target_process_identified": true,
  "stability": {
    "two_window_pass": true
  }
}
~~~

It must not directly output:

~~~text
detected_unit = training_iteration
~~~

just because this test application is YOLO training.

## 8. Semantic binding

After marker-free period detection:

~~~text
execution_cycle
      ↓
semantic binding
      ↓
training_iteration ?
~~~

Binding must be explicit.

If validated:

~~~text
status = bound
cycles_per_work_unit = 1
work_unit = training_iteration
~~~

then total runtime extrapolation can use the static total work estimate.

If not validated:

~~~text
status = unbound
detected_unit = execution_cycle
~~~

the platform can still report cycle latency / slowdown, but not a trusted total training runtime.

## 9. Phase 05 status

Completed:

~~~text
Generic Job inspection PASS
YOLO static adapter PASS
Unknown workload remains profileable PASS
Explicit non-YOLO work-unit metadata PASS
C03 marker-free trace schema preflight PASS
target process/context isolation preflight PASS
~~~

Current next step:

~~~text
Nsight SQLite
  → marker-free event extraction
  → recurring-pattern detection
  → execution_cycle period
  → freeze prediction
  → compare with hidden C03 ground truth
~~~

## 10. Phase 06 and Phase 07

After marker-free detection works on existing trace data:

### Phase 06

Run the same production-style method on short 5–15 s Kubernetes profiles on both RTX4090 and RTX5090.

No user-code instrumentation dependency is allowed.

### Phase 07

Only then run formal fixed 120 s profiles on both candidate nodes.

## 11. Runtime and energy semantics

Semantic-aware case:

~~~text
validated execution_cycle -> training_iteration binding
+
total_work_units = 640
+
runtime prediction per training_iteration
        ↓
predicted total runtime
~~~

Opaque/unbound case:

~~~text
execution_cycle period
+
loading state
        ↓
cycle latency / slowdown only
~~~

The real RTX4090/RTX5090 power-model bundles are not yet available, so real energy ranking remains profile-only.

## 12. Synthetic decision-path test

Synthetic results remain useful only for post-model control-flow validation:

~~~bash
python -m pre6g_experiment decide   --job examples/yolo26/user-job.yaml   --results examples/yolo26/synthetic-node-results.json   --output generated/yolo26-production-job.yaml   --allow-synthetic
~~~

Synthetic values must not be reported as measured performance or energy results.
