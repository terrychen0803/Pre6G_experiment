# High-load target-process trace 實測依據

本文件整理來源 workspace `analysis/high_load_01` 的 frozen outputs，作為 shared-GPU 流程的工程依據。

## 實驗資料

- Device：RTX5090。
- Workloads：24 個 YOLO26 configurations。
- Condition：兩個背景 GPU processes 造成的 high-load contention。
- Profiles：24 個 `nsys_trace_01`。
- 每個 profile 的 target trace inventory：一個 CUDA process、一個 CUDA context，全部使用 CUDA hardware tracing。

## Trace-only period detector

Detector 只讀：

- CUDA kernel start timestamps。
- CUDA kernel short-name IDs。

Detector 禁止使用 NVTX、`iterations.csv`、workload ID/config、baseline runtime 和 GPU Metrics。NVTX 只在 detection 完成後作 hidden audit label。

Adaptive deployment policy 結果：

| Metric | Result |
|---|---:|
| Coverage | 24/24（100%） |
| Mean emission horizon | 13.54 s |
| Prefix mean APE | 1.47% |
| Prefix median APE | 1.14% |
| Prefix P90 APE | 3.72% |
| Prefix max APE | 4.46% |
| Prefix within 10% | 100% |

固定 horizon 的重要結果：

| Horizon | Accepted | Prefix mean APE | Prefix P90 APE |
|---:|---:|---:|---:|
| 5 s | 12/24 | 4.63% | 13.21% |
| 9 s | 22/24 | 1.73% | 4.04% |
| 12 s | 24/24 | 2.31% | 3.16% |
| 15 s | 24/24 | 1.48% | 3.03% |

因此 5 秒可以作早期嘗試，但不能作全 workload 的固定完成條件。推薦 adaptive 7/9/12/15/20/30 秒策略，或先以 15 秒作 MVP default。

`full-run APE` 包含 profiling 後段 contention/load drift，不等同 detector 在 emission window 的錯誤；deployment gate 應以 prefix/two-window stability 為主。

## Trace-only runtime prediction

特徵：

```text
log_detected_period_ms
log_kernel_event_rate_hz
log_unique_kernel_count
log_median_kernel_duration_us
log_mean_kernel_duration_us
log_kernel_busy_fraction
anchor_robust_cv
```

Nested leave-one-workload-out 結果：

| Metric | Result |
|---|---:|
| Runtime MAPE | 7.42% |
| Median APE | 6.87% |
| P90 APE | 12.31% |
| Max APE | 17.50% |
| Within 10% | 79.2% |
| Overhead MAE | 8.08 percentage points |

五次 unprofiled baseline repeats 本身的 workload CV mean/median/max 為 6.67%/5.96%/13.84%，因此 runtime error 必須連同 high-load label variability 解讀。

## Unified clean/high-load 結果

同一 frozen trace schema 同時評估 clean/high-load 時：

- `trace_plus_load_interactions`：both-condition MAPE 6.27%。
- `trace_regime_moe`：both-condition MAPE 7.53%，condition classification accuracy 97.9%。
- strict clean-only model → high-load：MAPE 68.70%。

結論不是「任意既有 trace model 都能直接處理 sharing」，而是：target-process trace 解決訊號歸屬問題；production model 仍須用涵蓋實際 sharing/load regimes 的資料訓練與驗證。

## 對平台流程的約束

1. Shared GPU 不再是 automatic rejection。
2. Shared mode 必須使用 `target-process-cuda-trace` backend。
3. 禁止 shared mode 靜默回退到 device-wide GPU Metrics period detector。
4. Node result 保存 process/context identity、hardware-trace flag、emission horizon 和 sharing state。
5. Runtime model manifest 必須宣告支援的 sharing strategies/load regimes。
6. 5 秒未通過 confidence gate 時延長 capture，而不是強制輸出。
