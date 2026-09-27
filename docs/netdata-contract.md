# Telemetry feature contract

## Scope

The production monitoring path is split by responsibility:

- Netdata Parent/Child: system, CPU, memory, temperature, and top CPU-process telemetry.
- NVIDIA DCGM Exporter: NVIDIA device-level GPU telemetry.
- Nsight Systems 2026: target-process CUDA behavior for runtime prediction.

Do not require Netdata itself to expose NVIDIA devices. The validated Netdata child container does not use runtimeClassName=nvidia, does not expose /dev/nvidia*, and does not contain nvidia-smi.

## Canonical feature schema

The platform preserves the original 18-field logical schema:

~~~
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
~~~

The first 16 fields have a validated collection path.

Top1 GPU% and Top2 GPU% are optional platform extensions until a validated per-process GPU collector is deployed. If a specific node-bound power model lists either field as required, that model is not ready for automatic ranking.

## Source mapping

### Netdata

Netdata provides:

~~~
CPU User%
CPU System%
CPU IOWait%
Load 1min
Load 5min
Load 15min
Mem Used(MB)
Mem Free(MB)
CPU Temp(°C)
Top1 CPU%
Top2 CPU%
Top3 CPU%
~~~

The controller queries historical data through the Netdata Parent and target hostname:

~~~
/host/<hostname>/api/v1/...
~~~

Child agents intentionally bind localhost:19999 and stream to the Parent.

### DCGM Exporter

DCGM provides:

| Canonical feature | DCGM metric |
|---|---|
| GPU Util% | DCGM_FI_DEV_GPU_UTIL |
| GPU Mem Used(MB) | DCGM_FI_DEV_FB_USED |
| GPU Temp(°C) | DCGM_FI_DEV_GPU_TEMP |
| GPU Power(W) | DCGM_FI_DEV_POWER_USAGE |

Validated exporter cadence:

~~~
DCGM_EXPORTER_INTERVAL=1000
~~~

The collector should address the exporter Pod for the intended node directly when node identity matters; a load-balanced Service must not accidentally return telemetry from another node.

## Collector metadata

Each canonical feature must preserve source metadata. Example:

~~~json
{
  "canonical_name": "GPU Util%",
  "source": "dcgm",
  "source_metric": "DCGM_FI_DEV_GPU_UTIL",
  "unit": "%"
}
~~~

Feature names used by the model must not depend on collector-specific naming.

## Time contract

All experiment timestamps use UTC Unix nanoseconds.

The wrapper should save:

~~~
pre_window_start_ns
application_start_ns
profile_start_ns
steady_window_start_ns
steady_window_end_ns
profile_end_ns
application_end_ns
post_window_end_ns
~~~

Netdata continuously collects and is queried after the run with absolute after/before timestamps.

DCGM Exporter is not treated as the historical database for this workflow. scripts/collect_dcgm.py actively polls the selected exporter during the experiment and stores each sample.

The DCGM sample timestamp is the midpoint between request start and response end:

~~~
sample_timestamp_ns = (request_start_ns + request_end_ns) / 2
~~~

This reduces timestamp bias from API request latency.

## Alignment contract

Do not require exact timestamp equality between Netdata and DCGM.

Current alignment policy:

~~~
method = nearest timestamp
tolerance = 750 ms
minimum coverage = 90%
maximum Netdata gap = 2 s
maximum DCGM gap = 2 s
~~~

The aligned artifact should retain:

~~~
timestamp_ns
netdata_timestamp_ns
dcgm_timestamp_ns
alignment_delta_ms
~~~

and quality metadata:

~~~
netdata_samples
dcgm_samples
aligned_samples
alignment_coverage
median_alignment_delta_ms
max_alignment_delta_ms
max_netdata_gap_s
max_dcgm_gap_s
~~~

The 2026-09-27 RTX5090 validation produced 100% coverage, median absolute delta 284.8 ms, and maximum delta 490.5 ms.

## Netdata readiness audit

Use scripts/audit_netdata.py only for the Netdata-owned system/CPU portion of the contract.

Example:

~~~bash
python scripts/audit_netdata.py   --node worker-4090=http://<parent-access-path>/host/<4090-hostname>   --node worker-5090=http://<parent-access-path>/host/<5090-hostname>   --output netdata-audit.json
~~~

The exact Parent access URL depends on where the script runs. Do not expose child port 19999 solely for this audit.

GPU readiness is audited separately through DCGM.

## DCGM collection

Example:

~~~bash
python scripts/collect_dcgm.py   --url http://<dcgm-pod-ip>:9400/metrics   --node <node-name>   --gpu-uuid GPU-...   --duration-s 120   --interval-ms 1000   --output dcgm.csv
~~~

The exporter-side collection interval and client-side polling interval are separate. Formal experiments should keep both near 1 s.

## Aggregation

Power-model inference must use exactly the aggregation and preprocessing defined by that node model's manifest.

Examples:

~~~json
{
  "GPU Power(W)": "mean",
  "GPU Util%": "mean",
  "CPU Temp(°C)": "last",
  "Top1 CPU%": "p95"
}
~~~

The deployment code must not independently choose mean/latest/p95.

If the model consumes per-timestamp feature vectors, keep the aligned time series and run the model at each aligned sample. If the model was trained on window aggregates, aggregate only according to its frozen training manifest.

## Power and energy semantics

The first production ranking contract accepts node-bound models whose output is:

~~~
target_semantics = node-total-power
target_unit = W
~~~

For a node-specific idle baseline:

~~~
P_incremental(t) = max(0, P_predicted_node(t) - P_idle_node)
~~~

and energy is integrated over time.

If GPU Power(W) is an input while the model target is NVIDIA GPU power itself, this can create target leakage. The model manifest must describe target semantics and required features explicitly; deployment must not infer them.

## Required-feature gate

Platform telemetry readiness and model readiness are separate.

A model may require only the validated 16-field core, or it may require additional features such as Top1 GPU% / Top2 GPU%.

Before inference:

1. Resolve the node-bound model.
2. Read required_features from its manifest.
3. Verify every required canonical feature is available and finite.
4. Reject missing features; never silently zero-fill them.
5. Verify node and physical GPU UUID binding.

See power-model-registry.md for the model binding contract.
