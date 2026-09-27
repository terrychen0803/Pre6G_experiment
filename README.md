# Pre6G Experiment

在 k3s 上執行「固定 120 秒 profiling → 離線 period detection → runtime/power 推論 → 能源排名 → 正式部署」的正式整合與驗證平台。

目前 repository 不宣稱 runtime 或 power model 已全部定版。平台區分：

- model-ready：runtime model 與該 node 對應的 power model 都通過版本、binding、schema、OOD、confidence 與 telemetry-quality gate，允許自動選點。
- profile-only：可完成各節點 dry-run、Nsight、Netdata/DCGM telemetry 與 artifact 保存，但不自動宣稱最佳節點。
- demo：只用 synthetic prediction 驗證控制流程，不可當成實驗結論。

## 核心流程

~~~
User batch/v1 Job
  → validate / estimate work units
  → discover eligible GPU nodes
  → telemetry preflight
  → one pinned Profile Job per node
  → fixed 120-second target-process Nsight profile
  → Netdata system telemetry + DCGM GPU telemetry
  → timestamp alignment + quality gate
  → offline period detection
  → runtime adapter
  → node-aware power-model registry/router
  → node-specific power adapter
  → energy/confidence/OOD/model-binding gate
  → rank comparable eligible nodes
  → create an unprofiled production Job pinned to the selected node
  → compare prediction with production ground truth
~~~

## Telemetry contract

The canonical feature schema is collector-independent.

Validated source ownership:

~~~
Netdata Parent/Child
  CPU User/System/IOWait
  Load 1/5/15
  Memory Used/Free
  CPU temperature
  Top1/Top2/Top3 CPU

NVIDIA DCGM Exporter
  GPU Util
  GPU framebuffer used
  GPU temperature
  GPU power

Nsight Systems 2026
  target-process CUDA trace for runtime prediction
~~~

DCGM exporter-side collection interval is pinned to approximately 1 second for the current experiment:

~~~
DCGM_EXPORTER_INTERVAL=1000
~~~

Netdata/DCGM alignment policy:

~~~
nearest timestamp
tolerance <= 750 ms
coverage >= 90%
max source gap <= 2 s
~~~

Top1 GPU% / Top2 GPU% remain optional platform extensions. If a particular node power model requires them, that model is not ready until a validated per-process GPU collector exists.

## Node-bound power models

Current energy prediction uses one power model per node.

The central Power Adapter resolves models by:

~~~
Kubernetes node name
+
physical GPU UUID
~~~

The first automatic-ranking contract requires every participating node model to output:

~~~
target semantics: node-total-power
unit: W
~~~

Ranking converts this to incremental power using the node-specific idle baseline.

Model binaries are not required to live in this repository. The repository stores the routing/manifest contract under models/power/. See docs/power-model-registry.md.

## Documentation

- [系統架構](docs/architecture.md)
- [完整實驗程序](docs/experiment-procedure.md)
- [Monitoring preflight / recovery SOP](docs/monitoring-preflight.md)
- [Telemetry feature contract](docs/netdata-contract.md)
- [Node-bound power model registry](docs/power-model-registry.md)
- [Dry-run Profile Job 部署](docs/dry-run-deployment.md)
- [模型與 iteration 未定時的處理](docs/model-and-work-estimation.md)
- [YOLO26 具體範例](docs/yolo26-walkthrough.md)
- [實際 k3s 叢集基線與 RTX 多節點 readiness](docs/cluster-baseline.md)
- [High-load trace 實測依據](docs/evidence/high-load-trace-results.md)
- [RTX 4090/5090 Nsight Systems 2026 smoke manifest](k8s/nsys2026-rtx-smoke.yaml)

## 已驗證的 RTX profiling baseline

2026-09-27 已在 k3s 的 RTX4090 與 RTX5090 worker 完成 CUDA + Nsight Systems E2E smoke test。

~~~
Architecture: x86_64
RuntimeClass: nvidia
GPU resource: nvidia.com/gpu.shared: 1
Nsight Systems: 2026.4.1.191-264138605071v0
Host install root: /opt/nvidia/nsight-systems-cli/2026.4.1
Container entrypoint: /opt/pre6g/nsight/bin/nsys
Trace: cuda,nvtx,osrt
CPU sampling: disabled
CPU context-switch sampling: disabled
~~~

Nsight 2026 必須掛載完整 installation root，再從 bin/nsys 啟動；不要只掛載 target-linux-x64 後直接執行 binary。兩個節點都已驗證可產生非空 .nsys-rep 與 SQLite，並成功解析 CUDA API、CUDA GPU Kernel 與 OS Runtime summaries。

RTX3090 暫不在本輪 integration scope；其儲存空間整理完成後再依相同 preflight 流程重新加入。

## 已驗證的 monitoring baseline

2026-09-27 已驗證：

- RTX4090/RTX5090 Netdata child Running/Ready。
- child 透過 Netdata Parent 提供 per-host historical system/CPU telemetry。
- 重複的 host-native Netdata 會因 hostNetwork + port 19999 造成 child CrashLoopBackOff；SOP 已記錄 recovery。
- RTX4090/RTX5090 DCGM Exporter 可提供 GPU Util、FB Used、GPU Temp、GPU Power。
- DCGM_EXPORTER_INTERVAL=1000 已完成 DaemonSet rollout。
- control-plane、RTX4090、RTX5090 system clocks 已同步且 NTP active。
- RTX5090 alignment validation：20/20 DCGM samples 成功對齊 Netdata，coverage 100%，median absolute delta 284.8 ms，max delta 490.5 ms。

上述 validation numbers 是 baseline evidence，不是模型 accuracy 結論。

## 本機示範

需求：Python 3.10+ 與 PyYAML。

~~~bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

python -m pre6g_experiment inspect   --job examples/yolo26/user-job.yaml

python -m pre6g_experiment decide   --job examples/yolo26/user-job.yaml   --results examples/yolo26/synthetic-node-results.json   --output generated/yolo26-production-job.yaml   --allow-synthetic

kubectl apply --dry-run=server   -f generated/yolo26-production-job.yaml
~~~

synthetic-node-results.json 只用來走通 decision path。真正部署時必須由 profile collector、runtime adapter、node-bound power adapter 與 telemetry-quality gate 產生同一份 schema。

## Telemetry scripts

Netdata system/CPU readiness：

~~~bash
python scripts/audit_netdata.py --help
~~~

DCGM fixed-cadence collection：

~~~bash
python scripts/collect_dcgm.py --help
~~~

Netdata/DCGM nearest alignment：

~~~bash
python scripts/align_telemetry.py --help
~~~

## Repository 邊界

這一版提供：

- 正式系統與資料契約。
- Netdata + DCGM monitoring readiness/recovery SOP。
- timestamp alignment policy 與 quality gate。
- node-bound power-model registry/manifest contract。
- Job YAML 工作量推導工具。
- 節點結果驗證、energy ranking 與 production Job 產生器。
- YOLO26 端到端範例。
- Kubernetes RBAC 與 namespace 範例。
- RTX4090/RTX5090 Nsight Systems 2026.4.1 Kubernetes smoke baseline。

這一版不包含：

- 尚未定版的 runtime model weights。
- 實際 node-bound power model binaries/scalers。
- NVIDIA driver、Nsight Systems、Netdata 或 DCGM images。
- 完整 Kubernetes controller reconcile loop。
- 尚未定版的 production runtime model bundle。
- 可直接用於正式 profiling 的固定 digest x86_64 YOLO26 training image；目前仍需完成 workload freeze。
- validated Top1/Top2 per-process GPU collector。

## 安全與正確性原則

- Shared GPU 是正式支援情境；shared mode 必須使用 target-process CUDA trace backend。
- Profile/production Job 必須使用相同 GPU sharing contract。
- power model 必須精確綁定 candidate node + physical GPU UUID，不得拿其他 node model fallback。
- automatic ranking 目前只接受可比較的 node-total-power/W 模型。
- 任一模型 unavailable、binding mismatch、schema mismatch、OOD、confidence 不足或 telemetry quality 不合格時，停止該 node 的自動 ranking。
- required feature 缺失時不得 zero-fill。
- 不從不完整 YAML 猜總 iteration 數。
- Profile Job 與 production Job 使用不同名稱；不修改原始 Job。
- 所有 artifacts 以 task_id/node/attempt 分區，記錄 checksum 與 absolute time window。
