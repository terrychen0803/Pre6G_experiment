# Pre6G 成果展示介面

用途：展示使用者未來可能看到的平台流程、已紀錄的實驗成果與尚待驗證的項目。

## 開啟方式

線上直接開啟（無需下載或安裝）：

- [客戶任務工作台](https://terrychen0803.github.io/Pre6G_experiment/)
- [實驗成果總覽](https://terrychen0803.github.io/Pre6G_experiment/dashboard/index.html)

也可以本機離線查看：

1. Clone 或下載此 repository。
2. 用 Edge、Chrome 或其他現代瀏覽器開啟 `dashboard/index.html`。
3. 使用左側「總覽」、「Runtime 實驗」、「Power 與限制」切換頁面。

不需要安裝套件、啟動後端或連線至 Kubernetes。檔案內含樣式及切頁程式，可離線開啟；支援淺色／深色外觀與窄螢幕。

## 客戶任務工作台（新增）

直接用瀏覽器開啟 [`workspace.html`](workspace.html)，或從原本成果頁左側的「客戶任務工作台」進入。此頁使用同資料夾下的 `workspace.css` 與 `workspace.js`，下載時請一併保留。

以「產線瑕疵辨識模型訓練」為例，呈現客戶需要理解的資訊：

- 任務名稱、模型、資料集、epochs / batch、GPU 需求與節能偏好。
- 接收任務 → 分析候選節點 → 選定節點 → 執行訓練 → 完成的生命週期。
- 配置結果、選中 hostname、預測訓練時間與節點總能耗，以及選點理由。
- 分析 loading（已完成幾個節點）與訓練進度；右側另外展示 GPU / 記憶體負載及執行中任務數。
- 候選節點比較、任務動態、完成後的成果檔案名稱示意。

點選任務卡片中的五個流程階段，即可切換「接收任務、分析中、已選點、執行中、已完成」展示情境；階段之間的箭頭會依完成狀態變色。「待確認」位於選點階段旁，是預測未通過品質檢查時的另一種結果。所有狀態都是固定示意，不會自動計時或啟動任何工作。

「預測能耗差異（相較次佳節點）」以依配置偏好排序後的第二名候選節點為比較基準，差異 =（選定節點能耗 − 次佳節點能耗）/ 次佳節點能耗 × 100%。此用語適用多節點情境；本展示仍使用兩個節點的固定範例。

這是未來產品介面的虛構情境。任務身分、資料集名稱、負載百分比、時間線、執行進度、完成狀態及成果檔名都是示意值；目前平台未提供此頁的即時進度或成果下載功能。訓練迭代 / epoch 進度只是產品呈現概念，不代表現有 marker-free detector 已能提供 production 進度回報。

960-unit 的時間與能耗比較參考現有研究資料（RTX4090 約 60.8 s / 21.54 kJ，RTX5090 約 123.6 s / 57.15 kJ）。這些研究模型仍有 OOD / `validation_required` 限制，示意頁中的成功配置與執行不是 production gate 已通過的證據。steady training 預測不包含啟動、驗證與存檔，完成頁另列的是虛構任務耗時。選點前負載固定保留，執行中的目前負載另行呈現，避免混淆兩種時間點。

## 與整合程式的關係

- `scripts/run_experiment_pipeline.py` 執行流程並寫入 JSON、log 及 Job YAML。
- 本頁的數據直接寫在 HTML 內，不讀取 runner 輸出，也不會隨實驗執行自動更新。
- 頁面沒有提交任務、執行模型、部署 Job 或修改 cluster 的功能。
- GitHub 檔案頁提供原始碼；線上展示由 GitHub Pages 提供，任何取得網址的人皆可瀏覽。

## 線上部署與更新

`.github/workflows/pages.yml` 在 `main` 的 `dashboard/`、`pages/` 或部署設定變更時自動部署，也支援 GitHub Actions 頁面手動 Run workflow。

網站只發布四個展示資產（`index.html`、`workspace.html`、`workspace.css`、`workspace.js`）及首頁轉址。Python 程式、模型、測試資料與本機 generated 目錄都不會打包成網站。更新頁面後推送至 `main`，待 [Pages 部署工作](https://github.com/terrychen0803/Pre6G_experiment/actions/workflows/pages.yml) 成功即可看到新版本。

`pages/index.html` 是線上網站首頁，會導向 `dashboard/workspace.html`；原有實驗總覽仍位於 `dashboard/index.html`。GitHub repository Settings → Pages 的 Source 使用 GitHub Actions。

## 數據來源

- [早期 RTX5090 K3s smoke 及後續正式測試](../docs/evidence/rtx5090-k3s-profile-e2e.md)
- [RTX4090 正式測試](../docs/evidence/rtx4090-k3s-profile-e2e.md)
- [Runtime reference](../docs/evidence/pre6g-result-runtime-reference.md)
- [High-load trace results](../docs/evidence/high-load-trace-results.md)
- [最新跨節點研究比較](../docs/evidence/formal-cross-node-energy-comparison.md)

早期 smoke 與正式 960-unit 比較在頁面分開標示。研究排序中的功率 OOD 與 `validation_required` 狀態不代表 production gate 通過。
