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

## Phase 05 與 Phase 06/07 的差異

### Phase 05

只驗證 generic workload intake + short real execution：

- image 可啟動
- dataset/input 可存取
- CUDA/GPU resource 正常
- application 真的進入 workload
- runtime discovery metadata 可保存

不要求 Nsight 120 秒。

### Phase 06

同一 execution contract 加入 Nsight wrapper，先做 5–15 秒 short profile compatibility。

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

nodeSelector 保證這份 dry-run 只在指定 candidate node 執行。

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

- original argv 必須逐項保存。
- 不要把 argv join 成 shell 字串再重新 parse。
- adapter 不能改寫 argv。
- fixed Nsight path 必須使用 2026 installation。
- NVTX 可作 audit，但 runtime detector/model 不依賴 NVTX。

## Semantic artifacts

建立 Profile Job 前保存：

~~~text
source-job.yaml
execution-contract.json
workload-spec.json
~~~

Runtime discovery 後再保存：

~~~text
runtime-discovery.json
~~~

如果 workload semantics unknown：

- Profile Job 仍可執行。
- runtime discovery可嘗試補充 work unit / total work。
- 無法確認 total work時，後續只保留 per-cycle/per-work-unit evidence。

## Pod 內角色

MVP：

~~~text
Profile Pod
├── profiler/application container
│   └── nsys profile -- original application
├── collector sidecar
│   └── wait -> validate -> export -> upload
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
~~~

若 workload 在 120 秒前自然完成：

- 保存實際 capture 長度。
- 不為了湊滿 120 秒自動重啟。
- 若 target CUDA activity 不足，quality gate fail。

對無法 graceful stop 的 image，必須先在 Phase 06 做相容性驗證。

## Collector 後處理

Collector：

1. 驗證 .nsys-rep 存在、非空、可由相同版本 Nsight 開啟。
2. 匯出 profile.sqlite。
3. 驗證 CUDA kernel tables、StringIds、target process/context identity。
4. 離線分析 7/9/12/15/20/30 s prefixes。
5. 檢查 period/load drift。
6. 依 absolute timestamps 查 Netdata Parent。
7. 保存同窗口 DCGM samples。
8. nearest-align Netdata/DCGM。
9. 產生 canonical telemetry features。
10. 依 node-bound power model manifest 驗證 required features。
11. 產生 node-result.json + checksums。

Current telemetry ownership：

~~~text
Netdata -> CPU/system/Top CPU
DCGM    -> GPU Util/FB/Temp/Power
Nsight  -> target-process CUDA behavior
~~~

Top1/Top2 GPU若 model required 但 collector尚未驗證，power model不可 ready。

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

研究評估模式可做 sequential repeats / randomized node order；線上系統則可平行 candidate dry-run 以降低 decision latency。

## 從 dry-run 回到 production

完成 gate 後再次從原始 Job deep-copy production Job：

- 不含 Nsight wrapper。
- 不含 collector。
- 不含 profiling artifact volume。
- 不含 dry-run timeout。
- 加 selected nodeSelector。
- 保留 source application contract。
- sharing contract與 profile一致。

如果：

- total work未知且 policy需要 total-job ranking，
- runtime model unavailable，
- power model unavailable，
- model binding mismatch，
- telemetry quality fail，
- target process trace invalid，

則不偽造最佳節點。
