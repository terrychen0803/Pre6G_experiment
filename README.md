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
  |      workload-family adapter / known argv / config
  |
  +--> Automatic Work Discovery
  |      mounted dataset / dataset manifest / total work amount
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

`work_discovery.py` 不取代 workload-family adapter。它先呼叫既有 `estimate_work()`，只有在 total work 尚未解析時，才用 adapter 已解析出的 dataset path / epochs / batch 等資訊讀取原始 workload 已掛載的 dataset，補足 dataset cardinality 與 total work。

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
- [Profile Result handoff](docs/profile-result-contract.md)
- [Monitoring preflight / recovery SOP](docs/monitoring-preflight.md)
- [Telemetry feature contract](docs/netdata-contract.md)
- [Node-bound power model registry](docs/power-model-registry.md)
- [YOLO26 integration fixture](docs/yolo26-walkthrough.md)
- [實際 k3s 叢集基線](docs/cluster-baseline.md)
- [High-load trace 實測依據](docs/evidence/high-load-trace-results.md)
- [RTX5090 Kubernetes Profile Job E2E evidence](docs/evidence/rtx5090-k3s-profile-e2e.md)
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

Marker-free RTX5090 C03 integration validation：

~~~text
CUPTI_ACTIVITY_KIND_KERNEL present
StringIds present
kernel_count = 653976
kernel-start span = 17.980515013 s
single CUDA process/context
detected execution_cycle = 129.988614 ms
detector confidence = 0.83999
same-window NVTX audit oracle = 132.1907935 ms
same-window detector APE = 1.67%
~~~

NVTX 僅在 detector output freeze 後作 audit，不是 production detector input。

Runtime inference smoke：

~~~text
worker-side trace feature extraction PASS
frozen model = RTX5090_yolo_trace_only_v1
control-side predicted runtime = 109.251714 ms
C03 unprofiled smoke reference = 118.080153 ms
smoke comparison APE = 7.48%
~~~

7.48% 只代表 deployment smoke；C03 存在於 final-fit dataset，不能當 held-out 泛化指標。

Kubernetes RTX5090 Profile Job E2E smoke（2026-09-28）：

~~~text
artifact backend                 NFS-backed static RWX PV/PVC
cross-node RWX transport         PASS
Profile Job                      Complete (1/1)
Nsight report                    35 MB, worker-local
Nsight SQLite                    104 MB, worker-local
marker-free execution_cycle      126.8548825 ms
detector confidence              0.73041
complete cycles                  118
selection                        two-window stability
runtime-features.json            PASS
profile-result.json              PASS
ProfileResult status             ready-for-control-side-inference
manual SCP                       not used
~~~

正式 handoff 僅把小型 JSON artifacts 寫入 shared RWX PVC；大型 `.nsys-rep` / SQLite 留在 worker-local temporary storage。

同一份 Kubernetes artifact 已完成 control-side frozen runtime inference：

~~~text
model                         RTX5090_yolo_trace_only_v1
predicted runtime             115.6079292 ms / execution_cycle
schema/device/unit/binding    PASS
runtime-prediction.json       PASS
~~~

Automatic work-discovery smoke 也已在 Kubernetes Job 中完成：

~~~text
epochs source                 original Job argv
batch source                  original Job argv
dataset source                mounted dataset
training samples              512
steps per epoch               32
total work units              128
iterations.csv/NVTX/callback  not used
~~~

目前 validated YOLO binding registry 將 `yolo-v1 execution_cycle` 以 fail-closed policy 綁定至 `training_iteration`，`cycles_per_work_unit=1`。因此本次 steady-work runtime 為：

~~~text
115.6079292 ms × 128 = 14.79781494 s
~~~

這仍不是完整 whole-job runtime；startup / warmup / validation / checkpoint / finalization 尚未納入 non-steady overhead model。RTX4090 等價 Profile Job、power prediction 與 automatic placement 也尚未完成。

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
- automatic mounted-dataset work discovery（不需要 iteration marker）。
- fail-closed YOLO/yolo-v1 semantic binding registry。
- per-execution-cycle → per-work-unit → steady total runtime aggregation。
- Netdata + DCGM monitoring readiness/recovery。
- timestamp alignment quality gate。
- node-bound power-model registry contract。
- work-unit-aware decision layer。
- production Job renderer。
- RTX4090/RTX5090 Nsight 2026 Kubernetes baseline。
- current-cluster NFS-backed RWX shared artifact store 與跨節點 ProfileResult transport。
- RTX5090 Kubernetes Profile Job → marker-free detector → runtime features → ProfileResult E2E smoke。

目前尚未包含：

- arbitrary-workload production runtime model；
- RTX4090 frozen runtime model；
- real RTX4090/RTX5090 power model binaries/scalers；
- 完整 Kubernetes controller reconcile loop；
- validated Top1/Top2 per-process GPU collector。

## Current MVP runtime target

為了先跑通 multi-node prediction → power → ranking → production placement 的完整控制流程，current YOLO integration 暫時把：

~~~text
predicted_steady_runtime_s
~~~

作為目前的 **predicted training time**。它代表：

~~~text
predicted runtime per training_iteration
× automatically discovered total_work_units
~~~

目前不把 startup / warmup / validation / checkpoint / finalization 納入此 MVP 指標，因此：

~~~text
predicted_total_job_runtime_s = null
~~~

會繼續保留。Whole-job non-steady overhead model 延後到完整流程跑通後再加入，避免把尚未驗證的 overhead 混入目前 runtime predictor。

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


## Marker-free runtime implementation

The current project implementation now includes the deployment-oriented versions of the marker-free detector and runtime-model preparation path:

~~~text
scripts/extract_marker_free_trace.py
  Nsight SQLite
  -> fail-closed target globalPid/contextId selection
  -> marker-free-events.csv.gz
  -> extraction summary

scripts/evaluate_trace_event_periods.py
  target-process CUDA events
  -> YOLO-v1 recurring-period detector
  -> execution_cycle period/confidence/stability
  -> optional NVTX audit only

scripts/run_unified_trace_model.py
  offline clean/high-load model-development reference
  -> trace features
  -> timestamps.json anchored pre-run telemetry
  -> grouped leave-one-workload-out evaluation
~~~

Core reusable modules:

~~~text
src/pre6g_experiment/marker_free.py
src/pre6g_experiment/runtime_features.py
~~~

The current detector profile is explicitly named yolo-v1. Its 20–2000 ms search range and supported 2x harmonic correction are preserved from the validated YOLO reference path and are not treated as generic workload rules.

Pre-run telemetry no longer uses iterations.csv to find a window boundary. The platform uses application_start_ns from timestamps.json, with profile_start_ns as the fallback, and reads canonical aligned Netdata/DCGM telemetry.

Reference implementation results imported from Pre6G_result are documented in:

~~~text
docs/evidence/pre6g-result-runtime-reference.md
~~~


## Control-side frozen runtime inference

目前已加入第一個可直接載入的 deployment-smoke runtime model：

~~~text
models/runtime/RTX5090_yolo_trace_only_v1.json
~~~

正式角色分工：

~~~text
GPU worker Profile Job
  Nsight / marker-free detector / runtime feature extraction
        ↓
  runtime-features.json
        ↓
shared artifact store
        ↓
k3s control side
  frozen runtime model inference
        ↓
  runtime-prediction.json
~~~

Worker-side feature artifact：

~~~bash
PYTHONPATH=src python scripts/build_runtime_features.py \
  --sqlite profile.sqlite \
  --detection-json marker-free-discovery.json \
  --output runtime-features.json \
  --node "$NODE_NAME" \
  --device-id RTX5090
~~~

Control-side inference：

~~~bash
PYTHONPATH=src python scripts/predict_runtime.py \
  --model models/runtime/RTX5090_yolo_trace_only_v1.json \
  --features runtime-features.json \
  --output runtime-prediction.json
~~~

`predict_runtime.py` 只做 frozen inference，不重新 fit、不重新選 alpha。

Profile Job → Controller 的正式 handoff contract：

~~~text
docs/profile-result-contract.md
schemas/profile-result.schema.json
schemas/runtime-features.schema.json
schemas/runtime-prediction.schema.json
~~~

人工 `scp` 僅用於目前 component smoke test，不屬於正式 k3s controller workflow。

## Automatic work discovery and semantic runtime

這一層與既有 workload-family adapter 是串接關係，不是另一套互斥 adapter：

~~~text
original Job
  -> work.py / workload-family adapter
       workload family / argv semantics / candidate work unit
  -> work_discovery.py
       mounted dataset cardinality / total work
  -> runtime-prediction.json
  -> semantic_binding.py
       fail-closed execution_cycle -> work_unit binding
  -> runtime_aggregation.py
       per-work-unit runtime / steady total runtime
~~~

CLI：

~~~bash
PYTHONPATH=src python scripts/discover_work.py \
  --job source-job.yaml \
  --output workload-discovery.json

PYTHONPATH=src python scripts/aggregate_runtime.py \
  --runtime-prediction runtime-prediction.json \
  --work-discovery workload-discovery.json \
  --output semantic-runtime.json
~~~

Current schemas：

~~~text
schemas/work-discovery.schema.json
schemas/semantic-binding.schema.json
schemas/semantic-runtime.schema.json
~~~

YOLO mounted-dataset discovery uses application-visible `data=...` plus original `epochs` / `batch`; it does not inspect iteration timestamps, NVTX, callbacks, or `iterations.csv`.

## PDU1 Outlet1 power-model bundle

The repository includes the supplied ONNX power model and its exact Min-Max
scaler as a reproducible, standalone bundle:

```text
models/power/bundles/pdu1-outlet1-20260416-20260612/
```

Run a smoke prediction with:

```bash
python -m pip install -r requirements-power-model.txt
python scripts/predict_power_eq.py \
  examples/power/pdu1-outlet1-sample.json \
  --output generated/pdu1-outlet1-predictions.json \
  --reject-ood
```

The adapter accepts JSON or CSV telemetry and adds `PREDICTED_POWER_W` to each
record. See the [bundle documentation](models/power/bundles/pdu1-outlet1-20260416-20260612/README.md)
for the five-feature schema, normalization, artifact checksums, and known
validation gaps.

The bundle is intentionally marked `validation_required` and is not listed in
`models/power/registry.yaml`: the supplied artifacts do not identify their
Kubernetes node or physical GPU UUID, and outlet-power semantics have not yet
been confirmed as compatible with the project's `node-total-power` ranking
contract.
