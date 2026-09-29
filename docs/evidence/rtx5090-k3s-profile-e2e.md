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

## Control-side runtime inference

The exact Kubernetes-produced `runtime-features.json` was consumed on `icclz2` with the frozen deployment-smoke model:

~~~text
model                  RTX5090_yolo_trace_only_v1
device_id              RTX5090
detected_unit          execution_cycle
feature_count          7
predicted_runtime_ms   115.60792921841606
model_role             deployment-smoke
~~~

Schema, model ID, device binding, detector profile, feature count and finite-positive prediction checks all passed.

The shared artifact store now also contains:

~~~text
runtime-prediction.json
~~~

The first host-side write required an administrative install because the current NFS result directory was created through root-squashed Pod access and is owned by `nobody:nogroup`. This is a control-process identity/permissions integration issue, not a runtime-model inference failure; the formal controller should mount the RWX PVC and write with a compatible Pod identity.

## Automatic total-work discovery smoke

A separate Kubernetes work-discovery Job used only the original workload arguments and the workload-visible mounted dataset.

Observed sources:

~~~text
epochs       original Job argv
batch_size   original Job argv
dataset      mounted dataset
~~~

Observed result:

~~~text
epochs                  4
batch_size              16
training samples        512
steps_per_epoch         32
total work units        128
work unit               training_iteration
Job                     Complete (1/1)
duration                15 s
~~~

Production input policy:

~~~text
uses_iterations_csv     false
uses_nvtx               false
uses_callbacks          false
uses_epoch_timestamps   false
uses_batch_timestamps   false
~~~

Therefore the value 128 was not obtained from training-step positions or hidden instrumentation. It was derived from static workload semantics plus mounted-dataset cardinality:

~~~text
ceil(512 / 16) * 4 = 128
~~~

## Semantic binding and steady runtime

The validated YOLO/yolo-v1 binding is currently fail-closed:

~~~text
execution_cycle -> training_iteration
cycles_per_work_unit = 1
source = validated-yolo-v1-offline-evidence
~~~

For this smoke:

~~~text
predicted runtime / execution_cycle   115.60792921841606 ms
predicted runtime / training_iteration 115.60792921841606 ms
total work units                       128
predicted steady runtime               14.797814939957256 s
~~~

This value is explicitly a steady-work extrapolation, not yet a whole-job runtime. The current semantic runtime contract keeps:

~~~text
predicted_total_job_runtime_s = null
total_job_runtime_status = pending-non-steady-overhead-model
~~~

until startup, warmup, validation, checkpoint and finalization overhead are modeled separately.

## Current integration boundary

Completed:

~~~text
shared NFS/RWX artifact backend        PASS
cross-node Kubernetes RWX transport    PASS
RTX5090 profile environment preflight  PASS
RTX5090 Nsight Profile Job             PASS
marker-free detector                   PASS
runtime feature extraction             PASS
ProfileResult packaging                PASS
control-side frozen runtime inference  PASS
automatic mounted-dataset work discovery PASS
YOLO/yolo-v1 semantic binding smoke    PASS
steady runtime aggregation             PASS
~~~

Repository implementation now includes:

~~~text
src/pre6g_experiment/work_discovery.py
src/pre6g_experiment/semantic_binding.py
src/pre6g_experiment/runtime_aggregation.py
scripts/discover_work.py
scripts/aggregate_runtime.py
schemas/work-discovery.schema.json
schemas/semantic-binding.schema.json
schemas/semantic-runtime.schema.json
~~~

Formal repository validation on `icclz2` after updating to commit `88946cae30cf0694e0630c9a6740c280b8ca436b`:

~~~text
python -m unittest discover -s tests -v
Ran 25 tests
OK
~~~

The production-oriented aggregation CLI was then rerun against the actual Kubernetes-generated artifacts rather than the earlier inline Python smoke:

~~~text
scripts/aggregate_runtime.py
  runtime-prediction.json
  + workload-discovery.json
  -> semantic-runtime.json
~~~

Observed semantic runtime:

~~~text
work_unit                           training_iteration
total_work_units                    128
runtime_ms_per_execution_cycle      115.60792921841606
runtime_ms_per_work_unit            115.60792921841606
predicted_steady_runtime_s          14.797814939957256
predicted_total_job_runtime_s       null
total_job_runtime_status            pending-non-steady-overhead-model
aggregation_scope                   steady-work-only
~~~

The resulting `semantic-runtime.json` was persisted in the shared artifact store alongside the existing marker-free/runtime artifacts.

Not yet completed:

~~~text
whole-job non-steady overhead model
RTX4090 equivalent Profile Job
automatic controller create/wait/read/infer reconcile loop
real RTX4090 runtime model
real RTX4090/RTX5090 power prediction
runtime + power energy ranking
automatic production Job placement
formal fixed 120-second profiling
~~~

The RTX4090 equivalent worker path has now also completed. Whole-job non-steady composition remains deferred while the MVP treats steady training time as the runtime target.

## RTX4090 equivalent Profile Job

The same frozen worker implementation and workload fixture were replayed on node `iccl-s3-251230` with only the candidate-specific node/device binding changed.

~~~text
Job                               pre6g-profile-yolo26-4090-mvp-v2
node                              iccl-s3-251230
device_id                         RTX4090
Kubernetes Job                    Complete (1/1)
duration                          44 s
workload                          YOLO26 synthetic
train / val samples               512 / 64
epochs / batch / imgsz            4 / 16 / 320
worker implementation commit       be379cb840826b772e6eb7eab1af6b41429fd969
shared PVC                        pre6g-artifacts
~~~

Marker-free detector output:

~~~text
accepted                           true
detected_unit                      execution_cycle
detector_profile                   yolo-v1
detected_period_ms                 95.05752175
confidence                         0.9291919456
complete_cycles                    94
horizon_seconds                    9
selection_reason                   two-window stability
previous_horizon_seconds           7
previous_period_ms                 93.839766
harmonic_corrected                 false
target_context_id                  1
~~~

Production input policy remained marker-free:

~~~text
uses_iterations_csv                false
uses_nvtx                          false
uses_callbacks                     false
uses_epoch_labels                  false
uses_batch_labels                  false
~~~

The emitted ProfileResult is `ready-for-control-side-inference` and the shared artifact store contains:

~~~text
results/yolo26-e2e-5090-smoke-002/iccl-s3-251230/
  marker-free-discovery.json
  runtime-features.json
  profile-result.json
~~~

The detector artifact reports `hardware_trace=false`. In the current implementation this flag only reflects whether a specific Nsight diagnostic string (`Hardware tracing used for CUDA tracing`) appears in `DIAGNOSTIC_EVENT`; it is not a required detector gate. The required CUDA kernel table, target process/context, stable recurring period, confidence, and complete-cycle gates all passed.

The next multi-node runtime gate is a **device-matched RTX4090 frozen runtime model**. The existing RTX5090 bundle must not be reused on RTX4090.

### Historical RTX4090 trace compatibility smoke

The retained RTX4090 C03 `nsys_trace_01/profile.nsys-rep` was exported successfully with Nsight Systems 2026.4.1 and reprocessed by the current `yolo-v1` marker-free detector.

~~~text
baseline median target              34.53236780555556 ms
detected_period_ms                  48.77648
confidence                          0.9836988713
complete_cycles                     143
horizon_seconds                     7
selection_reason                    latest accepted fallback
two_window_stability_pass           false
harmonic_corrected                  false
~~~

The current seven-feature runtime schema was emitted successfully and all forbidden production inputs remained false. This establishes **schema/data compatibility** for historical RTX4090 traces, but the C03 historical trace does not satisfy the stricter two-window-stability gate because only one deployment horizon was ultimately selected. The historical batch builder therefore records this quality field explicitly rather than silently treating it as equivalent to the current K3s Profile Job evidence.

`scripts/build_historical_runtime_samples.py` batch-reprocesses C01-C24 one workload at a time, deletes temporary SQLite exports by default, preserves marker-free detection/runtime-feature artifacts, joins only the baseline `steady_window_mean_iter_ms` median as a training label, and writes quality/failure summaries for model-development review.

### RTX4090 trace-only deployment-smoke model

Historical batch rebuilding completed for all 24 YOLO workloads:

~~~text
samples                         24 / 24
detector accepted               24 / 24
two-window-stable samples       15
accepted fallback samples        9
failures                         0
~~~

A seven-feature ridge log-runtime model was fitted with leave-one-workload-out alpha selection over the fixed candidate set `[0.01, 0.1, 1.0, 10.0, 100.0]`.

~~~text
model_id                         RTX4090_yolo_trace_only_v1
selected alpha                   1.0
LOOW MAPE                        4.6175495953 %
median APE                       4.2598542698 %
P90 APE                          8.9385418504 %
max APE                         14.0269897896 %
MAE                              2.0414712063 ms
RMSE                             2.4141930808 ms
bias                            -0.0754359672 ms
~~~

The bundle is stored at:

~~~text
models/runtime/RTX4090_yolo_trace_only_v1.json
~~~

This remains a `deployment-smoke` model. Its training set is historical clean-condition RTX4090 data and includes nine accepted detector fallback samples; current K3s features therefore require OOD/quality interpretation before any production-quality ranking claim.

### Deferred RTX4090 high-load validation

The current RTX4090 runtime bundle is trained from historical clean-condition traces only. This is sufficient for the current end-to-end deployment smoke, but it does **not** validate runtime prediction under background GPU/CPU/memory contention.

Before claiming production-quality current-load placement on RTX4090, collect a paired high-load dataset using the same C01-C24 workload grid and current marker-free `yolo-v1` feature path, then evaluate at least:

~~~text
clean -> clean
clean -> high-load
mixed clean/high-load grouped LOOW
high-load held-out workloads
OOD behavior for current K3s Profile Job features
~~~

This work is intentionally deferred until the full runtime + power + placement workflow is integrated.

## Uploaded power-model integration

The uploaded bundle `models/power/bundles/pdu1-outlet1-20260416-20260612` is now wired to an aligned Netdata/DCGM inference smoke path.

Its frozen scaler requires exactly five simultaneous features:

~~~text
Netdata:
  CPU User%
  CPU Temp(°C)

DCGM:
  GPU Mem Used(MB)
  GPU Power(W)
  GPU Temp(°C)
~~~

The intended preprocessing path is:

~~~text
Netdata time series
        +
DCGM time series
        |
        v
scripts/align_telemetry.py
        |
        v
aligned telemetry rows
        |
        v
scripts/predict_power_from_aligned_telemetry.py
        |
        +--> predicted-power-series
        |
        +--> power-prediction-smoke summary
~~~

The adapter preserves the uploaded model's current `validation_required` status and does not make it ranking-eligible yet. Current blocking metadata are:

~~~text
Kubernetes node binding         missing
physical GPU UUID binding       missing
target semantics verification   missing
node idle power                 missing
production manifest status      not ready
~~~

The bundle target field is `ACTUAL_POWER_W`; its current manifest only infers `pdu-outlet-power` from artifact provenance. Automatic runtime/energy ranking remains blocked until the model owner confirms the physical node/outlet mapping and that the target is comparable node-total wall power.

The power adapter deliberately does not multiply profiled-window power by predicted training time while these gates are unresolved.
