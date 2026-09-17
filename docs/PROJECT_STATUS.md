# 專案目前狀態

更新日期：2026-09-17

本文件是目前 milestone、下一步與未完成驗收的唯一 source of truth。

## Current milestone

- Phase 2-6D implementation：✅ 完成。
- Phase 2-6D controlled synthetic-root acceptance：✅ 完成（2026-09-16）。
- Phase 2-6E final integration：✅ 自動化整合驗證完成。
- Phase 2-7 桌面啟動與安全更新：實作與自動化測試已加入；公司電腦／網路最小人工驗收待回公司執行，見 [操作文件](DESKTOP_LAUNCHER.md)。
- 下一步：Phase 2-7 公司環境最小人工驗收；不提前執行 Phase 2-8。
- Phase 2：尚未全部完成。
- Phase 2-8 真實多電腦、SMB lock pressure、斷線／中斷／恢復 acceptance：尚未完成。

Phase 2-6D 的完成表示正式版本選擇與預覽、建立新 working batch、日期調整、年度基準延長、Q80／Q90 新增旬填值、起始庫容重新確認、重新演算及正式保存 lineage 已完成受控 synthetic-root 驗收；不代表真實公司 SMB 環境已完成驗收。

Phase 2-6E 以 AppTest、synthetic filesystem 與 fake lock 完成正式版本接續、調整／重算、Step 5 正式保存、全新 session 重載／再次接續的整合回歸，並驗證來源 lineage／發布 previous 分離、historical annual 不 silent rebase、revision conflict 與來源年度損壞時拒絕保存。未發現需修改產品程式的整合缺陷；未變更 schema 或正式寫入契約。測試與邊界見 [shared-storage spec 的 2-6E](LOCAL_SHARED_STORAGE_SPEC.md)。

## 仍影響後續開發的既成決策

- 每台公司電腦在本機執行 Streamlit；正式資料保存於公司內網 shared storage。
- official estimate 與 annual data 都採 immutable version model；建立新版，不覆蓋舊版。
- `derived_from_official_version_id` 表示工作衍生來源；`previous_official_version_id` 表示正式發布順序，兩者分離。
- 載入 historical annual 後，不因 annual current 更新而 silent rebase；只有明確的延長或切換流程才能改變 active annual context。
- formal shared data 與 session-only／nonofficial data 分離；工作階段上傳或試算不會自動變成正式資料。
- 正式寫入、current revision、audit、lock、recovery 與 repair 必須維持 shared-storage spec 的既有安全契約。

## 尚未完成的環境風險

Phase 2-8 仍須在核准的真實環境，以最小充分範圍驗證：

- 兩台實體電腦同時操作。
- Windows／SMB OS file locking 與 lock pressure。
- 網路斷線、重新連線及權限錯誤。
- rename／replace timeout、中斷後診斷與恢復。
- 營運、備份、ACL 與人工 recovery／cleanup 責任。

上述項目不得以一般單機 synthetic acceptance 宣稱完成。

## 詳細規格與完成證據

- [Shared-storage 與 formal data contract](LOCAL_SHARED_STORAGE_SPEC.md)
- [V2 多情境 business spec](V2_MULTI_SCENARIO_SPEC.md)
- [Phase 2-5 受控驗收](PHASE_2_5_ACCEPTANCE.md)
- [Phase 2-6D 受控驗收](PHASE_2_6D_ACCEPTANCE.md)
- [開發與驗證流程](DEVELOPMENT_WORKFLOW.md)

**current milestone 只在此文件更新；README 與長篇 spec 不再重複維護完整 current-status list。**