# 完整實驗程序

本文件是 Pre6G_experiment 的正式實驗 SOP。每個 phase 都要留下可重現設定與 evidence；任何 gate 失敗都保存明確原因，不以零值、其他節點模型或未驗證 fallback 繼續自動 ranking。

目前平台主軸：

~~~text
Opaque User Job
  → Execution Contract
  → Workload Semantic Discovery
  → per-node dry-run
  → target-process Nsight trace
  → Netdata + DCGM aligned telemetry
  → runtime / node-bound power prediction
  → quality/model gate
  → cross-node ranking
  → production Job
  → ground-truth evaluation
~~~

YOLO 是目前用來跑通平台流程的第一個 integration fixture，不是平台支援邊界。

## Phase 00：Contract freeze

保存：

1. 原始 batch/v1 Job。
2. application container identity。
3. container image digest。
4. dataset/version/hash；若 workload 不使用 dataset 則記錄 not-applicable。
5. candidate GPU sharing strategy、resource name、replicas、physical GPU UUID。
6. Nsight version與 trace contract。
7. Netdata/DCGM feature schema、cadence、alignment policy。
8. runtime model manifest；尚未定版則 profile-only。
9. node-bound power model manifest；尚未取得則 status=unavailable。
10. workload semantic metadata 與其來源：declared / adapter / runtime discovery / unknown。

目前 RTX4090/RTX5090 integration baseline：

~~~text
Architecture: x86_64
Nsight Systems: 2026.4.1.191-264138605071v0
Host install root: /opt/nvidia/nsight-systems-cli/2026.4.1
Container mount: /opt/pre6g/nsight
Container CLI: /opt/pre6g/nsight/bin/nsys
RuntimeClass: nvidia
GPU resource: nvidia.com/gpu.shared
~~~

不要依賴 PATH 中裸 nsys。

## Phase 01：Cluster / GPU scheduling preflight

從 control-plane：

~~~bash
kubectl get nodes -o wide
kubectl get runtimeclass
kubectl get pods -A -o wide
~~~

逐 candidate node 確認：

- Ready=True
- shared GPU resource 存在
- runtimeClass nvidia 可用
- physical GPU UUID 可取得
- taint/toleration 不會阻擋測試 Job
- artifact/image path 有可用方案

GPU scheduling smoke 必須先於真實 workload。

## Phase 02：Nsight Systems 2026 preflight

RTX x86_64 要求：

1. /opt/nvidia/nsight-systems-cli/2026.4.1 存在。
2. Kubernetes 掛載完整 installation root。
3. Pod 內使用 /opt/pre6g/nsight/bin/nsys。
4. nsys --version 為 2026.4.1.191-264138605071v0。
5. trace=cuda,nvtx,osrt。
6. sample=none。
7. cpuctxsw=none。
8. 可產生非空 .nsys-rep。
9. report 可匯出 SQLite 並解析 CUDA kernel/API 與 OS runtime summary。

可重現 smoke manifest：

~~~text
k8s/nsys2026-rtx-smoke.yaml
~~~

## Phase 03：Nsight Kubernetes E2E smoke

在 RTX4090 / RTX5090 各跑一個小型 CUDA workload：

- Job Complete
- exact Nsight 2026 path/version
- CUDA hardware trace
- .nsys-rep
- SQLite export
- nsys stats
- target kernels present

只有兩台都 PASS 才進 monitoring/workload integration。

## Phase 04：Monitoring and time alignment

### 04A Netdata child

Candidate node child 必須 Running/Ready。

~~~bash
kubectl -n netdata get pods   -l 'app=netdata,role=child'   -o wide
~~~

### 04B Netdata Parent

每個 candidate hostname 必須能透過 Parent 查 historical telemetry：

~~~text
/host/<hostname>/api/v1/...
~~~

Netdata 負責：

- CPU User/System/IOWait
- Load 1/5/15
- Memory Used/Free
- CPU temperature
- Top1/Top2/Top3 CPU

### 04C DCGM device telemetry

Required metrics：

~~~text
DCGM_FI_DEV_GPU_UTIL
DCGM_FI_DEV_FB_USED
DCGM_FI_DEV_GPU_TEMP
DCGM_FI_DEV_POWER_USAGE
~~~

對應：

~~~text
GPU Util%
GPU Mem Used(MB)
GPU Temp(°C)
GPU Power(W)
~~~

Exporter cadence：

~~~text
DCGM_EXPORTER_INTERVAL=1000
~~~

DaemonSet rollout gate：

~~~text
DESIRED == CURRENT == READY == AVAILABLE == UPDATED
MISSCHEDULED == 0
~~~

### 04D Clock synchronization

Control-plane 與 candidate workers：

~~~bash
timedatectl status | grep -E 'Local time|Universal time|System clock synchronized|NTP service'
~~~

Required：

~~~text
System clock synchronized: yes
NTP service: active
~~~

### 04E Timestamp alignment

Netdata historical samples與 DCGM active collection以 absolute UTC timestamp 對齊。

Current gate：

~~~text
method = nearest timestamp
tolerance <= 750 ms
coverage >= 90%
max Netdata gap <= 2 s
max DCGM gap <= 2 s
~~~

使用：

~~~text
scripts/collect_dcgm.py
scripts/align_telemetry.py
~~~

2026-09-27 RTX5090 validation：

~~~text
DCGM samples = 20
Netdata samples = 26
Aligned = 20
Coverage = 100%
Median |delta| = 284.8 ms
Max |delta| = 490.5 ms
~~~

Top1/Top2 GPU 目前不是 validated core telemetry；若 power model 需要，該 model 維持 unavailable/schema_mismatch。

## Phase 05：Workload Intake & Semantic Discovery

這一階段不要求 runtime/power model ready，也不做 120 秒正式 profiling。

目標是證明平台可以接受 arbitrary batch/v1 Job，並把 execution 與 semantics 分離。

### 05A Generic Job inspection

~~~bash
python -m pre6g_experiment inspect --job user-job.yaml
~~~

輸出：

~~~text
execution_contract
  application container
  image
  command
  args
  env names
  resources
  volumes

workload_spec
  workload_family
  adapter
  parameters
  work.unit
  work.total_units
  source
  missing
  runtime_discovery_required
~~~

原始 command/args 不因 adapter 被改寫。

### 05B Semantic discovery priority

~~~text
1. explicit canonical metadata
2. registered workload adapter
3. runtime discovery
4. unknown
~~~

Explicit metadata：

~~~yaml
pre6g.io/workload-family: vision-training
pre6g.io/work-unit: training_iteration
pre6g.io/total-work-units: "640"
pre6g.io/workload-parameters-json: >-
  {"model":"...","batch_size":16}
~~~

未知 total work 仍 profileable。

### 05C YOLO integration fixture

目前第一個 adapter 是 YOLO，只用來驗證 semantic layer：

- --model
- --epochs
- --batch / --batch-size
- --imgsz / --img-size
- --amp
- dataset sample count annotation

若 metadata 完整：

~~~text
steps_per_epoch = ceil(training_samples / batch)
total_work_units = epochs × steps_per_epoch
work_unit = training_iteration
~~~

### 05D Runtime discovery

實際執行後比對 requested / discovered：

- actual batch
- dataloader length
- steps per epoch
- world size
- gradient accumulation
- observed work-unit boundary

Static estimate 若與 runtime discovery 不一致，後續 total-runtime 外推不得直接使用未驗證 static 值。

### 05E Short real execution

同一 immutable workload image/config 在 RTX4090 / RTX5090 各做短時間 execution compatibility test。

本階段先不要求 Nsight 120 秒，只確認：

- image 可啟動
- dataset/input 可存取
- CUDA 可用
- GPU shared resource 正常
- application 真正進入 steady work
- requested/discovered semantics 可保存

詳細 contract 見 docs/workload-intake.md。

## Phase 06：Short Profile Compatibility

把 Phase 05 已通過的同一 application contract包進固定 Nsight 2026。

Profile Job 必須：

- deep-copy source Job
- 保留 image/command/args/env/resources/volumes
- pin candidate node
- shared GPU contract不變
- 只增加 profiler wrapper / artifact path / timeout
- 使用 target-process trace
- 先跑短窗口，例如 5–15 秒

PASS：

- application仍能正常進入 workload
- .nsys-rep 完整
- target CUDA kernels 可識別
- Netdata/DCGM 同窗口可收集
- timestamp quality pass
- graceful/finalization行為可接受

## Phase 07：Formal 120-second Dry-run

每個 candidate node 建立獨立 Job：

~~~text
<source-name>-profile-<node>-<task-id>
~~~

要求：

- node pin
- nvidia.com/gpu.shared: 1
- backoffLimit: 0
- activeDeadlineSeconds: 300
- Nsight 2026 fixed path
- trace=cuda,nvtx,osrt
- sample=none
- cpuctxsw=none
- configured capture = 120 s
- workload若提早自然完成則保存實際長度
- report finalization後才做 offline detection
- target cycles >= 3
- 保存 timestamps.json

建議 timestamps：

~~~text
pre_window_start_ns
application_start_ns
profile_start_ns
steady_window_start_ns
steady_window_end_ns
profile_end_ns
application_end_ns
post_window_end_ns
~~~

## Phase 08：Feature extraction, prediction and ranking

### Runtime

Shared GPU只使用 target-process CUDA trace。

Generic runtime contract：

~~~text
work_unit
predicted_runtime_ms_per_work_unit
confidence
ood
~~~

runtime work_unit 必須與 workload spec 一致。

### Telemetry

1. Netdata Parent 查相同 absolute window。
2. DCGM保存 raw CSV。
3. canonical units。
4. timestamp alignment。
5. 保存 quality metadata。

### Power

依 candidate node + physical GPU UUID resolve node-bound model。

目前 4090 / 5090 真實 power model formats尚未取得，因此保持 profile-only。

第一版 ranking contract：

~~~text
target_semantics = node-total-power
target_unit = W
~~~

### Total runtime / energy

只有 total_work_units 已知才能：

~~~text
T_total ≈ runtime_per_work_unit × total_work_units
~~~

並進一步外推 total energy。

未知 total work時只保存 per-work-unit evidence，預設不自動部署。

## Phase 09：Production Job and ground-truth evaluation

從原始 source Job deep-copy：

- 新名稱
- 移除 profiling wrapper / collector / dry-run timeout
- 加 selected nodeSelector
- 保留原始 image/command/args/env/resources/volumes
- sharing contract與 profile相同

先：

~~~bash
kubectl apply --dry-run=server -f production-job.yaml
~~~

正式執行後收：

- runtime ground truth
- Netdata
- DCGM
- measured/derived energy

評估至少包含：

- runtime MAPE
- power MAE/MAPE
- energy MAPE
- ranking accuracy
- best-node hit rate
- energy regret
- profiling overhead
- decision latency
- OOD/rejected/missing-feature rate
- model-binding rejection rate
- alignment quality distribution

## Failure policy

| 狀況 | 行為 |
|---|---|
| Unknown workload semantics | 允許 profiling；不做 total-job extrapolation |
| total work未知 | per-work-unit result；預設不自動部署 |
| semantic adapter不存在 | generic/profile-only；可做 runtime discovery |
| runtime work_unit mismatch | reject prediction |
| 某 node Profile Job失敗 | 排除 node並保存原因 |
| 所有 node失敗 | 不建立 production Job |
| runtime model unavailable | profile-only |
| node-bound power model unavailable | profile-only / node不進 energy ranking |
| power model binding mismatch | reject，不 fallback |
| required telemetry missing | model not ready |
| alignment gate失敗 | telemetry invalid |
| shared GPU target process無法辨識 | 不得用 device-wide fallback |
| sharing state明顯漂移 | 重新 profile |
| prediction tie | tie/insufficient evidence |

## 目前進度

已完成：

~~~text
Phase 01 GPU scheduling baseline
Phase 02 Nsight 2026 preflight
Phase 03 Nsight Kubernetes smoke
Phase 04 Monitoring + timestamp alignment
~~~

目前進行：

~~~text
Phase 05 Workload Intake & Semantic Discovery
~~~

第一個 integration fixture 使用 YOLO26；後續會以 adapter方式擴充，而不修改 generic controller core。
