# 多節點同時 dry-run → master 預測 → 排名

`scripts/run_experiment_pipeline.py --mode cross-node` 將原本人工銜接的階段串起來。它適用於目前已驗證的 YOLO26 120 秒 profile fixture；尚不是通用 Kubernetes controller。示範設定在 `examples/yolo26/cross-node-dryrun.yaml`，分別綁定 RTX4090、RTX5090 的 Profile Job 模板、runtime model 和 node-bound power bundle。

流程：

```text
來源 YOLO26 Job + 候選節點設定
  → 同一批提交所有節點的 120 秒 dry-run Jobs
  → 等待全部完成；任一失敗就停止排名
  → 透過共用 pre6g-artifacts PVC 收回 ProfileResult / runtime features / telemetry
  → 核對 task ID、節點、GPU、telemetry 品質與訓練時間窗
  → master 執行 frozen runtime / power 模型
  → 彙整 ranking-input.json 並輸出 provisional-ranking.json
```

提交使用單次 `kubectl create -f dryrun-jobs.yaml`；Kubernetes 會讓各個已綁定不同 hostname 的 Job 並行排程。程式不會「先跑 4090 再跑 5090」。只有全部 Job 成功、產物齊全且 gate 通過才會排名。研究排序保留 power bundle 的 `validation_required` 與 scaler `range_exceeded` 資訊，不聲稱已通過正式 production gate。

## 安全的兩階段操作

在能存取 master/control side 工作目錄的環境安裝 `requirements.txt`、`requirements-power-model.txt`、`kubectl`，並確保 kubeconfig 能存取 `experiments` namespace。worker Job 會從 GitHub clone 專案，因此 `--worker-commit` 必須是**已推送**、含 `scripts/run_command_with_end_marker.py` 的完整 40 字 Git SHA。先在 repo 執行 `git rev-parse HEAD` 取得它。

第一階段只產生計畫，**不需要 kubectl、不建立叢集 Job**：

```bash
python scripts/run_experiment_pipeline.py \
  --mode cross-node \
  --cross-node-config examples/yolo26/cross-node-dryrun.yaml \
  --run-id yolo26-dryrun-001 \
  --worker-commit YOUR_PUSHED_40_CHARACTER_COMMIT_SHA \
  --output-dir generated/yolo26-dryrun-001
```

請先檢查 `cross-node-plan.json`、`dryrun-jobs.yaml`、`collector-pod.yaml`：特別是 kube context、namespace、PVC、節點 hostname、GPU UUID、Netdata/DCGM URL、影像、Nsight mount、權限，以及目前節點是否可承擔同時 profiling。示範 Profile Job 會以 `privileged: true` 存取 Nsight；叢集政策不允許時，需先調整經驗證的模板。執行前也需確認本機 power ONNX dependencies 已安裝，並留意模型目前仍為 research provisional。

第二階段在**同一輸出目錄**明確加上 `--execute` 和正確 kube context，才會真的提交兩個 dry-run Jobs：

```bash
python scripts/run_experiment_pipeline.py \
  --mode cross-node \
  --cross-node-config examples/yolo26/cross-node-dryrun.yaml \
  --run-id yolo26-dryrun-001 \
  --worker-commit YOUR_PUSHED_40_CHARACTER_COMMIT_SHA \
  --output-dir generated/yolo26-dryrun-001 \
  --execute \
  --kube-context YOUR_K3S_CONTEXT
```

程式會核對目前 context 與指定值相同、節點／PVC 存在、Job 名稱沒有重複，並執行 server-side dry-run。然後建立兩個 Profile Jobs、等待完成，建立短暫的唯讀 PVC collector Pod，以 `kubectl cp` 將小型產物收回 master，最後刪除**這次建立的 collector Pod**。Profile Jobs 與 PVC 產物會保留供稽核；程式不會刪除它們，也不會自動啟動完整訓練。若 Job 失敗或超時，保留紀錄並用新的 run ID 重測，不會把部分結果拿去排名。

worker 執行原本的 YOLO 命令，外面只加一層記錄目標程序起訖時間的包裝；master 會用此時間窗裁切約 1 Hz 對齊 telemetry，避免把 Nsight 最後報告處理時間誤算為訓練功率。若缺時間窗、缺樣本、邊界相距超過 2 秒或內部有超過 2 秒空隙，排名會失敗而非補值。這是實驗用的 application-window proxy，尚非經完整驗證的 steady-state power-window 定義。

成功後可查看：

```text
generated/yolo26-dryrun-001/
  cross-node-plan.json
  dryrun-jobs.yaml
  collector-pod.yaml
  dryrun-jobs-status.json
  workload-discovery.json
  artifacts/<node>/profile-result.json
  artifacts/<node>/runtime-features.json
  artifacts/<node>/telemetry/application-window.json
  predictions/<node>/semantic-runtime.json
  predictions/<node>/power-summary.json
  predictions/<node>/power-window-crop.json
  ranking-input.json
  provisional-ranking.json
  cross-node-run-summary.json
  run-summary.json
  logs/
```

`ranking-input.json` 可直接接續現有 YOLO26 完整訓練規劃，讓排名後**所有節點**再跑相同工作量，並在訓練後評估 PDU：

```bash
python scripts/run_experiment_pipeline.py \
  --mode validation \
  --job examples/yolo26/validation-source-job.yaml \
  --ranking-input generated/yolo26-dryrun-001/ranking-input.json \
  --validation-id yolo26-full-001 \
  --target-minutes 40 \
  --output-dir generated/yolo26-full-001
```

後續 Job 部署及每五分鐘平均 PDU 的比較步驟見 [YOLO26 長跑驗證](yolo26-longrun-validation-zh.md)。本功能不會讀取或修改外部 PDU 網站；PDU CSV 仍需在完整訓練後匯出。
