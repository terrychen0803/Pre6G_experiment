# Netdata feature contract

## 部署假設

每個 GPU worker 應有一個 Netdata child。實驗前必須實際稽核，而不是只確認 DaemonSet desired count。

```bash
kubectl -n netdata get daemonset,pods -o wide
kubectl -n netdata get pods -l app.kubernetes.io/component=child \
  -o custom-columns=NAME:.metadata.name,NODE:.spec.nodeName,READY:.status.containerStatuses[0].ready
```

還要逐 node 查 API，因為 Pod Ready 不代表 NVIDIA/sensor/apps collectors 都有資料。

## Schema v1

必要欄位：

```text
CPU User%
CPU System%
CPU IOWait%
Load 1min
Load 5min
Load 15min
Mem Used(MB)
Mem Free(MB)
CPU Temp(°C)
GPU Util%
GPU Mem Used(MB)
GPU Temp(°C)
GPU Power(W)
Top1 CPU%
Top2 CPU%
Top3 CPU%
Top1 GPU%
Top2 GPU%
```

Node/GPU metrics 可由 Netdata system、sensors 與 `nvidia_smi` contexts 取得。Top CPU 可由 `apps.plugin`/processes function 取得。

目前 Netdata NVIDIA GPU collector 主要是 per-GPU，不保證提供 Top1/Top2 per-process GPU utilization。若 energy model 需要真實 Top GPU，必須部署額外 node collector，例如 `nvidia-smi pmon` 或 DCGM process metrics，將輸出接回 feature service 或 Netdata custom collector。

## 時間窗口

Netdata 持續收集，不由單次 Job 啟停。Wrapper 保存 UTC Unix nanoseconds：

```text
pre_window_start
application_start
profile_start
steady_window_start
steady_window_end
profile_end
application_end
```

查詢 historical API 時使用 profile/steady window 的 absolute `after`、`before`，並關閉不必要的時間對齊。建議同時保留：

- 5～10 秒 pre-run background window。
- 至少 10 個 Netdata samples 的 workload window。
- 2 秒 post-roll。

若 dry-run 的單一 iteration 是毫秒級，仍要重複足夠 iterations 讓 Netdata 取得可用窗口。

## Aggregation

能耗模型推論必須使用和訓練完全相同的 aggregation。每個 feature 都要在 model manifest 指定，例如：

```json
{
  "GPU Power(W)": "mean",
  "GPU Util%": "mean",
  "CPU Temp(°C)": "last",
  "Top1 CPU%": "p95"
}
```

不要由部署程式自行選 mean 或 latest。

## 瞬時功率與能量

Energy adapter 對每一筆對齊後資料執行：

```text
[18 features, corresponding time] → predicted_power_w(t)
```

`corresponding time` 的 encoding 必須和訓練一致；若 timestamp 只用來對齊，就不能直接當模型特徵。Collector 保存完整的 `timestamp_unix_ns, predicted_power_w` 序列，以梯形積分得到 observed-window Joules，再以 steady-window power 與 runtime prediction 外推 production energy。

如果 target 是整台 node 的外部量測功率，`GPU Power(W)` 可作為輸入；如果 target 本身就是 NVIDIA GPU Power，則此欄會造成 target leakage，必須從模型輸入移除。

## Readiness audit

使用：

```bash
python scripts/audit_netdata.py \
  --node worker-5090=http://10.0.0.12:19999 \
  --node worker-4090=http://10.0.0.13:19999
```

工具會檢查 system charts、GPU contexts、temperature、Top CPU 與 Top GPU，並以非零 exit code 表示有必要 feature 缺失。
