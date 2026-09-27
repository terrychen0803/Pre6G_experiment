# 系統架構

## 元件

| 元件 | 執行位置 | 責任 |
|---|---|---|
| Experiment API / Controller | k3s server | 接收 Job、找候選節點、建立 Profile Job、等待結果、選點、建立 production Job |
| Profile Job Builder | controller | 讓固定版本 Nsight Systems 直接啟動原始 command |
| Application container | candidate worker | 在實際 shared-GPU 資源上短時間執行 workload |
| Profile collector | 同一 Pod 或 controller-side collector | 等待 report、驗證、nsys stats/export、feature extraction、上傳 artifact |
| Netdata child | 每個 node 的 DaemonSet | 持續收集 system/CPU/memory/temperature/process CPU time series，並 stream 到 Parent |
| Netdata Parent | central monitoring service | 保存各 child 的 historical system/CPU telemetry，提供依 hostname 查詢的 API |
| DCGM Exporter | 每個 NVIDIA node 的 DaemonSet | 以約 1 秒 cadence 提供 GPU utilization、framebuffer、temperature、power |
| Runtime adapter | central service 或 collector | 接受 target-process CUDA trace schema，輸出 runtime、confidence、OOD、model version |
| Power model registry/router | central service | 依 Kubernetes node + physical GPU UUID 選出該節點專屬 power model |
| Power adapter | central service | 將對齊後 canonical telemetry 餵入 node-bound model，輸出 node-total power prediction |
| Decision layer | controller | 檢查 model binding、telemetry quality、OOD/confidence，計算 incremental energy 並排序 |
| Artifact store | MinIO/S3/NFS | 保存 report、raw telemetry、aligned features、metadata、prediction 與正式執行觀測 |

## 正式資料流

~~~
User Job
  |
  v
candidate discovery
  |
  +-----------------------------+
  |                             |
  v                             v
RTX4090 Profile Job        RTX5090 Profile Job
  |                             |
  +--> Nsight 2026 trace        +--> Nsight 2026 trace
  |                             |
  +--> Netdata Parent query     +--> Netdata Parent query
  |                             |
  +--> DCGM collection          +--> DCGM collection
  |                             |
  v                             v
timestamp-aligned canonical telemetry
  |                             |
  v                             v
Runtime Adapter              Runtime Adapter
  |                             |
  v                             v
runtime prediction           runtime prediction

aligned telemetry             aligned telemetry
  |                             |
  v                             v
Power Model Router           Power Model Router
  |                             |
  +--> 4090 node model         +--> 5090 node model
  |                             |
  v                             v
node-total power             node-total power
  |                             |
  +------ subtract node-specific idle power ------+
                                                   |
                                                   v
                                           energy comparison
                                                   |
                                                   v
                                           production placement
~~~

## Telemetry responsibility

The canonical energy feature schema is not tied to one monitoring product.

Current validated ownership:

~~~
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
  target-process CUDA trace used by the runtime model
~~~

Top1/Top2 per-process GPU utilization remains an optional extension. If a specific node-bound power model requires it, that model is not ready until a validated collector supplies it.

## Node-bound power model routing

Power models are currently node-specific.

Routing key:

~~~
Kubernetes node name
+
physical GPU UUID
~~~

Do not route only by product name such as RTX4090/RTX5090.

A central Power Adapter loads the matching model through the model registry and converts its output to a common prediction contract. The decision layer therefore stays model-implementation agnostic.

Automatic ranking currently requires:

~~~
model_scope = node-bound
target_semantics = node-total-power
target_unit = W
bound_node == candidate node
bound_gpu_uuid == candidate physical GPU UUID
~~~

For ranking:

~~~
P_incremental = max(0, P_node_predicted - P_idle_node)
E_incremental = P_incremental × predicted runtime
~~~

For timestamped power predictions, integrate P_incremental(t) over time.

See power-model-registry.md.

## 為什麼不用 profiling sidecar attach

Application container 的 command 應改成：

~~~
nsys profile [options] -- original-command [args]
~~~

collector sidecar 只處理完成後的 report。這避免 PID namespace、attach race、SYS_PTRACE 與 privileged Pod。

## Kubernetes placement

MVP 不修改 kube-scheduler。Controller 為每個 candidate node 建立一個獨立 Job：

~~~yaml
nodeSelector:
  kubernetes.io/hostname: worker-5090
resources:
  limits:
    nvidia.com/gpu.shared: "1"
~~~

實際 resource name 以 device plugin 設定為準。若 renameByDefault=false，shared replica 仍可能名為 nvidia.com/gpu；平台不能只靠 resource name 判斷是否共享，必須讀取 node sharing label/config。

## Shared-GPU runtime backend

High-load 實驗顯示 device-wide GPU Metrics 會被背景程序污染。正式 shared-mode extractor 只讀 target-process CUDA trace：

~~~
CUPTI kernel start timestamp
CUDA kernel short-name ID
target process/context identity
~~~

NVTX、iteration CSV、workload ID 與 device-wide GPU Metrics 不進入 period detector/model input。

每個 Profile Job 從 Nsight launch 原始 application 起固定 profiling 120 秒，整個 Job wall-clock timeout 為 300 秒。如果 workload 在 120 秒前自然完成，保存實際長度。Report 完成並匯出 SQLite 後，才找出第一個 target CUDA kernel，detector 由該點離線分析 7/9/12/15/20/30 秒 prefix，並以可用的後續 30 秒區段檢查 period/load drift。

固定 capture length 與 detector window 是兩個不同參數。第一版不根據 detector 結果在線提早中止 Nsight。

## Telemetry time alignment

Canonical timestamp 使用 UTC Unix nanoseconds。

Netdata historical sample 使用 API 提供的 timestamp。

DCGM collector 記錄 request start/end，並以：

~~~
timestamp_ns = (request_start_ns + request_end_ns) / 2
~~~

作為該次 sample timestamp。

Current quality gate:

~~~
nearest timestamp alignment
tolerance <= 750 ms
alignment coverage >= 90%
max Netdata gap <= 2 s
max DCGM gap <= 2 s
~~~

三台目前納入流程的 control-plane / RTX worker 都必須先通過 NTP synchronized preflight。

## 狀態機

~~~
RECEIVED
→ VALIDATED
→ CANDIDATES_DISCOVERED
→ TELEMETRY_READY
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

任何一步都要保存原因明確的 failure state。不得以零、平均 GPU、其他 node model 或任意常數默默取代 unavailable model。

## Artifact layout

~~~
artifacts/<task-id>/<node>/<attempt>/
├── source-job.yaml
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

不要使用 k3s local-path RWO PVC 當成跨節點共享 artifact store；多節點平行 Profile Job 應使用 object storage、RWX storage，或先寫 node-local scratch 再上傳。
