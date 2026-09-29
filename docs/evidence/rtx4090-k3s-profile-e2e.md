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
