# 系統架構

## 元件

| 元件 | 執行位置 | 責任 |
|---|---|---|
| Experiment API / Controller | k3s server | 接收 Job、找候選節點、建立 Profile Job、等待結果、選點、建立 production Job |
| Profile Job Builder | controller | 使用 Pre6G_profiling 的方式讓 `nsys profile` 直接啟動原始 command |
| Application container | candidate worker | 在實際 shared-GPU 資源上短時間執行 workload |
| Profile collector | 同一 Pod | 等待 report、驗證、`nsys stats/export`、feature extraction、上傳 artifact |
| Netdata child | 每個 node 的 DaemonSet | 持續保留 node/GPU/process time series；不因單次實驗啟停 |
| Runtime adapter | central service 或 collector | 接受 target-process CUDA trace schema，輸出 runtime、confidence、OOD、model version |
| Power adapter | central service | 對每個 timestamp 的 18-feature vector 預測瞬時 watts，再積分成 energy |
| Artifact store | MinIO/S3/NFS | 保存 report、features、metadata、prediction 與正式執行觀測 |

## 為什麼不用 profiling sidecar attach

Application container 的 command 應改成：

```text
nsys profile [options] -- original-command [args]
```

collector sidecar 只處理完成後的 report。這避免 PID namespace、attach race、`SYS_PTRACE` 與 privileged Pod，也沿用 Pre6G_profiling 已驗證的工程路線。

## Kubernetes placement

MVP 不修改 kube-scheduler。Controller 為每個 candidate node 建立一個帶有以下條件的獨立 Job：

```yaml
nodeSelector:
  kubernetes.io/hostname: worker-5090
resources:
  limits:
    nvidia.com/gpu.shared: "1"
```

實際 resource name 以 device plugin 設定為準。若 `renameByDefault=false`，shared replica 仍可能名為 `nvidia.com/gpu`；平台不能只靠 resource name 判斷是否共享，必須讀取 node sharing label/config。

## Shared-GPU runtime backend

High-load 實驗證明 device-wide GPU Metrics 會被背景程序污染，但由 `nsys profile` 直接 launch application 後，CUDA trace 可保留 target-process kernel events。正式 shared-mode extractor 只讀：

```text
CUPTI kernel start timestamp
CUDA kernel short-name ID
target process/context identity
```

NVTX、iteration CSV、workload ID 與 GPU Metrics 都不進入 detector/model input。Adaptive detector 在 7/9/12/15/20/30 秒檢查 confidence 與相鄰窗口穩定度，達標即停止；不要把固定 5 秒當作全 workload SLA。

完成 ranking 後，controller 由原始 Job deep-copy 出新的 production Job，再加入所選 `nodeSelector`。Profile Job 與 production Job 都不得覆寫原始 YAML。

## 狀態機

```text
RECEIVED
→ VALIDATED
→ CANDIDATES_DISCOVERED
→ NETDATA_READY
→ PROFILE_JOBS_CREATED
→ PROFILING
→ FEATURES_EXTRACTED
→ PREDICTED
→ GATED
→ RANKED
→ PRODUCTION_JOB_CREATED
→ RUNNING
→ COMPLETED
```

任何一步都要保存原因明確的 failure state。不得以零、平均 GPU 或任意常數默默取代 unavailable model。

## Artifact layout

```text
artifacts/<task-id>/<node>/<attempt>/
├── source-job.yaml
├── profile-job.yaml
├── profile.nsys-rep
├── profile.sqlite
├── nsys-stats.csv
├── marker-free-features.json
├── netdata-raw.json
├── netdata-features.json
├── timestamps.json
├── runtime-prediction.json
├── power-prediction.json
├── node-result.json
└── checksums.json
```

不要使用 k3s `local-path` RWO PVC 當成跨節點共享 artifact store；多節點平行 Profile Job 應使用 object storage、RWX storage，或先寫 node-local scratch 再上傳。
