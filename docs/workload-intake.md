# Generic workload intake and semantic discovery

## Goal

Pre6G_experiment must accept arbitrary Kubernetes batch/v1 Jobs. YOLO is only the first integration fixture; the platform core must not require YOLO-specific flags or training semantics.

The intake path is split into two contracts:

~~~
User Job
   |
   +--> Execution Contract
   |      image / command / args / env / resources / volumes
   |      preserved without semantic interpretation
   |
   +--> Workload Semantic Contract
          explicit metadata
          registered workload adapter
          runtime discovery
          or unknown
~~~

A workload can be profileable even when its total work amount is unknown.

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

The platform does not need to understand argument semantics in order to launch a dry-run.

For example, both of these are valid opaque workloads:

~~~text
python3 train.py --custom-flag 123
ffmpeg -i /data/input.mp4 -c:v libx265 -preset slow
~~~

The Profile Job wraps the original argv with Nsight rather than rewriting application parameters.

## Canonical workload semantic contract

Semantic discovery produces schemas/workload-spec.schema.json.

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
    "assumptions": [],
    "missing": [],
    "runtime_discovery_required": false
  }
}
~~~

The platform uses generic names:

~~~
work_unit
total_work_units
runtime_per_work_unit
energy_per_work_unit
~~~

Examples:

| Workload | work_unit |
|---|---|
| YOLO / vision training | training_iteration |
| LLM fine-tuning | optimizer_step |
| LLM inference | generated_token |
| FFmpeg | frame |
| FAISS build | vector_insert |
| unknown cyclic CUDA workload | cycle, only after validated discovery |

Do not force every workload into the term "iteration".

## Discovery priority

### 1. Explicit canonical metadata

Preferred when the submitter or an upstream admission service already knows the work amount.

~~~yaml
metadata:
  annotations:
    pre6g.io/workload-family: video-encoding
    pre6g.io/work-unit: frame
    pre6g.io/total-work-units: "18000"
    pre6g.io/workload-parameters-json: >-
      {"codec":"h265","resolution":"3840x2160","preset":"slow"}
~~~

The JSON parameter object is descriptive/model input metadata. It does not replace command/args.

### 2. Registered workload adapter

An adapter understands one known workload family and converts framework-specific configuration into the canonical schema.

The first adapter is YOLO.

Current YOLO adapter can read:

- --model
- --epochs
- --batch / --batch-size
- --imgsz / --img-size
- --amp
- pre6g.io/dataset-train-samples

If epochs, batch size, and dataset sample count are available under the stated assumptions:

~~~
steps_per_epoch = ceil(training_samples / batch)
total_work_units = epochs * steps_per_epoch
work_unit = training_iteration
~~~

Future adapters may include Hugging Face/LLM training, FFmpeg, FAISS, and other application families.

Adding an adapter should not change the controller execution path.

### 3. Runtime discovery

Static Job metadata can be incomplete or wrong at runtime.

A workload-specific wrapper or discovery hook may report:

- actual batch size
- dataloader length
- optimizer steps
- world size
- gradient accumulation
- generated token target
- total frame count
- observed periodic unit boundaries

Runtime-discovered semantics should be stored separately from requested parameters so differences remain visible.

Example:

~~~json
{
  "requested": {
    "batch_size": 16
  },
  "discovered": {
    "actual_batch_size": 16,
    "steps_per_epoch": 32,
    "world_size": 1,
    "gradient_accumulation": 1
  }
}
~~~

The platform should prefer validated discovered values over assumptions when deriving total runtime.

### 4. Unknown

An unknown workload remains valid for profiling:

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

Allowed:

- short execution compatibility test
- Nsight target-process trace
- Netdata/DCGM telemetry
- period/cycle discovery when technically valid
- artifact collection

Not allowed without additional semantics:

- extrapolating a per-unit prediction to a total runtime
- extrapolating to total energy
- claiming a valid automatic placement based on total job cost

## Backward compatibility

The original prototype annotation remains accepted:

~~~yaml
pre6g.io/total-iterations: "640"
~~~

It is interpreted as:

~~~text
work_unit = training_iteration
total_work_units = 640
~~~

New integrations should use pre6g.io/work-unit + pre6g.io/total-work-units.

## Runtime prediction contract

A generic runtime result should declare the semantic unit:

~~~json
{
  "runtime": {
    "status": "ready",
    "work_unit": "training_iteration",
    "predicted_runtime_ms_per_work_unit": 42.1
  }
}
~~~

The decision layer verifies that the runtime model work unit matches the workload work unit before multiplying by total_work_units.

The legacy predicted_runtime_ms_per_iteration field is retained only for compatibility.

## Phase 05 acceptance criteria

Phase 05 validates workload intake, not final prediction accuracy.

PASS requires:

1. arbitrary batch/v1 Job can be inspected;
2. original image/command/args/resources are captured as an execution contract;
3. semantic discovery produces a valid canonical workload spec;
4. unknown semantics do not block profiling;
5. the YOLO fixture can be interpreted through the YOLO adapter;
6. runtime-discovered values can later be compared with requested/estimated values;
7. the same unmodified application contract can be used to create node-pinned dry-run Jobs.

YOLO-specific container execution and short cluster runs are the first integration test, not the platform boundary.
