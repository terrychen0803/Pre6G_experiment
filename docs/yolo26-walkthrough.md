# YOLO26 integration fixture

YOLO26 is the first workload used to exercise the generic Pre6G_experiment intake and profiling pipeline. It is not the platform schema and it must not introduce YOLO-specific assumptions into the controller core.

## 1. Source Job

Example:

~~~text
examples/yolo26/user-job.yaml
~~~

The platform reads two independent views.

Execution contract:

~~~text
application container
image
command
args
resources
volumes
~~~

Semantic discovery:

~~~text
workload_family = vision-training
adapter = yolo
~~~

The original argv remains the source of truth for application execution.

## 2. Inspect the Job

~~~bash
python -m pre6g_experiment inspect   --job examples/yolo26/user-job.yaml
~~~

Expected structure:

~~~json
{
  "execution_contract": {
    "application_container": "trainer",
    "image": "...",
    "command": ["python3"],
    "args": ["train_yolo26.py", "..."]
  },
  "workload_spec": {
    "workload_family": "vision-training",
    "adapter": "yolo",
    "profileable": true,
    "parameters": {
      "model": "yolo26n.yaml",
      "epochs": 20,
      "batch_size": 16,
      "input_size": 640,
      "dataset_train_samples": 512
    },
    "work": {
      "status": "estimated",
      "unit": "training_iteration",
      "total_units": 640
    }
  }
}
~~~

The 640-unit estimate is valid only under the declared assumptions:

~~~text
steps_per_epoch = ceil(512 / 16) = 32
total_work_units = 20 * 32 = 640
~~~

Runtime discovery must later verify actual batch, dataloader length, world size, gradient accumulation, and sampler/drop-last behavior.

## 3. Phase 05 scope

Phase 05 does not attempt final runtime/energy prediction.

The first acceptance test is:

~~~text
generic Job inspection
  -> canonical workload spec
  -> immutable YOLO test image/config
  -> short real execution on RTX4090
  -> short real execution on RTX5090
~~~

PASS requires:

- same workload image/config on both nodes;
- application starts;
- dataset is accessible;
- CUDA is available;
- shared GPU resource works;
- training enters real iteration execution;
- requested and discovered workload semantics can be saved.

Nsight 120-second capture is not part of Phase 05.

## 4. Current YOLO fixture is not yet deployment-ready

The example image is intentionally still a placeholder:

~~~text
registry.example.edu/pre6g/yolo26-train@sha256:REPLACE_WITH_IMMUTABLE_DIGEST
~~~

The dataset PVC is also an example contract until the Phase 05 image/data distribution method is frozen.

Before real Kubernetes execution, freeze:

- x86_64 image;
- Python / PyTorch / CUDA / Ultralytics versions;
- training script revision;
- dataset semantic hash;
- workload configuration;
- immutable image identity.

The existing yolo26_runtime_prediction project is the reference workload implementation for this fixture, but Pre6G_experiment remains the formal platform repository.

## 5. Phase 06 short Nsight compatibility

After Phase 05 short execution passes, the controller/profile builder deep-copies the same application contract and wraps the original argv:

~~~text
/opt/pre6g/nsight/bin/nsys profile
  --trace=cuda,nvtx,osrt
  --sample=none
  --cpuctxsw=none
  --output=<artifact-path>/profile
  --
  <original command> <original args>
~~~

Run a bounded 5-15 second test on RTX4090 and RTX5090.

PASS requires:

- same workload behavior as the non-profiled Phase 05 run;
- profile.nsys-rep exists and is valid;
- target CUDA kernels are visible;
- Netdata and DCGM telemetry are available in the same time window;
- timestamp alignment gate passes.

## 6. Phase 07 formal 120-second dry-run

Only after Phase 06 passes:

~~~text
RTX4090 -> fixed 120 s target-process trace
RTX5090 -> fixed 120 s target-process trace
~~~

The application must be able to continue long enough to cover the fixed capture window. The platform does not change the semantic identity of a work unit merely to extend the test duration; a longer bounded benchmark configuration can be introduced as a dedicated platform fixture while preserving model/batch/input/precision settings.

## 7. Runtime and energy semantics

Generic runtime output:

~~~json
{
  "runtime": {
    "status": "ready",
    "work_unit": "training_iteration",
    "predicted_runtime_ms_per_work_unit": 42.1
  }
}
~~~

The workload spec and runtime result must use the same work_unit before total runtime is calculated.

For this fixture:

~~~text
T_total ~= runtime_per_training_iteration * total_training_iterations
~~~

The real RTX4090/RTX5090 power-model bundles are not yet available, so the current platform remains profile-only for real energy ranking.

## 8. Synthetic decision-path test

Synthetic node results can still exercise the post-model control path:

~~~bash
python -m pre6g_experiment decide   --job examples/yolo26/user-job.yaml   --results examples/yolo26/synthetic-node-results.json   --output generated/yolo26-production-job.yaml   --allow-synthetic
~~~

These values are not experimental results and must not be reported as measured model accuracy or energy savings.
