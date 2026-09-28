# k3s Cluster Baseline

本文件保存 Pre6G Experiment 實際 k3s 多 GPU 整合測試的叢集基線，目的在於讓後續 profiling、Netdata、runtime/power prediction 與 production placement 都有可追溯的環境依據。

> Snapshot date: 2026-09-28
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

## 2. Current RTX experiment scope

本輪整合測試先以 RTX 4090 與 RTX 5090 為正式候選節點。RTX 3090 因本機儲存空間不足，暫緩加入本輪 Pod / profiling 驗證，待儲存空間整理後再重新納入。

| GPU | Kubernetes node | Node status | OS / kernel | GPU resource | GPU smoke | Nsight 2026 CUDA smoke |
|---|---|---|---|---|---|---|
| RTX 3090 | deferred | not in current test scope | not re-audited | not re-audited | Deferred | Deferred |
| RTX 4090 | `iccl-s3-251230` | Ready | Ubuntu 24.04.3 / `6.17.0-40-generic` | `nvidia.com/gpu.shared: 4` | PASS | PASS |
| RTX 5090 | `mirc516-20250605` | Ready | Ubuntu 24.04.3 / `7.0.0-30-generic` | `nvidia.com/gpu.shared: 4` | PASS | PASS |

已在 Pod 內確認 GPU identity：

- RTX 4090：NVIDIA GeForce RTX 4090，24,564 MiB，driver 595.84。
- RTX 5090：NVIDIA GeForce RTX 5090，32,607 MiB，driver 580.173.02。
- 兩個節點皆為 Linux `x86_64`。

## 3. GPU resource contract

RTX 4090 與 RTX 5090 worker 都 advertise：

```text
nvidia.com/gpu.shared: 4
```

因此本輪 Profile Job 與 production Job 使用：

```yaml
resources:
  requests:
    nvidia.com/gpu.shared: "1"
  limits:
    nvidia.com/gpu.shared: "1"
```

且必須使用：

```yaml
runtimeClassName: nvidia
```

Profile 與 production 不得在 `nvidia.com/gpu.shared` 與 `nvidia.com/gpu` 之間靜默切換。sharing strategy/resource name 必須記入 experiment metadata。

## 4. Nsight Systems 2026 contract

RTX 4090 與 RTX 5090 均已確認安裝並成功在 Kubernetes Pod 內使用：

```text
NVIDIA Nsight Systems version 2026.4.1.191-264138605071v0
```

Host installation root：

```text
/opt/nvidia/nsight-systems-cli/2026.4.1
```

安裝 layout：

```text
2026.4.1/
├── bin/
│   └── nsys -> ../target-linux-x64/nsys
├── host-linux-x64/
└── target-linux-x64/
    └── nsys
```

Kubernetes 不應只掛載 `target-linux-x64/` 後直接執行 binary。Nsight Systems 2026.4.1 會要求透過安裝 layout 中的 launcher/symlink 啟動。已驗證的掛載方式是將完整 installation root 掛入 Pod：

```yaml
volumeMounts:
  - name: nsys-runtime
    mountPath: /opt/pre6g/nsight
    readOnly: true

volumes:
  - name: nsys-runtime
    hostPath:
      path: /opt/nvidia/nsight-systems-cli/2026.4.1
      type: Directory
```

容器內固定使用：

```text
/opt/pre6g/nsight/bin/nsys
```

而不是依賴 host/container 的 `PATH`。兩台目前 shell 預設 `nsys` 仍可能解析到 CUDA 12.8 內附的 Nsight Systems 2024.6.2，因此正式 Pre6G profiling 一律使用上述絕對路徑。

已驗證 trace configuration：

```text
--trace=cuda,nvtx,osrt
--sample=none
--cpuctxsw=none
```

RTX 5090 host 的 CPU profiling environment 因 `kernel.perf_event_paranoid=4` 不允許 perf-based CPU sampling，但上述設定已在 Pod 內成功產生 CUDA trace、CUDA API summary、CUDA GPU Kernel summary 與 OS Runtime summary，因此目前不需要為 Pre6G target-process CUDA tracing 放寬 CPU perf 權限。

可重現 smoke manifest：

```text
k8s/nsys2026-rtx-smoke.yaml
```

## 5. Nsight Kubernetes E2E result

2026-09-27 的 v3 smoke test 在兩個節點都完成：

```text
RTX4090 → Job Complete
RTX5090 → Job Complete
```

兩個 Pod 都完成：

1. CUDA 12.8 `nvcc` 編譯。
2. 無 profiler 執行 CUDA vector-add。
3. Nsight Systems 2026.4.1 launch target process。
4. 產生非空 `profile.nsys-rep`。
5. 產生 `profile.sqlite`。
6. `cuda_api_sum` 成功。
7. `cuda_gpu_kern_sum` 成功，辨識 500 次 `vector_add` kernel。
8. `osrt_sum` 成功。
9. 最終 marker `NSYS2026_SMOKE_PASS`。

工程上另外確認：對同一份 `.nsys-rep` 連續執行多次 `nsys stats` 可能因既存 SQLite timestamp 檢查失敗。因此目前 smoke/collector 應以單次 stats invocation 一次指定所需 reports，或明確管理 `--force-export` / SQLite lifecycle。

## 6. RTX 5090 DiskPressure integration setting

RTX 5090 root filesystem 目前接近滿載，曾因 kubelet hard eviction threshold 在 Profile Job image pull 階段觸發：

```text
DiskPressure=True
Pod Reason=Evicted
ephemeral-storage available < configured threshold
```

Live kubelet `configz` 在第一次 Profile preflight 時確認：

```yaml
evictionHard:
  imagefs.available: "50Gi"
  nodefs.available: "50Gi"

evictionPressureTransitionPeriod: 5m

evictionMinimumReclaim:
  imagefs.available: "10%"
  nodefs.available: "10%"
```

第一次 preflight 的 `fetch-pre6g` initContainer 已成功，但在拉取約 4.6 GB 的 Ultralytics image 時跨過 50 GiB threshold，Pod 因 ephemeral-storage pressure 被 eviction。

為了只完成本輪 E2E integration smoke，RTX5090 暫時加入 kubelet drop-in：

```text
/var/lib/rancher/k3s/agent/etc/kubelet.conf.d/99-pre6g-temp-eviction.conf
```

內容：

```yaml
apiVersion: kubelet.config.k8s.io/v1beta1
kind: KubeletConfiguration

evictionHard:
  imagefs.available: "10Gi"
  nodefs.available: "10Gi"
```

重新啟動 `k3s-agent` 後，control-plane live `configz` 已確認 10 GiB override 生效，node 回到 `Ready`，後續 RTX5090 Profile preflight 與完整 Profile Job 均成功。

這個 10 GiB 值只為短期 integration smoke 解鎖流程，不是 production 建議。RTX5090 root filesystem cleanup 仍是後續必要維運工作；正式長時間 profiling 前應恢復保守 threshold 並重新檢查磁碟餘量。

## 7. NVIDIA runtime and monitoring state

NVIDIA device plugin 與 DCGM exporter 在 RTX 4090/5090 基線檢查中均為 Running，且兩個節點的 GPU scheduling smoke test 已 PASS。

Netdata 狀態需要在正式 120 秒 profiling 前重新 audit：

- RTX 4090：先前基線為 Running，仍需重新執行 feature audit。
- RTX 5090：先前 Netdata child 因 DiskPressure 被 Evicted；DiskPressure 已解除，但 Netdata child readiness 與 feature contract 尚未重新驗證。
- RTX 3090：本輪 deferred。

因此目前可進行 Nsight/runtime pipeline integration，但在 Netdata audit PASS 前，不應執行正式 energy inference/ranking。

## 8. Other nodes observed in the cluster

以下節點目前不屬於本輪 RTX 4090/5090 實驗候選集：

| Node | Status at baseline | Notes |
|---|---|---|
| `gx10-c206` | Ready | 既有 ARM64/GX10 profiling reference，不混入本輪 RTX x86_64 candidate set |
| `icclz1` | NotReady | 不 eligible |
| `iccls2` | NotReady | 不 eligible |
| `icclz2` | Ready | control-plane，不作 GPU candidate |

## 9. Shared artifact backend and current E2E gate

Current-cluster artifact persistence is now fixed as:

~~~text
control-plane NFS export
  -> static PV pre6g-artifacts-nfs
  -> PVC experiments/pre6g-artifacts
  -> ReadWriteMany
~~~

Cross-node Kubernetes smoke has passed:

~~~text
RTX5090 Pod write                 PASS
RTX4090 Pod cross-node read       PASS
RTX4090 Pod write                 PASS
control-side filesystem readback  PASS
~~~

RTX5090 profile environment preflight also passed with the fixed repository commit, Nsight Systems 2026.4.1, Pre6G imports/scripts, GPU visibility, and RWX PVC write.

The first complete worker-side profile integration then passed:

~~~text
task                          yolo26-e2e-5090-smoke-002
RTX5090 Profile Job           Complete (1/1)
marker-free detector          PASS
runtime feature extraction    PASS
ProfileResult packaging       PASS
RWX handoff                   PASS
~~~

Detailed evidence:

~~~text
docs/evidence/rtx5090-k3s-profile-e2e.md
~~~

Next integration gates:

1. 在 control side 對這份新的 Kubernetes `runtime-features.json` 執行 frozen RTX5090 runtime inference，產生 `runtime-prediction.json`。
2. 對 RTX4090 建立等價 Profile Job path。
3. 重新驗證 RTX4090/5090 Netdata child 與必要 telemetry feature contract。
4. 準備固定 digest 的正式 workload/profiler image，移除 runtime Git fetch MVP。
5. 執行 formal fixed 120-second profiling。
6. 接入 node-bound power model、runtime+power ranking 與 production Job placement。
7. RTX3090 儲存空間整理後，再重新加入 candidate set。

## 10. Reproducible cluster audit commands

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

## 11. Compatibility note with Pre6G_profiling

`Pre6G_profiling/docs/TARGET_SPEC.md` v1.2 remains the frozen GX10/ARM64 Phase 1–6 contract and should not be rewritten retroactively.

RTX 4090/5090 are a separate x86_64 deployment extension. The validated Nsight Systems 2026 behavior, mount layout and Kubernetes smoke results are documented separately in `Pre6G_profiling/docs/RTX_X86_NSIGHT_2026_EXTENSION.md`.
