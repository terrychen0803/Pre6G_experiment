# YOLO26 正式 30–50 分鐘 dry-run 預測實驗

狀態：**implementation merged candidate / 尚未完成 30–50 分鐘實機 ground-truth 驗證**。

此流程與 functional/integration fixture 分離。正式 workload 已在看到 long-run ground truth 前固定；本次因時程限制，只要求程式、配置與 CI 驗證完成，不把未執行的 40 分鐘實測宣稱為已驗證結果。

## 固定 workload

Sizing reference 使用已完成的 functional run `yolo26-functional-003`：

- reference node: `iccl-s3-251230` / RTX4090
- reference prediction: 45.77585973120491 ms / training iteration
- target: 40 minutes
- train samples: 512
- batch: 16
- steps / epoch: 32
- fixed epochs: **1639**
- fixed total work units: **52448 training iterations**

以 functional prediction 作為 sizing 參考時，52448 iterations 對應約 RTX4090 40.014 分鐘、RTX5090 41.112 分鐘的 steady compute。這只是事前 sizing estimate，不是 formal ground truth。

正式來源 Job：

`examples/yolo26/formal-40min-source-job.yaml`

### Formal workload revision v2：training-only

第一次 formal 120 秒實測 `yolo26-formal-dryrun-001` 在 RTX5090 約第 6 epoch 被 Ultralytics 的 fitness-collapse recovery 中止。該 run 的 profiler 實際使用預設 `val=True`／`patience=100`，同時 `save=False`，因此 fitness collapse 發生時沒有 `last.pt` 可供 recovery，container 以 exit code 1 結束。這不是 Kubernetes deadline、Nsight、DCGM 或 telemetry alignment 失敗。

本研究的 formal benchmark 目標是固定 training iterations 的 runtime / energy，而不是 synthetic dataset 的 detection accuracy。因此 workload revision v2 明確固定：

- `val=False`
- `save=False`
- `patience=0`

這三個參數必須同時出現在 source Job、RTX4090 profile template、RTX5090 profile template；cross-node prepare/preflight 會 fail closed。這避免 validation fitness/recovery 與 checkpoint I/O 改變 benchmark workload。

它明確標記：

- `pre6g.io/experiment-stage: formal-experiment`
- `pre6g.io/test-purpose: dryrun-full-workload-prediction`
- `pre6g.io/full-workload-fixed: "true"`
- `pre6g.io/work-unit: training_iteration`
- `pre6g.io/total-work-units: "52448"`
- `pre6g.io/formal-workload-revision: "v2-training-only"`

## Dry-run 與 full workload 的分離

正式 dry-run **不再把 epochs 縮小**。兩個 candidate 的 profiling template 都保留完整 `epochs=1639`，只由 Nsight capture policy 限制前 120 秒：

```text
Fixed original workload
  1639 epochs / 52448 iterations
          |
          +-----------------------------+
          |                             |
          v                             |
120-second dry-run                      |
Nsight --duration=120                   |
--kill=sigterm                          |
          |                             |
          v                             |
runtime/power inference                 |
          |                             |
          v                             |
per-iteration prediction × 52448 <------+
          |
          v
predicted full steady runtime / energy
          |
          v
optional later ground-truth full run
same 1639 epochs on every candidate
```

Formal cross-node config：

`examples/yolo26/formal-cross-node-dryrun.yaml`

Profile templates：

- `k8s/formal/yolo26-rtx4090-formal-40min-dryrun-120s.yaml`
- `k8s/formal/yolo26-rtx5090-formal-40min-dryrun-120s.yaml`

## 程式保護

`run_cross_node_dryrun.py` 現在同時支援 functional 與 formal stage，並要求 config、source Job、profile template 的 experiment annotations 一致。Formal stage 另外要求：

- source Job 必須 `full-workload-fixed=true`
- sizing target 必須介於 30–50 分鐘
- 必須記錄 sizing reference node
- source 的 epochs / dataset / batch 算出的 work units 必須與宣告的 total work units 一致
- profiling template 必須保持與 source 完全相同的 model / epochs / imgsz / batch / AMP / dataset count
- formal training-only policy 必須保持 `val=False / save=False / patience=0`
- profiling wall-clock 仍固定 `--duration=120`

Ground-truth planner 遇到 `full-workload-fixed=true` 時，不再根據新的 prediction 重新調整 epochs；它會保持原始 1639 epochs / 52448 iterations，避免 prediction 反過來改變被驗證的 workload。

## 執行方式

正式 120 秒 cross-node dry-run：

```bash
COMMIT=$(git rev-parse HEAD)
CTX=$(kubectl config current-context)

python scripts/run_experiment_pipeline.py \
  --mode cross-node \
  --cross-node-config examples/yolo26/formal-cross-node-dryrun.yaml \
  --run-id yolo26-formal-dryrun-002 \
  --worker-commit "$COMMIT" \
  --output-dir generated/yolo26-formal-dryrun-002 \
  --preflight-only \
  --kube-context "$CTX"
```

若 preflight 通過，可把 `--preflight-only` 改成 `--execute`。這只需要約 120 秒級 profiling 加上分析時間，不需要先跑完整 40 分鐘 ground truth。

若未來要產生所有 candidate 的完整 ground-truth Jobs，必須使用 formal source Job 與 formal dry-run 的 ranking input。Planner 會保留固定 workload，而不是重新 sizing。

## 尚未驗證的部分

本次合併不代表下列項目已實測：

- 1639 epochs 在 RTX4090/RTX5090 的實際 trainer wall-clock 是否真的落在 30–50 分鐘；
- dry-run 對完整 52448 iterations runtime 的 formal prediction error；
- 外部 PDU full-run energy ground truth；
- prediction ranking 是否命中實際最低整機能耗節點。

因此正式論文結果仍需要後續 full-run ground truth。現在合併的是**可執行且受測的 formal workflow implementation**，不是 formal accuracy result。
