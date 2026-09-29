# RTX4090 k3s formal profile evidence

## Formal Phase 07 120-second Profile Job PASS

The RTX4090 formal YOLO profile Job completed on the intended worker:

~~~text
Job                                 pre6g-formal-yolo26-4090-120s-v1
Task                                yolo26-formal-4090-120s-001
Node                                iccl-s3-251230
Device                              RTX4090
Status                              Complete (1/1)
Kubernetes Job duration             3m10s
Pod restarts                        0
Configured Nsight duration          120 s
~~~

The operator confirmed that an external, non-Kubernetes GPU workload was
running continuously on the RTX4090 throughout this formal profiling interval.
The run is therefore classified as:

~~~text
condition                           loaded_uncontrolled
background_load_source              external_non_k8s_gpu_workload
background_load_overlap             full formal profiling interval
~~~

The marker-free detector accepted an execution cycle without production
instrumentation inputs:

~~~text
detected_unit                       execution_cycle
detector_profile                    yolo-v1
detected_period_ms                  95.1193265
confidence                          0.9148343451610317
complete_cycles                     94
target_global_pid                   281476771872768
target_context_id                   1
uses_iterations_csv                 false
uses_nvtx                           false
uses_callbacks                      false
uses_epoch_labels                   false
uses_batch_labels                   false
~~~

The packaged ProfileResult status is
`ready-for-control-side-inference`. The runtime feature vector contains the
same seven trace-only features used by the frozen RTX4090 deployment-smoke
model:

~~~text
log_detected_period_ms              4.555132171827602
log_kernel_event_rate_hz            10.47849225784902
log_unique_kernel_count             4.430816798843313
log_median_kernel_duration_us       0.16889853646181388
log_mean_kernel_duration_us         1.2691095654453446
log_kernel_busy_fraction            -2.06790873466991
anchor_robust_cv                    0.045998265438231246
~~~

Trace summary:

~~~text
kernel_event_count                  319885
kernel_event_rate_hz                35542.77777777778
unique_kernel_count                 84
median_kernel_duration_us           1.184
mean_kernel_duration_us             3.5576832674242307
kernel_busy_fraction                0.1264499457777778
~~~

Small artifacts were copied to the shared artifact store under:

~~~text
results/yolo26-formal-4090-120s-001/iccl-s3-251230/
~~~

This milestone validates the formal marker-free runtime-profile path for the
loaded RTX4090 condition. Power/energy readiness is not claimed here; telemetry
alignment quality and the node-bound power-model inference still need to be
checked separately.


## Frozen RTX4090 runtime-model inference

The formal runtime features from the loaded RTX4090 profile were passed to the
frozen device-bound deployment-smoke model:

~~~text
model_id                    RTX4090_yolo_trace_only_v1
device_id                   RTX4090
detected_unit               execution_cycle
predicted_runtime_ms        63.320823290313726
detected_period_ms          95.1193265
detector_confidence         0.9148343451610317
feature_count               7
model_role                  deployment-smoke
~~~

The 95.1193265 ms detector period is an observed marker-free trace quantity.
The 63.320823290313726 ms value is the frozen model prediction per
`execution_cycle`; the detector period is not substituted for the prediction.

The model was trained from historical clean RTX4090 traces, while this formal
run was executed under an uncontrolled external GPU background load. Therefore
this result is retained as deployment-smoke evidence rather than a held-out
accuracy claim. Semantic work discovery and execution-cycle-to-work-unit binding
must be applied before extrapolating a steady workload runtime.


## RTX4090 semantic work discovery and steady-runtime aggregation

The formal YOLO source Job was resolved from static semantics without iteration
markers or callbacks:

~~~text
workload_family                    YOLO26
adapter                            yolo
epochs                             30
batch_size                         16
dataset_train_samples              512
steps_per_epoch                    32
total_work_units                   960
work_unit                          training_iteration
discovery_mode                     static-semantic-contract
~~~

The validated YOLO/yolo-v1 binding resolved one marker-free
`execution_cycle` to one `training_iteration`:

~~~text
cycles_per_work_unit               1.0
~~~

Combining the frozen RTX4090 model output with this work contract produced:

~~~text
predicted_runtime_ms_per_execution_cycle   63.320823290313726
predicted_runtime_ms_per_work_unit          63.320823290313726
total_work_units                            960
predicted_steady_runtime_s                  60.78799035870118
predicted_total_job_runtime_s               null
total_job_runtime_status                    pending-non-steady-overhead-model
aggregation_scope                           steady-work-only
~~~

The 60.78799 s value is the predicted semantic steady-work duration for the
complete 30-epoch workload. It is not the measured profile duration and it does
not include non-steady startup, validation, checkpointing, or teardown
overheads.


## RTX4090 historical Netdata replay and alignment PASS

The initial formal Job used the lowercase Kubernetes node name in the Netdata
Parent host route. Netdata registered this child as `ICCL-S3-251230`, and the
per-host route is case-sensitive. The original query therefore returned HTTP
404. The workload/profile trace did not need to be rerun.

The original absolute telemetry window was replayed from the continuously
collected Netdata Parent history using the corrected host route:

~~~text
Netdata parent host route          /host/ICCL-S3-251230
after_ns                           1790700241687260922
before_ns                          1790700389015322350
Netdata samples                    148
DCGM samples                       148
aligned samples                    148
alignment coverage                 1.0
median alignment delta             276.158295 ms
max alignment delta                276.449477 ms
max Netdata gap                    1.0 s
max DCGM gap                       1.000973357 s
alignment pass                     true
~~~

This repairs the telemetry chain for the existing formal RTX4090 run without
changing the runtime trace. The formal manifest was also corrected to use the
case-preserving Netdata Parent hostname for future runs.

Power-window cropping is intentionally deferred until the profiler termination
path is confirmed: a configured 120-second deadline may be projected from
`profile_start_ns` only when Nsight actually reached its configured duration,
not when the workload naturally exited early.


## RTX4090 natural-exit confirmation

The retained container runtime log resolves the formal capture termination
semantics. The telemetry wrapper recorded:

~~~text
command_returncode                  0
accepted_command_returncodes        [143]
command_returncode_accepted         true
~~~

The Nsight invocation was configured with `--duration=120`,
`--kill=sigterm`, and `--stop-on-exit=true`, but YOLO reported
`30 epochs completed` at 2026-09-30T00:45:55.294403352+08:00 and its final
workload output at 2026-09-30T00:45:55.500159060+08:00. Nsight subsequently
entered report collection/finalization and returned 0 rather than the accepted
duration-kill code 143.

Therefore this RTX4090 formal run is classified as:

~~~text
termination_mode                    natural-target-exit
configured_capture_limit_s          120
duration_kill_observed              false
outer_profile_start_ns              1790700245940091691
last_yolo_output_ns                 1790700355500159060
start_to_last_yolo_output_s         109.560067369
outer_profile_end_ns                1790700387015257021
~~~

The outer ~141.075 s wrapper interval includes Nsight/report finalization and
must not be used as the workload power horizon. Likewise,
`profile_start + 120 s` is not valid for this run because the target exited
naturally before the configured duration limit. Downstream power-window
processing must use a natural-exit-aware boundary; the retained final YOLO
wall-clock output is used as the current operational end marker for this
specific run, with the limitation that it is a log-derived application-end
proxy rather than a persisted profiler boundary.
