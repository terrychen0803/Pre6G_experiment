# 多節點同時 dry-run → master 預測 → 排名

目前 4090／5090 的 preflight 與 smoke 驗證紀錄見 [cross-node preflight/smoke evidence](evidence/cross-node-preflight-smoke-20261001.md)。這些檢查尚未包含 120 秒 profiling、預測排名或長時間訓練。

`scripts/run_experiment_pipeline.py --mode cross-node` 將原本人工銜接的階段串起來。它適用於目前已驗證的 YOLO26 120 秒 profile fixture；尚不是通用 Kubernetes controller。示範設定在 `examples/yolo26/cross-node-dryrun.yaml`，分別綁定 RTX4090、RTX5090 的 Profile Job 模板、runtime model 和 node-bound power bundle。

Collector 已固定到 control-side master。示範設定使用 `collector_node: icclz2`，產生的 `collector-pod.yaml` 會帶入 `nodeSelector: {kubernetes.io/hostname: icclz2}`，因此 collector 不會再被 Kubernetes 任意排到 GX10、RTX3090 或其他非本流程節點。若 master 的 Kubernetes node name 不是 `icclz2`，必須先把 `collector_node` 改成 `kubectl get nodes` 顯示的實際 master hostname；不要為了 collector 額外把 GX10 納入候選節點。

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

提交使用單次 `kubectl create -f dryrun-jobs-resolved.yaml`；Kubernetes 會讓各個已綁定不同 hostname 的 Job 並行排程。程式不會「先跑 4090 再跑 5090」。只有全部 Job 成功、產物齊全且 gate 通過才會排名。研究排序保留 power bundle 的 `validation_required` 與 scaler `range_exceeded` 資訊，不聲稱已通過正式 production gate。

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

請先檢查 `cross-node-plan.json`、`dryrun-jobs.yaml`、`collector-pod.yaml`：特別是 kube context、namespace、PVC、候選節點 hostname、`collector_node`、GPU UUID、Netdata URL、影像、Nsight mount、權限，以及目前節點是否可承擔同時 profiling。Master/collector node 必須 Ready、無 DiskPressure、能掛載 `pre6g-artifacts` 的 RWX NFS，且目前 collector manifest 沒有 tolerations，因此 master 若有 `NoSchedule`／`NoExecute` taint，preflight 會直接失敗並要求先明確處理叢集排程政策。計畫檔中的 `DCGM_ENDPOINT_UNRESOLVED` 是刻意保留的安全標記，**不可直接提交 `dryrun-jobs.yaml`**；正式執行時才會依 `dcgm_exporter` 設定尋找每個節點上 Running/Ready 的 exporter Pod，將當時的 Pod IP 寫入 `dryrun-jobs-resolved.yaml`，並以此檔做 server-side dry-run 與提交。示範 Profile Job 會以 `privileged: true` 存取 Nsight；叢集政策不允許時，需先調整經驗證的模板。執行前也需確認本機 power ONNX dependencies 已安裝，並留意模型目前仍為 research provisional。

第二階段可先在**同一輸出目錄**執行唯讀叢集檢查；它會解析 DCGM Pod IP，產出 `dryrun-jobs-resolved.yaml` 與 `dcgm-endpoints.json`，並做 Kubernetes server-side dry-run，**不建立任何叢集資源**：

```bash
python scripts/run_experiment_pipeline.py \
  --mode cross-node \
  --cross-node-config examples/yolo26/cross-node-dryrun.yaml \
  --run-id yolo26-dryrun-001 \
  --worker-commit YOUR_PUSHED_40_CHARACTER_COMMIT_SHA \
  --output-dir generated/yolo26-dryrun-001 \
  --preflight-only \
  --kube-context YOUR_K3S_CONTEXT
```

取得許可後，可用獨立 run ID 執行短暫的實機 smoke test。這會先重新執行 preflight，接著同時建立 4090／5090 各一個檢查 Pod（每個 Pod 請求 `nvidia.com/gpu.shared: 1`，最長 300 秒），驗證 Pod 內 GPU UUID、Nsight CLI、GitHub、指定 GPU 的 DCGM 指標、Netdata API，以及雙向 NFS/PVC 標記讀取；**不會執行 YOLO 訓練**：

```bash
python scripts/run_experiment_pipeline.py \
  --mode cross-node \
  --cross-node-config examples/yolo26/cross-node-dryrun.yaml \
  --run-id yolo26-smoke-001 \
  --worker-commit YOUR_PUSHED_40_CHARACTER_COMMIT_SHA \
  --output-dir generated/yolo26-smoke-001 \
  --smoke-only \
  --kube-context YOUR_K3S_CONTEXT
```

程式會保存 `smoke-pods.yaml`、各 Pod 的 log，成功時另存 `smoke-result.json`，並在成功或失敗時嘗試刪除**僅本次建立**的檢查 Pod；若刪除失敗，整體 smoke test 會報錯。成功後 NFS 上會保留兩個小型 `smoke/<run-id>/<node>/marker.json` 供稽核，請再從 master 的 `/srv/pre6g-artifacts/smoke/<run-id>/` 做唯讀回查。這個測試只證明連線、掛載與基本工具可用，**不等於**完整 Nsight trace、長時間訓練或模型品質驗證。

等 smoke test 與 master 回查通過後，才在**新 run ID／輸出目錄**以 `--execute` 提交兩個 dry-run Jobs；程式會**重新**解析 DCGM Pod IP 與重做 preflight，避免使用先前快照：

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

程式會核對目前 context 與指定值相同、namespace 與 `nvidia` RuntimeClass 存在、PVC 綁定的 PV 確實為 NFS/RWX、候選節點 Ready 且沒有 DiskPressure 並提供 `nvidia.com/gpu.shared`、master/collector node Ready 且無 DiskPressure/阻擋排程的 taint、每節點恰有一個 Ready 的 DCGM exporter Pod、Job 名稱沒有重複，並執行 server-side dry-run。然後建立兩個 Profile Jobs、等待完成，在設定的 master node 建立短暫的唯讀 PVC collector Pod，以 `kubectl cp` 將小型產物收回 master 工作目錄，最後刪除**這次建立的 collector Pod**。Profile Jobs 與 PVC 產物會保留供稽核；程式不會刪除它們，也不會自動啟動完整訓練。若 Job 失敗或超時，保留紀錄並用新的 run ID 重測，不會把部分結果拿去排名。

這些 API 檢查**不能代替實機 smoke test**：執行前仍須確認 NFS 跨候選節點寫入，以及 **master 上 collector Pod 對同一 PVC 的讀取能力**（含 NFS client、網路路由、export 權限與 root-squash）、Nsight host path 與 GPU UUID、Pod 到 DCGM/Netdata 的連線、worker GitHub 連線、映像可拉取與磁碟餘量。`dcgm_exporter` 的 namespace／label selector 如與現場不同，應先修改設定；不要改回固定 Pod IP。RTX3090 目前不在候選清單。

worker 執行原本的 YOLO 命令，外面只加一層記錄目標程序起訖時間的包裝；master 會用此時間窗裁切約 1 Hz 對齊 telemetry，避免把 Nsight 最後報告處理時間誤算為訓練功率。若缺時間窗、缺樣本、邊界相距超過 2 秒或內部有超過 2 秒空隙，排名會失敗而非補值。這是實驗用的 application-window proxy，尚非經完整驗證的 steady-state power-window 定義。

成功後可查看：

```text
generated/yolo26-dryrun-001/
  cross-node-plan.json
  dryrun-jobs.yaml
  dryrun-jobs-resolved.yaml
  dcgm-endpoints.json
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

