# Dry-run Profile Job 部署

## 目的與不變條件

Controller 不直接修改或執行使用者提交的原始 Job。它為每個候選節點 deep-copy 一份獨立 Profile Job，保留 application image、command、args、env、security context、resource requests/limits 與資料 volumes，只加入 profiling wrapper、節點固定、artifact volume 和 dry-run 停止政策。

Profile Job 和最後的 production Job 必須使用相同 GPU sharing strategy 與 resource name，例如 `nvidia.com/gpu.shared: 1`。Dry-run 的 output/checkpoint 必須寫到隔離路徑，不能覆寫 production checkpoint 或修改共享 dataset。

## Controller 產生每節點 Job

假設候選節點是 `worker-4090` 和 `worker-5090`，Controller 產生：

```text
yolo26-train-profile-worker-4090-<task-id>
yolo26-train-profile-worker-5090-<task-id>
```

每份 Job 至少套用：

```yaml
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
```

`nodeSelector` 保證這份 dry-run 只在指定候選節點執行。Controller 應先檢查該節點 Ready、GPU sharing label/resource、taint/toleration、資料 volume 與 artifact store；不符合者不建立或立即排除。

## Application command 包裝

不要由 sidecar attach 已啟動的 PID。Profile Job 將 application container 的原始 command/args 保存到 manifest，再讓 Nsight 直接 launch 它：

```text
nsys profile
  --trace=cuda,nvtx
  --sample=none
  --cpuctxsw=none
  --output=/artifacts/<task-id>/<node>/<attempt>/profile
  --
  <original-command> <original-args>
```

正式實作可用 image entrypoint wrapper 組合參數，但不能經過 shell 字串重新解析使用者 command。`nvtx` 可以收集作研究 audit；detector/runtime model 不得讀 NVTX。Profile image 必須固定 Nsight 版本並記錄 driver、CUDA、container image digest 與完整 argv。

## Pod 內角色

MVP 可由 application/profile container 完成 Nsight capture，再由同 Pod collector sidecar 處理 artifacts：

```text
Profile Pod
├── profiler/application container
│   └── nsys profile -- original application
├── collector sidecar
│   └── 等待 completion marker、export SQLite、抽 feature、上傳
└── shared artifact volume
```

兩個 container 透過共享 artifact volume 協調。Profiler 在 report 完成後原子寫入 `capture.complete`; collector 不以檔案剛出現作為完成條件，避免讀到仍在 finalization 的 report。

Netdata child 仍由每個節點的既有 DaemonSet 持續監控，不放入 Profile Pod，也不隨 dry-run 啟停。Controller/collector 只使用保存的 absolute timestamps 查詢相同節點的歷史窗口。

## 固定 120 秒 profiling

`configured_capture_seconds=120` 從 Nsight launch 原始 application 後開始計算。這個起點可以由 wrapper 可靠記錄；第一個 target CUDA kernel 必須等 report 匯出後才能精確定位。Collector 另外記錄 `observed_cuda_active_seconds`，若初始化耗掉太多時間而沒有足夠 CUDA prefix，quality gate 應拒絕或重新 profile。整個 Job 仍受 `activeDeadlineSeconds=300` 限制。

```text
container start
  → nsys/application start
  → fixed 120-second profiling wall window
  → graceful application stop
  → nsys report finalization
  → capture.complete
```

若 workload 在 120 秒前自然完成，保存實際長度，不為湊滿時間重啟。若沒有足夠 target CUDA activity、無法正常 finalization，或 report 不完整，該節點結果失敗。

停止時先請 application 正常離開，再給 grace period；最後手段才終止 process tree。不能直接 kill Nsight 主程序後假設 report 可用。對不支援 graceful stop 的 image，Profile Job 必須標記停止方式與 report integrity，並在上線前做一次相容性測試。

## Collector 後處理

Collector 收到完成 marker 後執行：

1. 驗證 `.nsys-rep` 存在、非空且可由相同版本 Nsight 開啟。
2. 匯出 `profile.sqlite`，並保存 export log。
3. 驗證 `CUPTI_ACTIVITY_KIND_KERNEL`、`StringIds`、target `globalPid/contextId` 與 hardware-trace diagnostic。
4. 離線分析 7/9/12/15/20/30 秒 prefixes，選出符合 confidence 與 two-window stability 的 emission horizon。
5. 將 120 秒資料切成 30 秒區段，檢查 period、kernel event rate、busy fraction 與 sharing/load drift；後段只作 gate，除非另有重新訓練的長窗口模型。
6. 依 timestamps 查詢既有 Netdata child，產生 18-feature samples 與 P(t)。
7. 寫出 `node-result.json`、checksums，再上傳 object/RWX storage。

如果 target process 無法識別、不是 CUDA hardware trace、period/load regime 顯著漂移、模型 OOD 或 Netdata feature 不完整，保留 artifacts 但不讓該節點進入 ranking。

## 建立與監看

Controller 先用 server-side dry-run 驗證產生的 Kubernetes 物件，再建立 Job：

```bash
kubectl apply --dry-run=server -f generated/yolo26-profile-worker-4090.yaml
kubectl apply --dry-run=server -f generated/yolo26-profile-worker-5090.yaml

kubectl apply -f generated/yolo26-profile-worker-4090.yaml
kubectl apply -f generated/yolo26-profile-worker-5090.yaml

kubectl -n experiments get jobs,pods -l pre6g.io/task-id=<task-id> -o wide
kubectl -n experiments logs job/yolo26-train-profile-worker-5090-<task-id> -c profiler
kubectl -n experiments logs job/yolo26-train-profile-worker-5090-<task-id> -c collector
```

候選節點可以平行 dry-run，讓決策延遲接近一次 120 秒 profiling 加上初始化/finalization；研究評估模式則可隨機節點順序並做多次 repeat，避免時間漂移造成偏差。

## 從 dry-run 回到 production

所有候選節點結果完成 gate 後，Controller 排名並再次從原始 Job deep-copy production Job。Production Job 不包含 Nsight wrapper、collector、artifact volume 或 dry-run timeout，只加入所選節點的 `nodeSelector` 和 placement label。若所有 Profile Job 失敗、模型未 ready 或證據不足，平台不應偽造最佳節點。
