# Unified experiment workflow

`scripts/run_experiment_pipeline.py` 將原本分散執行的腳本串成單一、可續跑且有階段紀錄的流程。每次執行都會在指定目錄產生 `run-summary.json` 與各階段 log；失敗時也會保留已完成階段及錯誤原因。

## 用途與安裝

| 入口 | 用途 | 執行條件 |
|---|---|---|
| `scripts/run_experiment_pipeline.py` | 串接現有腳本，產生實驗 artifacts、log、決策報告與 Job YAML | Python 3.10+；profile 模式需提供 trace 與模型 |
| `dashboard/index.html` | 展示文件中的實驗成果與平台介面示意 | 用瀏覽器直接開啟，無需 Python 或伺服器 |
| `docs/project-assessment-zh.md` | 說明平台目的、已完成實驗與剩餘工作 | 閱讀文件 |

整合程式與展示介面彼此獨立。介面數據是固定的文件快照，不會自動讀取 runner 輸出，也沒有提交 Job、啟動實驗或存取 cluster 的功能。

在 repository 根目錄使用 Python 3.10+ 執行：

```bash
python -m pip install -r requirements.txt
python scripts/run_experiment_pipeline.py --help
```

使用 power inference 時另外安裝：

```bash
python -m pip install -r requirements-power-model.txt
```

Runner 會替子程序設定 `PYTHONPATH`，下方 Bash 範例的 `PYTHONPATH=src` 可省略。Windows PowerShell 可直接使用單行命令：

```powershell
python scripts/run_experiment_pipeline.py --mode demo --job examples/yolo26/user-job.yaml --output-dir generated/demo
```

若 Python 安裝提供的是 `python3` 或 `py`，請替換命令中的 `python`。

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

`--sqlite` 必須是已完成 Nsight export 的 SQLite；runner 不會建立 K3s Profile Job 或啟動 Nsight。正式 capture 與 Netdata 歷史資料擷取請參考 [重現檢查表](reproducibility-checklist.md) 與 `scripts/run_profile_with_telemetry.py`。Work discovery 若需讀取 dataset，執行環境須能存取原 Job 指定的 dataset 路徑。

同一批未改動的輸入可加入 `--resume`，已存在所有指定輸出路徑的階段會標為 `skipped-existing`。目前僅檢查路徑是否存在，不驗證檔案內容、前次成功狀態或輸入雜湊；修正輸入、模型或失敗輸出後，請使用新的 `--output-dir` 並完整重跑。未加 `--resume` 時，同目錄的同名成果會被覆寫。

正常完成的退出碼為 `0`，階段失敗為 `2`，後續階段停止。請查看 `run-summary.json` 的 `error` 和 `stages`，以及 `logs/` 中的 stdout/stderr。總結中的 `completed` 表示指定腳本執行成功，不等於模型已通過正式上線驗證。

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

Power bundle 目前仍為 `validation_required`。最新 manifest 已記錄 node / GPU UUID 綁定並確認外部電表量測的 `node-total-power` 語意；正式測試仍存在 OOD 與 held-out quality 缺口。當前排序目標為 gross node-total steady energy：功率乘上預測 steady runtime，不扣 idle power。`--reject-ood` 可能因這些已知 OOD 樣本而失敗，這是預期的 gate 行為。

## 4. 自動節點排序的輸入邊界

`--node-results` 可把已組合好的多節點結果送入現有 decision gate，通過後輸出 `production-job.yaml`。目前 repository 尚無 Kubernetes controller 去自動建立所有 candidate Profile Jobs，也尚無把 runtime、power、quality artifacts 自動組成 `node-results` 的 reconciler；這兩項仍是平台化的下一層工作，而不是本 runner 假裝已完成的功能。

Runner 的排序使用原 Job 的靜態工作量語意，`--node-results` 必須與該 Job 相符；它尚未把 mounted-dataset discovery 的結果傳入 decision CLI。產出的 Job YAML 也不會自動 `kubectl apply`。範例 Job 的 image digest 是 placeholder，僅供本機 demo，不可直接部署。

最新雙節點研究排序使用另一個明確獨立的入口，保留 OOD 標記但允許實驗比較，不會修改 production gate：

```bash
python scripts/provisional_rank_nodes.py --input docs/evidence/formal-cross-node-ranking-input.json --output generated/provisional-ranking.json
```

詳見 [正式跨節點比較紀錄](evidence/formal-cross-node-energy-comparison.md)。這份輸入的 `candidates` schema 與 runner 的 `--node-results` schema 不同，不能混用。

## 成果介面

開啟 `dashboard/index.html` 可查看目前流程、實驗證據與 production blockers 的靜態介面示意。內容來自 repository 中已紀錄的 evidence snapshot，不是即時 cluster dashboard。

GitHub 的 HTML 檔案頁只顯示原始碼；請 clone 或下載 repository 後，用瀏覽器開啟本機檔案。GitHub Pages 尚未設定。介面內容與資料來源見 [dashboard 使用說明](../dashboard/README.md)。
