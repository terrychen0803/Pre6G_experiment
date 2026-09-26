# k3s Cluster Baseline

本文件保存 Pre6G Experiment 第一次實際 k3s 多 GPU 整合測試的叢集基線，目的在於讓後續 profiling、Netdata、runtime/power prediction 與 production placement 都有可追溯的環境依據。

> Snapshot date: 2026-09-26
>
> 本 repository 為 public repository，因此不保存 master/worker 的實際網路位址。需要重新確認時，請在 control-plane 執行 `kubectl get nodes -o wide`。

## 1. Control plane

| Item | Value |
|---|---|
| Kubernetes distribution | k3s |
| k3s version | `v1.35.4+k3s1` |
| Control-plane node | `icclz2` |
| Control-plane OS | Ubuntu 22.04.4 LTS |
| Control-plane kernel | `6.8.0-136-generic` |
| NVIDIA RuntimeClass | `nvidia` available |

目前 cluster 同時存在 `nvidia` 與 `nvidia-experimental` RuntimeClass。Pre6G application/profile Job 預設使用 `runtimeClassName: nvidia`。

## 2. Target RTX worker mapping

本輪實驗的目標是 RTX 3090、RTX 4090、RTX 5090 三個 worker。

| GPU | Kubernetes node | Node status | OS / kernel | GPU resource | Device plugin | DCGM exporter | Netdata child |
|---|---|---|---|---|---|---|---|
| RTX 3090 | not discovered in current node list | **BLOCKED** | unknown | unknown | unknown | unknown | unknown |
| RTX 4090 | `iccl-s3-251230` | Ready | Ubuntu 24.04.3 / `6.17.0-40-generic` | `nvidia.com/gpu.shared: 4` | Running | Running | Running |
| RTX 5090 | `mirc516-20250605` | Ready | Ubuntu 24.04.3 / `7.0.0-30-generic` | `nvidia.com/gpu.shared: 4` | Running | Running | **Evicted** |

### RTX 3090 blocker

The expected RTX 3090 worker was not present in the current output of:

```bash
kubectl get nodes -o wide
```

Therefore the platform must not treat RTX 3090 as an eligible candidate node yet. Before profiling it, confirm that the worker has joined the current k3s cluster, is `Ready`, and advertises the expected NVIDIA GPU resource.

## 3. GPU resource contract

The Ready RTX 4090 and RTX 5090 workers advertise the shared GPU resource:

```text
nvidia.com/gpu.shared: 4
```

The RTX 4090 node also reports `nvidia.com/gpu: 0`, which is consistent with the cluster exposing the time-sliced/renamed shared resource rather than an allocatable exclusive `nvidia.com/gpu` resource.

For the current deployment domain, Profile Job and production Job should therefore request:

```yaml
resources:
  requests:
    nvidia.com/gpu.shared: "1"
  limits:
    nvidia.com/gpu.shared: "1"
```

Do not silently switch between `nvidia.com/gpu.shared` and `nvidia.com/gpu` between dry-run and production. The sharing strategy/resource contract is part of the experiment metadata.

## 4. NVIDIA runtime and monitoring state

The cluster currently exposes:

```text
RuntimeClass: nvidia
RuntimeClass: nvidia-experimental
```

The NVIDIA device plugin is Running on the verified RTX 4090 and RTX 5090 workers. DCGM exporter is also Running on both verified workers.

This is sufficient to proceed to GPU scheduling/CUDA smoke tests on those two nodes, but it does not yet prove that the Nsight Systems profile image and host integration are compatible with each worker.

## 5. Netdata readiness

For the target workers:

- RTX 4090 / `iccl-s3-251230`: Netdata child is Running.
- RTX 5090 / `mirc516-20250605`: the latest Netdata child is Evicted.
- RTX 3090: not auditable until the node is visible in the cluster.

The RTX 5090 Netdata state is a blocker for formal energy inference. Profiling artifacts may still be collected for runtime-pipeline debugging, but the node must not enter production energy ranking until Netdata readiness and the required feature contract are restored and audited.

The required follow-up remains:

```bash
kubectl -n netdata get pods -o wide
python scripts/audit_netdata.py ...
```

and all required contexts/units/non-NaN values, including the expected GPU process/load features, must pass the Netdata contract.

## 6. Other nodes observed in the cluster

These nodes are currently outside the target RTX 3090/4090/5090 experiment set:

| Node | Status | Notes |
|---|---|---|
| `gx10-c206` | Ready | advertises `nvidia.com/gpu.shared: 4`; Netdata child currently CrashLoopBackOff |
| `icclz1` | NotReady | GPU shared resource exists but node is not eligible while NotReady |
| `iccls2` | NotReady | NVIDIA-related Pods are not healthy; not eligible |
| `icclz2` | Ready | control-plane node; not part of this GPU candidate set |

These nodes must not be mixed into the RTX three-node experiment unless the experiment definition is explicitly expanded.

## 7. Immediate integration-test gates

Before running the full 120-second three-node profiling experiment, complete the following gates in order:

1. Restore/join the RTX 3090 worker and confirm its Kubernetes node name.
2. Restore the RTX 5090 Netdata child and run the Netdata feature audit.
3. Run a pinned GPU scheduling smoke test on RTX 4090 and RTX 5090, then RTX 3090 after it joins.
4. From each Pod, verify the expected GPU model with `nvidia-smi`.
5. Verify x86_64 Nsight Systems compatibility independently on each RTX worker.
6. Only then generate one pinned Profile Job per eligible node and start the fixed 120-second profiling flow.

## 8. Reproducible cluster audit commands

Use the following commands to refresh this snapshot:

```bash
echo "===== NODES ====="
kubectl get nodes -o wide

echo
echo "===== RUNTIME CLASS ====="
kubectl get runtimeclass

echo
echo "===== NVIDIA / GPU PODS ====="
kubectl get pods -A -o wide | grep -Ei 'nvidia|gpu' || true

echo
echo "===== NETDATA ====="
kubectl get pods -A -o wide | grep -i netdata || true

echo
echo "===== GPU RESOURCES ====="
for n in $(kubectl get nodes -o name); do
  echo
  echo "===== $n ====="
  kubectl describe "$n" | grep -E 'nvidia.com/gpu|nvidia.com/gpu.shared' || true
done
```

## 9. Compatibility note with Pre6G_profiling

The existing `Pre6G_profiling` Kubernetes validation was primarily performed on the GX10 reference environment. The RTX 3090/4090/5090 workers are a separate x86_64 deployment target and require their own Nsight Systems/runtime smoke test.

A successful GX10 Profile Job must not be treated as proof that the same profiler image, host Nsight path, architecture, or tracing configuration is valid on all RTX workers.
