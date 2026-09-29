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

Both uploaded power bundles are now node-bound and their `ACTUAL_POWER_W` targets are confirmed by the model owner as external-meter whole-node wall power:

~~~text
RTX4090
  node        iccl-s3-251230
  GPU UUID    GPU-39ace77c-cb0f-dd47-ae6b-12014c25b1d1
  bundle      pdu1-outlet1-20260416-20260612
  target      node-total-power
  inputs      5 features

RTX5090
  node        mirc516-20250605
  GPU UUID    GPU-a4e6b1ee-8a31-991f-dc82-fdab833483c4
  bundle      pdu1-outlet7-20260416-20260612
  target      node-total-power
  inputs      7 features
~~~

The RTX5090 model consumes Netdata fields `Top1 CPU%`, `Top2 CPU%`, `Top3 CPU%`, `Mem Used(MB)`, `Mem Free(MB)`, `CPU User%` plus DCGM `GPU Power(W)`. The RTX4090 model consumes Netdata `CPU User%`, `CPU Temp(°C)` plus DCGM `GPU Mem Used(MB)`, `GPU Power(W)`, `GPU Temp(°C)`. The power adapter now routes all of these canonical features to the validated collector source.

The adapter still preserves `validation_required` rather than making either model ranking-eligible. Remaining blockers are:

~~~text
node idle power                 missing
held-out model metrics         not frozen in the manifest
missing-value policy           not frozen
formal OOD policy              not frozen
production manifest status     not ready
~~~

Power inference may proceed as an integration smoke from aligned Netdata/DCGM telemetry, but automatic runtime/energy placement must remain gated until those items are resolved.

### Telemetry evidence and synchronization boundary

Historical GitHub results confirm that telemetry has been exercised before, but the raw telemetry files are not stored in the shared artifact backend used by the current K3s Profile Job.

Repository evidence:

~~~text
Pre6G_result/analysis/unified_trace_model/samples.csv
  high_load_01 rows contain derived pre-run telemetry summaries
  clean rows contain no equivalent raw telemetry-derived values

Pre6G_experiment/docs/netdata-contract.md
  records the 2026-09-27 Netdata/DCGM alignment validation:
  coverage = 100%
  median absolute delta = 284.8 ms
  max delta = 490.5 ms
~~~

The current RTX4090/RTX5090 K3s runtime Profile Job evidence did not capture Netdata/DCGM telemetry in the same formal profile window. Therefore those runtime artifacts and the previous telemetry validation must not be treated as one synchronized experiment.

The deployment telemetry flow (validated first with a short integration dry-run, then reused unchanged for the formal 120-second Phase 07 capture) is:

~~~text
pre-window
   |
   +--> Netdata already collecting continuously
   +--> start DCGM polling
   +--> record absolute UTC Unix-ns wrapper timestamps
   |
application/profile start
   +--> Nsight target-process capture
   +--> Netdata continues
   +--> DCGM continues
   |
profile/application end
   |
post-window
   +--> stop DCGM polling
   +--> query Netdata for the same absolute time interval
   |
align Netdata <-> DCGM by timestamp
   |
crop/aggregate according to the frozen power-model contract
   |
power inference
~~~

Nsight CUDA-event timestamps may remain local to the Nsight report; they do not need to be numerically identical to Unix timestamps used by Netdata/DCGM. The wrapper provides the common wall-clock profile/application boundaries used to associate the telemetry window with the same dry-run.

The repository synchronization path is now:

~~~text
scripts/query_netdata_window.py
  queries Netdata Parent history after the run
  absolute after/before window from timestamps.json
  preserves Netdata database timestamps
  emits canonical CPU/system/Top-CPU telemetry

scripts/collect_dcgm.py
  actively polls the selected DCGM exporter during the run
  UTC Unix-ns request-midpoint timestamps
  1 s polling; bounded or signal-terminated

scripts/run_profile_with_telemetry.py
  records pre/profile/post wall-clock boundaries
  starts only the DCGM active poller
  runs the wrapped Nsight/workload command
  queries Netdata historical data after the run
  nearest-aligns Netdata/DCGM
  writes alignment-quality.json and aligned-telemetry.csv

scripts/collect_netdata.py
  retained only as a diagnostic/legacy live-polling utility
~~~

This supersedes the earlier worker-side Netdata live-polling implementation. The next live-cluster gate is validation of the historical Parent query against the same RTX5090 Profile Job window.

### RTX5090 DCGM collector regression smoke

A live 3-second collector smoke on node `mirc516-20250605` confirmed that the DCGM exporter endpoint and canonical collector now work together after fixing the CSV identity-field mismatch.

~~~text
endpoint       http://10.42.5.51:9400/metrics
node           mirc516-20250605
GPU UUID       GPU-a4e6b1ee-8a31-991f-dc82-fdab833483c4
samples        3
interval       1000 ms
collector_rc   0
~~~

All required metrics were present exactly once for the target GPU:

~~~text
DCGM_FI_DEV_GPU_UTIL
DCGM_FI_DEV_FB_USED
DCGM_FI_DEV_GPU_TEMP
DCGM_FI_DEV_POWER_USAGE
~~~

The emitted CSV contains the canonical fields `node`, `gpu_uuid`, `gpu_model`, `driver_version`, `gpu_index`, GPU utilization, framebuffer used, temperature, and power. The exporter `Hostname` label is now used to validate the requested node rather than being written as an undeclared CSV field.

### Unified RTX5090 telemetry alignment partial failure

A live unified run completed the YOLO workload and generated the Nsight report successfully, but the telemetry quality gate failed on one criterion:

~~~text
netdata_samples                 41
dcgm_samples                    42
aligned_samples                 38
alignment_coverage              0.9047619  PASS (>= 0.90)
median_alignment_delta_ms       40.64
max_alignment_delta_ms          541.22     PASS (<= 750 ms)
max_dcgm_gap_s                  1.0006     PASS (<= 2 s)
max_netdata_gap_s               3.4995     FAIL (> 2 s)
~~~

The workload command returned 0 and `profile.nsys-rep` was generated. The failure therefore belongs to the power/telemetry quality path, not the runtime trace path.

The wrapper now keeps this distinction explicit: telemetry alignment quality remains fail-closed for power/ranking, but a `pass=false` alignment no longer aborts the runtime trace pipeline unless `--require-alignment-pass` is requested. This prevents one telemetry gap from discarding otherwise valid Nsight runtime evidence while preserving the downstream decision gate.

### Unified RTX5090 Profile Job end-to-end completion

A subsequent live run of `pre6g-unified-yolo26-5090-mvp-v1` completed successfully (`1/1`, 51 s) and persisted the marker-free detector result, runtime features, packaged ProfileResult, raw Netdata/DCGM telemetry, aligned telemetry, quality report, logs, and profile-window timestamps to the shared RWX artifact store.

The marker-free detector accepted an `execution_cycle` with:

~~~text
detected_period_ms          111.62083475
confidence                  0.7399524734
complete_cycles             134
horizon_seconds             15
hardware_trace              true
two_window_stability_pass   true
NVTX required               false
~~~

The production-input policy confirms that the detector does not use `iterations.csv`, NVTX, callbacks, epoch labels, or batch labels.

Telemetry was preserved but remains ineligible for power/ranking because the quality gate failed:

~~~text
netdata_samples             38
dcgm_samples                39
aligned_samples             29
alignment_coverage          0.7435897   FAIL (< 0.90)
median_alignment_delta_ms   45.57
max_alignment_delta_ms      547.17      PASS (<= 750 ms)
max_netdata_gap_s           3.5016      FAIL (> 2 s)
max_dcgm_gap_s              1.0006      PASS (<= 2 s)
~~~

This run therefore validates the unified runtime-profile artifact path end to end while intentionally keeping the power/energy decision path fail-closed until Netdata sampling continuity is corrected.

### RTX5090 frozen runtime-model inference from unified ProfileResult

The frozen deployment-smoke model `RTX5090_yolo_trace_only_v1` was run against the runtime features generated by the successful unified Profile Job. The model consumed 7 trace-derived features and produced:

~~~text
predicted_runtime_ms      101.81161627254178 per execution_cycle
detected_period_ms        111.62083475
detector_confidence       0.7399524733549142
model_role                deployment-smoke
~~~

For the current synthetic YOLO training fixture (512 training images, batch 16, 4 epochs), static work discovery gives 128 training work units. Under the current YOLO-only semantic binding of one `execution_cycle` per training work unit, this corresponds to a predicted steady-state training runtime of approximately 13.0319 s. This is a steady-state aggregate only; it excludes startup, validation, checkpointing, teardown, and other non-steady whole-job overhead.

### Netdata allmetrics filtering A/B latency test

A live A/B test compared the RTX5090 Netdata Parent `allmetrics` endpoint with and without chart filtering. Filtering reduced the normal response latency and payload size substantially, but did not remove the intermittent approximately 5-second stall:

~~~text
FULL
charts median      2648
latency median     0.0310 s
latency max        5.0326 s
slow >2.0 s        1 / 15

FILTERED
charts median      142
latency median     0.0084 s
latency max        5.0125 s
slow >2.0 s        1 / 15
~~~

The filtered response still contained the required charts (`system.cpu`, `system.ram`, 79 app CPU charts, and 24 temperature charts). This indicates that response payload size contributes to normal request latency but is not the primary cause of the recurring ~5 s sampling hole. The next diagnostic should compare the Parent path with the node-local Netdata child from a host-networked pod before changing the telemetry quality thresholds.


### Telemetry architecture correction: Netdata historical query

The live RTX5090 runs exposed intermittent ~5 s latency in client-side `/allmetrics` polling. An A/B payload-size test reduced normal response latency but did not remove the ~5 s stall. This evidence is retained because it identified an implementation mismatch rather than a reason to relax the telemetry quality gate.

The formal path is now aligned with the original telemetry contract:

~~~text
Netdata Child -> Parent
  continuous 1 Hz monitoring/history
        |
Profile Job records absolute pre/profile/post boundaries
        |
Nsight + workload
DCGM active polling during the run
        |
post_window_end_ns
        |
query_netdata_window.py
  Parent historical /api/v1/data
  [pre_window_start_ns, post_window_end_ns]
        |
historical netdata.csv + dcgm.csv
        |
align_telemetry.py
~~~

Therefore the earlier `max_netdata_gap_s ~= 3.5 s` results from live `/allmetrics` polling are not treated as evidence that the Netdata database itself had the same sampling gap. The new historical-query path must be validated on-cluster before power/ranking is marked ready. The runtime trace result remains valid independently.


### Historical Netdata query unit-test checkpoint

On the k3s control-plane checkout at commit `9c18e17507ebfb8fe29190c66b5a3c0b1acb650a`, the new historical-query helper passed all four focused unit tests:

~~~text
test_historical_url_uses_absolute_window ... ok
test_parse_array_payload                    ... ok
test_parse_objectrows_payload               ... ok
test_top_cpu_requires_three_finite_series   ... ok
Ran 4 tests
OK
~~~

The next gate is a live replay against the already-completed RTX5090 unified run: use that run's `pre_window_start_ns` and `post_window_end_ns` to query Netdata Parent history, then align the returned historical `netdata.csv` with the preserved `dcgm.csv`. This directly tests whether the earlier live-polling gaps disappear without re-running the workload.


### RTX5090 historical Netdata replay validation PASS

The previously completed unified RTX5090 run was replayed against the Netdata Parent historical database using its preserved absolute `pre_window_start_ns` and `post_window_end_ns`. No workload rerun was required.

Historical query result:

~~~text
schema_version                    pre6g.netdata-historical-query/v1
samples                           38
first_timestamp_ns                1790672881000000000
last_timestamp_ns                 1790672918000000000
temperature_chart                 sensors.temperature_k10temp-pci-00c3_temp1_Tctl_input
app CPU charts discovered         79
app CPU charts queried            71
app CPU charts unavailable        8
query_mode                        historical
timestamp_source                  netdata-database
~~~

The unavailable app CPU charts were transient process-group charts and were skipped according to the fail-closed rule that still requires at least three usable historical app CPU series for Top1/Top2/Top3 CPU derivation.

Alignment against the preserved DCGM stream from the same run passed all current quality gates:

~~~text
netdata_samples                   38
dcgm_samples                      39
aligned_samples                   39
alignment_coverage                1.0000
median_alignment_delta_ms         416.918144
max_alignment_delta_ms            577.618817
max_netdata_gap_s                 1.0000
max_dcgm_gap_s                    1.000598916
pass                              true
~~~

For comparison, the superseded live `/allmetrics` polling path on this same dry-run window produced 74.36% alignment coverage and a 3.50 s maximum Netdata gap. The historical replay therefore confirms that the earlier gap was introduced by client-side live polling rather than by the Netdata database sampling cadence.

This validates the production telemetry architecture for RTX5090:

~~~text
Netdata Child/Parent continuous history
+ absolute dry-run timestamps
+ post-run Netdata historical query
+ in-run DCGM active polling
+ nearest-timestamp alignment
~~~

The next integration gate is to run the unified Profile Job with this historical-query implementation directly, so the shared artifacts are generated by the corrected path in one end-to-end execution.


### Corrected unified RTX5090 historical-telemetry E2E PASS

A fresh unified Profile Job using the corrected historical Netdata implementation completed successfully in 79 seconds and generated `pre6g.profile-wall-clock/v2` timestamps. The wrapper no longer performs profile-time Netdata `/allmetrics` polling.

This run is an **integration smoke for the telemetry/runtime pipeline, not the formal Phase 07 120-second profiling result**. Its `profile_start_ns` to `profile_end_ns` interval is about 33.4 s. The formal workflow keeps the same telemetry architecture but uses the configured 120-second Nsight capture policy before final artifact generation (unless the original workload naturally exits earlier, in which case the shorter actual capture is preserved and quality-gated).

Wall-clock boundaries:

~~~text
pre_window_start_ns    1790691679973178578
profile_start_ns       1790691684226052274
profile_end_ns         1790691717639312101
post_window_end_ns     1790691719639381777
application_*          null
steady_window_*        null
~~~

The null application/steady fields are intentional because the generic wrapper does not invent framework-specific boundaries.

The post-run Netdata Parent historical query produced 40 database-timestamped samples. It discovered 79 app CPU charts, queried 69 usable historical series, and skipped 10 transient/unavailable process-group charts while retaining enough data for Top1/Top2/Top3 CPU derivation.

The Netdata/DCGM alignment quality passed:

~~~text
netdata_samples                 40
dcgm_samples                    40
aligned_samples                 40
alignment_coverage              1.0000
median_alignment_delta_ms       5.8773065
max_alignment_delta_ms          12.54152
max_netdata_gap_s               1.0000
max_dcgm_gap_s                  1.000326783
pass                            true
~~~

This is the first complete unified Job validation of the intended production telemetry design:

~~~text
Netdata Child/Parent continuous history
+ absolute Profile Job timestamps
+ post-run historical Netdata query
+ in-run DCGM active polling
+ nearest-timestamp alignment
~~~

The earlier live-polling path is superseded for production Profile Jobs.


### RTX5090 formal Phase 07 120-second Profile Job E2E PASS

The first formal RTX5090 Phase 07 Job completed successfully:

~~~text
Job                                 pre6g-formal-yolo26-5090-120s-v2
Task                                yolo26-formal-5090-120s-002
Status                              Complete (1/1)
Kubernetes Job duration             4m9s
Configured Nsight duration          120 s
Nsight termination policy           --kill=sigterm --stop-on-exit=true
Trace                               cuda,nvtx,osrt
Formal workload fixture             YOLO, epochs=30
~~~

The wrapper records the outer Nsight/workload command boundary, not the exact
internal capture boundary. For this run:

~~~text
profile_start_ns                    1790694093333607841
profile_end_ns                      1790694235024921504
outer profile-command interval      141.691313663 s
~~~

The interval is longer than 120 s because it includes Nsight shutdown/report
finalization. It must not be interpreted as the capture duration itself.
Likewise, the marker-free detector's `capture_start_ns` / `capture_end_ns`
describe the selected detector prefix, not the full formal Nsight capture.

Telemetry alignment passed:

~~~text
netdata_samples                     148
dcgm_samples                        148
aligned_samples                     147
alignment_coverage                  0.9932432432
median_alignment_delta_ms           109.026266
max_alignment_delta_ms              109.500122
max_netdata_gap_s                   1.0
max_dcgm_gap_s                      1.000438756
pass                                true
~~~

Marker-free detection passed without production instrumentation inputs:

~~~text
accepted                            true
detected_unit                       execution_cycle
detector_profile                    yolo-v1
detected_period_ms                  133.61405375
confidence                          0.6711229308
harmonic_corrected                  true
horizon_seconds                     9
complete_cycles                     67
two_window_stability_pass           true
hardware_trace                      true
uses_iterations_csv                 false
uses_nvtx                           false
uses_callbacks                      false
uses_epoch_labels                   false
uses_batch_labels                   false
~~~

The packaged ProfileResult is:

~~~text
status                              ready-for-control-side-inference
device_id                           RTX5090
node                                mirc516-20250605
runtime feature count               7
kernel events in selected prefix    224064
unique kernels                      81
~~~

Small runtime/telemetry artifacts were persisted to the shared RWX store under:

~~~text
results/yolo26-formal-5090-120s-002/mirc516-20250605/
~~~

This validates the current formal Phase 07 pipeline end to end for the RTX5090
fixture. A remaining timestamp-contract improvement is to persist an explicit
formal capture boundary (or equivalent Nsight-derived capture duration) so
power-window cropping does not use the wider outer command interval.


### Formal 120-second aligned telemetry window

For the validated RTX5090 Phase 07 run, the configured capture window was
projected from the wrapper's outer profile start plus the explicitly configured
120-second Nsight duration:

~~~text
capture_start_ns                  1790694093333607841
configured_capture_end_ns         1790694213333607841
configured_capture_seconds        120.0
all aligned telemetry rows        147
rows inside configured window     120
first selected timestamp_ns       1790694094108950101
last selected timestamp_ns        1790694213109151685
~~~

The 120 selected rows are consistent with the approximately 1 Hz aligned
Netdata/DCGM cadence and cover essentially the complete configured capture
interval.

This crop is valid for this run because the formal profiler reached its
configured duration and terminated the target through the expected
`--kill=sigterm` path. It must not be generalized to a workload that naturally
exits before 120 seconds. The repository still needs an explicit persisted
configured-capture/deadline contract so downstream power processing does not
infer the window ad hoc from the wider outer Nsight command interval.


### RTX5090 formal 120-second power inference smoke

The node-bound RTX5090 ONNX power bundle was executed against the 120 aligned
telemetry rows cropped to the configured formal capture window.

Input/model binding:

~~~text
model_id                          pdu1-outlet7-rtx5090-20260416-20260612
bound_node                        mirc516-20250605
bound_gpu_uuid                    GPU-a4e6b1ee-8a31-991f-dc82-fdab833483c4
target_semantics                  node-total-power
target_unit                       W
formal telemetry rows             120
alignment quality                 PASS
~~~

The inference path completed, but the bundle remains fail-closed for automatic
ranking:

~~~text
status                            validation_required
ranking_eligible                  false
ood                               true
idle_power_w                      null
mean_predicted_power_w            462.1193374633789
time_weighted_mean_power_w        462.28196331549725
min_predicted_power_w             407.21124267578125
max_predicted_power_w             510.050048828125
observed_window_s                 119.000201584
observed_window_energy_j          55011.64682319146
~~~

The current OOD messages are caused by GPU Power(W) values above the recorded
training maximum of 414.48 W; 46 formal-window rows triggered this range check,
with observed examples reaching 454.784 W. Inference was intentionally run
without `--reject-ood`, so these values were extrapolated rather than clipped.

Current blockers remain:

~~~text
bundle manifest status is not ready
node idle power is missing
telemetry contains out-of-domain feature values
~~~

Therefore this result validates the power-inference integration path only. It
must not yet be used for automatic energy ranking or treated as validated power
prediction accuracy. The next validation step is to summarize every required
feature against the bundle's recorded training range and quantify the OOD
extent before deciding whether the model/data coverage must be extended.


### RTX5090 formal power-feature OOD audit

The seven required power-model inputs from the 120-row formal telemetry window
were compared against the scaler's recorded training ranges:

~~~text
FEATURE             TRAIN_MIN   TRAIN_MAX    OBS_MIN   OBS_MEAN   OBS_MAX   OOD_ROWS
Top2 CPU%               0.800     240.000     53.993    102.654   116.004          0
Top1 CPU%               0.900    1595.000    197.973    209.759   338.009          0
Top3 CPU%               0.400     240.000      3.002     36.739   100.007          0
Mem Used(MB)         2506.000   54414.000  30014.600  33096.761 33407.720          0
Mem Free(MB)          794.000  123746.000   1566.711   2684.751  6800.875          0
CPU User%               0.000      51.300      7.724      9.962    13.905          0
GPU Power(W)            0.000     414.480    283.902    399.903   454.784         46
~~~

Only `GPU Power(W)` is outside the recorded training range. 46/120 rows
(38.33%) exceed 414.48 W; the observed maximum is 454.784 W, 40.304 W
(+9.72%) above that upper bound. No clipping is applied. This indicates a
power-model training-coverage/OOD issue for the current high-power RTX5090
regime rather than a telemetry schema mismatch in the other six inputs.

Automatic ranking remains disabled until the bundle's validation/OOD policy and
idle-power baseline are resolved. The 120-second profiling-window energy is
diagnostic only; scheduling energy must combine predicted workload runtime with
a comparable predicted incremental power contract.


### RTX5090 formal runtime inference

The frozen RTX5090 trace-only runtime model was run on the runtime features
emitted by the formal 120-second Profile Job:

~~~text
model_id                         RTX5090_yolo_trace_only_v1
model_role                       deployment-smoke
detected_unit                    execution_cycle
predicted_runtime_ms             128.7727655060272
detected_period_ms               133.61405375
detector_confidence              0.6711229307546983
feature_count                    7
~~~

The predicted runtime is the frozen-model estimate per `execution_cycle`.
The detector period is an observed marker-free trace quantity and must not be
used as prediction ground truth or substituted for the model output.

This result is not yet a whole-job runtime. It must first be combined with
static total-work discovery and the validated YOLO/yolo-v1 semantic binding.
The current model remains a deployment-smoke model rather than a held-out
production-accuracy claim.
