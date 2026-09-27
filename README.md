# Pre6G Experiment

在 k3s 上執行「固定 120 秒 profiling → 離線 period detection → runtime/power 推論 → 能源排名 → 正式部署」的實驗規格與可執行原型。

目前這個 repository 的定位是整合與驗證平台，不宣稱 runtime 模型已經定版。平台會明確區分：

- `model-ready`：runtime 與 power model 都通過版本、schema、OOD 與 confidence gate，允許自動選點。
- `profile-only`：可以完成各節點 dry-run、Netdata 擷取與 artifact 保存，但不自動宣稱最佳節點。
- `demo`：用標示為 synthetic 的預測結果驗證控制流程，不可當成實驗結論。

## 核心流程

```text
User batch/v1 Job
  → validate / estimate work units
  → discover eligible GPU nodes
  → one pinned Profile Job per node
  → collect a fixed 120-second target-process profile
  → run period detection offline on trace prefixes/windows
  → target-process CUDA trace features + timestamp-aligned Netdata features
  → runtime adapter + power adapter
  → energy/confidence/OOD gate
  → rank eligible nodes
  → create an unprofiled production Job pinned to the selected node
  → compare prediction with production ground truth
```

詳細設計見：

- [系統架構](docs/architecture.md)
- [可匯入 draw.io 的完整架構圖](docs/diagrams/pre6g-experiment-architecture.drawio)
- [完整實驗程序](docs/experiment-procedure.md)
- [Dry-run Profile Job 部署](docs/dry-run-deployment.md)
- [模型與 iteration 未定時的處理](docs/model-and-work-estimation.md)
- [Netdata feature contract](docs/netdata-contract.md)
- [YOLO26 具體範例](docs/yolo26-walkthrough.md)
- [實際 k3s 叢集基線與 RTX 多節點 readiness](docs/cluster-baseline.md)
- [High-load trace 實測依據](docs/evidence/high-load-trace-results.md)
- [RTX 4090/5090 Nsight Systems 2026 smoke manifest](k8s/nsys2026-rtx-smoke.yaml)

## 已驗證的 RTX profiling baseline

2026-09-27 已在 k3s 的 RTX 4090 與 RTX 5090 worker 完成 CUDA + Nsight Systems E2E smoke test。兩個節點目前的 profiling contract 為：

```text
Architecture: x86_64
RuntimeClass: nvidia
GPU resource: nvidia.com/gpu.shared: 1
Nsight Systems: 2026.4.1.191-264138605071v0
Host install root: /opt/nvidia/nsight-systems-cli/2026.4.1
Container entrypoint: /opt/pre6g/nsight/bin/nsys
Trace: cuda,nvtx,osrt
CPU sampling: disabled
CPU context-switch sampling: disabled
```

Nsight 2026 必須掛載完整 installation root，再從 `bin/nsys` 啟動；不要只掛載 `target-linux-x64` 後直接執行 binary。兩個節點都已驗證可產生非空 `.nsys-rep` 與 SQLite，並成功解析 CUDA API、CUDA GPU Kernel 與 OS Runtime summaries。

RTX 3090 暫不在本輪 integration scope；其儲存空間整理完成後再依相同 preflight 流程重新加入。

## 本機示範

需求：Python 3.10+ 與 PyYAML。

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

python -m pre6g_experiment inspect --job examples/yolo26/user-job.yaml

python -m pre6g_experiment decide --job examples/yolo26/user-job.yaml --results examples/yolo26/synthetic-node-results.json --output generated/yolo26-production-job.yaml --allow-synthetic

kubectl apply --dry-run=server -f generated/yolo26-production-job.yaml
```

`synthetic-node-results.json` 只用來走通 decision path。真正部署時必須由 profile collector、runtime model adapter 與 energy model adapter 產生同一份 schema。

## Repository 邊界

這一版提供：

- 系統與資料契約。
- Netdata child readiness audit。
- Job YAML 工作量推導工具。
- 節點結果驗證、能量計算與 production Job 產生器。
- YOLO26 端到端範例。
- Kubernetes RBAC 與 namespace 範例。
- RTX 4090/5090 的 Nsight Systems 2026.4.1 Kubernetes smoke-test baseline。

這一版不包含：

- 尚未定版的 runtime model 權重。
- 使用者既有的 energy model 權重。
- NVIDIA driver、Nsight Systems 或 Netdata image。
- 完整 Kubernetes controller reconcile loop。
- 尚未定版的 production runtime model bundle；目前 high-load trace 實驗為候選模型依據。
- 可直接用於正式 profiling 的固定 digest x86_64 YOLO26 training image；目前仍需完成 workload image 凍結。

## 安全原則

- Shared GPU 是正式支援情境；shared mode 必須使用 target-process CUDA trace backend，不能回退到 device-wide GPU Metrics period detector。
- Profile/production Job 可請求 `nvidia.com/gpu.shared: 1`，並記錄 sharing strategy、replicas、physical GPU UUID 與 co-tenant state。
- 任一模型 unavailable、schema mismatch、OOD 或 confidence 不足時，停止自動排名。
- 不從不完整 YAML 猜出一個看似精確的總 iteration 數。
- Profile Job 與 production Job 使用不同名稱；不修改或直接執行原始 Job。
- 所有 artifacts 以 `task_id/node/attempt` 分區，並記錄 checksum 與時間窗口。
