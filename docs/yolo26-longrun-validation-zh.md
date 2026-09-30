# YOLO26 長跑跨節點驗證（研究流程）

> **狀態提醒：**目前 `examples/yolo26/validation-source-job.yaml` 已明確歸類為 functional/integration fixture（30 epochs / 960 iterations），不是正式 30–50 分鐘 user workload。本文的 validation planner 可作為後續 ground-truth 長跑工具，但在「原始 full workload 與 120 秒 dry-run workload 分離」完成前，不應把目前 fixture 的 prediction 當成 formal runtime accuracy 結果。

本流程把已完成的 dry-run 預測結果凍結、選出預測節點，再在**所有候選節點**執行同一份完整 YOLO26 訓練，最後比較實際時間與外部 PDU 的整機用電。它用於驗證 runtime / power 排序，不是正式自動調度器，也不能以合成圖像宣稱物件偵測 mAP 準確度。

## 已有與新增的邊界

- `--mode profile`：處理**已取得**的單節點 Nsight SQLite、telemetry 與模型；不建立 K3s Profile Job，也不自動彙整多節點預測。
- 可先使用 [`--mode cross-node`](cross-node-dryrun-zh.md) 同時建立候選節點 dry-run Jobs，回收共用 PVC 產物，在 master 預測並自動產出 `pre6g.provisional-ranking-input/v1`；若沿用先前人工 profile 證據，欄位範例見 `docs/evidence/formal-cross-node-ranking-input.json`。同一份輸入 Job、資料、batch、GPU sharing 與 profiler 設定必須一致。
- `--mode validation`：檢查來源 Job 的工作量與排名輸入一致，凍結研究排序，依選中節點的目標分鐘數計算共同訓練 epochs，產生每節點 Job YAML、預測與部署指令。**不會自動部署。**
- `--mode validation-evaluate`：從完成的 Pod JSON 取 trainer container 起迄時間；可加上每節點 PDU CSV 來積分整機用電、評估選擇是否命中實際最低耗電節點。

目前兩份 power bundle 還是 `validation_required`；此流程使用 `research-provisional-model-output`，**不繞過正式 production gate**。新版 power scaler 超參考範圍只記錄 `range_exceeded`，不屬於模型原生 OOD，也不是獨立拒絕條件。Runtime 的 OOD 判斷仍是另一件事。排序用的是預測 steady gross node energy；PDU 量的是整段 trainer 執行時的 gross node energy，兩者比較只能稱作 *proxy error*，不能當作嚴格的模型準確率。

## 1. 本機建立驗證計畫

先安裝 `requirements.txt`，在專案根目錄執行：

```bash
python scripts/run_experiment_pipeline.py \
  --mode validation \
  --job examples/yolo26/validation-source-job.yaml \
  --ranking-input docs/evidence/formal-cross-node-ranking-input.json \
  --validation-id yolo26-20261001-a \
  --target-minutes 40 \
  --output-dir generated/yolo26-20261001-a
```

`validation-id` 請每次更換，且使用 Kubernetes DNS label 允許的小寫字母、數字與連字號；同一次的 `output-dir` 也應唯一。這份示範來源 Job 沿用已跑過的 `ultralytics/ultralytics:8.4.104`、`yolo26n.yaml`、512 張固定生成的 320×320 合成訓練圖、64 張驗證圖、batch 16、AMP 關閉、單 GPU、`nvidia.com/gpu.shared: 1`。init container 每個節點生成同一資料集，正式 trainer 不掛 Nsight、不抓 1 秒 telemetry、不會因 early stopping 停止（`patience=0`）。映像目前是版本 tag，不是 digest；正式比較前應先解析並固定成同一不可變 digest，確認兩節點 image architecture 與 pull 權限。若要改真實資料集，請替換來源 Job 的 data 路徑與對應 PVC，更新樣本數註解，並重新 dry-run；不可把目前模型預測直接套到不同資料、解析度或 batch。

先前文件曾以舊版 prediction 寫死 1185 epochs／37920 iterations 與 40.0／81.4 分鐘的範例；這些數值已不適用目前模型，已移除以避免和最新 functional run 混淆。`yolo26-dryrun-002` 的最新 functional prediction 約為 RTX4090 46.1338 ms/iteration、RTX5090 47.2247 ms/iteration；對目前 960 iterations fixture 只代表約 44–45 秒 steady compute。正式 30–50 分鐘 workload 必須在下一階段固定其完整工作量，再以相同 work units 做 dry-run prediction 與 full-run ground truth 比較，不應從這個小型 fixture 的 30 epochs 結果直接宣稱正式 runtime。

產物：`workload-discovery.json`、`provisional-ranking.json`、`validation-plan.json`、`validation-jobs.yaml`、`deploy-commands.txt`、`run-summary.json` 和 `logs/`。`validation-plan.json` 明載 frozen selection、每節點預測、資料及功率模型限制。

## 2. 審核後部署與留存時間

在可連線 K3s、且已確認 kube context、namespace、quota、GPU availability、PDU outlet 對應及其他工作負載干擾的機器，進入計畫輸出資料夾，按 `deploy-commands.txt` 執行。關鍵指令如下：

```bash
kubectl config current-context
kubectl apply --dry-run=server -n experiments -f validation-jobs.yaml
kubectl apply -n experiments -f validation-jobs.yaml
kubectl wait -n experiments --for=condition=complete job -l pre6g.io/validation-id=yolo26-20261001-a --timeout=3h
kubectl get pods -n experiments -l pre6g.io/validation-id=yolo26-20261001-a -o json > validation-pods.json
```

這裡 `kubectl apply` 會**真的啟動所有節點的訓練**，不是僅部署預測選中節點。不要對同一 validation ID 重複啟動；若任何 Job 失敗、重試、OOM 或被中止，應保留 Pod log／事件，另開新 ID 重測，而非混入完整跑完的比較。保留 `validation-jobs.yaml`、`validation-pods.json`、每節點 `kubectl logs job/<job-name> -c trainer`、映像 digest、節點 GPU 佔用情況和 NTP 校時狀態。`Pod.status.containerStatuses[].state.terminated.startedAt/finishedAt` 是 trainer 程序的計時邊界，不包含 init container 建資料或排隊；若要評估使用者端總等待時間，另算 Job creation 到 completion。

## 3. 匯入外部 PDU 五分鐘平均值

每個節點對應一個電表 outlet；匯出 CSV 至本機，例如：

```csv
timestamp,power_w
2026-10-01T14:05:00+08:00,351.2
2026-10-01T14:10:00+08:00,367.8
```

上例的 timestamp 必須帶時區。本工具要求你指定這個時間點表示其**前五分鐘平均值**（`--pdu-interval end`）或**後五分鐘平均值**（`start`）；目前只確認數值是五分鐘平均，尚未確認外部平台使用哪個時間戳慣例，請先核實再輸入。匯出範圍必須覆蓋每個 trainer 的完整起迄時間，包含前後邊界；缺點、重複、重疊或空隙會報錯，不補值、不假裝有更高時間解析度。程式按每個五分鐘區間與 trainer 時窗的實際重疊秒數計算 Wh；它不是五分鐘「瞬時讀數」的梯形積分。若網站給的是 kWh／累積電度，不能直接送入 `power_w`，須另做轉換與檢查。

先只評估時間（PDU 尚未匯出時）：

```bash
python scripts/run_experiment_pipeline.py \
  --mode validation-evaluate \
  --validation-plan generated/yolo26-20261001-a/validation-plan.json \
  --pods-json generated/yolo26-20261001-a/validation-pods.json \
  --output-dir generated/yolo26-20261001-a-time-review
```

取得所有候選節點各自的 PDU CSV 後，再於**新輸出資料夾**跑完整評估：

```bash
python scripts/run_experiment_pipeline.py \
  --mode validation-evaluate \
  --validation-plan generated/yolo26-20261001-a/validation-plan.json \
  --pods-json generated/yolo26-20261001-a/validation-pods.json \
  --pdu iccl-s3-251230=pdu-outlet1.csv \
  --pdu mirc516-20250605=pdu-outlet7.csv \
  --pdu-interval end \
  --timestamp-column timestamp \
  --power-column power_w \
  --output-dir generated/yolo26-20261001-a-pdu-review
```

結果在 `validation-result.json`，含每節點實際 trainer 秒數、PDU gross Wh、預測／實際 proxy 差異、預測選擇是否命中最低實測 gross Wh。只要任一節點訓練未成功或 PDU 未齊，`evaluation_complete=false`，不會判定命中。外部負載、GPU 共用、節點待機耗電及五分鐘粒度都會影響測量；建議另取測試前後的 idle baseline 與同時段 GPU occupancy，讓後續能區分 gross 與可歸因於任務的 incremental energy。

## 尚需你確認的外部資料

請提供一小段**去識別化** PDU CSV 或欄位截圖，以及兩個 outlet 與節點的實際對應、timestamp 起點／終點慣例與時區。這樣才能用真實匯出格式做最後一次 parser 驗證。這次準備工作沒有連線 K3s，也沒有啟動 30–50 分鐘訓練。
