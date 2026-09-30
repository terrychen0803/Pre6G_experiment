# YOLO26 正式 30–50 分鐘 dry-run 預測實驗計畫

狀態：**planned / 尚未部署**。本文件刻意與目前 functional/integration fixture 分離；在 functional branch 完成 clean unattended run 並 merge 到 `main` 後，再以獨立 branch 實作正式 workload。

## 研究目標

正式實驗要驗證的是：對一個**原始完整執行時間約 30–50 分鐘**的 YOLO26 training workload，只執行前 120 秒 dry-run profiling，利用 profiling 特徵預測原始完整 workload 的 runtime 與 energy，最後在所有候選節點完整執行同一工作量取得 ground truth。

```text
Original full workload (fixed before formal evaluation)
  → discover original total work units
  → run only first 120 s under Nsight + Netdata + DCGM
  → predict runtime per training iteration
  → multiply by ORIGINAL full-workload iterations
  → predict steady runtime / energy
  → rank nodes
  → run the SAME complete workload on every candidate node
  → compare actual trainer runtime and external PDU energy
```

## 與 functional fixture 的必要分離

目前 functional fixture 固定為 512 train samples、batch 16、30 epochs、960 iterations，用途只是在短時間內驗證 cross-node 程式資料流。Formal workload 不得沿用「30 epochs 是完整工作量」的語意。

正式實作時需要把以下三個概念分開：

1. **full workload definition**：使用者原始完整 Job，work units 在 dry-run 前即固定。
2. **dry-run capture policy**：只限制 profiling wall-clock，例如 Nsight `--duration=120 --kill=sigterm`；不得把完整 Job 的 epochs 改小來代表 dry-run。
3. **ground-truth full run**：不掛 Nsight、使用相同 full workload work units，在每個 candidate node 完整跑完。

## Formal merge gate

正式實驗 branch 至少要通過：

- full workload 與 dry-run template 的 model/data/imgsz/batch/AMP 等 workload identity 一致；
- dry-run 只因 120 秒 capture policy 被中止，full-workload total units 仍來自原始 Job；
- prediction 明確區分 steady compute runtime 與 whole-job runtime；
- RTX4090／RTX5090 使用相同完整 work units；
- 收集實際 trainer start/finish 作 runtime ground truth；
- PDU 時間窗完整覆蓋 trainer window 後才能計算 energy ground truth；
- 報告 prediction error，而不是只報 node ranking；
- 不把 functional-validation 的 960-iteration 結果混入 formal accuracy result。

## 仍待固定的實驗參數

正式 workload 的 exact epochs／dataset size 應在 formal branch 中固定，並在實驗前記錄選擇依據。目標是讓完整 workload 落在約 30–50 分鐘，但不能在看到最終 ground truth 後再調整工作量；若需要 pilot sizing，pilot 與 formal evaluation 必須使用不同 run IDs 並在結果中分開標示。
