# Pre6G 成果展示介面

用途：展示使用者未來可能看到的平台流程、已紀錄的實驗成果與尚待驗證的項目。

## 開啟方式

1. Clone 或下載此 repository。
2. 用 Edge、Chrome 或其他現代瀏覽器開啟 `dashboard/index.html`。
3. 使用左側「總覽」、「Runtime 實驗」、「Power 與限制」切換頁面。

不需要安裝套件、啟動後端或連線至 Kubernetes。檔案內含樣式及切頁程式，可離線開啟；支援淺色／深色外觀與窄螢幕。

## 與整合程式的關係

- `scripts/run_experiment_pipeline.py` 執行流程並寫入 JSON、log 及 Job YAML。
- 本頁的數據直接寫在 HTML 內，不讀取 runner 輸出，也不會隨實驗執行自動更新。
- 頁面沒有提交任務、執行模型、部署 Job 或修改 cluster 的功能。
- GitHub 檔案頁提供原始碼；目前沒有部署 GitHub Pages 網站。

## 數據來源

- [早期 RTX5090 K3s smoke 及後續正式測試](../docs/evidence/rtx5090-k3s-profile-e2e.md)
- [RTX4090 正式測試](../docs/evidence/rtx4090-k3s-profile-e2e.md)
- [Runtime reference](../docs/evidence/pre6g-result-runtime-reference.md)
- [High-load trace results](../docs/evidence/high-load-trace-results.md)
- [最新跨節點研究比較](../docs/evidence/formal-cross-node-energy-comparison.md)

早期 smoke 與正式 960-unit 比較在頁面分開標示。研究排序中的功率 OOD 與 `validation_required` 狀態不代表 production gate 通過。
