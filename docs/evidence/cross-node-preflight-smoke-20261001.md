# Cross-node preflight and smoke evidence

Date: 2026-10-01 (Asia/Taipei)

This evidence records the read-only Kubernetes preflight and short, non-training smoke test reported from the master host. It covers RTX4090 and RTX5090 only; RTX3090 remains excluded.

## Cluster preflight

| Check | Result |
| --- | --- |
| `experiments` namespace | Active |
| `nvidia` RuntimeClass | Present |
| `experiments/pre6g-artifacts` PVC | Bound, 100 GiB, RWX |
| `pre6g-artifacts-nfs` PV | Bound, Retain, NFS export path `/srv/pre6g-artifacts` |
| RTX4090 node `iccl-s3-251230` | Ready, no DiskPressure, `nvidia.com/gpu.shared: 4` |
| RTX5090 node `mirc516-20250605` | Ready, no DiskPressure, `nvidia.com/gpu.shared: 4` |
| DCGM exporter | Ready Pods were listed on both candidate nodes |
| Integrated `--preflight-only` | Passed; no cluster resource was created |

The NFS server address and Pod IPs are omitted from this public record.

## Smoke test

The integrated `--smoke-only` command completed with `[smoke passed]`. Its per-node probes check GPU UUID visibility, the mounted Nsight CLI version, GitHub reachability, the required DCGM metrics for the candidate GPU UUID, the Netdata host metrics endpoint, and cross-node reads of small marker files on the shared PVC.

Master-side follow-up confirmed both marker files under `smoke/yolo26-smoke-001/`. A label query returned no remaining smoke Pods in the `experiments` namespace.

## 120-second dry-run follow-up and collector incident

A subsequent integrated `--execute` run (`yolo26-dryrun-001`) completed both candidate profiling Jobs successfully. The master-side NFS export contained the expected ProfileResult, runtime features, discovery file, aligned telemetry, alignment-quality file, application-window file, and raw DCGM/Netdata telemetry for both `iccl-s3-251230` and `mirc516-20250605`.

The pipeline then stopped in the collect phase because the collector Pod had no scheduling constraint and Kubernetes assigned it to `gx10-c206`, which is not part of the 4090/5090 candidate workflow. That Pod failed to mount `pre6g-artifacts-nfs` and never became Ready. This did **not** invalidate the completed profiling artifacts already stored on NFS, but master-side runtime/power prediction and ranking were not reached.

The workflow now requires `collector_node` and the example pins it to master node `icclz2`. The collector Pod uses a hostname `nodeSelector`; preflight also checks that the master/collector node exists, is Ready, has no DiskPressure, and has no blocking `NoSchedule`/`NoExecute` taint while the collector has no tolerations. The master must be able to mount/read the same RWX NFS PVC. GX10 does not need to be added to the workflow or repaired solely for artifact collection.

Existing failed run IDs are still not a resume mechanism: preserve `/srv/pre6g-artifacts/results/<run-id>/` for audit/recovery, and use a new run ID for a fresh integrated execution unless an explicit recovery path is added later.

## Software tests and remaining scope

- GitHub Actions unit tests passed for commit `c08ff9669e762d1a51e9cd58b10ae6b0192db6a2`: [workflow run](https://github.com/terrychen0803/Pre6G_experiment/actions/runs/36768682898).
- The later `yolo26-dryrun-001` run established successful 120-second profiling artifact production on both candidate nodes, but collection failed before master-side runtime/power prediction and ranking.
- No 30–50 minute training run or PDU comparison was performed.

The evidence therefore supports worker-side profiling and shared-NFS artifact production, plus the earlier smoke checks; it does not yet establish successful end-to-end collection/prediction/ranking, model prediction quality, or full training results.


## Functional/integration run `yolo26-dryrun-002`

The next functional run used the master-pinned collector configuration. Integrated preflight passed without creating Kubernetes resources. Both RTX4090 and RTX5090 profiling Jobs completed, and the collector successfully returned all six required artifacts per node to the master-side output directory: `profile-result.json`, `runtime-features.json`, `marker-free-discovery.json`, `telemetry/aligned-telemetry.csv`, `telemetry/alignment-quality.json`, and `telemetry/application-window.json`.

The first prediction attempt then exposed a separate control-side packaging gap: ONNX Runtime was not installed in the master virtual environment. After installing `requirements-power-model.txt`, ONNX Runtime 1.23.2 reported `CPUExecutionProvider`, and prediction/ranking resumed from the already collected artifacts without re-running GPU profiling.

| Node | Device | Predicted runtime / training iteration | Predicted node steady power | Predicted steady gross energy |
| --- | --- | ---: | ---: | ---: |
| `iccl-s3-251230` | RTX4090 | 46.1338 ms | 207.993 W | 9.212 kJ |
| `mirc516-20250605` | RTX5090 | 47.2247 ms | 281.839 W | 12.777 kJ |

The ranking selected RTX4090 under the current research-provisional steady-energy objective. Both power outputs remain `validation_required`; this is not a production or formal model-accuracy result.

This run establishes that the individual functional capabilities work through profiling → shared NFS → master collector → runtime inference → power inference → ranking. It does **not** yet satisfy the clean unattended end-to-end merge gate because the initial `--execute` invocation stopped for the missing ONNX Runtime dependency and prediction was resumed manually.

The fixture is explicitly classified as `functional-validation`: 512 training samples, batch 16, 30 epochs, 960 training iterations. At the current per-iteration predictions this represents only about 44–45 seconds of predicted steady training compute, not the intended future 30–50 minute formal workload.

That remaining live merge gate was subsequently satisfied by `yolo26-functional-003`, described below.


## Clean unattended functional run `yolo26-functional-003`

A new run was started after the functional-validation annotations and control-side ONNX dependency preflight were added. The integrated workflow completed without manual package installation, private-function resume, or artifact copying.

`cross-node-run-summary.json` reported:

- `status: completed`
- start: `2026-09-30T21:14:37.875332+00:00`
- finish: `2026-09-30T21:19:21.164944+00:00`
- selected node: `iccl-s3-251230` / RTX4090

Both nodes produced all six required master-side artifacts: `profile-result.json`, `runtime-features.json`, `marker-free-discovery.json`, `telemetry/aligned-telemetry.csv`, `telemetry/alignment-quality.json`, and `telemetry/application-window.json`.

The resulting research-provisional ranking was:

| Node | Device | Runtime / training iteration | Predicted steady runtime for 960 units | Predicted node steady power | Predicted steady gross energy |
| --- | --- | ---: | ---: | ---: | ---: |
| `iccl-s3-251230` | RTX4090 | 45.7759 ms | 43.9448 s | 208.495 W | 9.162 kJ |
| `mirc516-20250605` | RTX5090 | 47.0321 ms | 45.1508 s | 281.777 W | 12.722 kJ |

The provisional steady-energy objective selected RTX4090, with 27.9835% lower predicted steady gross energy than the runner-up under the current model outputs. Both power outputs remain `validation_required`, `production_ready` remains false, and these 960-unit values are functional/integration evidence only—not formal 30–50 minute runtime or energy accuracy results.

This run satisfies the functional merge gate: one clean `--execute` completed profiling → shared NFS → master collector → runtime inference → power inference → ranking with no manual recovery. The functional/integration phase can therefore be considered complete.
