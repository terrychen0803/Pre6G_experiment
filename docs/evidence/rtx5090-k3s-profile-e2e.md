# RTX5090 Kubernetes Profile Job E2E evidence

本文件保存 2026-09-28 在實際 k3s cluster 上完成的 RTX5090 Profile Job integration smoke。目的不是宣稱 arbitrary-workload 或 production scheduling 已完成，而是確認：

~~~text
Kubernetes Profile Job
  -> Nsight Systems 2026 target-process trace
  -> local SQLite export
  -> marker-free execution-cycle detection
  -> runtime feature extraction
  -> ProfileResult packaging
  -> NFS-backed RWX PVC
  -> control-side visible artifact
~~~

可以在不使用人工 SCP、iterations.csv 或 production NVTX iteration labels 的情況下跑通。

## Cluster artifact transport

本輪已將 current-cluster shared artifact backend 固定為：

~~~text
control-plane NFS export
  /srv/pre6g-artifacts
        |
        v
static PersistentVolume
  pre6g-artifacts-nfs
  ReadWriteMany
  ReclaimPolicy=Retain
        |
        v
PersistentVolumeClaim
  experiments/pre6g-artifacts
  100Gi
  ReadWriteMany
~~~

已完成跨 node smoke：

~~~text
RTX5090 Pod -> RWX PVC write       PASS
RTX4090 Pod -> RTX5090 file read   PASS
RTX4090 Pod -> RWX PVC write       PASS
control side -> both files read    PASS
~~~

NFS-backed RWX PVC 是 current deployment backend；repository-level transport abstraction 仍保持 `shared-artifact-store`，未把 generic contract 限制成只能使用 NFS。

## RTX5090 profile environment preflight

Profile preflight 固定執行 repository commit：

~~~text
be379cb840826b772e6eb7eab1af6b41429fd969
~~~

實測：

~~~text
node                         mirc516-20250605
GPU                          NVIDIA GeForce RTX 5090
driver                       580.173.02
CUDA reported by nvidia-smi  13.0
Nsight Systems               2026.4.1.191-264138605071v0
Python                       3.12.3
Pre6G module imports         PASS
Pre6G script CLI imports     PASS
RWX PVC write/readback       PASS
~~~

Repository source is fetched into a Pod-lifetime `emptyDir` only for this integration MVP. A later production implementation should bake the frozen implementation into an immutable profiler image.

## DiskPressure integration note

The first preflight attempt was evicted before the application preflight ran because the RTX5090 node crossed its 50 GiB kubelet `nodefs.available` / `imagefs.available` hard-eviction threshold while pulling the approximately 4.6 GB Ultralytics image.

For this short integration smoke only, the RTX5090 node hard threshold was temporarily changed to:

~~~yaml
evictionHard:
  imagefs.available: "10Gi"
  nodefs.available: "10Gi"
~~~

Live kubelet `configz` confirmed the override. This is an operational smoke-test setting, not a production recommendation; root filesystem capacity cleanup remains required before long profiling runs.

## Short workload fail-closed check

The first real Profile Job intentionally reused the older tiny synthetic fixture:

~~~text
train images  8
val images    4
epochs        2
batch         4
~~~

Nsight and SQLite export succeeded, but target CUDA trace span was only:

~~~text
4.922 s
~~~

The detector correctly stopped with:

~~~text
trace is shorter than the minimum detector horizon
~~~

No runtime feature or ProfileResult artifact was fabricated. This confirms the minimum-horizon gate fails closed.

## Successful RTX5090 Profile Job

The second integration fixture was enlarged only to provide enough execution cycles:

~~~text
train images  512
val images    64
epochs        4
batch         16
imgsz         320
amp           false
workers       0
~~~

The application remained ordinary Ultralytics YOLO26 training under Nsight:

~~~text
image           ultralytics/ultralytics:8.4.104
GPU resource    nvidia.com/gpu.shared: 1
RuntimeClass    nvidia
target node     mirc516-20250605
~~~

Job result:

~~~text
Kubernetes Job  Complete
completions     1/1
duration        61 s
~~~

Worker-local heavy trace artifacts:

~~~text
profile.nsys-rep  35 MB
profile.sqlite    104 MB
~~~

These remained in Pod-local `emptyDir` and were not copied into the shared controller handoff path.

## Marker-free detector result

The production-style `yolo-v1` detector accepted the trace:

~~~text
status                    detected
detected_unit             execution_cycle
detected_period_ms        126.8548825
confidence                0.7304067523
complete_cycles           118
horizon_seconds           15
selection_reason          two-window stability
two_window_stability      PASS
previous_horizon_seconds  12
previous_period_ms        129.8957505
harmonic_corrected        false
target_context_id         1
hardware_trace            true
~~~

Production input policy in the emitted artifact:

~~~text
uses_iterations_csv  false
uses_nvtx            false
uses_callbacks       false
uses_epoch_labels    false
uses_batch_labels    false
~~~

NVTX existed in the captured application trace but was not required or consumed by the production detector.

## Runtime feature artifact

The worker emitted schema:

~~~text
pre6g.runtime-features/v1
~~~

Ordered feature vector:

~~~text
log_detected_period_ms            4.843043775647481
log_kernel_event_rate_hz         10.249226246429718
log_unique_kernel_count           4.394449154672439
log_median_kernel_duration_us    -0.14618251017808145
log_mean_kernel_duration_us       0.9888381530456949
log_kernel_busy_fraction         -2.5774461584888613
anchor_robust_cv                  0.2621598531248087
~~~

Trace summary:

~~~text
kernel_event_count          423910
kernel_event_rate_hz        28260.6667
unique_kernel_count         81
median_kernel_duration_us   0.864
mean_kernel_duration_us     2.6881095
kernel_busy_fraction        0.07596777
~~~

## ProfileResult handoff

Task:

~~~text
yolo26-e2e-5090-smoke-002
~~~

Shared artifact path:

~~~text
results/yolo26-e2e-5090-smoke-002/mirc516-20250605/
  marker-free-discovery.json
  runtime-features.json
  profile-result.json
~~~

Observed sizes:

~~~text
marker-free-discovery.json  1268 bytes
runtime-features.json       1657 bytes
profile-result.json         2423 bytes
~~~

Packaged ProfileResult:

~~~text
schema_version  pre6g.profile-result/v1
device_id       RTX5090
status          ready-for-control-side-inference
transport       shared-artifact-store
scp             smoke-test-only
~~~

This validates the intended separation:

~~~text
worker local
  large .nsys-rep / SQLite
       |
       v
worker-side marker-free analysis
       |
       v
small JSON artifacts
       |
       v
NFS-backed RWX PVC
       |
       v
control side
~~~

## Current integration boundary

Completed:

~~~text
shared NFS/RWX artifact backend       PASS
cross-node Kubernetes RWX transport  PASS
RTX5090 profile environment preflight PASS
RTX5090 Nsight Profile Job           PASS
marker-free detector                 PASS
runtime feature extraction           PASS
ProfileResult packaging              PASS
control-side visibility              PASS
~~~

Not yet completed by this smoke:

~~~text
control-side frozen runtime inference on this new Kubernetes artifact
RTX4090 equivalent Profile Job
automatic controller create/wait/read/infer reconcile loop
real RTX4090 runtime model
real RTX4090/RTX5090 power prediction
runtime + power energy ranking
automatic production Job placement
formal fixed 120-second profiling
~~~

The immediate next integration step is to consume this exact `runtime-features.json` on the control side with the frozen RTX5090 deployment-smoke runtime model and emit `runtime-prediction.json`, then repeat the worker path on RTX4090.
