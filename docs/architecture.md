# 系統架構

## 核心原則

Pre6G_experiment 對使用者 Job 採三層處理：

~~~text
Execution Contract
  image / command / args / env / resources / volumes

Static Semantic Contract
  workload family
  canonical parameters
  candidate work unit
  total work units

Marker-Free Runtime Discovery
  target-process CUDA trace
  recurring execution cycle
  period / confidence / stability
~~~

Execution Contract 決定「如何執行」；Static Semantic Contract 決定「平台對 workload 的語意理解」；Marker-Free Runtime Discovery 決定「實際執行呈現什麼週期行為」。

正式 production path 不要求修改使用者程式碼。

## Production 禁止依賴

下列資料可用於研究 fixture validation，但不能成為 production detector input：

~~~text
iterations.csv
NVTX iteration markers
training callbacks
source-code instrumentation
user-added timing markers
framework-specific marker injection
~~~

## 元件

| 元件 | 執行位置 | 責任 |
|---|---|---|
| Experiment API / Controller | k3s server | 接收 Job、驗證 execution contract、找候選節點、建立 Profile Job、等待結果、選點、建立 production Job |
| Static Semantic Layer | controller | explicit metadata / known argv / config / dataset metadata → canonical workload spec |
| Workload Adapter Registry | controller | 針對已知 framework 將既有 Job/config 轉成 canonical semantics；不修改 application code |
| Profile Job Builder | controller | deep-copy source Job，保留 application contract，只加入 Nsight wrapper、node pin、artifact 與 dry-run policy |
| Marker-Free Trace Extractor | collector / central service | Nsight SQLite → target-process CUDA event sequence |
| Period Detector | collector / central service | recurring CUDA pattern → execution_cycle period / confidence / stability |
| Semantic Binding Layer | controller / runtime adapter | execution_cycle ↔ declared/adapter work_unit；無法驗證時保持 unbound |
| Netdata child | 每個 node | system/CPU/memory/temp/process CPU time series |
| Netdata Parent | central monitoring | per-host historical system telemetry |
| DCGM Exporter | 每個 NVIDIA node | GPU utilization、framebuffer、temperature、power |
| Runtime adapter | central service | marker-free trace features + semantic binding → runtime per work unit / confidence / OOD |
| Power model registry/router | central service | node + physical GPU UUID → node-bound power model |
| Power adapter | central service | canonical telemetry → node-total power |
| Decision layer | controller | 驗證 semantic/model/telemetry gates，計算 energy 並排序 |
| Artifact store | MinIO/S3/NFS | source/profile Job、trace、marker-free features、telemetry、semantic spec、predictions、ground truth |

## 正式資料流

~~~text
                     User batch/v1 Job
                           |
              +------------+------------+
              |                         |
              v                         v
      Execution Contract       Static Semantic Layer
                                     |
                           argv/config/metadata
                                     |
                                     v
                           Canonical Workload Spec
                                     |
                                     v
                         candidate discovery
                                     |
                 +-------------------+-------------------+
                 |                                       |
                 v                                       v
          RTX4090 dry-run                          RTX5090 dry-run
                 |                                       |
                 v                                       v
      target-process Nsight trace              target-process Nsight trace
                 |                                       |
                 v                                       v
        marker-free extraction                  marker-free extraction
                 |                                       |
                 v                                       v
         execution_cycle period                  execution_cycle period
                 |                                       |
                 +-------------------+-------------------+
                                     |
                                     v
                             Semantic Binding
                                     |
                         +-----------+-----------+
                         |                       |
                         v                       v
                      bound                  unbound
                         |                       |
                         v                       v
               runtime per work unit      cycle latency /
               + total-work path          slowdown only
                         |
                         v
                 aligned Netdata/DCGM
                         |
                         v
               node-bound power model
                         |
                         v
                    energy ranking
                         |
                         v
                  production placement
~~~

## execution_cycle 與 work_unit

Marker-free detector 的第一層輸出必須是：

~~~text
detected_unit = execution_cycle
period_ms
confidence
complete_cycles
~~~

不能因為測試 workload 是 training job，就直接把 execution_cycle 稱為 training_iteration。

Static Semantic Layer 可能知道：

~~~text
work_unit = training_iteration
total_work_units = 640
~~~

但兩者之間還要通過 Semantic Binding gate：

~~~text
execution_cycle
     ↕ validated binding
training_iteration
~~~

可能的 binding 狀態：

~~~text
bound
unbound
conflict
insufficient_evidence
~~~

只有 bound 才能把 marker-free cycle runtime 外推到 semantic work unit。

## Semantic-aware mode

條件：

~~~text
work_unit known
total_work_units known
execution_cycle -> work_unit binding validated
runtime model ready
~~~

此時：

~~~text
T_total = runtime_per_work_unit × total_work_units
~~~

再結合 power prediction 估計 total energy。

## Opaque mode

若使用者 Job 完全沒有可可靠解析的 workload semantics：

~~~text
detected_unit = execution_cycle
total_work_units = unknown
semantic binding = unbound
~~~

平台仍可輸出：

- execution-cycle latency；
- slowdown under current load；
- relative local performance；
- trace stability / OOD evidence。

但不可宣稱精確 total job runtime / total energy。

## Marker-Free trace contract

正式 shared-GPU runtime path 使用 target-process CUDA trace。

Nsight：

~~~text
/opt/pre6g/nsight/bin/nsys profile
  --trace=cuda,nvtx,osrt
  --sample=none
  --cpuctxsw=none
  -- <original command> <original args>
~~~

NVTX 可存在於 report，但 detector 不讀 NVTX。

Marker-free extractor最低需要：

~~~text
CUPTI_ACTIVITY_KIND_KERNEL
StringIds

kernel fields:
  start
  end
  shortName
  globalPid
  contextId
  streamId
~~~

首版 event representation：

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

禁止加入：

~~~text
iteration_id
epoch
batch_in_epoch
NVTX-derived labels
callback-derived labels
~~~

## Period target

Period target 是 recurring pattern 的 start-to-start cadence，而不是單純將 kernel duration 相加。

~~~text
cycle_start[n]
      |
      +---- elapsed wall cadence ----+
                                     |
                              cycle_start[n+1]
~~~

這樣才能保留 kernel gap、CPU launch delay、sync/data-movement 與 contention 對 work-unit latency 的影響。

## YOLO C03 marker-free preflight

RTX5090 C03 已以 Nsight Systems 2026.4.1.191 驗證：

~~~text
CUPTI_ACTIVITY_KIND_KERNEL present
StringIds present
kernel_count = 653976
kernel-start span = 17.980515013 s

usable fields:
  start
  end
  shortName
  globalPid
  deviceId
  contextId
  streamId
~~~

kernel table 中目前是一個 dominant CUDA process/context group：

~~~text
globalPid = 327417436569600
contextId = 1
kernel_count = 653976
~~~

後續 C03 validation 已證明 yolo-v1 可 recover stable execution_cycle；same-window hidden-oracle APE 為 1.67%。

## Research ground truth policy

Instrumented benchmark 可保留作 offline validation。

正確順序：

~~~text
marker-free detector input only
        ↓
freeze detector output
        ↓
reveal hidden instrumentation ground truth
        ↓
compute error
~~~

不得在 detector tuning/current-run inference 中讀取 iterations.csv 或 NVTX iteration labels。

## Telemetry responsibility

Current validated ownership：

~~~text
Netdata Parent/Child
  continuously monitors and stores:
    CPU User/System/IOWait
    Load 1/5/15
    Memory Used/Free
    CPU temperature
    Top1/Top2/Top3 CPU process utilization

DCGM Exporter
  actively polled during the Profile Job:
    GPU Util
    GPU framebuffer used
    GPU temperature
    GPU power

Nsight Systems
  target-process CUDA behavior
~~~

Formal Profile Jobs do not create a second 1 Hz Netdata sampler. They only record the absolute wall-clock boundaries, run Nsight/workload plus the DCGM poller, and query the Netdata Parent historical database after the run:

~~~text
pre_window_start_ns
       |
DCGM start / pre-roll
       |
profile_start_ns
       |
Nsight + original workload
       |
profile_end_ns
       |
post-roll / DCGM stop
       |
post_window_end_ns
       |
       +--> Netdata Parent historical query [pre_window_start, post_window_end]
       +--> dcgm.csv from active polling
                         |
                         v
                  timestamp alignment
~~~

This keeps Netdata request latency outside the measurement cadence. The live-polling `collect_netdata.py` path is diagnostic only.

Top1/Top2 per-process GPU utilization remain optional extension。

## Node-bound power model routing

Power models currently node-specific。

Routing key：

~~~text
Kubernetes node name
+
physical GPU UUID
~~~

Automatic ranking currently requires：

~~~text
model_scope = node-bound
target_semantics = node-total-power
target_unit = W
bound_node == candidate node
bound_gpu_uuid == candidate physical GPU UUID
~~~

For ranking：

~~~text
P_incremental = max(0, P_node_predicted - P_idle_node)
~~~

若 power prediction 是 time series，積分 P_incremental(t)。

RTX4090 / RTX5090 真實 power model formats 尚未取得，因此 real workflow 目前保持 profile-only。

## Kubernetes placement

MVP 不修改 kube-scheduler。每個 candidate node 建立獨立 dry-run Job：

~~~yaml
nodeSelector:
  kubernetes.io/hostname: worker-5090
resources:
  requests:
    nvidia.com/gpu.shared: "1"
  limits:
    nvidia.com/gpu.shared: "1"
~~~

Profile 與 production 必須使用相同 sharing contract。

## Formal 120-second capture

Phase 07：

~~~text
configured capture = 120 s
wall timeout = 300 s
detector prefixes = 7/9/12/15/20/30 s
minimum complete cycles = 3
~~~

固定 capture length 與 detector window 是不同參數。Detector 第一版離線分析，不根據 period result 在線提前停止 Nsight。

## Telemetry time alignment

Canonical timestamp = UTC Unix nanoseconds。

Netdata historical samples use the timestamp stored by the Netdata database and returned by the Parent historical API. The Profile Job queries them only after the workload using the recorded absolute window.

DCGM：

~~~text
timestamp_ns = (request_start_ns + request_end_ns) / 2
~~~

Alignment gate：

~~~text
nearest timestamp
tolerance <= 750 ms
coverage >= 90%
max Netdata gap <= 2 s
max DCGM gap <= 2 s
~~~

## 狀態機

~~~text
RECEIVED
→ EXECUTION_VALIDATED
→ STATIC_SEMANTICS_DISCOVERED
→ CANDIDATES_DISCOVERED
→ TELEMETRY_READY
→ PROFILE_JOBS_CREATED
→ PROFILING
→ MARKER_FREE_EVENTS_EXTRACTED
→ EXECUTION_CYCLE_DETECTED
→ SEMANTIC_BINDING_EVALUATED
→ FEATURES_EXTRACTED
→ MODELS_RESOLVED
→ PREDICTED
→ GATED
→ RANKED
→ PRODUCTION_JOB_CREATED
→ RUNNING
→ COMPLETED
~~~

Semantic status unknown / binding unbound 可以繼續 profiling；只有 total-job prediction/ranking 才形成 gate。

## Artifact layout

~~~text
artifacts/<task-id>/<node>/<attempt>/
├── source-job.yaml
├── execution-contract.json
├── workload-spec.json
├── profile-job.yaml
├── profile.nsys-rep
├── profile.sqlite
├── marker-free-events.csv
├── marker-free-discovery.json
├── semantic-binding.json
├── timestamps.json
├── raw/
│   ├── netdata.json
│   └── dcgm.csv
├── processed/
│   ├── netdata.csv
│   ├── dcgm.csv
│   └── telemetry-aligned.csv
├── telemetry-quality.json
├── runtime-prediction.json
├── power-model-manifest.json
├── power-prediction.json
├── node-result.json
└── checksums.json
~~~

研究 fixture 的 iterations.csv / summary.json 等 instrumentation artifacts 不屬於 production-required artifact contract。

不要使用 k3s local-path RWO PVC 當跨節點共享 artifact store。


## Runtime implementation mapping

The deployment repository now maps the previously validated reference workflow into explicit platform components.

~~~text
PreG_result reference
  evaluate_trace_event_periods.py
        |
        v
Pre6G_experiment
  src/pre6g_experiment/marker_free.py
  scripts/evaluate_trace_event_periods.py

Pre6G_result reference
  run_unified_trace_model.py
        |
        v
Pre6G_experiment
  src/pre6g_experiment/runtime_features.py
  scripts/run_unified_trace_model.py
~~~

The platform version intentionally changes four integration rules:

1. NVTX is optional post-detection audit data. CUPTI kernel activity and StringIds are the required production trace tables.
2. globalPid/contextId isolation happens before period detection. A multi-group trace is rejected unless the target is explicitly selected.
3. The current 20–2000 ms period range and supported 2x harmonic correction belong to the yolo-v1 detector profile; they are not generic workload semantics.
4. Pre-run telemetry is selected from absolute timestamps.json boundaries and canonical aligned Netdata/DCGM samples. iterations.csv is not used to locate the telemetry window.

This keeps the detector/runtime-model logic aligned with the validated project results while making the execution path suitable for opaque Kubernetes workloads.


## Control-side runtime inference boundary

正式部署中，GPU worker 與 control side 的責任分界固定如下：

~~~text
RTX worker Profile Job
  user dry-run
  Nsight Systems 2026
  target PID/context isolation
  marker-free period detection
  runtime trace feature extraction
        ↓
  runtime-features.json
        ↓
shared artifact store
        ↓
k3s control side
  model binding
  frozen runtime inference
  quality / OOD gate
  power inference
  ranking
  production Job creation
~~~

Worker 應盡量在本地把大型 Nsight trace 壓縮成小型 feature/result artifact；control side 不需要為每次 prediction 搬移完整 SQLite。

目前正式 runtime inference entrypoint：

~~~text
scripts/predict_runtime.py
src/pre6g_experiment/runtime_model.py
~~~

第一個 frozen deployment-smoke bundle：

~~~text
models/runtime/RTX5090_yolo_trace_only_v1.json
~~~

此 model 僅綁定：

~~~text
device_id = RTX5090
detector_profile = yolo-v1
detected_unit = execution_cycle
workload_family = YOLO26 validation family
role = deployment-smoke
~~~

因此 RTX4090 或其他 workload family 不可 fallback 使用此 model。

Profile Job 與 Controller 之間的 artifact contract：

~~~text
docs/profile-result-contract.md
schemas/profile-result.schema.json
schemas/runtime-features.schema.json
schemas/runtime-prediction.schema.json
~~~

人工 SCP 只用於 bring-up smoke，不是 production transport。正式 controller 應透過 shared artifact store 取得每個 candidate 的 ProfileResult。

## RTX5090 C03 end-to-end component evidence

目前已完成一筆 worker → control-side component smoke：

~~~text
marker-free detected period     129.988614 ms
detector confidence             0.83999
same-window NVTX oracle         132.1907935 ms
same-window detector APE        1.67%

control-side frozen prediction  109.251714 ms
unprofiled C03 smoke reference  118.080153 ms
smoke comparison APE            7.48%
~~~

7.48% 僅驗證 deployment data path 與 frozen inference 可執行；C03 存在於 final-fit dataset，因此不可把它當作新的 held-out model accuracy。
