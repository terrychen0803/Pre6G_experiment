# Unified experiment workflow

`scripts/run_experiment_pipeline.py` 將原本分散執行的腳本串成單一、可續跑且有階段紀錄的流程。每次執行都會在指定目錄產生 `run-summary.json` 與各階段 log；失敗時也會保留已完成階段及錯誤原因。

## 1. 本機 synthetic demo

這條路徑用現有的 synthetic node results 驗證工作量語意、節點 gate、energy score 排序與 production Job renderer。它不代表真實模型準確度。

```bash
PYTHONPATH=src python scripts/run_experiment_pipeline.py \
  --mode demo \
  --job examples/yolo26/user-job.yaml \
  --output-dir generated/demo
```

主要輸出：

```text
generated/demo/
  workload-discovery.json
  production-job.yaml
  decision-report.json
  run-summary.json
  logs/
```

## 2. 真實 marker-free profile

這條路徑接收 worker 產生的 Nsight SQLite，依序完成 execution-cycle detection、runtime features、ProfileResult 封裝、frozen model inference，以及 steady-work runtime aggregation。

```bash
PYTHONPATH=src python scripts/run_experiment_pipeline.py \
  --mode profile \
  --job source-job.yaml \
  --sqlite profile.sqlite \
  --node mirc516-20250605 \
  --device-id RTX5090 \
  --runtime-model models/runtime/RTX5090_yolo_trace_only_v1.json \
  --task-id my-profile-001 \
  --workload-id my-yolo-run \
  --output-dir generated/my-profile
```

若中途失敗，修正輸入後加入 `--resume`，已存在完整輸出的階段會標為 `skipped-existing`。

## 3. 選用 power inference

若已有通過 `align_telemetry.py` 的資料，可在 profile command 加入：

```bash
--aligned-telemetry aligned.csv \
--alignment-quality alignment-quality.json \
--power-bundle models/power/bundles/pdu1-outlet7-20260416-20260612 \
--reject-ood
```

也可直接提供兩份原始 CSV，runner 會先執行 timestamp alignment 再進行 power inference：

```bash
--netdata-telemetry netdata.csv \
--dcgm-telemetry dcgm.csv \
--power-bundle models/power/bundles/pdu1-outlet7-20260416-20260612 \
--reject-ood
```

Power bundle 目前仍為 `validation_required`。流程可以產生 power smoke 結果，但在 node / GPU UUID、target semantics、idle power 與 held-out quality 未確認前，不應用它宣稱 production energy ranking。

## 4. 自動節點排序的輸入邊界

`--node-results` 可把已組合好的多節點結果送入現有 decision gate，通過後輸出 `production-job.yaml`。目前 repository 尚無 Kubernetes controller 去自動建立所有 candidate Profile Jobs，也尚無把 runtime、power、quality artifacts 自動組成 `node-results` 的 reconciler；這兩項仍是平台化的下一層工作，而不是本 runner 假裝已完成的功能。

## 成果介面

開啟 `dashboard/index.html` 可查看目前流程、實驗證據與 production blockers 的靜態介面示意。內容來自 repository 中已紀錄的 evidence snapshot，不是即時 cluster dashboard。
