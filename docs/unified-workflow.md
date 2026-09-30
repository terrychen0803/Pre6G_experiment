# Unified experiment workflow

`scripts/run_experiment_pipeline.py` 將原本分散執行的腳本串成單一、有階段紀錄的流程。Demo / profile 可用 `--resume` 跳過現存輸出；validation / validation-evaluate 必須使用新輸出目錄。每次執行都會在指定目錄產生 `run-summary.json` 與各階段 log；失敗時也會保留已完成階段及錯誤原因。

## 用途與安裝

| 入口 | 用途 | 執行條件 |
|---|---|---|
| `scripts/run_experiment_pipeline.py` | 串接單節點分析、YOLO26 跨節點 dry-run 協調、全節點長跑規劃與 PDU 評估 | Python 3.10+；cross-node 執行需 kubectl / kubeconfig / power 模型依賴 |
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
--power-bundle models/power/bundles/pdu1-outlet7-20260416-20260612
```

也可直接提供兩份原始 CSV，runner 會先執行 timestamp alignment 再進行 power inference：

```bash
--netdata-telemetry netdata.csv \
--dcgm-telemetry dcgm.csv \
--power-bundle models/power/bundles/pdu1-outlet7-20260416-20260612
```

Power bundle 目前仍為 `validation_required`。最新 manifest 已記錄 node / GPU UUID 綁定並確認外部電表量測的 `node-total-power` 語意；正式測試仍缺 held-out quality 與缺值政策驗證。當前排序目標為 gross node-total steady energy：功率乘上預測 steady runtime，不扣 idle power。Power scaler 超出參考範圍只記錄 `range_exceeded` / `range_warnings`，不截斷也不以此拒絕推論；runtime OOD 是另一項獨立檢查。舊版 `--reject-ood` 已移除。

## 4. 自動節點排序的輸入邊界

`--node-results` 可把已組合好的多節點結果送入現有嚴格 decision gate，通過後輸出 `production-job.yaml`。目前尚無常駐 Kubernetes controller 或通用的 `node-results` reconciler；新增的 `--mode cross-node` 是明確執行的 YOLO26 實驗協調命令，會為此 fixture 建立所有 candidate Profile Jobs，回收產物並組成**研究用** `ranking-input.json`，不冒充嚴格 production gate 的 `node-results`。

Runner 的正式排序使用原 Job 的靜態工作量語意，`--node-results` 必須與該 Job 相符；它尚未把 mounted-dataset discovery 的結果傳入 decision CLI。產出的 Job YAML 也不會自動 `kubectl apply`。`examples/yolo26/user-job.yaml` 的 image digest 是 placeholder，僅供本機 demo，不可直接部署；長跑驗證另有實驗來源 Job。

最新雙節點研究排序使用另一個明確獨立的入口，保留 OOD 標記但允許實驗比較，不會修改 production gate：

```bash
python scripts/provisional_rank_nodes.py --input docs/evidence/formal-cross-node-ranking-input.json --output generated/provisional-ranking.json
```

詳見 [正式跨節點比較紀錄](evidence/formal-cross-node-energy-comparison.md)。這份輸入的 `candidates` schema 與 runner 的 `--node-results` schema 不同，不能混用。

`--mode cross-node` 會先以 plan-only 模式產生多節點 dry-run YAML；只有明確指定 `--execute --kube-context` 才會同時提交 Jobs、回收共用 PVC 產物並在 master 預測排名。詳見 [跨節點 dry-run 協調說明](cross-node-dryrun-zh.md)。

## 5. YOLO26 長跑驗證與 PDU

`--mode validation` 使用已凍結的跨節點 dry-run 排名輸入，產生每節點完整訓練 Job 與明確 `kubectl apply` 指令，但不自動部署。`--mode validation-evaluate` 匯入完成的 Pod JSON 及可選的每節點五分鐘平均 PDU CSV，計算實測執行時間與整機 gross Wh。完整用途、指令、限制與例子見 [YOLO26 長跑跨節點驗證](yolo26-longrun-validation-zh.md)。

## 成果介面

開啟 `dashboard/index.html` 可查看目前流程、實驗證據與 production blockers 的靜態介面示意。內容來自 repository 中已紀錄的 evidence snapshot，不是即時 cluster dashboard。

線上可直接開啟 [客戶任務工作台](https://terrychen0803.github.io/Pre6G_experiment/) 或 [實驗成果總覽](https://terrychen0803.github.io/Pre6G_experiment/dashboard/index.html)。GitHub Pages 自動發布靜態介面，與 runner 分開。也可下載 repository 後用瀏覽器離線開啟。介面内容、資料來源及部署說明見 [dashboard 使用說明](../dashboard/README.md)。
