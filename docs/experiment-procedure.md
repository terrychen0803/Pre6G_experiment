# 完整實驗程序

## Phase 0：凍結契約

1. 保存原始 Job、container image digest、dataset manifest/hash。
2. 記錄候選 GPU node 的 sharing strategy、resource name、replicas、physical GPU UUID 與目前 allocation。
3. 凍結 Nsight version、metric set、frequency、feature schema。
4. 保存 runtime/power model manifest；未定版時標記 `profile-only`。
5. 定義 power model 輸出是 node total watts、task incremental watts 或 total joules。

## Phase 1：Cluster preflight

```bash
kubectl get nodes -o wide
kubectl describe node <node>
kubectl get runtimeclass
kubectl get pods -A -o wide
```

逐 node 驗證 CUDA、Nsight environment、CUDA hardware trace、時鐘同步與 object-store connectivity。Shared GPU 是正式 deployment domain；Profile Job 與 production Job 必須使用相同 sharing strategy/resource contract。

## Phase 2：Netdata audit

1. 確認每個 candidate node 都有 Ready child。
2. 逐 node 呼叫 `/api/v1/allmetrics?format=json`。
3. 驗證必要 contexts、units 與非 NaN values。
4. 特別驗證 Top1/Top2 GPU；缺失就停止 energy inference。
5. 保存 audit JSON 作為該次 experiment artifact。

## Phase 3：Job inspection 與 work estimate

```bash
python -m pre6g_experiment inspect --job user-job.yaml
```

輸出 work estimate 的 source、confidence 與缺少的 metadata。禁止只看到 `epochs` 就猜 total iterations。

## Phase 4：建立 Profile Jobs

每個 candidate node 建立獨立名稱：

```text
<source-name>-profile-<node>-<task-id>
```

Profile Job：

- pin 到一個 node。
- request 一個 shared GPU replica，例如 `nvidia.com/gpu.shared: 1`。
- `backoffLimit: 0`。
- `activeDeadlineSeconds: 300`，避免初始化、Nsight finalization 或上傳永久卡住。
- 用 Nsight 直接 launch 原始 command。
- 從 Nsight launch 原始 application 起固定 profiling 120 秒；若 workload 提前自然完成，保存實際 capture 長度，並在匯出後記錄實際 CUDA-active span。
- 完成 report/SQLite 後，在 7/9/12/15/20/30 秒 prefix 離線執行 detector；30/60/90/120 秒區段只作長期 stability/load-drift gate。
- 至少三個完整 cycles；固定 capture 不代表 runtime model 可以改吃 120 秒 aggregate feature。
- collector 驗證 report 並上傳。

線上模式可平行跑所有 candidate nodes；研究評估另做隨機節點順序、每節點至少三次的 sequential repeats。

Profile Job 如何由原始 Job 轉換、固定節點、包裝 command、停止 workload 與回收 artifact，見 [Dry-run Profile Job 部署](dry-run-deployment.md)。

## Phase 5：Feature extraction

1. 從 target process 的 CUDA kernel start timestamp 與 short-name ID 產生 trace runtime input。
2. 用同一份 timestamp metadata 查 Netdata pre/workload windows。
3. 正規化 `°C`、MB、W 與百分比欄名/單位。
4. 逐 timestamp 執行 energy model，保存 P(t)，以梯形積分計算 profiling-window energy，再取得 steady power。
5. 執行 missing、range、sample-count、gap、schema 與 OOD checks。

## Phase 6：Prediction 與 ranking

每個 node 產生 `node-result.json`。若 total iterations 已知：

```text
predicted_total_runtime_s
predicted_total_energy_j
predicted_incremental_energy_j
```

若未知則只報 per-iteration metrics。任何模型未 ready 時，保存結果但停止自動 ranking。

建議 score：

```text
score = predicted_incremental_energy_j × uncertainty_penalty
```

若兩節點差距小於合併 uncertainty interval，結果應是 `tie/insufficient evidence`，而不是強制選最小小數點值。

## Phase 7：Production Job

由 source Job deep-copy：

- 新 Job 名稱。
- 移除 server-managed metadata/status/selector。
- 移除 profiling wrapper、collector、artifact mounts。
- 加入 selected nodeSelector。
- 保留原始 image、command、args、env、security context、resource 與資料 volume。

先執行：

```bash
kubectl apply --dry-run=server -f production-job.yaml
```

經驗證後才正式 apply。

## Phase 8：Ground truth 與評估

正式 Job 同樣查 Netdata，但不啟用 Nsight。保存實際 runtime、平均/積分功率與能量。

至少報告：

- runtime MAPE。
- power MAE/MAPE。
- energy MAPE。
- ranking accuracy。
- best-node hit rate。
- energy regret。
- profiling overhead/cost。
- decision latency。
- rejected/OOD/missing-feature rate。

## Failure policy

| 狀況 | 行為 |
|---|---|
| 某 node Profile Job 失敗 | 排除該 node，保存 failure reason |
| 所有 node 失敗 | 不建立 production Job |
| runtime model unavailable | profile-only，不排名 |
| power feature 缺失 | 不做 energy ranking |
| total iterations unknown | per-iteration result；預設不自動部署 |
| shared GPU target process 無法辨識 | 排除該次 profile，不得改用 device-wide GPU Metrics period |
| sharing state 在 ranking 前顯著漂移 | prediction 失效；重新 profile 或改選下一節點 |
| prediction tie | 使用政策型次要條件或回到 default scheduler |
