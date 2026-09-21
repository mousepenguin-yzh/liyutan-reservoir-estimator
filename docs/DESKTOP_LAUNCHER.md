# 桌面啟動與更新（Phase 2-7）

## 維護者一次性安裝

1. 在本機固定磁碟安裝 Python 3.12（含 tkinter、pip、venv），以及 Git for Windows；讓 Python 與 Git 位於使用者 PATH。先完成 GitHub repository 讀取權限／Git Credential Manager 登入，日常更新不會要求使用者輸入 Git 指令或憑證。
2. 將本 repository 的已審核 main clone 到固定本機目錄。不要放在 U:、UNC、網路磁碟、junction、同步的正式資料目錄。這份 checkout 是穩定啟動器，請保留；不要從暫時開發分支安裝。
3. 維護者執行 `powershell -NoProfile -File scripts/install_desktop_shortcut.ps1`，建立桌面 **Liyutan Estimator** 捷徑。如公司執行政策阻擋，交由 IT 核准，勿要求一般使用者繞過政策。
4. 雙擊捷徑，按「檢查更新」及「安裝顯示的版本」。第一次需要 GitHub 與 PyPI 套件來源連線。完成後按「啟動程式」。
5. 公司正式使用前，由維護者依 [shared-storage spec](LOCAL_SHARED_STORAGE_SPEC.md) 設定使用者環境變數並重新開啟啟動器。啟動器繼承既有 LIYUTAN 設定，不新增或自動開啟 formal-write、annual-write、recovery capability。未設定共享模式時是明示的相容模式。

更新來源固定為本專案 GitHub 的 main；合併 PR 才發布至一般使用者。啟動器不更新開發 checkout。GitHub branch protection 與合併前 CI 應由維護者管理。

## 一般使用者操作

- 雙擊桌面捷徑，按「啟動程式」，瀏覽器開啟本機頁面。啟動器與頁面側欄都顯示 git 版本及完整 commit。
- 日常可離線啟動已安裝版本（正式共享資料是否可用仍由既有應用程式判斷）。
- 更新前先下載未保存的工作批次 JSON，按「停止程式」。關閉瀏覽器不等於停止；停止會失去未保存工作階段。
- 按「檢查更新」比較目前及遠端版本，再按一次「安裝顯示的版本」。下載、套件安裝、驗證與切換會自動完成，視窗顯示進度、成功或錯誤。
- 檢查與安裝之間 main 若變動，仍安裝畫面上選定的完整 commit；再次檢查可取得更新的版本。
- 更新錯誤時保留原啟動版本；可再次按「啟動程式」。初次安裝失敗則需排除問題後重試。
- 程式執行時更新被阻擋。重複啟動桌面捷徑會提醒使用原視窗。

## 本機安裝與失敗處理

程式版本、獨立 Python 環境與啟動記錄位於 `%LOCALAPPDATA%\LiyutanEstimator`：

- `releases/<隨機識別碼>/repository`：固定 commit 的 Git checkout。
- `releases/<隨機識別碼>/runtime`：該版獨立 venv，不更新現有環境。
- `active.json`：驗證成功後以同磁碟 atomic replace 切換的本機指標。
- `controller.lock`：本機啟動器 OS lock，程序退出即釋放，與 shared-storage lock 無關。
- `streamlit.log`：本機啟動輸出，可能含執行資訊，勿直接提交 repository 或任意分享。

下載、pip、AppTest 或指標切換失敗均不修改原版本。更新驗證移除所有 LIYUTAN 環境設定並明確關閉共享模式；套件安裝忽略外部 PIP 環境與設定檔、停用 cache 及互動輸入，避免 target/prefix 設定將套件寫到其他位置。公司若必須使用私有套件鏡像，需先由維護者評估支援，不能以 PIP_TARGET 繞過隔離；不讀取、驗證或清理任何正式 shared root。正式資料的 schema、current、revision、audit、lineage 與安全寫入契約完全不變。

| 錯誤 | 維護者處理 |
| --- | --- |
| Git/Python 執行失敗或逾時 | 檢查工具、GitHub 權限、公司 proxy、套件來源及磁碟空間；更新錯誤不直接展示可能含憑證的 subprocess 輸出 |
| 本機版本缺檔／有人工修改 | 保留檔案供檢查；不要 reset 使用者修改，不自動覆蓋；維護者修復本機安裝 |
| 啟動記錄損壞 | 維護者核對已知版本及驗證結果後修復本機記錄；不以 mtime 或目錄排序猜版本 |
| 啟動失敗／逾時 | 檢查本機 streamlit.log、端點安全軟體及 Python 環境 |
| 啟動器異常中止 | 原 Streamlit 子程序可能仍在執行，先由維護者確認並停止該程序，再重新啟動；不自動終止其他 Python 程序 |
| 網路磁碟／路徑重導 | 移至固定本機磁碟，移除 junction／symlink 安裝路徑 |

舊版本與失敗候選不自動刪除，以免破壞仍可用環境。維護者可在停止所有相關程序、核對 active.json 與要保留的回退版本後，另行安排本機空間清理；這不授權正式共享資料清理。啟動器 bootstrap 本身目前由維護者升級（停止後更新其 checkout）；一般使用者按鈕更新的是 Streamlit 應用程式及相依套件。套件版本沿用 repository requirements.txt，未凍結的套件會在候選環境重新解析，失敗不影響舊環境。

## 驗證邊界

自動化使用 pytest tmp_path、synthetic Git repository、injected subprocess／replace failure，以及共享功能關閉的 AppTest。Windows CI 驗證本機 lock 與更新流程；Linux CI 執行 repository full verification。這些不是公司實機驗收證據。

Phase 2-7 公司環境最小人工驗收範圍如下；已執行結果見 [驗收紀錄](PHASE_2_7_ACCEPTANCE.md)，目前狀態統一見 [PROJECT_STATUS](PROJECT_STATUS.md)：

1. 在一台公司電腦完成維護者安裝；一般使用者雙擊桌面捷徑，確認瀏覽器、中文顯示與 commit。
2. 確認公司網路可檢查／安裝 main 版本，以及明確顯示成功或需維護者處理的連線錯誤。
3. 使用事先核准、確認不是正式 root 的 test root 檢查啟動器傳遞既有共享設定與唯讀來源顯示；正式 root 的任何人工操作須另獲明確授權。
4. 記錄使用的 root、結果與 synthetic 資料保留／清理決定。

不進行 multi-PC、SMB lock pressure、斷線恢復、ACL 或 formal-write/recovery 驗收。目前完成狀態只見 [PROJECT_STATUS](PROJECT_STATUS.md)。
