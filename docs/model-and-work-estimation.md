# 模型與工作量尚未定版時的處理

## 核心原則

Pre6G_experiment 將三件事分開：

1. **Execution Contract**：如何原封不動地執行使用者的 Job。
2. **Static Workload Semantic Contract**：平台從 Job / config / metadata 理解到的 workload family、parameters、work unit 與 total work。
3. **Marker-Free Runtime Discovery**：平台從 target-process CUDA trace 偵測到的 recurring execution cycle、period、confidence 與 stability。

因此：

~~~text
不知道 total work
≠
不能 profiling

偵測到 execution_cycle
≠
已證明它就是 training_iteration
~~~

Unknown workload 仍可做 dry-run、Nsight、Netdata/DCGM、cycle discovery 與 artifact collection；只是不能把 execution-cycle latency 直接外推成可信的 total runtime / total energy。

## Production runtime discovery 不依賴 instrumentation

正式 production path 允許：

- original Job / argv；
- mounted config；
- explicit metadata；
- dataset metadata；
- target-process CUDA trace；
- Netdata/DCGM telemetry。

正式 detector 不依賴：

~~~text
iterations.csv
NVTX iteration markers
training callbacks
epoch/batch labels
summary.json iteration timing
source-code instrumentation
~~~

這些 instrumented artifacts 只能作 research validation ground truth。

## Static semantic discovery 優先序

### 1. Explicit canonical metadata

~~~yaml
metadata:
  annotations:
    pre6g.io/workload-family: vision-training
    pre6g.io/work-unit: training_iteration
    pre6g.io/total-work-units: "640"
    pre6g.io/workload-parameters-json: >-
      {"model":"yolo26n","batch_size":16,"input_size":640}
~~~

這些 metadata 不取代 application command/args。

### 2. Registered workload adapter

Adapter 可以解析它宣告支援的既有 framework interface，例如：

- known CLI flags；
- standard config file；
- dataset manifest。

目前第一個 prototype adapter 是 YOLO。

在 single-GPU、無 gradient accumulation、無 drop-last 等明示 assumptions 下：

~~~text
steps_per_epoch = ceil(training_samples / effective_batch_size)
total_work_units = epochs × steps_per_epoch
candidate work_unit = training_iteration
~~~

這是 static semantic estimate，不是 marker-free detector output。

### Automatic mounted-dataset work discovery

既有 workload-family adapter 保持 semantic source of truth；新的 work-discovery layer 只補足 total work，不重新定義 workload family。

~~~text
original Job argv/config
      |
      v
work.py / registered adapter
      |
      +--> workload_family
      +--> parameters
      +--> candidate work_unit
      |
      v
work_discovery.py
      |
      +--> application-visible dataset YAML/path
      +--> mounted dataset cardinality
      |
      v
total_work_units
~~~

YOLO current path：

~~~text
epochs          <- original argv/config
batch           <- original argv/config
data path       <- original argv/config
training samples<- mounted dataset
steps/epoch     = ceil(samples / batch)
total units     = epochs * steps/epoch
~~~

因此不需要使用者提供 training-step timestamp、batch boundary 或 iteration marker。若 dataset/config 無法可信解析，total work 保持 unknown，平台 fail closed。

### 3. Unknown

若無法取得可信 static semantics：

~~~text
profileable = true
work.status = unknown
work.unit = null
work.total_units = null
~~~

平台仍可進入 marker-free profiling。

## Marker-free runtime discovery

Marker-free detector 先輸出：

~~~json
{
  "detected_unit": "execution_cycle",
  "period_ms": 31.2,
  "confidence": 0.96,
  "complete_cycles": 87
}
~~~

Detector 不應先驗地把 execution_cycle 命名成 training_iteration、optimizer_step、frame 或 token。

原因是：

~~~text
1 semantic work unit
可能包含
N 個 CUDA recurring cycles
~~~

或：

~~~text
1 detected CUDA cycle
可能只是
1 semantic work unit 的子週期
~~~

因此 period detection 與 semantic interpretation 必須分開。

## Semantic binding gate

Static semantic layer 可能知道：

~~~text
candidate work_unit = training_iteration
total_work_units = 640
~~~

Marker-free detector 知道：

~~~text
detected_unit = execution_cycle
period_ms = ...
~~~

需要額外 binding：

~~~text
execution_cycle
      ↓
semantic binding
      ↓
training_iteration
~~~

Binding state：

~~~text
bound
unbound
conflict
insufficient_evidence
~~~

若 bound，還需保存：

~~~text
cycles_per_work_unit
binding source
binding version
~~~

Example：

~~~json
{
  "status": "bound",
  "detected_unit": "execution_cycle",
  "work_unit": "training_iteration",
  "cycles_per_work_unit": 1,
  "source": "validated-workload-adapter"
}
~~~

Production binding 不得讀取目前 run 的 hidden iterations.csv / NVTX label。

## Runtime model lifecycle

Semantic binding validated 後，generic runtime prediction 才能宣告 semantic unit：

~~~json
{
  "status": "ready",
  "backend": "target-process-cuda-trace",
  "supported_sharing_strategies": ["time-slicing"],
  "capture_policy": "fixed-profile-wall-window",
  "configured_capture_seconds": 120,
  "observed_cuda_active_seconds": 120,
  "model_version": "...",
  "feature_schema_version": "...",
  "work_unit": "training_iteration",
  "predicted_runtime_ms_per_work_unit": 42.1,
  "confidence": 0.93,
  "ood": false
}
~~~

如果 binding 尚未成立：

~~~text
detected_unit = execution_cycle
~~~

只能報 cycle latency / slowdown evidence，不應偽裝成 semantic per-work-unit prediction。

## Work unit 抽象

| Workload | semantic work_unit |
|---|---|
| Vision training | training_iteration |
| LLM fine-tuning | optimizer_step |
| LLM inference | generated_token |
| Video encoding | frame |
| FAISS build | vector_insert |

Detector-level unit：

~~~text
execution_cycle
~~~

只有 binding validated 後，兩者才可以連接。

## Total runtime

若：

~~~text
semantic binding validated
work_unit known
total_work_units known
runtime model ready
~~~

才允許：

~~~text
T_total_s =
    T_startup_s
  + N_warmup × T_warmup_per_unit_s
  + N_steady × T_predicted_per_work_unit_s
  + T_finalize_s
~~~

簡化 steady-state 外推：

~~~text
T_steady ≈ predicted_runtime_per_work_unit × total_work_units
~~~

如果 total_work_units 未知：

- 可以報 cycle latency；
- 可以報 relative slowdown；
- 可以做 OOD / stability evaluation；
- 預設不建立 total-job energy placement。

Current implementation：

~~~text
src/pre6g_experiment/work_discovery.py
src/pre6g_experiment/semantic_binding.py
src/pre6g_experiment/runtime_aggregation.py

scripts/discover_work.py
scripts/aggregate_runtime.py
~~~

Current schemas：

~~~text
schemas/work-discovery.schema.json
schemas/semantic-binding.schema.json
schemas/semantic-runtime.schema.json
~~~

`runtime_aggregation.py` 目前只輸出 steady-work runtime。它刻意保留：

~~~text
predicted_total_job_runtime_s = null
total_job_runtime_status = pending-non-steady-overhead-model
~~~

直到 startup / warmup / validation / checkpoint / finalization 的 non-steady overhead 有獨立、可驗證的 model/contract，避免把 steady-state extrapolation 誤標成 whole-job runtime。

## Total energy

第一版 power model contract：

~~~text
target_semantics = node-total-power
target_unit = W
~~~

Node incremental power：

~~~text
P_incremental(t) =
    max(0, P_node_predicted(t) - P_idle_node)
~~~

只有 total-runtime semantics 成立後才能合理外推 total energy。

## Research validation methodology

Instrumented fixture 正確用途：

~~~text
marker-free detector
       ↓
freeze output
       ↓
reveal hidden instrumentation
       ↓
compare error
~~~

例如目前 YOLO C03 hidden validation ground truth：

~~~text
steady-window mean iteration = 31.141946287 ms
GPU-event mean = 27.943115252 ms
32 batches / epoch
~~~

這些數字不能作 marker-free detector input。

## Backward compatibility

舊 prototype annotation：

~~~yaml
pre6g.io/total-iterations: "640"
~~~

仍會被轉成：

~~~text
candidate work_unit = training_iteration
total_work_units = 640
~~~

但這只代表 static semantic declaration，不代表 marker-free detector 已證明 execution_cycle 與 training_iteration 一一對應。

## 自動選點 gate

所有條件成立才允許 total-job automatic placement：

- runtime adapter status=ready；
- power adapter status=ready；
- marker-free detector stability pass；
- semantic binding status=bound；
- runtime work_unit 與 workload spec 相符；
- total_work_units 已知；
- model schema versions 相符；
- ood=false；
- confidence 達門檻；
- target-process trace detector至少觀察三個完整 cycles；
- Netdata/DCGM alignment quality達門檻；
- 必要 feature 無缺失；
- target CUDA process/context 已辨識；
- sharing state完整；
- node-bound power model綁定 candidate node + physical GPU UUID。

若 semantic binding 或 total work 未知，平台保持 profile-only / relative-performance mode。


## Current runtime-model implementation

The current deployment repository includes an offline reference runner:

~~~text
scripts/run_unified_trace_model.py
~~~

It preserves the clean/high-load grouped evaluation structure from the validated reference workspace, but changes the telemetry contract for deployment alignment.

Required per sample:

~~~text
condition
workload_id
sqlite_path
detection_json
target_runtime_ms
timestamps_json
telemetry_csv
~~~

Optional:

~~~text
trace_runtime_ms
baseline_repeat_cv_percent
~~~

Pre-run telemetry window:

~~~text
anchor = application_start_ns
fallback = profile_start_ns

window =
[anchor - 5 s, anchor)
~~~

The source is timestamps.json plus canonical aligned Netdata/DCGM telemetry. iterations.csv is never used to locate the window.

All clean/high-load samples are required to provide telemetry in this deployment-oriented runner. This avoids reproducing the historical missing-telemetry/condition-identity confound.

The runner remains an offline model-development/reference evaluator. A future production runtime adapter must load a frozen model/scaler/feature-schema bundle and perform single-request inference rather than retraining inside the scheduling path.


## Frozen runtime model versus model-development runner

Two runtime paths now coexist and must not be confused.

Model-development / offline evaluation:

~~~text
scripts/run_unified_trace_model.py
~~~

This runner may fit models, perform grouped validation, and compare model variants. It is not called by the production scheduler.

Control-side deployment inference:

~~~text
scripts/predict_runtime.py
src/pre6g_experiment/runtime_model.py
models/runtime/<frozen-model>.json
~~~

The production inference path:

~~~text
runtime-features.json
  -> model/schema binding
  -> frozen standardization
  -> frozen coefficients
  -> predicted_runtime_ms
~~~

It does not:

~~~text
select alpha
fit Ridge
run cross-validation
read C01-C24 model-development samples
read iterations.csv
use NVTX
~~~

## Current frozen deployment-smoke bundle

~~~text
models/runtime/RTX5090_yolo_trace_only_v1.json
~~~

Binding:

~~~text
device_id        RTX5090
workload_family  YOLO26 validation family
detector_profile yolo-v1
detected_unit    execution_cycle
model_type       ridge_log_runtime
alpha            0.1
role             deployment-smoke
~~~

The final alpha is frozen from the prior model-development evidence. Deployment inference never re-selects it.

The bundle stores:

~~~text
feature_names
feature means
feature scales
intercept
coefficients
target semantics
training provenance
binding limitations
~~~

C03 component smoke:

~~~text
predicted_runtime_ms = 109.251714
reference_runtime_ms = 118.080153
smoke APE            = 7.48%
~~~

This 7.48% value is only a deployment smoke comparison because C03 is represented in the final-fit dataset. Held-out/generalization evidence remains the separate Pre6G_result grouped evaluation.

## Model routing rule

A frozen runtime model may be used only when its binding matches the candidate result.

At minimum:

~~~text
device_id
detected_unit
detector_profile
required feature names
model schema version
~~~

Mismatch means reject prediction; never silently reuse the RTX5090 model on RTX4090.

The long-term registry should resolve:

~~~text
(candidate node/device, workload/model family, detector profile)
  -> frozen runtime model bundle
~~~

Unknown or unmatched candidates remain profile-only until a valid bundle exists.
