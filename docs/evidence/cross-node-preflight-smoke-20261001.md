# Cross-node preflight and smoke evidence

Date: 2026-10-01 (Asia/Taipei)

This evidence records the read-only Kubernetes preflight and short, non-training smoke test reported from the master host. It covers RTX4090 and RTX5090 only; RTX3090 remains excluded.

## Cluster preflight

| Check | Result |
| --- | --- |
| `experiments` namespace | Active |
| `nvidia` RuntimeClass | Present |
| `experiments/pre6g-artifacts` PVC | Bound, 100 GiB, RWX |
| `pre6g-artifacts-nfs` PV | Bound, Retain, NFS export path `/srv/pre6g-artifacts` |
| RTX4090 node `iccl-s3-251230` | Ready, no DiskPressure, `nvidia.com/gpu.shared: 4` |
| RTX5090 node `mirc516-20250605` | Ready, no DiskPressure, `nvidia.com/gpu.shared: 4` |
| DCGM exporter | Ready Pods were listed on both candidate nodes |
| Integrated `--preflight-only` | Passed; no cluster resource was created |

The NFS server address and Pod IPs are omitted from this public record.

## Smoke test

The integrated `--smoke-only` command completed with `[smoke passed]`. Its per-node probes check GPU UUID visibility, the mounted Nsight CLI version, GitHub reachability, the required DCGM metrics for the candidate GPU UUID, the Netdata host metrics endpoint, and cross-node reads of small marker files on the shared PVC.

Master-side follow-up confirmed both marker files under `smoke/yolo26-smoke-001/`. A label query returned no remaining smoke Pods in the `experiments` namespace.

## Software tests and scope

- GitHub Actions unit tests passed for commit `c08ff9669e762d1a51e9cd58b10ae6b0192db6a2`: [workflow run](https://github.com/terrychen0803/Pre6G_experiment/actions/runs/36768682898).
- No 120-second profiling Job was submitted.
- No master-side runtime/power prediction or ranking was performed.
- No 30–50 minute training run or PDU comparison was performed.

This evidence validates prerequisites and basic connectivity only. It does not establish successful Nsight trace capture, model prediction quality, or end-to-end training results.

