# 模型與工作量尚未定版時的處理

## 核心原則

Pre6G_experiment 將兩件事分開：

1. **Execution Contract**：如何原封不動地執行使用者的 Job。
2. **Workload Semantic Contract**：平台是否理解這個 workload 的模型參數、work unit 與 total work。

因此：

~~~text
不知道 total work
≠
不能 profiling
~~~

Unknown workload 仍可做 dry-run、Nsight、Netdata/DCGM、period/cycle discovery 與 artifact collection；只是不能把 per-work-unit prediction 外推成可信的 total runtime / total energy。

完整 intake contract 見 [Generic workload intake](workload-intake.md)。

## Runtime model lifecycle

每個 prediction 必須帶足夠的 model identity、trace contract 與 work-unit semantics。Generic contract 建議：

~~~json
{
  "status": "ready",
  "backend": "target-process-cuda-trace",
  "supported_sharing_strategies": ["time-slicing"],
  "capture_policy": "fixed-profile-wall-window",
  "configured_capture_seconds": 120,
  "observed_cuda_active_seconds": 120,
  "wall_timeout_seconds": 300,
  "detector_windows_seconds": [7, 9, 12, 15, 20, 30],
  "model_version": "...",
  "feature_schema_version": "...",
  "work_unit": "training_iteration",
  "predicted_runtime_ms_per_work_unit": 42.1,
  "confidence": 0.93,
  "ood": false
}
~~~

允許的狀態：

- ready：可進入 gate。
- unavailable：profiling 可完成，但不可自動選點。
- schema_mismatch：保留 artifact，等待相容 extractor/model。
- rejected_ood：模型存在，但這筆 workload 不在有效範圍。

Legacy predicted_runtime_ms_per_iteration 暫時保留相容性；新 adapter/model 應使用 work_unit + predicted_runtime_ms_per_work_unit。

模型定版前，平台以 profile-only 運作。

## Workload semantic discovery 優先序

### 1. Explicit canonical metadata

最可信的方式：

~~~yaml
metadata:
  annotations:
    pre6g.io/workload-family: vision-training
    pre6g.io/work-unit: training_iteration
    pre6g.io/total-work-units: "640"
    pre6g.io/workload-parameters-json: >-
      {"model":"yolo26n","batch_size":16,"input_size":640}
~~~

其中：

- workload-family：描述 workload family。
- work-unit：定義 runtime prediction 的語意單位。
- total-work-units：完整任務的 work amount。
- workload-parameters-json：可選的 canonical parameter map。

這些 metadata 不取代 application command/args。

### 2. Registered workload adapter

平台可針對已知 framework 提供 adapter。

第一個 prototype adapter 是 YOLO。它可從原始 argv 與 dataset metadata 抽取：

- model
- epochs
- batch size
- input size
- AMP
- training sample count

在 single-GPU、無 gradient accumulation、無 drop-last 等明示 assumptions 下：

~~~text
steps_per_epoch = ceil(training_samples / effective_batch_size)
total_work_units = epochs × steps_per_epoch
work_unit = training_iteration
~~~

未來可新增：

- HuggingFace / LLM fine-tuning adapter
- FFmpeg adapter
- FAISS adapter
- 其他 application-specific adapter

Adapter 是 semantic layer；不應改寫 execution contract。

### 3. Runtime discovery

即使 static metadata 看起來完整，runtime 仍可能不同。

例如：

- OOM auto batch reduction
- gradient accumulation
- distributed world size
- sampler / drop-last
- dataset filtering
- dynamic sequence length / batching

因此 dry-run 可記錄：

- actual batch size
- dataloader length / steps per epoch
- optimizer steps
- world size
- gradient accumulation
- actual work-unit boundaries

Requested 與 discovered 值應分開保存。

Runtime discovery 可以更新 total-work estimate，但不能偷偷改變 model input schema；是否能作為模型特徵由 model manifest 決定。

### 4. Unknown

若仍無法確認：

- profileable=true
- work.status=unknown
- work.unit=null 或只有已知 unit
- work.total_units=null
- runtime_discovery_required=true

此時允許 profiling，但：

- 不輸出虛假的 total runtime。
- 不輸出虛假的 total energy。
- 若自動 placement 需要 total job cost，保持 profile-only。
- 可保存 per-cycle / per-work-unit evidence，待語意確認後再外推。

## Work unit 抽象

平台不把所有 workload 都稱為 iteration。

例如：

| Workload | work_unit |
|---|---|
| Vision training | training_iteration |
| LLM fine-tuning | optimizer_step |
| LLM inference | generated_token |
| Video encoding | frame |
| FAISS build | vector_insert |

只有 runtime prediction 的 work_unit 與 workload spec 的 work_unit 一致，才能：

~~~text
T_total = runtime_per_work_unit × total_work_units
~~~

Decision layer 會拒絕 work-unit mismatch。

## Backward compatibility

舊 prototype annotation：

~~~yaml
pre6g.io/total-iterations: "640"
~~~

仍可使用，會轉換為：

~~~text
work_unit = training_iteration
total_work_units = 640
~~~

新工作負載應改用：

~~~text
pre6g.io/work-unit
pre6g.io/total-work-units
~~~

## 總 runtime 與 energy

Generic steady-state approximation：

~~~text
T_total_s =
    T_startup_s
  + N_warmup × T_warmup_per_unit_s
  + N_steady × T_predicted_per_work_unit_s
  + T_finalize_s
~~~

Energy：

~~~text
E_observed_window_j = trapezoid_integral(P_predicted(t), t)

P_incremental(t) =
    max(0, P_node_predicted(t) - P_idle_node)

E_total_j ≈
    E_startup_j
  + E_steady_j
  + E_finalize_j
~~~

如果 runtime model 只預測 steady work unit，startup/warmup/finalization 必須分開量測或列為未建模誤差。

## 自動選點 gate

所有條件成立才允許自動選點：

- runtime adapter status=ready
- power adapter status=ready
- runtime work_unit 與 workload spec 相符
- 若要 total-job ranking，total_work_units 已知
- schema versions 相符
- ood=false
- confidence 達門檻
- target-process trace detector 至少觀察三個完整 cycles
- Netdata/DCGM alignment quality 達門檻
- 必要 feature 無缺失
- shared mode 使用 target-process-cuda-trace
- target CUDA process 已辨識、trace 為 hardware trace
- sharing state 完整記錄
- node-bound power model 綁定 candidate node + physical GPU UUID

若 total work 未知，平台可以保留 per-work-unit prediction，但預設不建立 production Job。
