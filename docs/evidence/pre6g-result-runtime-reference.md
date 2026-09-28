# Pre6G_result runtime reference evidence

This document records implementation-oriented reference evidence imported from the current Pre6G_result workspace. It is used to guide the Pre6G platform implementation. It is not a statement that the full production pipeline or arbitrary-workload generalization is already validated.

Source workflow:

~~~text
Pre6G_result
  analysis/high_load_01/evaluate_trace_event_periods.py
  analysis/high_load_01/run_unified_trace_model.py
~~~

## Marker-free detector reference

The reference detector uses CUDA kernel start timestamps and kernel short-name IDs. NVTX is joined after detection for audit only.

### Clean RTX5090

Adaptive deployment policy:

~~~text
profiles                 24
coverage                 24/24 = 100%
mean emission horizon    9.38 s
prefix mean APE          0.33%
prefix median APE        0.27%
prefix P90 APE           0.63%
prefix max APE           1.15%
prefix <=10%             100%
full-run mean APE        0.56%
full-run median APE      0.34%
~~~

### High-load RTX5090

Adaptive deployment policy:

~~~text
profiles                 24
coverage                 24/24 = 100%
mean emission horizon    13.54 s
prefix mean APE          1.47%
prefix median APE        1.14%
prefix P90 APE           3.72%
prefix max APE           4.46%
prefix <=10%             100%
full-run mean APE        11.04%
full-run median APE      8.41%
~~~

The high-load full-run error is not purely detector error. The reference report explicitly notes that it also includes future load drift after the early profiling window.

## High-load runtime prediction reference

The trace runtime model uses marker-free trace features and predicts the median unprofiled steady-state runtime.

~~~text
profiles                         24
runtime MAPE                     7.42%
runtime median APE               6.87%
runtime P90 APE                 12.31%
runtime max APE                 17.50%
within 10%                      79.17%
direct detected-period MAPE     17.76%
~~~

This supports keeping runtime prediction as a separate stage after period detection instead of treating the detected profile period as the final unprofiled runtime.

## Unified clean/high-load reference

Selected combined-condition results from the existing RTX5090 clean/high-load evaluation:

| Model | Combined MAPE |
|---|---:|
| trace-only | 10.43% |
| trace + load flag | 9.47% |
| trace + load interactions | 6.27% |
| trace + pre-run Netdata | 9.48% |
| trace-regime MoE | 7.53% |
| soft trace-regime interactions | 6.75% |

Strict transfer reference:

~~~text
clean -> clean trace-only MAPE      3.89%
clean -> high-load trace-only MAPE 68.70%
high-load -> clean trace-only MAPE 208.71%
~~~

These strict-transfer results show that current-condition evidence cannot be assumed interchangeable with clean-condition evidence in the current implementation.

## Production alignment changes in Pre6G_experiment

The deployment repository intentionally changes four integration details while preserving the reference detector/runtime-model logic:

1. NVTX_EVENTS is optional audit data, not a required production table.
2. Target globalPid/contextId is isolated before period detection. Multi-group traces fail closed unless a target is explicitly selected.
3. The existing 20–2000 ms range and supported 2x harmonic correction are named yolo-v1; they are not claimed as generic workload rules.
4. Pre-run telemetry is anchored by timestamps.json and canonical aligned Netdata/DCGM timestamps. Production-oriented feature extraction never uses iterations.csv to locate the pre-run window.

## Scope limitations

The reference numbers above come from the current RTX5090 YOLO workload collection and its clean/high-load conditions.

They do not establish:

- RTX4090 accuracy;
- arbitrary-workload accuracy;
- arbitrary-load generalization;
- production power-model readiness;
- production end-to-end scheduling accuracy.

The immediate project goal is to reproduce the validated algorithmic path inside the deployment architecture, then validate the complete RTX4090/RTX5090 workflow under the same marker-free contracts.


## Deployment smoke completed in Pre6G_experiment

The validated reference logic has now been exercised through the deployment-oriented path.

RTX5090 C03 marker-free detector:

~~~text
detected execution_cycle          129.988614 ms
detector confidence               0.8399946
same-window NVTX audit oracle     132.1907935 ms
same-window detector APE          1.6659%
full-steady audit oracle          126.65958 ms
~~~

The production run did not use NVTX or iterations.csv. NVTX was revealed only after the marker-free output was frozen.

Control-side frozen runtime inference:

~~~text
frozen model                      RTX5090_yolo_trace_only_v1
predicted runtime                 109.251714 ms
C03 unprofiled smoke reference    118.080153 ms
smoke comparison APE              7.4766%
~~~

This 7.48% is a component/deployment smoke comparison, not a held-out generalization result, because C03 is represented in the 48-sample final-fit dataset used to freeze the deployment-smoke model.

The production-oriented control flow is therefore:

~~~text
worker marker-free trace
  -> runtime-features.json
  -> shared artifact handoff
  -> control-side frozen model
  -> runtime-prediction.json
~~~

Manual SCP was used only to validate the interface before the Kubernetes artifact handoff is automated.
