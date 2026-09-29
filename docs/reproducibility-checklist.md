# Reproducibility checklist

This document separates the **validated experiment contract** from the
remaining cluster-specific/bootstrap work required to reproduce the current
Pre6G experiment on a fresh system.

## Reproducibility levels

### Method / experiment contract

The repository currently records the intended experiment method:

- generic Kubernetes Job intake;
- marker-free target-process CUDA profiling;
- YOLO `yolo-v1` detector profile;
- semantic binding and total-work discovery;
- fixed Phase 07 formal capture policy:
  - configured Nsight capture = 120 s;
  - Job wall timeout = 300 s;
  - `--trace=cuda,nvtx,osrt`;
  - `--sample=none`;
  - `--cpuctxsw=none`;
  - `--duration=120`;
  - `--kill=sigterm`;
  - `--stop-on-exit=true`;
- natural workload completion before 120 s is preserved rather than restarted;
- Netdata Child/Parent continuous monitoring;
- post-run absolute-window Netdata historical query;
- in-run DCGM active polling;
- timestamp alignment / telemetry quality gate;
- runtime feature extraction and frozen runtime inference;
- node-bound power-model bundles and fail-closed ranking policy.

Short integration jobs are evidence/smoke tests only. They must not be reported
as the formal Phase 07 120-second profiling result.

## Validated current-cluster software contract

The current RTX4090/RTX5090 integration baseline records:

- k3s `v1.35.4+k3s1`;
- RuntimeClass `nvidia`;
- shared GPU resource `nvidia.com/gpu.shared`;
- Nsight Systems `2026.4.1.191-264138605071v0`;
- host Nsight install root
  `/opt/nvidia/nsight-systems-cli/2026.4.1`;
- container Nsight path `/opt/pre6g/nsight/bin/nsys`;
- DCGM exporter cadence approximately 1 s;
- Netdata/DCGM canonical feature and alignment contracts.

See `docs/cluster-baseline.md`, `docs/monitoring-preflight.md`, and
`docs/netdata-contract.md`.

## Already stored in Git

The following are version-controlled:

- runtime/marker-free/work-discovery implementation;
- telemetry historical-query / DCGM / alignment implementation;
- schemas and tests;
- runtime model bundles;
- RTX4090/RTX5090 power model bundles;
- experiment SOP and architecture;
- Nsight 2026 smoke manifest;
- namespace/RBAC baseline;
- current E2E evidence.

## Still required before calling a fresh-cluster deployment turnkey-reproducible

The following are not yet fully captured as portable deployment artifacts:

1. **Formal Phase 07 Job manifest/template**
   - The currently prepared RTX5090 120-second formal Job is still a
     control-plane `/tmp` manifest.
   - It must be committed only after the first formal run is validated so the
     repository contains the exact tested manifest/template.

2. **Monitoring installation manifests**
   - Netdata Parent/Child deployment configuration is not stored in this
     repository.
   - DCGM Exporter deployment configuration is not stored here.
   - A fresh cluster therefore cannot recreate monitoring from this repository
     alone yet.

3. **Shared artifact storage manifests**
   - The current NFS export, static PV
     `pre6g-artifacts-nfs`, and PVC
     `experiments/pre6g-artifacts` are documented, but their complete
     deployable manifests/bootstrap procedure are not yet version-controlled.

4. **NVIDIA shared-GPU bootstrap**
   - The repository documents the resource contract
     `nvidia.com/gpu.shared`, but does not yet contain the complete fresh-node
     NVIDIA device-plugin/time-slicing installation/configuration procedure.

5. **Nsight host installation**
   - The repository records the required version/path and Kubernetes mount
     contract, but Nsight Systems itself is a host prerequisite and is not
     installed by this repository.

6. **Portable endpoint resolution**
   - Current bring-up Jobs have used a node-specific DCGM exporter Pod IP.
   - Pod IPs are ephemeral and must not be frozen into a reproducible formal
     manifest.
   - The formal builder/controller should resolve the exporter for the target
     node at Job creation time, or use an equivalent node-aware stable
     endpoint.

7. **Immutable application/profiler image**
   - Current integration work uses runtime Git fetch and image tags.
   - A reproducible release should use a versioned profiler image and pin
     application images by digest.
   - Dataset/config identity or hashes must also be stored per formal run.

8. **Node-specific configuration**
   - Kubernetes node names and physical GPU UUIDs are deployment data, not
     universal constants.
   - A fresh cluster must audit and bind its own node names/GPU UUIDs before
     model routing.

9. **RTX5090 temporary DiskPressure override**
   - The 10 GiB kubelet eviction override was an integration-smoke workaround,
     not a reproducible production baseline.
   - A fresh system should provision adequate storage and use an intentional
     kubelet policy rather than copying this workaround blindly.

## Target fresh-system reproduction sequence

Once the missing deployment assets above are committed, a new system should be
reproducible in this order:

~~~text
host / k3s bootstrap
  -> NVIDIA driver/runtime/device-plugin + GPU-sharing configuration
  -> Nsight Systems 2026.4.1 host installation
  -> Netdata Child/Parent deployment
  -> DCGM Exporter deployment
  -> shared RWX artifact backend
  -> namespace / RBAC
  -> cluster + clock + GPU + monitoring preflight
  -> Nsight smoke
  -> workload intake / short compatibility profile
  -> formal Phase 07 120-second profile
  -> marker-free detection / runtime features
  -> Netdata historical query + DCGM alignment
  -> frozen runtime inference
  -> node-bound power inference
  -> quality / OOD / model gates
  -> candidate comparison / production placement
~~~

## Release criterion

Do not call the repository **fresh-cluster turnkey reproducible** until:

- the exact formal 120-second Job/template has passed and is committed;
- monitoring and artifact-storage deployment are reproducible from Git;
- target-node DCGM resolution has no ephemeral Pod-IP dependency;
- application/profiler images and datasets/configs are immutable or hashed;
- a clean-cluster rehearsal follows the documented sequence successfully.

Until then, the repository is **method-reproducible and current-cluster
traceable**, but not yet a one-command fresh-cluster reconstruction.
