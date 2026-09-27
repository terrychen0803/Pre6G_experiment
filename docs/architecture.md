# 系統架構

## 核心原則

Pre6G_experiment 對使用者 Job 採兩層處理：

~~~text
Execution Contract
  image / command / args / env / resources / volumes

Workload Semantic Contract
  workload family
  canonical parameters
  work unit
  total work units
  discovery source
~~~

Execution Contract 決定「如何執行」，Semantic Contract 決定「平台理解多少，以及能否外推 total runtime / total energy」。

Unknown semantics 不阻止 profiling。

## 元件

| 元件 | 執行位置 | 責任 |
|---|---|---|
| Experiment API / Controller | k3s server | 接收 Job、驗證 execution contract、找候選節點、建立 Profile Job、等待結果、選點、建立 production Job |
| Workload Semantic Layer | controller | explicit metadata → adapter → runtime discovery → unknown，輸出 canonical workload spec |
| Workload Adapter Registry | controller | 針對已知 framework 將 argv/config 轉成 canonical workload semantics；目前先有 YOLO adapter |
| Profile Job Builder | controller | deep-copy source Job，保留 application contract，只加入 Nsight wrapper、node pin、artifact 與 dry-run policy |
| Application container | candidate worker | 在實際 shared-GPU 資源上執行使用者 workload |
| Profile collector | 同一 Pod 或 controller-side collector | 驗證 report、export、feature extraction、artifact upload |
| Netdata child | 每個 node | system/CPU/memory/temp/process CPU time series |
| Netdata Parent | central monitoring | per-host historical system telemetry |
| DCGM Exporter | 每個 NVIDIA node | GPU utilization、framebuffer、temperature、power |
| Runtime adapter | central service 或 collector | target-process CUDA trace → runtime per work unit / confidence / OOD |
| Power model registry/router | central service | node + physical GPU UUID → node-bound power model |
| Power adapter | central service | canonical telemetry → node-total power |
| Decision layer | controller | 驗證 work-unit/model/telemetry gates，計算 energy 並排序 |
| Artifact store | MinIO/S3/NFS | source/profile Job、trace、telemetry、semantic spec、predictions、ground truth |

## 正式資料流

~~~text
                     User batch/v1 Job
                           |
              +------------+------------+
              |                         |
              v                         v
      Execution Contract        Semantic Discovery
                                   |
                      +------------+------------+
                      |            |            |
                   explicit      adapter      runtime
                   metadata                   discovery
                      |            |            |
                      +------------+------------+
                                   |
                                   v
                         Canonical Workload Spec
                                   |
                                   v
                         candidate discovery
                                   |
                 +-----------------+-----------------+
                 |                                   |
                 v                                   v
          RTX4090 dry-run                      RTX5090 dry-run
                 |                                   |
          Nsight target trace                  Nsight target trace
          Netdata historical                   Netdata historical
          DCGM collection                      DCGM collection
                 |                                   |
                 v                                   v
          aligned telemetry                    aligned telemetry
                 |                                   |
                 +------------ runtime ------------+
                 |                                   |
                 v                                   v
        runtime per work unit               runtime per work unit

                 +------------- power -------------+
                 |                                   |
                 v                                   v
          4090 node model                      5090 node model
                 |                                   |
                 v                                   v
          node-total power                     node-total power
                 |                                   |
                 +---------- normalization ----------+
                                   |
                                   v
                         total-job comparison
                     only if total work is known
                                   |
                                   v
                         production placement
~~~

## Workload semantic contract

Canonical schema：

~~~text
schemas/workload-spec.schema.json
~~~

核心欄位：

~~~text
workload_family
adapter
profileable
parameters
work.status
work.unit
work.total_units
work.source
work.missing
runtime_discovery_required
~~~

Discovery priority：

~~~text
1. explicit canonical metadata
2. registered adapter
3. runtime discovery
4. unknown
~~~

平台核心不解析任意 CLI 的語意；只由 adapter 處理它宣告支援的 framework。

目前 first adapter = YOLO。未來加入 LLM / FFmpeg / FAISS adapter 不需要改 execution path。

## Generic runtime semantics

不要把所有 workload 都強制視為 iteration。

Examples：

~~~text
YOLO training      -> training_iteration
LLM fine-tuning    -> optimizer_step
LLM inference      -> generated_token
FFmpeg             -> frame
FAISS build        -> vector_insert
~~~

Runtime adapter 建議輸出：

~~~json
{
  "work_unit": "training_iteration",
  "predicted_runtime_ms_per_work_unit": 42.1
}
~~~

若 workload spec work unit 與 runtime prediction work unit 不一致，decision layer 拒絕外推。

只有 total_work_units 已知，才允許：

~~~text
T_total = T_per_work_unit * total_work_units
~~~

Unknown total work 仍可保留 per-cycle/per-unit evidence，但不應宣稱 total-job runtime/energy。

## Telemetry responsibility

Current validated ownership：

~~~text
Netdata
  CPU User/System/IOWait
  Load 1/5/15
  Memory Used/Free
  CPU temperature
  Top1/Top2/Top3 CPU process utilization

DCGM Exporter
  GPU Util
  GPU framebuffer used
  GPU temperature
  GPU power

Nsight Systems
  target-process CUDA trace
~~~

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

RTX4090 / RTX5090 真實 power model formats尚未取得，因此 real workflow目前保持 profile-only。

## Profile Job wrapping

Controller 不重新理解或重寫 application parameters。

原始：

~~~text
<original-command> <original-args>
~~~

Profile：

~~~text
/opt/pre6g/nsight/bin/nsys profile
  --trace=cuda,nvtx,osrt
  --sample=none
  --cpuctxsw=none
  --output=<artifact-path>/profile
  --
  <original-command> <original-args>
~~~

Adapter 只做 semantic discovery，不參與 argv reconstruction。

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

## Shared-GPU runtime backend

正式 shared-mode extractor只讀 target-process CUDA trace：

~~~text
CUPTI kernel start timestamp
CUDA kernel short-name ID
target process/context identity
~~~

Device-wide GPU metrics不作 period detector fallback。

正式 capture：

~~~text
configured capture = 120 s
wall timeout = 300 s
detector prefixes = 7/9/12/15/20/30 s
minimum complete cycles = 3
~~~

在此之前，Phase 05/06先做短 execution/profile compatibility。

## Telemetry time alignment

Canonical timestamp = UTC Unix nanoseconds。

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
→ SEMANTICS_DISCOVERED
→ CANDIDATES_DISCOVERED
→ TELEMETRY_READY
→ SHORT_COMPATIBILITY_PASSED
→ PROFILE_JOBS_CREATED
→ PROFILING
→ FEATURES_EXTRACTED
→ MODELS_RESOLVED
→ PREDICTED
→ GATED
→ RANKED
→ PRODUCTION_JOB_CREATED
→ RUNNING
→ COMPLETED
~~~

Semantic status unknown 可以繼續到 profiling；只有需要 total-job prediction/ranking 時才形成 gate。

## Artifact layout

~~~text
artifacts/<task-id>/<node>/<attempt>/
├── source-job.yaml
├── execution-contract.json
├── workload-spec.json
├── runtime-discovery.json
├── profile-job.yaml
├── profile.nsys-rep
├── profile.sqlite
├── nsys-stats.csv
├── marker-free-features.json
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

不要使用 k3s local-path RWO PVC 當跨節點共享 artifact store。
