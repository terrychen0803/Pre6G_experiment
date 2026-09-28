# Pre6G Experiment

在 k3s 上執行「Generic workload intake → marker-free dry-run profiling → runtime / power prediction → energy-aware placement → production ground truth」的整合與驗證平台。

YOLO26 只是目前第一個 integration fixture；平台核心不假設使用者一定是 YOLO、AI training，或一定使用 iteration 作為 work unit。

目前 repository 區分：

- model-ready：runtime model 與該 node 對應的 power model 都通過 semantic binding、版本、binding、schema、OOD、confidence 與 telemetry-quality gate，允許自動選點。
- profile-only：可完成 workload intake、marker-free per-node dry-run、Nsight、Netdata/DCGM telemetry 與 artifact 保存，但不自動宣稱最佳節點。
- demo：只用 synthetic prediction 驗證控制流程，不可當成實驗結論。

## 核心流程

~~~text
User batch/v1 Job
  |
  +--> Execution Contract
  |      image / command / args / env / resources / volumes
  |
  +--> Static Semantic Discovery
  |      explicit metadata / known argv / config / dataset metadata
  |
  v
candidate discovery
  -> telemetry preflight
  -> target-process Nsight trace
  -> marker-free CUDA event extraction
  -> recurring execution-cycle detection
  -> semantic binding
  -> Netdata system telemetry + DCGM GPU telemetry
  -> timestamp alignment + quality gate
  -> runtime adapter
  -> node-aware power-model registry/router
  -> node-specific power adapter
  -> total-work / model / telemetry gate
  -> rank comparable eligible nodes
  -> create an unprofiled production Job
  -> compare prediction with production ground truth
~~~

不知道 total work 不代表不能 profiling。若 semantic binding 或 total work 仍 unknown，平台保持 profile-only / relative-performance mode，不把 cycle latency 錯誤外推為 total runtime 或 total energy。

## Marker-free production contract

正式 runtime discovery 不要求修改使用者程式碼。

Allowed inputs：

~~~text
original Job / argv / mounted config / metadata
target-process CUDA trace
Netdata / DCGM telemetry
~~~

Production detector 不依賴：

~~~text
iterations.csv
NVTX iteration markers
training callbacks
epoch/batch IDs
summary.json iteration timing
source-code instrumentation
~~~

Instrumented benchmark artifacts 只能在 detector output freeze 後作 hidden ground-truth validation。

詳見 [Marker-Free Workload Discovery](docs/marker-free-workload-discovery.md)。

## execution_cycle 與 work_unit

Marker-free detector 第一層只輸出：

~~~text
detected_unit = execution_cycle
period_ms
confidence
complete_cycles
~~~

Static semantic layer 可能知道：

~~~text
candidate work_unit = training_iteration
total_work_units = 640
~~~

但必須先通過 semantic binding：

~~~text
execution_cycle
      ↓ validated binding
semantic work_unit
~~~

只有 binding validated 且 total_work_units 已知，才允許 total-job runtime / energy extrapolation。

## Generic workload contract

正式 semantic schema：

~~~text
schemas/workload-spec.schema.json
~~~

核心抽象：

~~~text
workload_family
parameters
work_unit
total_work_units
source
~~~

例如：

| Workload | semantic work_unit |
|---|---|
| Vision training | training_iteration |
| LLM fine-tuning | optimizer_step |
| LLM inference | generated_token |
| FFmpeg | frame |
| FAISS build | vector_insert |

目前第一個 adapter 是 YOLO，但 adapter 只負責 static semantic translation，不改寫 application argv，也不提供 production iteration marker。

## Telemetry contract

Validated source ownership：

~~~text
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
  target-process CUDA trace
~~~

DCGM exporter-side collection interval：

~~~text
DCGM_EXPORTER_INTERVAL=1000
~~~

Netdata/DCGM alignment policy：

~~~text
nearest timestamp
tolerance <= 750 ms
coverage >= 90%
max source gap <= 2 s
~~~

Top1 GPU% / Top2 GPU% remain optional platform extensions. If a particular node power model requires them, that model is not ready until a validated per-process GPU collector exists.

## Node-bound power models

Current energy architecture uses one power model per node.

Routing key：

~~~text
Kubernetes node name
+
physical GPU UUID
~~~

第一版 automatic-ranking contract：

~~~text
model_scope = node-bound
target_semantics = node-total-power
target_unit = W
~~~

真正 RTX4090 / RTX5090 power model bundles 尚未取得，因此目前 real workflow 仍維持 profile-only。Registry placeholder 不會虛構 model format。

## Documentation

- [系統架構](docs/architecture.md)
- [完整實驗程序](docs/experiment-procedure.md)
- [Generic workload intake](docs/workload-intake.md)
- [Marker-Free Workload Discovery](docs/marker-free-workload-discovery.md)
- [模型與工作量處理](docs/model-and-work-estimation.md)
- [Dry-run Profile Job 部署](docs/dry-run-deployment.md)
- [Monitoring preflight / recovery SOP](docs/monitoring-preflight.md)
- [Telemetry feature contract](docs/netdata-contract.md)
- [Node-bound power model registry](docs/power-model-registry.md)
- [YOLO26 integration fixture](docs/yolo26-walkthrough.md)
- [實際 k3s 叢集基線](docs/cluster-baseline.md)
- [High-load trace 實測依據](docs/evidence/high-load-trace-results.md)
- [RTX4090/5090 Nsight Systems 2026 smoke manifest](k8s/nsys2026-rtx-smoke.yaml)

## 已驗證 baseline

RTX4090 與 RTX5090：

~~~text
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

Monitoring baseline：

- Netdata child Running/Ready。
- Parent per-host historical query PASS。
- DCGM GPU Util / FB Used / Temp / Power PASS。
- DCGM_EXPORTER_INTERVAL=1000 rollout PASS。
- control-plane、RTX4090、RTX5090 NTP synchronized。
- RTX5090 alignment validation：20/20 samples aligned，coverage 100%，median absolute delta 284.8 ms，max delta 490.5 ms。

Marker-free RTX5090 C03 preflight：

~~~text
CUPTI_ACTIVITY_KIND_KERNEL present
StringIds present
kernel_count = 653976
kernel-start span = 17.980515013 s
single dominant CUDA process/context
usable fields = start/end/shortName/globalPid/deviceId/contextId/streamId
~~~

這只代表 trace 可進入 marker-free extraction，不代表 period detector 已驗證成功。

## CLI

需求：Python 3.10+ 與 PyYAML。

~~~bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

python -m pre6g_experiment inspect   --job examples/yolo26/user-job.yaml
~~~

inspect output：

~~~text
execution_contract
workload_spec
~~~

Synthetic decision-path：

~~~bash
python -m pre6g_experiment decide   --job examples/yolo26/user-job.yaml   --results examples/yolo26/synthetic-node-results.json   --output generated/yolo26-production-job.yaml   --allow-synthetic
~~~

Synthetic values only validate control flow.

## Telemetry scripts

~~~bash
python scripts/audit_netdata.py --help
python scripts/collect_dcgm.py --help
python scripts/align_telemetry.py --help
~~~

## Repository 邊界

目前提供：

- Generic batch/v1 workload intake。
- execution contract / static semantic contract 分離。
- marker-free production architecture。
- canonical workload schema。
- YOLO static semantic adapter prototype。
- Netdata + DCGM monitoring readiness/recovery。
- timestamp alignment quality gate。
- node-bound power-model registry contract。
- work-unit-aware decision layer。
- production Job renderer。
- RTX4090/RTX5090 Nsight 2026 Kubernetes baseline。

目前尚未包含：

- production marker-free period detector implementation；
- production runtime model bundle；
- real RTX4090/RTX5090 power model binaries/scalers；
- 完整 Kubernetes controller reconcile loop；
- validated semantic-binding registry；
- validated Top1/Top2 per-process GPU collector。

## 正確性原則

- User Job 的 image/command/args/env/resources/volumes 是 execution source of truth。
- Adapter 不改寫 application argv。
- Production runtime discovery 不要求 user instrumentation。
- Marker-free detector先輸出 execution_cycle，不直接假設 semantic work unit。
- Unknown semantics / unbound cycle 不阻止 profiling，但阻止不可靠 total-job extrapolation。
- Shared GPU 使用 target-process CUDA trace。
- Device-wide GPU metrics 不作 period-detection fallback。
- Profile/production Job 使用相同 GPU sharing contract。
- node-bound power model 精確綁定 node + physical GPU UUID。
- required feature 缺失不得 zero-fill。
- artifacts 以 task_id/node/attempt 分區並保存 checksum 與 absolute time window。
