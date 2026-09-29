# Node-bound power model registry

## Purpose

Pre6G currently assumes one energy/power prediction model per candidate node. The controller remains central; model selection is performed by a node-aware Power Adapter rather than by deploying one adapter service per node.

The required routing key is:

- Kubernetes node name.
- Physical GPU UUID.

The GPU model name is descriptive metadata only. It must not be used as the sole routing key because two nodes can contain the same GPU model but have different CPU, memory, cooling, power limits, background services, or model calibration.

## Architecture

~~~
profile result for node
        |
        v
Power Adapter
        |
        +--> Model Registry lookup(node, gpu_uuid)
                  |
                  +--> node-specific model bundle
                            |
                            v
                    predicted node power
                            |
                            v
                    common prediction contract
~~~

The ranking layer does not need to know whether individual nodes use different regressors, neural networks, scalers, or hyperparameters. Every adapter must normalize its output to the same contract before ranking.

## Registry layout

The repository stores manifests and routing metadata, not production model binaries:

~~~
models/power/
├── registry.yaml
└── manifests/
    ├── iccl-s3-251230.yaml
    └── mirc516-20250605.yaml
~~~

Model binaries, scalers, and other large artifacts should live in the configured artifact/model store and be referenced by immutable URI plus checksum.

Small research artifacts may be checked in under `models/power/bundles/` for
reproducibility before registration. Such a bundle must remain outside
`registry.yaml` and carry a non-ready status until its node/GPU binding, target
semantics, validation results, and deployment policy satisfy this contract.

## Manifest contract

A production-ready power model must declare at least:

~~~yaml
schema_version: pre6g.power-model-manifest/v1
status: ready
model_scope: node-bound
model_id: power-example-v1
model_version: 1.0.0

node_binding:
  kubernetes_node: worker-name
  gpu_uuid: GPU-...
  gpu_model: NVIDIA GeForce RTX ...

feature_schema_version: pre6g-energy-features/v2
required_features:
  - CPU User%
  - CPU System%
  - GPU Util%
  - GPU Power(W)

target:
  semantics: node-total-power
  unit: W

artifact:
  uri: s3://.../model.bin
  sha256: ...

preprocessing:
  manifest_uri: s3://.../preprocessing.json
  sha256: ...
~~~

Do not mark a model ready until the exact feature order, units, normalization/scaler state, missing-value policy, training domain, OOD policy, and output semantics are frozen.

## Binding gate

Before inference, the controller/adapter must verify:

~~~
candidate node name == manifest node_binding.kubernetes_node
candidate physical GPU UUID == manifest node_binding.gpu_uuid
~~~

A mismatch is fatal for automatic energy ranking and should produce a status such as model_binding_mismatch. Do not silently fall back to another node model, a model with the same GPU product name, or a constant prediction.

Replacing a GPU in the same server therefore invalidates the old node-bound model until the manifest/model is retrained or explicitly revalidated.

## Common target semantics

The first production contract requires all models participating in cross-node energy ranking to predict:

~~~
target.semantics = node-total-power
target.unit = W
~~~

Node-specific idle power is then removed after prediction:

The fixed dry-run/profile window is used to **estimate a representative workload power level or power behavior**, not as the ranking horizon itself. The diagnostic energy obtained by integrating power only over the profiling window must not be used as the candidate's scheduling energy.

For the current steady-state MVP, ranking composes the two independent predictions:

~~~text
T_predicted_steady
  = predicted_runtime_per_work_unit × total_work_units

P_predicted_incremental
  = max(0, P_predicted_node_steady - P_idle_node)

E_predicted_steady
  = P_predicted_incremental × T_predicted_steady
~~~

If a future power adapter predicts a time-varying power trajectory over semantic work units, that trajectory may be integrated over the **predicted workload runtime**. In either case, the 120-second profiling-window energy is validation/diagnostic evidence only.


~~~
P_incremental(t) = max(0, P_node_predicted(t) - P_idle_node)

E_incremental = integral(P_incremental(t), t)
~~~

This keeps nodes with different idle baselines comparable.

Do not mix node-total-power and task-incremental-power models in one automatic ranking operation. If future experiments introduce another target semantic, add an explicit normalization layer and version the ranking contract.

## Feature availability

The platform currently has a validated 16-feature core:

- CPU User%
- CPU System%
- CPU IOWait%
- Load 1min
- Load 5min
- Load 15min
- Mem Used(MB)
- Mem Free(MB)
- CPU Temp(°C)
- GPU Util%
- GPU Mem Used(MB)
- GPU Temp(°C)
- GPU Power(W)
- Top1 CPU%
- Top2 CPU%
- Top3 CPU%

Top1 GPU% and Top2 GPU% remain optional platform extensions because the current validated DCGM path is device-level telemetry.

A node-specific model may still require Top1 GPU% / Top2 GPU%. In that case the model must remain unavailable/schema_mismatch until a validated per-process GPU collector provides those fields. The platform must not substitute device-wide GPU Util%.

## Readiness states

Recommended model states:

- ready: binding, artifact, schema, preprocessing, target semantics, OOD policy, and required features all pass.
- unavailable: no production model bundle is installed for that node.
- schema_mismatch: model exists but the telemetry/extractor contract is incompatible.
- model_binding_mismatch: node or physical GPU UUID differs from the manifest.
- rejected_ood: the current observation is outside the model training domain.

Automatic ranking requires at least two comparable ready candidate nodes when the experiment intends to make a cross-node placement claim. With only one ready node, prediction may still be reported, but the system should not present it as a validated cross-node comparison.

## Prediction output

Each node result should include:

~~~json
{
  "power": {
    "status": "ready",
    "model_scope": "node-bound",
    "model_id": "power-worker-v1",
    "model_version": "1.0.0",
    "bound_node": "worker-name",
    "bound_gpu_uuid": "GPU-...",
    "feature_schema_version": "pre6g-energy-features/v2",
    "target_semantics": "node-total-power",
    "target_unit": "W",
    "steady_power_w": 350.0,
    "idle_power_w": 80.0,
    "confidence": 0.93,
    "ood": false,
    "missing_features": []
  }
}
~~~

The decision layer validates this contract before comparing energy across nodes.
