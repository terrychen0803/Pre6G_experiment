# 模型與工作量尚未定版時的處理

## Runtime model lifecycle

每個 prediction 必須帶：

```json
{
  "status": "ready",
  "backend": "target-process-cuda-trace",
  "supported_sharing_strategies": ["time-slicing"],
  "model_version": "...",
  "feature_schema_version": "...",
  "predicted_runtime_ms_per_iteration": 42.1,
  "confidence": 0.93,
  "ood": false
}
```

允許的狀態：

- `ready`：可進入 gate。
- `unavailable`：profiling 可完成，但不可自動選點。
- `schema_mismatch`：保留 artifact，等待相容 extractor/model。
- `rejected_ood`：模型存在，但這筆 workload 不在有效範圍。

模型定版前，平台以 `profile-only` 運作。這讓 k3s、Netdata、artifact、time alignment 與 Nsight pipeline 可以先驗證，不必等待最終模型。

## 工作量推導優先序

### 1. 使用者／平台明示 annotation

最可信的方式：

```yaml
metadata:
  annotations:
    pre6g.io/total-iterations: "640"
```

### 2. 從 Job args 與資料集 metadata 推導

對 YOLO training：

```text
steps_per_epoch = ceil(training_samples / effective_batch_size)
total_iterations = epochs × steps_per_epoch
```

前提是 YAML 或可信任的 dataset manifest 同時提供：

- `epochs`
- `batch`
- training sample count
- distributed world size / gradient accumulation 規則
- sampler/drop-last 行為

只有 `epochs` 和 `batch`，沒有 dataset size 時，不能得出 total iterations。

### 3. Runtime discovery

若 image entrypoint 在啟動時才能解析 dataset，wrapper 可在 dry-run 記錄：

- dataloader length / steps per epoch
- global batch size
- world size
- gradient accumulation
- observed iteration boundaries

這個 discovery metadata 可以加入能量外推，但不能當作 runtime model feature，除非模型契約明確允許。

### 4. Unknown

仍無法確認時：

- 報告 `energy_j_per_iteration`。
- 可以比較每 iteration 的相對排名。
- 不輸出虛假的 total energy。
- 若 startup/finalization 在節點間差異不可忽略，不自動建立 production Job。

## 總 runtime 與 energy

```text
T_total_s = T_startup_s
          + N_warmup × T_warmup_iter_s
          + N_steady × T_predicted_iter_s
          + T_finalize_s

E_observed_window_j = trapezoid_integral(P_predicted(t), t)

E_total_j ≈ E_startup_j + P_steady_w × T_steady_s + E_finalize_j

E_incremental_j = max(0, P_predicted_w - P_idle_w) × T_total_s
```

若模型只預測 steady iteration runtime，startup/warmup/finalization 必須分開量測或報告為未建模誤差。

## 自動選點 gate

所有條件成立才允許自動選點：

- runtime adapter `status=ready`
- power adapter `status=ready`
- schema versions 相符
- `ood=false`
- confidence 達門檻
- target-process trace detector 至少觀察三個完整 cycles
- Netdata window samples 達門檻且無過大 gap
- 必要 feature 無缺失
- shared mode 使用 `target-process-cuda-trace`，且 model manifest 明確列出該 sharing strategy
- target CUDA process 已辨識、trace 為 hardware trace
- sharing state（strategy、replicas、physical GPU、co-tenants）完整記錄
- 可計算 total work，或使用者明確允許 per-iteration ranking
