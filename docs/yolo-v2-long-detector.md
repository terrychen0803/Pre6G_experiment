# `yolo-v2-long`：新版長 trace detector

`yolo-v2-long` 是從 `yolo26_runtime_prediction/analysis/high_load_01/long_trace_period_detector.py` 移植的 **120–180 秒 Nsight trace 週期軌跡診斷**。每個完整 15 秒窗獨立使用既有 `yolo-v1` 的 CUDA kernel anchor 偵測，先跳過前 10 秒；輸出接受窗比例、早晚漂移、窗間 CV、半／雙週期衝突與觀察期間的諧波平均週期。預設至少 6 個完整窗、80% 窗被接受且最新窗可靠；漂移或 CV 超過 10% 時回報 `unreliable_for_extrapolation`，不輸出推薦週期。

正式長工作量設定 `examples/yolo26/formal-cross-node-dryrun.yaml` 已標記 `long_detector_profile: yolo-v2-long`。RTX4090／RTX5090 的 120 秒 Profile Job 會在原有 `yolo-v1` 後**平行**產生 `detector-v2/trajectory.json` 與 `detector-v2/windows.csv`，跨節點 collector 會回收這兩個產物。`trajectory.json` 明示 `detector_profile=yolo-v2-long`、`base_window_detector_profile=yolo-v1`、`used_as_runtime_model_input=false`。此 detector 只讀目標 CUDA process/context 的 kernel events；NVTX、`iterations.csv`、callback 和訓練參數都不是輸入。

Master 會把 v2 摘要附到 `ranking-input.json` 與 `provisional-ranking.json` 的 `long_detector_diagnostic`，方便後續依穩定狀態分層評估 ETA。它不參與目前的 runtime 或能源公式。

執行跨節點流程時，`--worker-commit` 必須是包含 v2 腳本與模組的完整 Git SHA；`prepare` 會在部署前檢查，避免節點 checkout 舊提交後找不到新版 detector。

目前 RTX4090 與 RTX5090 的 frozen runtime model manifest 均綁定 `detector_profile=yolo-v1`，因此**不以 v2 的週期覆蓋 v1 runtime features**。現有模型仍使用原本的 `marker-free-discovery.json` 與 `runtime-features.json`。若未來要讓 v2 決定穩態時間，需要在 120–180 秒 trace 上建立新訓練資料、重訓或驗證 frozen 模型，並修改 model manifest 的 profile binding；不能只改 CLI 參數。

本機測試包含無 NVTX 的 125 秒合成 trace（偵測約 100 ms 週期並通過穩定門檻）和前後週期漂移的合成 trace（拒絕外推）。一份真實 RTX5090 C03 trace 約 18 秒，新版回報 `too_few_complete_windows`，所以它不是 120 秒效果證據。要量測 v2 對正式模型／ETA 的影響，仍需伺服器產生新的長 trace 與自然完成的獨立 Job。
