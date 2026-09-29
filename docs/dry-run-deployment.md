# Dry-run Profile Job 部署

## 目的與不變條件

Controller 不直接修改使用者提交的原始 Job。它為每個 candidate node deep-copy 一份獨立 Profile Job，保留 application image、command、args、env、security context、resource requests/limits 與 data volumes，只加入：

- profiling wrapper
- node pin
- artifact path
- dry-run timeout / stop policy
- Pre6G metadata

Workload adapter 不參與 command reconstruction。Semantic discovery 與 execution contract 必須解耦。

Profile Job 和最後的 production Job 必須使用相同 GPU sharing strategy 與 resource name，例如：

~~~yaml
requests:
  nvidia.com/gpu.shared: "1"
limits:
  nvidia.com/gpu.shared: "1"
~~~

Dry-run output/checkpoint 必須寫到隔離路徑，不能覆寫 production checkpoint 或修改共享 dataset。

## Production marker-free 原則

正式 runtime profiling 必須在不修改 user code 的前提下完成。

允許：

~~~text
original application argv
Nsight target-process CUDA trace
Netdata / DCGM telemetry
static Job/config metadata
~~~

禁止作為 production detector 必要輸入：

~~~text
iterations.csv
NVTX iteration markers
training callbacks
epoch/batch markers
summary.json iteration timing
source-code instrumentation
~~~

若 research fixture 本身有這些資料，只能在 detector output freeze 後拿來做 ground-truth validation。

## Phase 05 / 06 / 07 的差異

### Phase 05

驗證：

- generic workload intake；
- static semantic extraction；
- marker-free Nsight SQLite schema；
- target-process/context isolation；
- recurring execution-cycle discovery。

目前 RTX5090 C03 已完成 marker-free trace preflight，下一步是 event extraction。

### Phase 06

使用實際 generic Job，在 RTX4090 / RTX5090 做 5–15 秒 short compatibility profile：

- 不修改 user application；
- 不依賴 instrumentation；
- 驗證 marker-free cycle detection；
- 驗證 Netdata Parent historical window + DCGM active polling 的同窗口資料。

### Phase 07

Phase 06 PASS 後才做 fixed 120-second formal dry-run。

## Controller 產生每節點 Job

假設 source Job：

~~~text
<source-name>
~~~

candidate nodes：

~~~text
worker-4090
worker-5090
~~~

產生：

~~~text
<source-name>-profile-worker-4090-<task-id>
<source-name>-profile-worker-5090-<task-id>
~~~

每份 Job 至少：

~~~yaml
spec:
  backoffLimit: 0
  activeDeadlineSeconds: 300
  template:
    metadata:
      labels:
        pre6g.io/task-id: <task-id>
        pre6g.io/mode: dry-run
        pre6g.io/target-node: worker-5090
    spec:
      restartPolicy: Never
      nodeSelector:
        kubernetes.io/hostname: worker-5090
~~~

## Application command 包裝

不要 sidecar attach 已啟動 PID。

Source application：

~~~text
<original-command> <original-args>
~~~

Profile Job：

~~~text
/opt/pre6g/nsight/bin/nsys profile
  --trace=cuda,nvtx,osrt
  --sample=none
  --cpuctxsw=none
  --output=/artifacts/<task-id>/<node>/<attempt>/profile
  --
  <original-command> <original-args>
~~~

重要：

- original argv 必須逐項保存；
- 不把 argv join 成 shell 字串再重新 parse；
- adapter 不能改寫 argv；
- fixed Nsight path 使用 2026 installation；
- NVTX 即使存在也只作 audit，marker-free detector 不讀 NVTX。

## Marker-free artifacts

建立 Profile Job 前保存：

~~~text
source-job.yaml
execution-contract.json
workload-spec.json
~~~

Nsight finalize 後：

~~~text
profile.nsys-rep
profile.sqlite
marker-free-events.csv
marker-free-discovery.json
semantic-binding.json
~~~

其中 marker-free-events.csv 只能從 target-process CUDA events 產生，不含：

~~~text
iteration_id
epoch
batch_in_epoch
NVTX-derived labels
callback-derived labels
~~~

## Marker-free event extraction

第一版從 Nsight SQLite 讀：

~~~text
CUPTI_ACTIVITY_KIND_KERNEL
StringIds
~~~

最低欄位：

~~~text
start
end
shortName
globalPid
contextId
streamId
~~~

輸出 event representation：

~~~text
start_ns
end_ns
duration_ns
short_name_id
global_pid
context_id
stream_id
relative_start_ns
inter_arrival_ns
~~~

先 isolate target process/context，再做 period detection。

## Period detection

Detector 目標是 recurring start-to-start cadence，而不是單純 sum(kernel duration)。

~~~text
cycle_start[n]
      |
      +---- elapsed wall cadence ----+
                                     |
                              cycle_start[n+1]
~~~

Detector 第一層輸出：

~~~text
detected_unit = execution_cycle
period_ms
confidence
complete_cycles
two-window stability
~~~

不得先驗地把 detected unit 命名成 training_iteration。

## Semantic binding

Static semantic layer 與 marker-free detector 分開。

Example：

~~~text
Static:
  candidate work_unit = training_iteration
  total_work_units = 640

Marker-free:
  detected_unit = execution_cycle
  period = X ms
~~~

只有 semantic binding status=bound，才可以把 X 映射成 runtime_per_training_iteration。

如果 unbound：

~~~text
cycle latency / slowdown only
no total-job extrapolation
~~~

## Pod 內角色

MVP：

~~~text
Profile Pod
├── profiler/application container
│   └── nsys profile -- original application
├── collector sidecar
│   └── validate -> export -> marker-free extract -> upload
└── shared artifact volume
~~~

Profiler 在 report 完成後原子寫 capture.complete。Collector 不以檔案剛出現作為 report-ready 訊號。

Netdata 與 DCGM 都是既有 node monitoring components，不跟單次 Job 啟停。

## Fixed 120-second formal profiling

Phase 07：

~~~text
configured_capture_seconds = 120
activeDeadlineSeconds = 300
~~~

流程：

~~~text
container start
  -> nsys/application start
  -> fixed 120 s profile wall window
  -> graceful application stop
  -> nsys report finalization
  -> capture.complete
  -> SQLite export
  -> marker-free extraction
  -> period detection
~~~

若 workload 在 120 秒前自然完成：

- 保存實際 capture 長度；
- 不為了湊滿 120 秒自動重啟；
- target CUDA activity / complete cycles 不足則 quality gate fail。

## Collector 後處理

Collector：

1. 驗證 .nsys-rep 存在、非空、可由相同版本 Nsight 開啟。
2. 匯出 profile.sqlite。
3. 驗證 CUPTI kernel table、StringIds、target process/context identity。
4. 建立 marker-free-events.csv。
5. 離線分析 7/9/12/15/20/30 s prefixes。
6. 輸出 execution_cycle period / confidence / stability。
7. 評估 semantic binding。
8. 依 absolute timestamps 查 Netdata Parent。
9. 保存同窗口 DCGM samples。
10. nearest-align Netdata/DCGM。
11. 產生 canonical telemetry features。
12. 依 node-bound power model manifest 驗證 required features。
13. 產生 node-result.json + checksums。

Current telemetry ownership：

~~~text
Netdata -> CPU/system/Top CPU
DCGM    -> GPU Util/FB/Temp/Power
Nsight  -> target-process CUDA behavior
~~~

## 建立與監看

Controller 先 server-side dry-run：

~~~bash
kubectl apply --dry-run=server -f generated/profile-worker-4090.yaml
kubectl apply --dry-run=server -f generated/profile-worker-5090.yaml
~~~

再建立：

~~~bash
kubectl apply -f generated/profile-worker-4090.yaml
kubectl apply -f generated/profile-worker-5090.yaml

kubectl -n experiments get jobs,pods   -l pre6g.io/task-id=<task-id> -o wide
~~~

## 從 dry-run 回到 production

完成 gate 後再次從原始 Job deep-copy production Job：

- 不含 Nsight wrapper；
- 不含 collector；
- 不含 profiling artifact volume；
- 不含 dry-run timeout；
- 加 selected nodeSelector；
- 保留 source application contract；
- sharing contract 與 profile 一致。

若：

- semantic binding unknown；
- total work未知且 policy需要 total-job ranking；
- runtime model unavailable；
- power model unavailable；
- model binding mismatch；
- telemetry quality fail；
- target process/context trace invalid；

則不偽造最佳節點。


## Worker-side runtime feature/result finalization

正式 k3s Profile Job 在 Nsight report 匯出與 marker-free detector 完成後，還要在 worker 上完成兩個小型 artifact：

~~~text
runtime-features.json
profile-result.json
~~~

第一步：

~~~bash
PYTHONPATH=src python scripts/build_runtime_features.py \
  --sqlite /artifacts/profile.sqlite \
  --detection-json /artifacts/marker-free-discovery.json \
  --output /artifacts/runtime-features.json \
  --node "$NODE_NAME" \
  --device-id "$DEVICE_ID" \
  --workload-id "$WORKLOAD_ID"
~~~

第二步：

~~~bash
PYTHONPATH=src python scripts/package_profile_result.py \
  --task-id "$TASK_ID" \
  --node "$NODE_NAME" \
  --device-id "$DEVICE_ID" \
  --detection-json /artifacts/marker-free-discovery.json \
  --runtime-features-json /artifacts/runtime-features.json \
  --output /artifacts/profile-result.json
~~~

這兩步都不讀：

~~~text
iterations.csv
NVTX iteration labels
training callbacks
epoch/batch labels
~~~

## Profile Job → Controller transport

正式流程不使用人工 SCP。

建議 artifact path：

~~~text
results/<task_id>/<node>/profile-result.json
~~~

transport abstraction：

~~~text
shared-artifact-store
~~~

repository-level backend 仍可為 RWX PVC / NFS / MinIO / S3-compatible store。Current k3s deployment 已將實作固定為 control-plane NFS export + static RWX PV/PVC（`pre6g-artifacts-nfs` / `experiments/pre6g-artifacts`），並完成 RTX4090/RTX5090 cross-node read/write smoke。

Controller 只需要讀小型 ProfileResult/runtime feature artifact，再在 control side 執行：

~~~bash
PYTHONPATH=src python scripts/predict_runtime.py \
  --model models/runtime/<device-model>.json \
  --features results/<task_id>/<node>/runtime-features.json \
  --output results/<task_id>/<node>/runtime-prediction.json
~~~

完整 handoff contract：

~~~text
docs/profile-result-contract.md
schemas/profile-result.schema.json
schemas/runtime-features.schema.json
schemas/runtime-prediction.schema.json
~~~

人工 `scp` 僅限 component bring-up/smoke，不得出現在正式 controller reconcile path。

## 2026-09-28 RTX5090 Kubernetes E2E status

Current integration task:

~~~text
yolo26-e2e-5090-smoke-002
~~~

validated the worker-side path:

~~~text
Kubernetes Job
  -> RTX5090
  -> Nsight Systems 2026.4.1
  -> worker-local .nsys-rep / SQLite
  -> yolo-v1 marker-free detector
  -> runtime-features.json
  -> profile-result.json
  -> NFS-backed RWX PVC
  -> control-side visible artifacts
~~~

Result:

~~~text
Job                             Complete (1/1)
detected_unit                   execution_cycle
detected_period_ms              126.8548825
confidence                      0.7304067523
complete_cycles                 118
two-window stability            PASS
runtime feature extraction      PASS
ProfileResult packaging         PASS
ProfileResult status            ready-for-control-side-inference
manual SCP                      not used
~~~

The first tiny fixture failed closed before detection because the usable trace span was only 4.922 s, shorter than the detector minimum horizon. The detector was not modified; the integration workload was lengthened to provide sufficient execution cycles.

Large trace artifacts remained worker-local:

~~~text
profile.nsys-rep  35 MB
profile.sqlite    104 MB
~~~

Only the small JSON handoff artifacts were persisted to the RWX PVC.

Detailed evidence:

~~~text
docs/evidence/rtx5090-k3s-profile-e2e.md
~~~

Next gate: run the frozen RTX5090 runtime model on this newly produced Kubernetes `runtime-features.json` from the control side, then repeat the equivalent worker path on RTX4090.
