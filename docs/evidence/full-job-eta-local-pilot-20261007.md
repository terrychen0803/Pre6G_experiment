# 本機 RTX 3060 完整訓練呼叫 ETA 小型驗證（2026-10-07）

本次沿用在任何目標執行前凍結的同一份預測 `36.5182588459 s`，其來源為一個先前自然完成的校準執行 (`calibration_101`) 和一個 8 秒觀察的 dry run (`dry_202`)。目標 `target_303`、`target_404`、`target_505` 使用不同 seed，全部自然完成 4 epochs、256 batches。三個目標的結果沒有回填到預測。原始 JSON 位於本機 `yolo26_runtime_prediction/runs/v2/local_yolo_pilot/`，收集／評分程式位於 `analysis/yolo_phase_pilot.py` 與 `analysis/yolo_phase_eta_evaluation.py`。

| 獨立目標 | 實際完整時間 | 分階段 ETA APE | 穩態單步 × 256 APE |
| --- | ---: | ---: | ---: |
| `target_303` | 37.667 s | 3.05% | 25.75% |
| `target_404` | 41.286 s | 11.55% | 32.25% |
| `target_505` | 40.235 s | 9.24% | 30.49% |
| **平均** | — | **7.95%** | **29.50%** |

計算方法：先固定 `N_total=256`、`N_warmup=4`、`N_validation=4`、`N_checkpoint=4`。dry run 量啟動、warm-up 和穩態單步；先前完整 Job 提供 validation、checkpoint、finalization 時間。各階段依次數加總成 36.518 s。目標完整完成後，以 `100 × |預測秒數−實際秒數| / 實際秒數` 算每筆 APE，再取三筆平均。Pre6G 新的 `estimate_full_job_eta()` 使用相同凍結輸入重播，得到同一個 `36.5182588459 s`。

較大的兩筆誤差主要來自穩態單步變慢：dry run 為 0.1093 s，`target_404` 為 0.1239 s，`target_505` 為 0.1217 s。由於它會乘上 252 個穩態 batches，小幅單步偏差會放大成數秒的完整時間誤差。新版 detector 能辨識**觀察期間**的週期漂移，但尚未證明能預測 dry run 結束後的負載變化。

**解讀限制：**這三筆是同一張 RTX 3060 Laptop GPU、同一個小型合成 YOLO 工作量，且 8 秒 dry run 已看過 65/256 batches（約 25.4%）。穩態單步時間來自應用層同步 callback，並未經過 Nsight detector、`yolo-v2-long` 或既有 trace runtime model。校準與實測邊界是 `model.train(...)`，不含 Kubernetes 排隊、Pod 啟動與 init container。此結果只是分階段公式的本機初步估計，不能代表伺服器 4090/5090、2–3 分鐘對數小時工作量，或整條 Pre6G 流程的準確度。

正式驗證需在每個候選伺服器節點先收集自然完成的校準 Job，凍結模型及 phase 參數，再執行新的 120–180 秒 Nsight dry run 與獨立完整 Job。每次都保留 detector 版本、trace model 版本、GPU sharing 狀態、各階段排程、預測時間與完整 trainer/Job 時間，分別報告穩態誤差和完整 ETA 誤差。
