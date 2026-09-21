# Phase 2-7 公司環境最小人工驗收

驗收日期：2026-09-21。結論：Phase 2-7 公司環境最小人工驗收通過。

本紀錄依使用者於公司 Windows 電腦人工操作並回報的結果整理，不代表代理重新執行人工驗收。驗收範圍依 [桌面啟動器操作文件](DESKTOP_LAUNCHER.md)；目前 milestone 與後續工作以 [PROJECT_STATUS.md](PROJECT_STATUS.md) 為準。

## 環境與驗收版本

- 公司 Windows 電腦；Python 3.12.10、Git、tkinter、venv 均可用。
- 公司網路可取得 GitHub main。
- launcher 成功將 main commit `d71d9ed1078dd919ac3437299cc5cf51656e0424` 安裝至隔離環境；launcher 與 Streamlit 均正確顯示此 commit。
- 以下日期、年度與版本 ID 屬 synthetic fixture，不代表正式業務資料或實際驗收日期。

## 已完成項目與結果

| 項目 | 人工驗收結果 |
| --- | --- |
| 桌面入口 | 公司 Windows 電腦可由桌面捷徑啟動 launcher。 |
| 隔離安裝與版本辨識 | 可取得並隔離安裝上述 main commit，launcher 與 Streamlit 的 commit 顯示一致。 |
| 啟停流程 | 啟動、停止、再次啟動均正常。 |
| 相容模式 | 無 LIYUTAN 設定時，正常進入相容模式。 |
| 共享設定傳遞 | 使用下列 test root，從 PowerShell 啟動 launcher 後，可正確傳遞 shared-storage 設定並讀取 synthetic annual / official current。此項以 PowerShell 啟動驗證，不將結果擴大為桌面捷徑的環境設定傳遞驗收。 |
| 年度來源顯示 | App 顯示年度 `2027`、`06-中旬`、`2026-12-15`。 |
| 診斷與 evidence | diagnostics 為 `healthy`、evidence 為 `matched`；orphan、invalid、staging、quarantine、temp artifacts 均為 `0`。 |
| 能力開關 | formal-write、annual-write、recovery capability 均未開啟。 |
| 唯讀完整性 | shared root 啟動前後逐檔 `Path` / `Length` / `SHA256` 比對完全無差異。 |

## Synthetic shared root 與資料保留

本次人工驗收使用的完整路徑：

```text
U:\經管科\水庫庫容推估系統測試區\鯉魚潭\phase-2-7-acceptance
```

此路徑與 [shared-storage spec](LOCAL_SHARED_STORAGE_SPEC.md) 定義的正式 root 分離。正式 `U:\經管科\水庫庫容推估系統\鯉魚潭` 未使用。

Fixture 準備時重用 repository 的 `tests/test_shared_storage_reader.py::_build_root` 與既有 schema fixtures，包含 annual / official immutable bundles、current 指標與 audit events；已以 production reader / loader 驗證可讀取。Annual current 為 `annual-synthetic-2027`、official current 為 `estimate-synthetic-1`，兩者 revision 均為 `1`。`source/original.xlsx` 為既有 fixture 的合成佔位內容，不是可填報 Excel。

依使用者指示保留 acceptance fixture，不執行清理；本次文件收尾不讀寫該 root，也不重新建立或修改 fixture。不將共享資料檔案、checksum 清單或其他實際 artifacts 加入 repository；啟動前後無差異的結論依使用者回報記錄。

## 驗收邊界

本次完成單台公司電腦的桌面啟動、公司網路取得版本、隔離安裝、版本顯示、啟停、相容模式與 synthetic shared root 唯讀設定傳遞驗收；不宣稱已人工測試更新失敗或故障注入分支。

未執行 multi-PC、SMB lock pressure、斷線恢復、ACL 或 recovery 測試，這些仍屬 Phase 2-8。本次未開啟正式寫入、年度寫入或 recovery capability，也未對正式 root 進行操作。Phase 2-7 完成不代表 Phase 2 全部完成。

本次收尾僅更新文件，不修改產品程式、測試或 shared-storage 契約。文件驗證依 [DEVELOPMENT_WORKFLOW.md](DEVELOPMENT_WORKFLOW.md) Tier 0 執行 quick verification，full verification 由 PR CI 執行；review 與 merge 由使用者／reviewer 決定。
