# RTX 5090 / PDU1 Outlet7 power model

This node-bound bundle is associated with the repository's RTX 5090 node:

| Binding | Value |
|---|---|
| Kubernetes node | `mirc516-20250605` |
| Physical GPU UUID | `GPU-a4e6b1ee-8a31-991f-dc82-fdab833483c4` |
| GPU model | NVIDIA GeForce RTX 5090 |

The ONNX graph consumes three float32 inputs assembled from seven simultaneous
telemetry features:

| ONNX input | Shape | Features, in order |
|---|---:|---|
| `x_core` | `N x 1` | `CPU User%` |
| `x_direct` | `N x 1` | `GPU Power(W)` |
| `x_other` | `N x 5` | `Top2 CPU%`, `Top1 CPU%`, `Top3 CPU%`, `Mem Used(MB)`, `Mem Free(MB)` |

Each feature is min-max scaled according to `scaler.json`. The model output is
converted to watts with `prediction * 487.53 + 91.64`. Inputs are not silently
clipped.

## Simple inference

From the repository root:

```bash
python -m pip install -r requirements-power-model.txt
python scripts/predict_power_eq.py \
  examples/power/pdu1-outlet7-rtx5090-sample.json \
  --model models/power/bundles/pdu1-outlet7-20260416-20260612/model.onnx \
  --scaler models/power/bundles/pdu1-outlet7-20260416-20260612/scaler.json \
  --output generated/pdu1-outlet7-rtx5090-predictions.json \
  --reject-ood
```

For aligned Netdata/DCGM data and a platform-compatible smoke summary, use:

```bash
python scripts/predict_power_from_aligned_telemetry.py \
  --aligned-telemetry aligned-telemetry.json \
  --bundle-dir models/power/bundles/pdu1-outlet7-20260416-20260612 \
  --output-series generated/rtx5090-power-series.json \
  --output-summary generated/rtx5090-power-summary.json \
  --reject-ood
```

## Validation status

The node/GPU association is recorded. The model owner confirmed that
`ACTUAL_POWER_W` is measured by the external power meter and represents
whole-node wall power rather than NVIDIA GPU power, so the target is recorded
as `node-total-power`.

The bundle remains `validation_required` because held-out
validation metrics, missing-value policy, and a formal OOD policy are not yet
frozen. It is therefore not yet eligible for automatic energy ranking.

The original Notebook is retained for provenance. Its sample-data JSON was not
supplied, several comments are encoding-damaged, and its plot title mistakenly
names Outlet1 instead of Outlet7.


## Energy objective

The current Pre6G scheduling objective uses this model's whole-node wall-power
prediction directly when estimating gross node energy over the predicted task
runtime:

~~~text
E_predicted_steady_gross = P_predicted_node_steady × T_predicted_steady
~~~

An idle-power baseline is not required for this primary metric. If a future
study estimates task-incremental energy on a loaded node, it must use a
separately validated background-only counterfactual rather than assuming
machine idle power is the correct baseline.

## Artifact integrity

| File | SHA-256 |
|---|---|
| `model.onnx` | `9ae2f0db373ad39afcec841f7e9215d38b91d2f6cc277b39ac25b714a1e54754` |
| `scaler.json` | `31f2134066b4cd90fe4c8f74fe3017fa53ec3dbe2a0aa9ce902cdc3b967bdcd3` |
| `original-test.ipynb` | `d0290d6c67ac3a8b36d0eb9c9577a3ce9224c81888a6f047284520267d87fc47` |
