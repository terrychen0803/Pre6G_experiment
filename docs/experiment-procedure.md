# 完整實驗程序

本文件是 Pre6G_experiment 的正式實驗 SOP。每個 phase 都應留下可重現的設定與 evidence；任何 gate 失敗都要保存 failure reason，不可用零值、其他節點模型或未驗證 fallback 繼續自動 ranking。

## Phase 0：凍結契約

1. 保存原始 Job、container image digest、dataset manifest/hash。
2. 記錄候選 GPU node 的 sharing strategy、resource name、replicas、physical GPU UUID 與目前 allocation。
3. 凍結 Nsight version、metric set、frequency、feature schema。
4. 凍結 Netdata/DCGM telemetry schema 與 sampling/alignment policy。
5. 保存 runtime model manifest。
6. 依 node name + physical GPU UUID resolve 對應 power model manifest。
7. power model 尚未定版時標記 profile-only。
8. 定義 power model output semantics；第一版 automatic ranking 固定使用 node-total-power, unit=W。

目前 RTX4090/RTX5090 integration baseline：

~~~
Architecture: x86_64
Nsight Systems: 2026.4.1.191-264138605071v0
Host install root: /opt/nvidia/nsight-systems-cli/2026.4.1
Container mount: /opt/pre6g/nsight
Container CLI: /opt/pre6g/nsight/bin/nsys
RuntimeClass: nvidia
GPU resource: nvidia.com/gpu.shared
~~~

不要依賴 PATH 中的裸 nsys。兩個 worker 的 CUDA toolkit 仍可能把 nsys 解析到舊版，因此 Profile Job 必須使用上述絕對路徑。

## Phase 1：Cluster / clock / GPU preflight

從 control-plane：

~~~bash
kubectl get nodes -o wide
kubectl get runtimeclass
kubectl get pods -A -o wide
~~~

逐 node 驗證：

- node Ready。
- shared GPU resource 存在。
- candidate GPU UUID 與 model registry binding 一致。
- host Nsight 2026 install root 存在。
- artifact-store connectivity。
- system clock synchronized。

Clock check：

~~~bash
timedatectl status | grep -E 'Local time|Universal time|System clock synchronized|NTP service'
~~~

Required：

~~~
System clock synchronized: yes
NTP service: active
~~~

RTX x86_64 Nsight preflight：

1. /opt/nvidia/nsight-systems-cli/2026.4.1 存在。
2. Kubernetes 掛載完整 installation root。
3. Pod 內以 /opt/pre6g/nsight/bin/nsys 執行。
4. nsys --version 為 2026.4.1.191-264138605071v0。
5. --trace=cuda,nvtx,osrt --sample=none --cpuctxsw=none 可產生非空 .nsys-rep。
6. report 可匯出 SQLite 並解析 cuda_api_sum、cuda_gpu_kern_sum、osrt_sum。
7. host perf-based CPU profiling 不可用時，不代表 CUDA trace 失敗；目前 contract 不要求 CPU sampling。

可重現 smoke manifest：

~~~
k8s/nsys2026-rtx-smoke.yaml
~~~

## Phase 2：Monitoring readiness

### 2.1 Netdata child

~~~bash
kubectl -n netdata get pods   -l 'app=netdata,role=child'   -o custom-columns='NAME:.metadata.name,NODE:.spec.nodeName,READY:.status.containerStatuses[0].ready,PHASE:.status.phase,RESTARTS:.status.containerStatuses[0].restartCount'
~~~

Candidate node 必須：

~~~
READY=true
PHASE=Running
~~~

目前部署的 child：

- hostNetwork=true
- bind localhost:19999
- stream 到 Netdata Parent

若 host-native Netdata 已佔用 19999，Kubernetes child 會 CrashLoopBackOff。先保存 native config，停止/disable native service，確認 port free，再刪除 failed child 讓 DaemonSet 重建。詳細流程見 monitoring-preflight.md。

### 2.2 Netdata Parent

確認 candidate hostname 出現在 Parent mirrored_hosts。

Controller 正式查詢 historical telemetry 時走：

~~~
/host/<hostname>/api/v1/...
~~~

Netdata 負責：

- CPU User/System/IOWait
- Load 1/5/15
- Memory Used/Free
- CPU temperature
- Top1/Top2/Top3 CPU

使用 scripts/audit_netdata.py 做 system/CPU readiness audit。

### 2.3 DCGM Exporter

每個 NVIDIA candidate node 必須有 Ready exporter。

Required metrics：

~~~
DCGM_FI_DEV_GPU_UTIL
DCGM_FI_DEV_FB_USED
DCGM_FI_DEV_GPU_TEMP
DCGM_FI_DEV_POWER_USAGE
~~~

對應 canonical features：

~~~
GPU Util%
GPU Mem Used(MB)
GPU Temp(°C)
GPU Power(W)
~~~

Exporter-side cadence：

~~~
DCGM_EXPORTER_INTERVAL=1000
~~~

DaemonSet rollout 後要求：

~~~
DESIRED == CURRENT == READY == AVAILABLE == UPDATED
MISSCHEDULED == 0
~~~

如果 stale Pod 卡在 Ready=Unknown node 且 controller 已要求 deletion，先確認是監控 Pod，再依 monitoring-preflight.md 做 force-delete recovery。

### 2.4 Timestamp alignment gate

正式 DCGM collection 使用 scripts/collect_dcgm.py，Netdata 用同一 absolute window 查 Parent。

Alignment：

~~~
method = nearest timestamp
tolerance <= 750 ms
coverage >= 90%
max Netdata gap <= 2 s
max DCGM gap <= 2 s
~~~

使用 scripts/align_telemetry.py 產生 aligned CSV 與 quality JSON。

2026-09-27 RTX5090 validation：

~~~
DCGM samples = 20
Netdata samples = 26
Aligned = 20
Coverage = 100%
Median |delta| = 284.8 ms
Max |delta| = 490.5 ms
~~~

這是 validation evidence，不是把上述數值當固定 production expectation。

### 2.5 Top GPU process metrics

Top1 GPU% / Top2 GPU% 尚不是 validated core telemetry。

若 node power model manifest 需要這兩欄：

~~~
power.status = schema_mismatch / unavailable
~~~

直到有 validated per-process GPU collector。不得拿 device-wide GPU Util% 代替。

## Phase 3：Job inspection 與 work estimate

~~~bash
python -m pre6g_experiment inspect --job user-job.yaml
~~~

輸出 work estimate 的 source、confidence 與缺少 metadata。禁止只看到 epochs 就猜 total iterations。

YOLO training 若 metadata 完整：

~~~
steps_per_epoch = ceil(training_samples / effective_batch_size)
total_iterations = epochs × steps_per_epoch
~~~

仍必須確認 world size、gradient accumulation、sampler/drop-last 行為。

## Phase 4：建立 Profile Jobs

每個 candidate node 建立獨立名稱：

~~~
<source-name>-profile-<node>-<task-id>
~~~

Profile Job：

- pin 到一個 node。
- request 一個 shared GPU replica，例如 nvidia.com/gpu.shared: 1。
- backoffLimit: 0。
- activeDeadlineSeconds: 300。
- 使用 fixed Nsight 2026 直接 launch 原始 command。
- trace=cuda,nvtx,osrt。
- sample=none。
- cpuctxsw=none。
- 從 application launch 起固定 capture 120 秒。
- workload 提前自然完成時保存實際 capture。
- report finalize 後再做離線 period detection。
- 至少三個完整 cycles。
- 保存 timestamps.json。

建議 timestamp：

~~~
pre_window_start_ns
application_start_ns
profile_start_ns
steady_window_start_ns
steady_window_end_ns
profile_end_ns
application_end_ns
post_window_end_ns
~~~

nsys stats 對同一份 report 的 SQLite lifecycle 必須明確管理。已驗證做法是單次 invocation 要求多個 report。

線上模式可平行跑所有 candidate nodes；研究評估另做隨機節點順序、每節點至少三次 sequential repeats。

## Phase 5：Feature extraction

### 5.1 Runtime features

從 target process CUDA kernel start timestamp 與 short-name ID 建立 runtime model input。

Shared GPU 不得 fallback 到 device-wide GPU Metrics period detection。

### 5.2 System/GPU telemetry

1. 依 timestamps.json 查 Netdata Parent historical window。
2. 同窗口保存 DCGM raw CSV。
3. 正規化 canonical units。
4. nearest-align Netdata/DCGM。
5. 保存 raw + processed + alignment quality。
6. 依該 node power model manifest 的 required_features 驗證完整性。

### 5.3 Power-model routing

依：

~~~
candidate node
+
physical GPU UUID
~~~

resolve models/power/registry.yaml。

Power manifest 必須：

- status=ready
- model_scope=node-bound
- bound node match
- bound GPU UUID match
- feature schema match
- target semantics=node-total-power
- target unit=W
- model artifact/checksum valid
- required features available
- OOD=false
- confidence >= threshold

缺任何一項都不得自動 ranking。

### 5.4 Power prediction

如果模型是 per-timestamp：

~~~
aligned feature vector at t
        |
        v
node-specific power model
        |
        v
P_node_predicted(t)
~~~

再做：

~~~
P_incremental(t) = max(0, P_node_predicted(t) - P_idle_node)
~~~

並以 trapezoid integration 計算 observed-window energy。

如果模型使用 window aggregates，只能依 model manifest 指定 aggregation/preprocessing 執行。

## Phase 6：Prediction 與 ranking

每個 node 產生 node-result.json。

若 total iterations 已知：

~~~
predicted_total_runtime_s
predicted_total_energy_j
predicted_incremental_energy_j
~~~

若未知只報 per-iteration metrics。

Automatic ranking gate：

~~~
runtime model ready
power model ready
node/GPU binding exact match
schema compatible
OOD=false
confidence >= threshold
complete cycles >= 3
clock synchronized
Netdata samples >= 10
max Netdata gap <= 2 s
DCGM samples >= 10
max DCGM gap <= 2 s
alignment coverage >= 90%
max alignment delta <= 750 ms
required features complete
~~~

第一版 ranking 使用：

~~~
incremental_power = max(0, predicted_node_power - node_idle_power)

energy_per_iteration =
    incremental_power × runtime_per_iteration
~~~

如果 power prediction 是 time series，使用積分結果。

Score：

~~~
score = predicted_incremental_energy_j × uncertainty_penalty
~~~

若兩節點差距小於合併 uncertainty interval，結果應是 tie/insufficient evidence，而不是強制選最小小數點值。

如果只有一個 power model ready：

- 可回報該節點 prediction。
- 不應宣稱已完成可信的 cross-node energy ranking。

## Phase 7：Production Job

由 source Job deep-copy：

- 新 Job 名稱。
- 移除 server-managed metadata/status/selector。
- 移除 profiling wrapper、collector、artifact mounts。
- 加入 selected nodeSelector。
- 保留原始 image、command、args、env、security context、resource 與資料 volume。
- 保持和 Profile Job 相同 GPU sharing contract。

先：

~~~bash
kubectl apply --dry-run=server -f production-job.yaml
~~~

驗證後才正式 apply。

## Phase 8：Ground truth 與評估

Production Job 不啟用 Nsight，但仍收：

- runtime ground truth
- Netdata system telemetry
- DCGM GPU telemetry
- node-level measured/derived energy ground truth

至少報告：

- runtime MAPE
- power MAE/MAPE
- energy MAPE
- ranking accuracy
- best-node hit rate
- energy regret
- profiling overhead/cost
- decision latency
- rejected/OOD/missing-feature rate
- model-binding rejection rate
- telemetry alignment coverage/delta distribution

## Failure policy

| 狀況 | 行為 |
|---|---|
| 某 node Profile Job 失敗 | 排除該 node，保存 failure reason |
| 所有 node 失敗 | 不建立 production Job |
| runtime model unavailable | profile-only，不排名 |
| node-bound power model unavailable | 該 node 不進 energy ranking |
| power model node/GPU binding mismatch | reject 該 node，不 fallback 到其他 model |
| power target semantics 不一致 | 不跨 node ranking |
| required telemetry feature 缺失 | 該 power model 不 ready |
| Top GPU 是 model required feature 但 collector 未驗證 | schema_mismatch / unavailable |
| Netdata/DCGM alignment gate 失敗 | 該次 telemetry invalid |
| total iterations unknown | per-iteration result；預設不自動部署 |
| shared GPU target process 無法辨識 | 排除該次 profile，不得用 device-wide period fallback |
| sharing state 在 ranking 前顯著漂移 | prediction 失效；重新 profile |
| prediction tie | 回報 tie/insufficient evidence 或交回 policy/default scheduler |

## 目前階段

Monitoring Phase 已完成 RTX4090/RTX5090 baseline validation。下一個正式 phase：

1. freeze YOLO26 x86_64 image digest。
2. freeze dataset/version/hash。
3. freeze workload parameters。
4. 定義 iteration/work metadata。
5. 做短時間 YOLO + Nsight 2026 compatibility run。
6. 再進正式 120 秒 dry-run。
