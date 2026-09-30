# Power scaler range policy

## Decision

The supplied RTX4090 and RTX5090 power-model test paths do not implement
out-of-distribution detection. The min/max values stored in each
`scaler.json` are used for Min-Max preprocessing and describe the scaler/data
reference range.

Pre6G previously added an integration-side check:

~~~text
scaled < 0 or scaled > 1
  -> ood=true
  -> optional rejection
~~~

That check was not part of the original ONNX model and should not be described
as model-native OOD detection.

The current policy is:

~~~text
scaled < 0 or scaled > 1
  -> range_exceeded=true
  -> record range_warnings
  -> continue ONNX inference
  -> do not clip
  -> do not reject ranking solely for this reason
~~~

## Terminology

Use:

~~~text
scaler reference range
range_exceeded
range_warnings
extrapolation relative to scaler reference range
~~~

Do not infer from the scaler artifact alone that the interval is a formal
training-domain boundary. In particular, avoid calling it a "training range"
unless training provenance establishes that fact.

## Separation from runtime OOD

This policy applies only to the node-bound **power models**.

Runtime prediction may still have its own OOD/confidence policy because the
runtime model is a separate model family with a separate deployment contract.

## Current formal-run interpretation

The previously generated power predictions remain valid model outputs:

~~~text
RTX4090 representative predicted node power
  = 354.3231773345533 W

RTX5090 representative predicted node power
  = 462.28196331549725 W
~~~

The earlier integration labels are reinterpreted as scaler-range diagnostics:

~~~text
RTX4090:
  GPU Mem Used(MB) exceeded scaler reference range in 109/109 rows

RTX5090:
  GPU Power(W) exceeded scaler reference range in part of the formal window
~~~

These diagnostics do not change the current steady-energy calculation or
ranking result:

~~~text
RTX4090 predicted steady gross energy = 21.5386 kJ
RTX5090 predicted steady gross energy = 57.1482 kJ
selected node                         = iccl-s3-251230 (RTX4090)
~~~

## Remaining power-model readiness items

Scaler-range exceedance is no longer a readiness blocker. Remaining model
validation work is tracked separately, including held-out accuracy evidence and
a frozen missing-value policy.
