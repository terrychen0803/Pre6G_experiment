# Monitoring preflight and recovery SOP

This document is the operational checklist for the validated RTX4090/RTX5090 monitoring path used by Pre6G_experiment.

The production telemetry design is:

~~~
Netdata child on each worker
        |
        v
Netdata Parent
        |
        +--> CPU/system/background process telemetry

DCGM Exporter on each NVIDIA worker
        |
        +--> NVIDIA device telemetry at 1 s cadence

Nsight Systems 2026
        |
        +--> target-process CUDA trace

Netdata + DCGM
        |
        v
timestamp alignment
        |
        v
canonical energy feature vector
~~~

Do not require the Netdata container itself to access /dev/nvidia*.

## Scope

Current validated candidate nodes:

- RTX4090: iccl-s3-251230
- RTX5090: mirc516-20250605

RTX3090 is outside the current integration scope until its local storage issue is resolved.

## Step 1: node and clock readiness

Run from the control-plane:

~~~bash
kubectl get nodes -o wide
~~~

Candidate nodes must be Ready.

On the control-plane and each candidate worker:

~~~bash
timedatectl status | grep -E 'Local time|Universal time|System clock synchronized|NTP service'

chronyc tracking 2>/dev/null | grep -E 'Reference ID|System time|Last offset|RMS offset|Leap status' || true
~~~

Required result:

~~~
System clock synchronized: yes
NTP service: active
~~~

The validated 2026-09-27 run passed this check on the control-plane, RTX4090, and RTX5090.

## Step 2: Netdata child readiness

The deployed child selector is:

~~~
app=netdata
role=child
~~~

Do not use app.kubernetes.io/component=child for this deployment.

~~~bash
kubectl -n netdata get pods   -l 'app=netdata,role=child'   -o custom-columns='NAME:.metadata.name,NODE:.spec.nodeName,READY:.status.containerStatuses[0].ready,PHASE:.status.phase,RESTARTS:.status.containerStatuses[0].restartCount'
~~~

For every active candidate node:

~~~
READY=true
PHASE=Running
~~~

## Step 3: duplicate host-native Netdata recovery

The child DaemonSet uses hostNetwork=true and binds only:

~~~
127.0.0.1:19999
~~~

A host-native Netdata service listening on port 19999 will therefore collide with the Kubernetes child and cause CrashLoopBackOff.

Typical log:

~~~
Address already in use
Cannot setup listen port(s). Is Netdata already running?
~~~

Check the worker:

~~~bash
sudo ss -ltnp | grep ':19999' || true
systemctl status netdata --no-pager || true
~~~

If an unintended host-native Netdata instance owns port 19999:

1. Save its service/configuration for rollback.
2. Stop it.
3. Confirm port 19999 is free.
4. Disable the host-native service.
5. Delete the failed Kubernetes child Pod so the DaemonSet recreates it.

Do not uninstall the package during troubleshooting; keep rollback possible.

After recovery, the worker-local API must return the Kubernetes Netdata version:

~~~bash
curl -sS http://127.0.0.1:19999/api/v1/info
~~~

## Step 4: Netdata Parent streaming

The child intentionally listens on localhost only. The controller should use the Netdata Parent for historical node data rather than opening child port 19999 externally.

Confirm Parent:

~~~bash
kubectl -n netdata get svc -o wide
kubectl -n netdata get pods -o wide | grep netdata-parent
~~~

Check mirrored hosts:

~~~bash
PARENTPOD=$(kubectl -n netdata get pods   -o name | grep '/netdata-parent-' | head -n1 | cut -d/ -f2)

kubectl -n netdata exec "$PARENTPOD" --   curl -sS http://127.0.0.1:19999/api/v1/info
~~~

The active candidate hostnames must appear in mirrored_hosts.

Per-host data path:

~~~
/host/<hostname>/api/v1/...
~~~

Example:

~~~bash
kubectl -n netdata exec "$PARENTPOD" --   curl -fsS   http://127.0.0.1:19999/host/<hostname>/api/v1/allmetrics?format=json
~~~

## Step 5: Netdata system/CPU feature audit

Netdata is responsible for:

- CPU User%
- CPU System%
- CPU IOWait%
- Load 1min
- Load 5min
- Load 15min
- Mem Used(MB)
- Mem Free(MB)
- CPU Temp(°C)
- Top1 CPU%
- Top2 CPU%
- Top3 CPU%

The script scripts/audit_netdata.py checks the required system charts, CPU temperature availability, and process CPU charts.

GPU availability is not part of this audit.

## Step 6: DCGM Exporter readiness

~~~bash
kubectl get pods -A -o wide | grep -i dcgm
kubectl get svc -A -o wide | grep -i dcgm
kubectl get daemonset -A | grep -i dcgm
~~~

For candidate NVIDIA nodes, the exporter Pod must be Running and Ready.

Required metrics:

~~~
DCGM_FI_DEV_GPU_UTIL
DCGM_FI_DEV_FB_USED
DCGM_FI_DEV_GPU_TEMP
DCGM_FI_DEV_POWER_USAGE
~~~

These map to:

~~~
GPU Util%
GPU Mem Used(MB)
GPU Temp(°C)
GPU Power(W)
~~~

Query an individual exporter Pod rather than the ClusterIP service when node identity matters.

## Step 7: enforce 1 s DCGM collection cadence

Back up the DaemonSet and metric ConfigMap first.

The validated setting is:

~~~
DCGM_EXPORTER_INTERVAL=1000
~~~

Check:

~~~bash
kubectl -n gpu-monitoring get ds dcgm-exporter   -o jsonpath='{range .spec.template.spec.containers[0].env[*]}{.name}={.value}{"\n"}{end}'   | grep DCGM_EXPORTER_INTERVAL
~~~

After changing the DaemonSet, require:

~~~
DESIRED == CURRENT == READY == AVAILABLE == UPDATED
MISSCHEDULED == 0
~~~

Check:

~~~bash
kubectl -n gpu-monitoring get ds dcgm-exporter   -o custom-columns='DESIRED:.status.desiredNumberScheduled,CURRENT:.status.currentNumberScheduled,READY:.status.numberReady,AVAILABLE:.status.numberAvailable,UPDATED:.status.updatedNumberScheduled,MISSCHEDULED:.status.numberMisscheduled'
~~~

## Step 8: stale DaemonSet Pod recovery

A rolling update can stall when old exporter Pods remain on nodes whose Ready condition is Unknown.

Diagnose:

~~~bash
kubectl get nodes   -o custom-columns='NAME:.metadata.name,READY:.status.conditions[?(@.type=="Ready")].status'

kubectl -n gpu-monitoring get pods   -l 'app.kubernetes.io/name=dcgm-exporter' -o wide
~~~

If the DaemonSet controller has already requested deletion and a stale Pod remains Terminating on an unreachable node, force-delete only that stale API object:

~~~bash
kubectl -n gpu-monitoring delete pod <stale-pod>   --grace-period=0 --force
~~~

Warning: force deletion removes the API object without proving the process stopped on the unreachable host. Use this only for confirmed stale monitoring Pods, not application workloads.

## Step 9: DCGM collection

Prefer direct Pod-IP access from the control-plane when cluster networking permits it:

~~~bash
curl -fsS http://<dcgm-pod-ip>:9400/metrics
~~~

Use scripts/collect_dcgm.py for formal runs. It:

- polls at a fixed monotonic cadence,
- records request start/end,
- uses the response-window midpoint as timestamp_ns,
- preserves GPU UUID and node identity,
- writes the four canonical GPU fields.

The collector interval and exporter collection interval are separate controls. Both should be configured near 1 s.

## Step 10: Netdata/DCGM alignment

Canonical time is UTC Unix nanoseconds.

Netdata historical samples use the API-provided Unix timestamp. DCGM samples use the request midpoint timestamp_ns.

Alignment policy:

~~~
method: nearest timestamp
tolerance: 750 ms
minimum coverage: 90%
maximum Netdata gap: 2 s
maximum DCGM gap: 2 s
~~~

Use scripts/align_telemetry.py after Netdata extraction.

The validated RTX5090 test produced:

~~~
DCGM samples: 20
Netdata samples: 26
Aligned samples: 20
Alignment coverage: 100%
Median absolute delta: 284.8 ms
Maximum absolute delta: 490.5 ms
~~~

These numbers are evidence from one validation run, not universal thresholds. The thresholds above are the current platform quality gate.

## Step 11: per-process GPU metrics

Top1 GPU% and Top2 GPU% are not supplied by the currently validated device-level DCGM path.

Do not substitute device-wide GPU utilization.

If a node-bound power model requires these fields, mark the model unavailable/schema_mismatch until a validated per-process GPU collector is added.

## PASS criteria before profiling

A candidate node may enter energy-aware profiling only if:

- node Ready,
- clock synchronized,
- Netdata child Ready,
- node visible through Netdata Parent,
- Netdata system/CPU feature audit passes,
- DCGM exporter Ready,
- four required DCGM device metrics available,
- exporter and collector cadence configured for approximately 1 s,
- telemetry alignment quality passes,
- the node-bound power model declares no missing required feature.

Monitoring failures should produce a reasoned exclusion, not zero-filled features.
