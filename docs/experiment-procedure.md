# 完整實驗程序

## Phase 0：凍結契約

1. 保存原始 Job、container image digest、dataset manifest/hash。
2. 記錄候選 GPU node 與 `nvidia.com/gpu` capacity。
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

逐 node 驗證 CUDA、Nsight environment、GPU metrics permission、時鐘同步與 object-store connectivity。正式比較使用 `nvidia.com/gpu: 1`，不使用 shared GPU 結果宣稱性能或能耗優劣。

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
- request 一張獨占 GPU。
- `backoffLimit: 0`。
- 設定 bounded timeout。
- 用 Nsight 直接 launch 原始 command。
- 預熱後收集至少三個完整 cycles，建議 10～15 秒。
- collector 驗證 report 並上傳。

線上模式可平行跑所有 candidate nodes；研究評估另做隨機節點順序、每節點至少三次的 sequential repeats。

## Phase 5：Feature extraction

1. Marker-free Nsight extractor 產生 runtime model input。
2. 用同一份 timestamp metadata 查 Netdata pre/workload windows。
3. 正規化 `°C`、MB、W 與百分比欄名/單位。
4. 依 power model manifest aggregation。
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
| GPU contention 超標 | retry、排除，或使用明確支援 contention 的模型 |
| prediction tie | 使用政策型次要條件或回到 default scheduler |

