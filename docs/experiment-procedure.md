# 完整實驗程序

本文件是 Pre6G_experiment 的正式實驗 SOP。每個 phase 都要留下可重現設定與 evidence；任何 gate 失敗都保存明確原因，不以零值、其他節點模型或未驗證 fallback 繼續自動 ranking。

目前平台主軸：

~~~text
Opaque User Job
  → Execution Contract
  → Static Semantic Discovery
  → Marker-Free Dry-run
  → target-process CUDA trace
  → execution-cycle discovery
  → semantic binding
  → Netdata + DCGM aligned telemetry
  → runtime / node-bound power prediction
  → quality/model gate
  → cross-node ranking
  → production Job
  → ground-truth evaluation
~~~

YOLO 是目前用來跑通平台流程的第一個 integration fixture，不是平台支援邊界。

正式 production path 不依賴：

~~~text
iterations.csv
NVTX iteration markers
training callbacks
source-code instrumentation
user-added timing markers
~~~

這些資料只能作為研究驗證的 hidden ground truth。

## Phase 00：Contract freeze

保存：

1. 原始 batch/v1 Job。
2. application container identity。
3. container image digest。
4. dataset/version/hash；若 workload 不使用 dataset 則記錄 not-applicable。
5. candidate GPU sharing strategy、resource name、replicas、physical GPU UUID。
6. Nsight version 與 trace contract。
7. Netdata/DCGM feature schema、cadence、alignment policy。
8. runtime model manifest；尚未定版則 profile-only。
9. node-bound power model manifest；尚未取得則 status=unavailable。
10. workload semantic metadata 與其來源：declared / adapter / config / unknown。

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

Netdata historical samples 與 DCGM active collection 以 absolute UTC timestamp 對齊。

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

## Phase 05：Workload Intake & Marker-Free Discovery

Phase 05 不要求 runtime/power model ready，也不做正式 120 秒 prediction。

目標是證明：

1. arbitrary batch/v1 Job 可被平台接收；
2. execution contract 與 workload semantics 分離；
3. marker-free CUDA trace 可找出 recurring execution behavior；
4. 不修改 user code；
5. 不依賴 instrumentation 才能做 production profiling。

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
~~~

原始 command/args 不因 adapter 被改寫。

目前已驗證：

- YOLO fixture 可由 adapter 推估 training_iteration work amount。
- unknown Job 仍 profileable=true。
- generic explicit metadata 可描述 frame 等非 training work unit。

### 05B Static semantic extraction

Production semantics 的允許來源：

~~~text
1. explicit canonical metadata
2. registered workload adapter
3. standard argv / mounted config / dataset metadata
4. unknown
~~~

不要求在 user process 內埋 callback。

Static semantics 可描述：

~~~text
workload family
model/config parameters
candidate work_unit
total_work_units
~~~

但 static semantics 與實際 CUDA recurring cycle 是不同資訊。

### 05C Marker-Free workload discovery

正式輸入：

~~~text
original User Job
+
target-process Nsight CUDA trace
~~~

Production detector 禁止依賴：

~~~text
iterations.csv
NVTX TRAIN_ITER markers
epoch/batch callbacks
summary.json iteration timing
source-code instrumentation
~~~

Nsight SQLite 最低事件欄位：

~~~text
start
end
shortName
globalPid
contextId
streamId
~~~

流程：

~~~text
CUPTI kernel events
  → isolate target process/context
  → build marker-free event sequence
  → detect recurring pattern
  → estimate start-to-start period
  → stability/confidence gate
  → emit detected_unit=execution_cycle
~~~

第一個 detector output 必須稱為 execution_cycle，不直接稱為 training_iteration。

詳見 docs/marker-free-workload-discovery.md。

### 05D Semantic binding gate

Marker-free detector：

~~~text
execution_cycle
period_ms
confidence
complete_cycles
~~~

Semantic layer：

~~~text
candidate work_unit
total_work_units
~~~

只有 binding 被驗證後才能：

~~~text
execution_cycle
  ↔
training_iteration / optimizer_step / frame / ...
~~~

如果 binding unknown：

~~~text
work unit remains execution_cycle
total-job extrapolation disabled
~~~

Opaque workload 仍可輸出 cycle latency、slowdown、relative local performance。

### 05E YOLO validation fixture

YOLO C03 既有 instrumented baseline 只作 hidden ground truth。

目前 validation-only evidence：

~~~text
batch = 16
imgsz = 320
32 batches / epoch
steady-window mean iteration = 31.141946287 ms
GPU-event mean = 27.943115252 ms
~~~

這些數字不得餵入 marker-free detector。

RTX5090 C03 trace preflight 已驗證：

~~~text
Nsight = 2026.4.1.191
CUPTI_ACTIVITY_KIND_KERNEL present
StringIds present
kernel_count = 653976
kernel start span = 17.980515013 s
single dominant globalPid/contextId group
usable columns:
  start
  end
  shortName
  globalPid
  deviceId
  contextId
  streamId
~~~

因此目前 Phase 05 下一步是 marker-free event extraction，而不是再次分析 iterations.csv。

## Phase 05F：Current marker-free implementation checkpoint

The platform implementation now contains the following executable path:

~~~text
scripts/extract_marker_free_trace.py
  -> target process/context isolation
  -> marker-free event artifact

scripts/evaluate_trace_event_periods.py
  -> yolo-v1 period detection
  -> execution_cycle period/confidence/stability

scripts/run_unified_trace_model.py
  -> offline clean/high-load runtime-model reference evaluation
  -> pre-run telemetry anchored by timestamps.json
~~~

Reference-alignment corrections already applied:

~~~text
NVTX_EVENTS          optional audit only
globalPid/contextId  isolated before detection
20–2000 ms + 2x rule yolo-v1 profile only
iterations.csv       not used for pre-run telemetry window
~~~

For the current RTX5090 C03 trace, marker-free extraction has already produced:

~~~text
kernel events        653976
kernel span          17.980515013 s
unique kernel IDs    81
CUDA streams         5
dominant stream      7
single CUDA group    globalPid=327417436569600, contextId=1
~~~

The next execution step is to run the repository scripts on this existing C03 SQLite and confirm that the yolo-v1 detector reproduces a stable execution_cycle without using instrumentation-derived labels.

## Phase 06：Short Profile Compatibility

把 Phase 05 已通過的同一 application contract 包進固定 Nsight 2026。

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
- target CUDA process/context 可識別
- marker-free execution cycle 可偵測
- Profile Job 正確記錄 absolute pre/profile/post window
- run 後可從 Netdata Parent historical database 擷取同窗口 system/CPU telemetry
- profile window 內 DCGM active polling 可收集 GPU telemetry
- Netdata historical / DCGM timestamp alignment quality pass
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
- workload 若提早自然完成則保存實際長度
- report finalization 後才做 offline marker-free detection
- target cycles >= 3
- 保存 timestamps.json

NVTX 可以存在於 report 作 audit，但 detector 不讀 NVTX。

## Phase 08：Feature extraction, prediction and ranking

### Runtime

Shared GPU 只使用 target-process CUDA trace。

Marker-free detector 先輸出：

~~~text
detected_unit = execution_cycle
period_ms
confidence
complete_cycles
~~~

Worker-side Profile Job 接著建立：

~~~text
runtime-features.json
profile-result.json
~~~

正式工具：

~~~text
scripts/build_runtime_features.py
scripts/package_profile_result.py
~~~

大型 Nsight trace 留在 worker/artifact store；controller 只需要取得小型 ProfileResult/runtime feature artifact。

Control side 使用 frozen model：

~~~text
scripts/predict_runtime.py
models/runtime/<device-bound-model>.json
~~~

`predict_runtime.py` 僅做 inference，不重新 fit、不重新選 alpha。

目前已可用於 deployment smoke：

~~~text
models/runtime/RTX5090_yolo_trace_only_v1.json
~~~

此 bundle 僅適用 RTX5090 + YOLO26 validation family + yolo-v1 trace feature schema；不可 fallback 給 RTX4090 或其他 workload。

只有 semantic binding validated 後，runtime adapter 才可把 execution_cycle prediction 綁定成 semantic work unit：

~~~text
work_unit
predicted_runtime_ms_per_work_unit
confidence
ood
~~~

runtime work_unit 必須與 workload spec 一致。

### Telemetry

1. Netdata Parent 查相同 absolute window。
2. DCGM 保存 raw CSV。
3. canonical units。
4. timestamp alignment。
5. 保存 quality metadata。

### Power

依 candidate node + physical GPU UUID resolve node-bound model。

目前 4090 / 5090 真實 power model formats 尚未取得，因此保持 profile-only。

第一版 ranking contract：

~~~text
target_semantics = node-total-power
target_unit = W
~~~

### Total runtime / energy

只有下列條件都成立才允許 total-job extrapolation：

~~~text
semantic binding validated
work_unit known
total_work_units known
runtime model ready
~~~

此時：

~~~text
T_total ≈ runtime_per_work_unit × total_work_units
~~~

若 total work 或 semantic binding unknown，只保存 per-cycle/per-work-unit evidence，預設不自動部署。

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

- marker-free period error
- cycle stability / confidence
- semantic-binding success/failure rate
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

研究 validation 可使用 instrumentation 作 hidden ground truth，但 production inference path 不可使用。

## Failure policy

| 狀況 | 行為 |
|---|---|
| Unknown workload semantics | 允許 marker-free profiling；不做 total-job extrapolation |
| execution cycle 可偵測但 semantic binding unknown | 報 cycle latency / slowdown；保持 profile-only |
| total work未知 | per-cycle/per-work-unit result；預設不自動部署 |
| semantic adapter不存在 | generic/profile-only |
| runtime work_unit mismatch | reject prediction |
| marker-free period不穩定 | reject該次 runtime evidence |
| target process/context無法辨識 | reject該次 profile |
| 某 node Profile Job失敗 | 排除 node並保存原因 |
| 所有 node失敗 | 不建立 production Job |
| runtime model unavailable | profile-only |
| node-bound power model unavailable | profile-only / node不進 energy ranking |
| power model binding mismatch | reject，不 fallback |
| required telemetry missing | model not ready |
| alignment gate失敗 | telemetry invalid |
| sharing state明顯漂移 | 重新 profile |
| prediction tie | tie/insufficient evidence |

## 目前進度

已完成：

~~~text
Phase 01 GPU scheduling baseline
Phase 02 Nsight 2026 preflight
Phase 03 Nsight Kubernetes smoke
Phase 04 Monitoring + timestamp alignment
Phase 05A Generic workload intake
Phase 05C marker-free trace preflight on RTX5090 C03
~~~

目前已完成：

~~~text
Phase 05C deployment detector integration validation
Phase 05C C03 same-window hidden-oracle validation
Phase 05D runtime trace feature extraction
Phase 05D RTX5090 frozen-model creation
Phase 05D control-side runtime inference smoke
~~~

目前下一步：

~~~text
Kubernetes Profile Job
  -> worker-side feature/result artifact
  -> shared artifact store
  -> control-side frozen inference
~~~

也就是把已驗證的 component path 收斂回 k3s automation；不再以人工 SCP 作正式 transport。


## Phase 08A：Kubernetes ProfileResult handoff

正式流程不使用人工 SCP。

Profile Job 在 candidate worker 完成 marker-free detector 與 feature extraction 後，寫入：

~~~text
results/<task_id>/<node>/
  marker-free-discovery.json
  runtime-features.json
  profile-result.json
~~~

契約：

~~~text
docs/profile-result-contract.md
schemas/profile-result.schema.json
schemas/runtime-features.schema.json
~~~

正式 transport 預設抽象為 shared-artifact-store；實際 RWX PVC / NFS / MinIO / S3 backend 尚未 freeze，因此 repository 不硬編一個未驗證 storage implementation。

Controller 等待各 candidate 的 ProfileResult 後，在 control side 執行 frozen runtime prediction，再進 power/ranking gate。

## Current component-level runtime inference evidence

RTX5090 C03：

~~~text
marker-free period                 129.988614 ms
same-window hidden oracle          132.1907935 ms
detector APE                       1.67%

frozen runtime model               RTX5090_yolo_trace_only_v1
control-side prediction            109.251714 ms
unprofiled smoke reference         118.080153 ms
smoke comparison APE               7.48%
~~~

7.48% 只用於 deployment smoke，不能當 held-out model accuracy，因為 C03 包含於 final-fit dataset。
