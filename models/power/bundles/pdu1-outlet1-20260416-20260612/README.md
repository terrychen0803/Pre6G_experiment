# PDU1 Outlet1 power model (2026-04-16 to 2026-06-12)

This bundle is annotated as the repository's RTX 4090 node model:

| Binding | Value |
|---|---|
| Kubernetes node | `iccl-s3-251230` |
| Physical GPU UUID | `GPU-39ace77c-cb0f-dd47-ae6b-12014c25b1d1` |
| GPU model | NVIDIA GeForce RTX 4090 |

This bundle predicts power in watts from five simultaneous CPU/GPU telemetry
features. The ONNX graph has three inputs:

| ONNX input | Shape | Features, in order |
|---|---:|---|
| `x_core` | `N x 1` | `CPU User%` |
| `x_direct` | `N x 1` | `GPU Power(W)` |
| `x_other` | `N x 3` | `GPU Mem Used(MB)`, `GPU Temp(°C)`, `CPU Temp(°C)` |

All five values are min-max scaled with `scaler.json`. The model output is
converted back to watts with `prediction * 516.72 + 0.0`. Inputs are not
clipped, because silently clipping telemetry can hide out-of-domain data.

## Run inference

From the repository root:

```bash
python -m venv .venv
source .venv/bin/activate              # Windows: .venv\Scripts\activate
python -m pip install -r requirements-power-model.txt
python scripts/predict_power_eq.py \
  examples/power/pdu1-outlet1-sample.json \
  --output generated/pdu1-outlet1-predictions.json \
  --reject-ood
```

Input can be JSON or CSV. JSON must contain an array of records (or an object
with a `records` array). The output preserves every input field and adds
`PREDICTED_POWER_W`.

The scaler artifact contains mojibake temperature names (`簞C`). The inference
adapter preserves artifact compatibility while accepting the readable forms
`°C` and `℃` in new input files.

## Current validation status

This bundle is **not registered as a production-ready Pre6G node model**. Its
RTX 4090 node/GPU association is now recorded, but the supplied files do not
identify idle power, sampling interval, train/validation split, test metrics,
missing-value policy, or a formal out-of-distribution policy. The original
notebook also refers to a sample-data JSON file that was not supplied.

The model owner confirmed that `ACTUAL_POWER_W` is measured by an external
power meter at PDU1 Outlet1 and represents whole-node wall power rather than
NVIDIA GPU power. The target is therefore recorded as `node-total-power`.

The bundle still remains `validation_required` because idle power, held-out
validation metrics, missing-value policy, and a formal OOD policy are not yet
frozen. These remaining gates must pass before automatic cross-node ranking.

## Artifact integrity

| File | SHA-256 |
|---|---|
| `model.onnx` | `123fe38042a51ce18aa202ebff1083cce9da80714b9ec7b564741679a85797fb` |
| `scaler.json` | `6e3938fccfe6da7c53432a0152dd4a6c1cce56b7c864903fefc117f7754cbbb2` |
| `original-test.ipynb` | `a9b634d3e42eff4971d250c14cd7f79e89b44a07c781415cce07d20765740a84` |

`original-test.ipynb` is retained for provenance. It is not the recommended
entry point because its paths assume all artifacts and the missing sample JSON
are in one directory, and several comments are encoding-damaged.
