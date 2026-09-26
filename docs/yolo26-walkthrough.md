# YOLO26 training 端到端範例

這個範例示範控制流程，不把 synthetic prediction 當作模型結果。

## 1. 輸入 Job

檔案：[user-job.yaml](../examples/yolo26/user-job.yaml)

關鍵內容：

```yaml
annotations:
  pre6g.io/dataset-train-samples: "512"
  pre6g.io/workload-family: "yolo26-training"
...
args:
  - train_yolo26.py
  - --epochs
  - "20"
  - --batch
  - "16"
```

因此在單 GPU、沒有 gradient accumulation、沒有 drop-last 的範例契約下：

```text
steps_per_epoch = ceil(512 / 16) = 32
total_iterations = 20 × 32 = 640
```

實際 Ultralytics image 若會修改 sampler/batch 行為，必須由啟動時 discovery metadata 覆核 640，不能只信靜態估算。

## 2. 檢查 Job

```bash
python -m pre6g_experiment inspect \
  --job examples/yolo26/user-job.yaml
```

預期看到：

```json
{
  "kind": "Job",
  "application_container": "trainer",
  "work": {
    "status": "estimated",
    "total_iterations": 640,
    "source": "yolo epochs * ceil(dataset samples / batch)"
  }
}
```

## 3. Netdata child audit

先由 k3s 找出 child Pod 與所在 node，再逐 node 暴露 API 或使用可路由的 child address：

```bash
kubectl -n netdata get pods -o wide

python scripts/audit_netdata.py \
  --node worker-4090=http://10.0.0.21:19999 \
  --node worker-5090=http://10.0.0.22:19999 \
  --output artifacts/netdata-audit.json
```

若 Top GPU 缺失，這是預期的待處理項，不應填零後繼續正式能耗推論。

## 4. 建立兩個 Profile Jobs

範例 Job 已請求：

```yaml
limits:
  nvidia.com/gpu.shared: "1"
```

若實際 cluster 未使用 `.shared` rename，改成 cluster 真正 advertise 的 resource name，但仍要在 node result 記錄 `strategy=time-slicing`。

對每個 node 使用 Pre6G_profiling builder：

```bash
profile-job-builder build \
  --input examples/yolo26/user-job.yaml \
  --container trainer \
  --name yolo26-profile-worker-4090-run1 \
  --artifact-pvc profile-artifacts \
  --output generated/yolo26-profile-worker-4090.yaml

profile-job-builder build \
  --input examples/yolo26/user-job.yaml \
  --container trainer \
  --name yolo26-profile-worker-5090-run1 \
  --artifact-pvc profile-artifacts \
  --output generated/yolo26-profile-worker-5090.yaml
```

在產生的 YAML 分別加入 hostname nodeSelector；正式多節點版應將此選項加入 builder，而不是靠手改。確認 artifact storage 可跨節點後執行：

```bash
kubectl apply --dry-run=server -f generated/yolo26-profile-worker-4090.yaml
kubectl apply --dry-run=server -f generated/yolo26-profile-worker-5090.yaml
kubectl apply -f generated/yolo26-profile-worker-4090.yaml
kubectl apply -f generated/yolo26-profile-worker-5090.yaml
```

## 5. Runtime 與 power prediction

Shared-mode runtime extractor 使用 target YOLO process 的 CUDA kernel events，不使用 device-wide GPU Metrics。根據現有 high-load RTX5090 實驗：adaptive detector 24/24 coverage、平均 emission 13.54 秒、prefix period mean/P90 APE 1.47%/3.72%；trace runtime model LOOW MAPE 7.42%。完整限制見 [實測依據](evidence/high-load-trace-results.md)。

平台執行時不在 13.54 秒提早停止。兩個節點都從 Nsight launch 原始 application 起固定 profiling 120 秒，wall-clock timeout 300 秒；完成 `.nsys-rep` 與 SQLite 後才找出第一個 target CUDA kernel、離線跑 7/9/12/15/20/30 秒 detector，並用可用的後續 30 秒區段檢查 shared-load drift。這保留既有短 prefix model contract，同時讓不同節點有一致的 dry-run capture policy。具體 Pod/Job 轉換見 [Dry-run Profile Job 部署](dry-run-deployment.md)。

Energy adapter 對 Netdata 的每個 timestamp 將 18 個 features 餵入模型，得到 P(t)，以梯形積分算出 dry-run window energy；ranking 使用 steady power 和預測 runtime 外推正式任務能量。

真正的 collector 應輸出 [node-result schema](../schemas/node-result.schema.json)。目前 runtime model 還在修改時，輸出：

```json
{
  "runtime": {
    "status": "unavailable",
    "reason": "production model not frozen"
  }
}
```

此時流程停在 profile-only。

為了測試後半段，可使用明確標記 synthetic 的範例：

```bash
python -m pre6g_experiment decide \
  --job examples/yolo26/user-job.yaml \
  --results examples/yolo26/synthetic-node-results.json \
  --output generated/yolo26-production-job.yaml \
  --allow-synthetic
```

範例資料中：

```text
worker-4090:
  52 ms/iteration, 330 W, idle 80 W
  incremental energy/iteration = (330-80) × 0.052 = 13.00 J

worker-5090:
  39 ms/iteration, 410 W, idle 95 W
  incremental energy/iteration = (410-95) × 0.039 = 12.285 J
```

在忽略 startup/finalization 的 demo 條件下，640 iterations：

```text
worker-4090 ≈ 8,320 J
worker-5090 ≈ 7,862.4 J
```

因此產生的 production Job 會 pin 到 `worker-5090`。

## 6. 驗證與正式執行

```bash
kubectl apply --dry-run=server \
  -f generated/yolo26-production-job.yaml

kubectl apply \
  -f generated/yolo26-production-job.yaml

kubectl wait --for=condition=complete \
  --timeout=30m job/yolo26-train-energy-selected
```

正式 Job 完成後，用相同 Netdata schema 計算 ground truth，再比較預測的 runtime、power、energy 和節點排名。
