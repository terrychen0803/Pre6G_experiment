# YOLO26 formal dry-run 001 failure evidence

Date: 2026-10-01 (Asia/Taipei)

Run ID: `yolo26-formal-dryrun-001`

This record preserves the first live formal-workload dry-run failure before the training-only workload revision.

## Observed failure

The integrated formal preflight passed. During `--execute`, the RTX5090 Job `pre6g-dryrun-yolo26-formal-dryrun-001-mirc516-20250605` failed with Pod container `exitCode=1`. The profiler started at `2026-09-30T21:45:12Z` and finished at `2026-09-30T21:46:33Z`, so this was not the 300-second Job deadline.

The YOLO trainer reached epoch 6 of 1639 and raised:

```text
RuntimeError: Fitness collapse detected but no valid last.pt is available for recovery
```

The effective trainer settings printed by Ultralytics showed `val=True`, `patience=100`, and `save=False`. The formal source Job had intended `patience=0`, exposing a source/profile semantic mismatch. Because validation fitness recovery depends on a valid checkpoint but checkpoint saving was disabled, this accuracy-oriented recovery path is unsuitable for the synthetic runtime/energy benchmark.

Telemetry itself completed successfully for the failed interval: 33 Netdata samples, 33 DCGM samples, 33 aligned samples, alignment coverage 1.0, and alignment quality `pass=true`. Nsight also generated `profile.nsys-rep`.

## Resolution

Formal workload revision `v2-training-only` fixes the benchmark semantics to:

- `val=False`
- `save=False`
- `patience=0`

The fixed source Job and both node profile templates must carry the same policy. Cross-node preparation and full-run planning now reject formal inputs if those options or the `pre6g.io/formal-workload-revision: v2-training-only` annotation are missing.

This failed run must not be used for ranking or formal ground-truth evaluation. The next live run must use a new ID, e.g. `yolo26-formal-dryrun-002`.
