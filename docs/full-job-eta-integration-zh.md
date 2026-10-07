# 在 Pre6G 跨節點流程估計完整訓練時間

`provisional_rank_nodes.py` 現在會在原有穩態訓練時間之外，輸出每個節點的 `full_job_eta`、`total_job_runtime_status` 和 `predicted_total_job_runtime_s`。估時計算為：

`pre6g_experiment decide` 的節點排名也接受結果 JSON 頂層相同的 `full_job_eta` 區塊；其 `predicted_steady_runtime_s` 明確表示原本的外推值，`total_runtime_s` 只在 ETA ready 時才有數值。

```text
startup + warmup_work_units × warmup_work_unit_s
  + (total_work_units - warmup_work_units) × trace_model_work_unit_s
  + validation_runs × validation_s
  + checkpoint_writes × checkpoint_s + finalization
```

穩態時間由原本的 Nsight detector、runtime model 和 semantic binding 提供。其餘階段由應用層 dry-run 觀察或**先前自然完成的完整 Job**校準；不使用 NVTX／`iterations.csv` 作正式輸入，也不把截斷的 120 秒 profile 當成完整 Job ground truth。若有任何必要欄位缺失，`status=incomplete`、`predicted_total_job_runtime_s=null`，並列出 `missing_plan_fields` 或 `missing_phases`。能源排名仍以既有 steady gross energy 選點，不因 ETA 加入而改動。

## 提供資料

在跨節點 config 加入可選欄位：

```yaml
full_job_eta_input: path/to/full-job-eta.json
```

檔案遵循 [schema](../schemas/full-job-eta-input.schema.json)，例如：

```json
{
  "schema_version": "pre6g.full-job-eta-input/v1",
  "work_unit": "training_iteration",
  "plan": {
    "total_work_units": 52448,
    "warmup_work_units": 4,
    "validation_runs": 0,
    "checkpoint_writes": 0
  },
  "nodes": {
    "iccl-s3-251230": {
      "dry_run": {"startup_s": 3.2, "warmup_work_unit_s": 0.11},
      "calibration": {"finalization_s": 0.8},
      "calibration_source_runs": [
        {"run_id": "earlier-complete-run", "completed_naturally": true}
      ]
    }
  }
}
```

上方秒數只是**格式示意**，不得當成 4090 實測校準。正式 `v2-training-only` Job 已設 `val=False`、`save=False`，預期不做每 epoch 的 validation/checkpoint；仍要核對框架是否在最後執行隱含操作，按實際次數填寫，不能直接由參數猜成 0。若在 120 秒 dry run 沒有發生 validation/checkpoint，就要用先前完整 Job 校準或維持 `incomplete`。校準來源須與資料、batch、模型、節點、GPU sharing 狀態及容器計時邊界相符。至少要提供先前自然完成的 run ID；程式無法單靠 ID 驗證真實來源，實驗紀錄仍需人工核對。

執行 `--mode cross-node` 時，設定檔路徑和 SHA256 會寫進 `cross-node-plan.json`；預測階段會再次核對檔案未變，再把內容寫入 `ranking-input.json`。也可以對既有 `ranking-input.json` 加入同名 `full_job_eta` 區塊，直接執行：

```bash
python scripts/provisional_rank_nodes.py --input ranking-input.json --output provisional-ranking.json
```

當 `--mode validation` 使用**固定完整工作量**時，`validation-plan.json` 會保留 ready ETA；如果 planner 重新調整 epochs，會將 ETA 標為 incomplete，避免沿用原工作量的預測。`--mode validation-evaluate` 對成功完成的 trainer container 另外輸出 `full_job_runtime_error_percent`；原有 `runtime_proxy_error_percent` 仍只表示穩態估計對完整 trainer 的 proxy 差異。

目前 repository 尚無每個伺服器節點自然完成的訓練 phase calibration，因此這個介面已可用，但**現有 formal 資料無法得出經驗證的完整 ETA**。需先在目標 4090/5090 收集完整 Job 的各階段時間，並用時間上更早的 run 校準、之後的 run 評估。先前本機 RTX 3060 的單組 YOLO pilot 不能直接套用至伺服器或 shared GPU。
