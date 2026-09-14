# Phase 2-5 正式推估受控測試區人工驗收紀錄

狀態：✅ 已完成

驗收日期：2026-09-14

## 驗收範圍

本次為 Phase 2-5 的「受控單機／多 session 測試區人工驗收」。驗收環境為 Windows、本機 Streamlit 與 `U:` SMB 測試 shared root；正式 shared root 全程未使用。

## 已完成項目

1. 初始化測試 shared root，production reader、system metadata、annual current、annual bundle 與 annual audit 均為 healthy，且不需要 built-in fallback。
2. 建立第一筆正式推估版本，official current revision 由 `0 → 1`。
3. 在同一個仍開啟的 batch 建立第二筆正式版本，revision 由 `1 → 2`；兩版 `batch_id` 相同，`estimate_version_id` 不同，舊版本未被修改。
4. 由第二個 Streamlit session 建立第三筆正式版本，revision 由 `2 → 3`；實際資料確認第三版使用不同 `batch_id`。
5. 舊 session 持有 revision 2 的 preview；revision 3 建立後，舊 session 自動顯示「正式保存預覽已失效，請重新產生」，且不能沿用舊 preview 正式保存。
6. 完全關閉 Streamlit 與瀏覽器後重新啟動，shared root 與 annual baseline 仍正常且 diagnostics healthy。
7. 驗收後唯讀盤點確認：
   - current revision = 3。
   - 共有 3 個不同且不可變的 official estimate versions。
   - 三個 bundle 均通過 production `validate_official_bundle()`。
   - official publish audit revision chain 為 `0 → 1 → 2 → 3`。
   - current matched audit 數量為 1。
   - orphan、invalid、staging 與 temp 數量均為 0。
   - `recovery_required = False`。
8. 驗收完成後，本次建立的 synthetic shared-storage 測試資料已清理。
9. 測試 root 原有的「年度資料填報」資料夾與全部 Excel 已逐檔確認 size、SHA-256 及時間戳前後一致並完整保留。

## 尚未涵蓋的實機驗收

這份紀錄不代表下列項目已完成：

- 公司多電腦同時操作。
- 真實 SMB 斷線。
- OS lock contention 壓力測試。
- 中斷／恢復實機測試。

上述多人、網路、中斷、復原與營運驗收仍屬 Phase 2-8；下一個功能階段為 Phase 2-6「跨電腦／正式版本載入與接續工作」。
