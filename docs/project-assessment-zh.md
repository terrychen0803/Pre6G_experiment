# Pre6G 專案流程與實驗盤點

## 平台目的

Pre6G 的目標不是單純量測某張 GPU 的速度，而是接收使用者原始 Kubernetes `batch/v1 Job`，在不要求 workload 加入 iteration marker、callback 或客製 instrumentation 的前提下，建立候選節點當下的 runtime 與 power 證據，最後只在資料品質與模型契約都通過時進行 energy-aware placement。

平台的核心保護原則：

- 使用者的 image、command、args、env、resources 與 volumes 是 execution source of truth。
- Production detector 僅使用 target-process CUDA trace；NVTX 與 `iterations.csv` 只能作事後 audit。
- `execution_cycle` 不會自動被當作 application work unit；必須先通過 workload-specific semantic binding。
- Power feature 缺失、模型 OOD、clock/alignment 品質不足或 node/GPU 綁定不符時，一律 fail closed。
- Profile Job 與 production Job 分離；最終 production Job 不攜帶 profiler。

## 端到端資料流

```text
User Job
  -> execution contract + static workload semantics
  -> total-work discovery (argv/config/mounted dataset)
  -> candidate-node Profile Job
  -> target-process Nsight CUDA trace
  -> marker-free recurring execution-cycle detector
  -> runtime features -> frozen runtime model
  -> Netdata + DCGM alignment -> node-bound power model
  -> semantic runtime aggregation + quality/OOD gates
  -> compare eligible candidate nodes
  -> render unprofiled production Job with selected hostname
  -> production ground-truth comparison
```

## 已完成的實驗與可主張結果

| 項目 | 已完成內容 | 目前可主張範圍 |
|---|---|---|
| Generic Job intake | 保留原 Job execution contract；YOLO adapter 只解讀語意 | 任意 Job 可 profile；目前只有部分 workload 可估 total work |
| Automatic work discovery | YOLO argv、mounted dataset cardinality、epochs、batch 推得 training iterations | 已驗證 512 samples、32 steps/epoch、128 total units 的 smoke |
| Marker-free detector | CUDA kernel timestamp/name recurring-period detection；target PID/context fail-closed | RTX5090 clean/high-load 各 24/24 coverage；只驗證 `yolo-v1` profile |
| Clean RTX5090 detector | Adaptive prefix detector | mean APE 0.33%、P90 0.63%、max 1.15% |
| High-load RTX5090 detector | Shared/high-load trace reference | mean APE 1.47%、P90 3.72%、max 4.46% |
| RTX5090 runtime prediction | Trace-only / combined-condition model reference | high-load MAPE 7.42%；trace + load interactions combined MAPE 6.27% |
| RTX4090 runtime model | 24 個 historical clean workloads、7-feature ridge log-runtime | LOOW MAPE 4.62%；仍是 deployment-smoke，未含 high-load validation |
| Telemetry preflight/alignment | Netdata Parent/Child、DCGM、1 秒 interval、nearest timestamp quality gate | RTX5090 smoke 20/20 aligned、100% coverage、max delta 490.5 ms |
| Kubernetes Profile Job E2E | RTX5090 Job complete、worker-local Nsight、NFS RWX JSON handoff | detector 126.855 ms、confidence 0.730、118 cycles、ProfileResult ready |
| Control-side runtime inference | Frozen RTX5090 model 讀取 shared runtime features | 115.608 ms/execution cycle；128 units steady runtime 14.798 s |
| Power model adapter | 兩個 ONNX/scaler bundle、feature mapping、OOD/missing-feature gate | 只能主張 inference smoke；尚不可主張可比較的 production energy ranking |
| Placement decision | Runtime/power/quality gate、incremental energy score、production Job renderer | synthetic two-node path 已通；真實 automatic placement 尚未完成 |

## 不能過度解讀的結果

- Detector 的 period accuracy 不等於整體 runtime model accuracy。
- `predicted_steady_runtime_s` 不包含 startup、warmup、validation、checkpoint 與 finalization；因此 `predicted_total_job_runtime_s` 仍為 `null`。
- RTX5090 C03 的 7.48% runtime smoke comparison 不是 held-out generalization，因為該 workload 出現在 final-fit dataset。
- Clean 與 high-load 不可互換；clean-only model 轉到 high-load 的 reference MAPE 為 68.70%。
- 兩個 power bundle 雖可推論，仍缺 target semantics、idle power、held-out quality 與完整 node/GPU UUID binding 驗證。

## 目前缺口

1. Kubernetes controller/reconciler：自動建立每個 candidate Profile Job、等待 artifact、重試與清理。
2. Artifact assembler：把 runtime、power、sharing 與 quality artifacts 組成正式多節點 `node-results`。
3. Power production validation：確認 PDU outlet 對應 node-total wall power、idle baseline、physical GPU UUID 與 held-out accuracy。
4. RTX4090 paired high-load dataset 與 OOD policy。
5. Whole-job non-steady overhead model與真正 production run 的 prediction-vs-ground-truth evaluation。
6. 超出 YOLO 的 workload detector profile、semantic binding 與 runtime model。

## 本次新增的整合層

`scripts/run_experiment_pipeline.py` 將 work discovery、marker-free detection、runtime features、ProfileResult、runtime inference、semantic aggregation、telemetry alignment、power inference與 decision renderer 串成一個可續跑流程。每一階段都有 log，最後統一輸出 `run-summary.json`。它整合現有可靠元件，但不假裝取代尚未實作的 Kubernetes controller。

`dashboard/index.html` 是目前證據的靜態使用者介面示意，清楚標出已完成項目與 production blockers；不是即時監控頁。
