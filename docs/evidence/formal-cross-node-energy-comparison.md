# Formal cross-node steady-energy comparison

## Scope

This document records the current YOLO26 formal cross-node comparison using the
model outputs as provisional research results so the end-to-end placement and
production-validation workflow can continue.

This is an explicit experiment-level override of the production readiness gate.
It does **not** change the strict automatic-ranking contract in
`src/pre6g_experiment/decision.py`. Power-model OOD/readiness limitations are
preserved in the evidence.

The comparison uses:

~~~text
work_unit           training_iteration
total_work_units    960
energy objective    gross node-total steady energy
~~~

Formula:

~~~text
T_predicted_steady
  = predicted_runtime_ms_per_work_unit / 1000
  × total_work_units

E_predicted_steady_gross
  = predicted_node_total_steady_power_w
  × T_predicted_steady
~~~

The observed profiling-window integrated energy is not used as the ranking
horizon.

## RTX4090

~~~text
node                                  iccl-s3-251230
runtime_model                         RTX4090_yolo_trace_only_v1
predicted_runtime_ms_per_work_unit    63.320823290313726
total_work_units                      960
predicted_steady_runtime_s            60.78799035870118

power_model                           pdu1-outlet1-20260416-20260612
representative_power_method           time-weighted mean over natural-exit proxy window
predicted_node_total_steady_power_w   354.3231773345533
power_model_status                    validation_required
power_ood                             true
power_ood_detail                      GPU Mem Used(MB), 109/109 rows

energy_j_per_work_unit                22.43603529966374
predicted_steady_gross_energy_j       21538.59388767719
predicted_steady_gross_energy_kj      21.53859388767719
predicted_steady_gross_energy_wh      5.982942746576998
~~~

## RTX5090

~~~text
node                                  mirc516-20250605
runtime_model                         RTX5090_yolo_trace_only_v1
predicted_runtime_ms_per_work_unit    128.7727655060272
total_work_units                      960
predicted_steady_runtime_s            123.62185488578614

power_model                           pdu1-outlet7-rtx5090-20260416-20260612
representative_power_method           time-weighted mean over formal profile crop
predicted_node_total_steady_power_w   462.28196331549725
power_model_status                    validation_required
power_ood                             true
power_ood_detail                      GPU Power(W) exceeds recorded range in part of the window

energy_j_per_work_unit                59.529326859692404
predicted_steady_gross_energy_j       57148.153785304705
predicted_steady_gross_energy_kj      57.14815378530471
predicted_steady_gross_energy_wh      15.874487162584643
~~~

## Provisional research placement

Using the model predictions directly for this experiment-level continuation:

~~~text
RTX4090 predicted steady gross energy   21.5386 kJ
RTX5090 predicted steady gross energy   57.1482 kJ
provisional selected node               iccl-s3-251230
~~~

The RTX4090 value is approximately 62.31% lower than the RTX5090 value for the
same 960-work-unit steady-work estimate.

This selection is a **provisional research placement**, not a production-ready
automatic ranking claim, because both node-bound power bundles remain
`validation_required` and currently report OOD observations.

## Next phase

Continue Phase 09 with an unprofiled production Job deep-copied from the
original source Job and pinned to:

~~~text
kubernetes.io/hostname: iccl-s3-251230
~~~

The production Job must not contain Nsight, profiling collectors, or a dry-run
timeout. Its ground truth is collected only for post-decision evaluation and is
not fed back into the production prediction path.


## Final ranking test result

The provisional cross-node ranking script completed successfully with the
current frozen runtime predictions and node-bound power-model outputs.

~~~text
ranking_mode = research-provisional-model-output
selected_node = iccl-s3-251230
selected_device = RTX4090
selected_energy_kj = 21.538593887677195
energy_reduction_vs_runner_up_percent = 62.31095414106675
~~~

Final ordering:

~~~text
Rank 1: iccl-s3-251230 (RTX4090)
        runtime = 60.788 s
        power   = 354.323 W
        energy  = 21.539 kJ

Rank 2: mirc516-20250605 (RTX5090)
        runtime = 123.622 s
        power   = 462.282 W
        energy  = 57.148 kJ
~~~

This completes the current requested workflow through the
`RANKED -> NODE_SELECTED` stage. Production-job execution and ground-truth
validation are intentionally deferred.
