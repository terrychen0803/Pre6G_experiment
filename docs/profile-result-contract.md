# Kubernetes Profile Result Contract

This document defines the handoff between a GPU Profile Job and the Pre6G control-side runtime predictor.

The production architecture is Kubernetes-native. Manual `scp` was used only during component smoke validation and is not part of the formal workflow.

## Placement of responsibilities

~~~text
k3s control side
  Controller / Predictor
        |
        | creates candidate Profile Jobs
        |
        +-------------------+
        |                   |
        v                   v
   RTX4090 worker      RTX5090 worker
   Profile Job         Profile Job
        |                   |
   user dry-run          user dry-run
   Nsight 2026           Nsight 2026
   marker-free           marker-free
   runtime features      runtime features
        |                   |
        +--------+----------+
                 |
                 v
          shared artifact store
                 |
                 v
         control-side predictor
                 |
         frozen model inference
                 |
          quality / model gates
                 |
             ranking
~~~

The worker performs trace-heavy processing locally so the control side does not need to receive the complete Nsight SQLite database.

## Worker-side artifacts

Each candidate Profile Job writes the following artifacts under a task/node/attempt namespace:

~~~text
results/<task_id>/<node>/
  marker-free-discovery.json
  runtime-features.json
  profile-result.json
  telemetry/
  provenance/
~~~

Large local trace artifacts may remain in the worker-local or artifact-store trace area according to retention policy.

The minimum controller handoff is `profile-result.json`.

Schema:

~~~text
schemas/profile-result.schema.json
~~~

Runtime feature schema:

~~~text
schemas/runtime-features.schema.json
~~~

## Worker-side commands

After Nsight report export and marker-free period detection:

~~~bash
PYTHONPATH=src python scripts/build_runtime_features.py \
  --sqlite /artifacts/profile.sqlite \
  --detection-json /artifacts/marker-free-discovery.json \
  --output /artifacts/runtime-features.json \
  --node "$NODE_NAME" \
  --device-id "$DEVICE_ID" \
  --workload-id "$WORKLOAD_ID"

PYTHONPATH=src python scripts/package_profile_result.py \
  --task-id "$TASK_ID" \
  --node "$NODE_NAME" \
  --device-id "$DEVICE_ID" \
  --detection-json /artifacts/marker-free-discovery.json \
  --runtime-features-json /artifacts/runtime-features.json \
  --output /artifacts/profile-result.json
~~~

The worker-side feature artifact is intentionally small. It contains the selected marker-free period metadata and the ordered trace feature map required by the runtime model.

## Transport

Preferred production transport:

~~~text
shared-artifact-store
~~~

Examples include a RWX PVC, NFS-backed artifact path, MinIO, or S3-compatible object storage.

The repository does not currently hard-code one storage backend because the cluster storage contract has not yet been frozen.

Formal path template:

~~~text
results/<task_id>/<node>/profile-result.json
~~~

Manual SSH/SCP transfer is explicitly:

~~~text
smoke-test-only
~~~

It must not appear in the controller reconcile path.

## Control-side inference

The control side resolves a model by candidate device/model binding and runs:

~~~bash
PYTHONPATH=src python scripts/predict_runtime.py \
  --model models/runtime/RTX5090_yolo_trace_only_v1.json \
  --features results/<task_id>/<node>/runtime-features.json \
  --output results/<task_id>/<node>/runtime-prediction.json
~~~

Prediction schema:

~~~text
schemas/runtime-prediction.schema.json
~~~

The inference CLI never tunes an alpha and never fits a model. It only validates the model/feature binding, standardizes the ordered features with the frozen scaler, and evaluates the frozen ridge-log-runtime model.

## Current model status

The repository now includes:

~~~text
models/runtime/RTX5090_yolo_trace_only_v1.json
~~~

This is a deployment-smoke model derived from the existing RTX5090 YOLO clean/high-load reference dataset.

It is intentionally constrained:

~~~text
device_id       RTX5090
workload_family YOLO26 validation family
detector        yolo-v1
input           trace-only features
role            deployment-smoke
~~~

It is not a generic production model for arbitrary workloads and is not valid for RTX4090.

Until a model for a candidate node passes its binding and quality gates, that candidate remains profile-only for runtime-based automatic ranking.

## Controller gate

A candidate runtime prediction is eligible only if:

~~~text
Profile Job completed
marker-free detection accepted
detector confidence gate passed
target process/context identified
runtime feature schema passed
model device binding matched
model detector-profile binding matched
all required model features present
runtime prediction finite and positive
semantic binding passed for semantic total-runtime use
~~~

The future controller reconcile loop should wait for one ProfileResult per candidate node, run the corresponding frozen model on the control side, then continue to power prediction and ranking.
