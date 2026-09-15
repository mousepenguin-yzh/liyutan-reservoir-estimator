# Phase 2-6D 正式版本接續 UI 受控人工驗收

狀態：待執行。這份文件只規劃 synthetic shared root／受控 test root 的單機驗收；不得使用正式 `U:` 根目錄。真正雙電腦、SMB lock pressure、斷線、rename／replace timeout 與營運驗收留在 Phase 2-8。

## 前置條件與安全界線

- 使用獨立暫存目錄建立至少兩個 annual immutable versions（A、B），current 指向 B。
- 建立至少兩個 official immutable versions，current → previous chain 完整；舊正式版使用 A，current 可使用 A 或 B。另放一個 orphan，確認一般 UI 不顯示。
- 只把 `LIYUTAN_SHARED_ROOT` 指到上述 synthetic/test root，並再次人工核對路徑不是正式 `U:`。
- 此驗收不開啟或測試真正 SMB concurrency；任何正式保存也只能寫入受控 test root。
- 驗收前後保存 synthetic root 的檔案清單與 checksum，確認純載入與尚未按正式保存時沒有共享寫入。

## A. 正式版本選擇與預覽

1. 開啟第一階段「工作批次來源」，選擇「從正式版本接續」。
2. 確認 current 排第一，previous chain 可選，orphan／staging／tmp 不出現。
3. 逐一選取版本但不按建立，確認目前 working batch 不變。
4. 核對預覽的 batch name、projection range、initial capacity、scenario names、created time、operator、正式備註、source annual 與 derived lineage；完整 version ID 只在進階資訊。

## B. 載入 current 正式版本

1. 先建立一份未保存 working batch，再選 current 並完成單次取代確認。
2. 按「從此正式版本建立新工作」。
3. 確認 new batch ID 不等於 source batch ID、scenario IDs 保留、derived_from 等於所選 current、active results 與正式保存 preview 已清除。
4. 確認既有跨批次 comparison registry 未被清空，正式 snapshot 與共享檔案 bytes 未改變。

## C. 載入 old 正式版本

1. 選 previous chain 上的舊版並建立新工作。
2. 確認 new batch、日期、起始／歷史庫容、水庫參數、情境、入流、出流、daily outflow 與 override 還原。
3. 重新整理頁面，確認來源資訊、lineage、batch 與 authoritative outflow 穩定。

## D. old annual A 與 current B

1. 載入使用 A 的舊正式版，系統 current 保持 B。
2. 確認畫面同時顯示「工作年度 A」與「系統目前年度 B」，且不是錯誤狀態。
3. 核對 Q helper 與 demand helper 使用 A，reservoir parameters 使用 source official snapshot。
4. 不改日期直接 rerun，確認 active annual 仍是 A，overlap inputs 沒有被 B 改寫。
5. 暫時讓 A missing/corrupt，確認工作可查看，但 annual helper／正式保存 disabled，且不切到 B；復原 fixture 後再繼續。

## E. 日期延長

1. 修改 projection end，但尚未按確認，確認 active session dates 與 batch 不變。
2. 同旬內延長並按「套用日期變更」，確認不切 A→B，新增 daily rows 使用既有旬 working outflow。
3. 延長到 brand-new periods，核對畫面列出的新增旬、B 與「原有旬不重算」提示。
4. 只按「使用目前年度基準延長」後才完成：active annual 切 B、新增入流 blank、新增出流取 B、daily rows 完整，overlap 逐值不變。

## F. Q90／Q80

1. 對 scenario A 的新增空白旬按 Q90、scenario B 按 Q80、scenario C 保持人工。
2. 確認只填最近 added + blank cells，overlap 與已人工填寫的 added cell 不被覆蓋。
3. 若 fixture 有 shared added period，只用「新增共用旬」按鈕；確認 shared 與全部 scenarios 一致，UI 不提供逐 scenario 不同 Q。

## G. 起始日期修改與庫容重新確認

1. staged 修改 projection start 並套用。
2. 確認 batch initial capacity 真正為 pending/None，畫面不代入 0 或舊值，第四階段計算 disabled。
3. 輸入有效新庫容並按確認，確認 pending 解除且新值進入 batch；負值、NaN 或非法值不可接受。

## H. 重新計算

1. 保持一個 scenario 新增旬未填，確認畫面列出待填；shared 待填時確認所有情境均被阻止。
2. 補齊必要入流並執行批次計算。
3. 確認只產生新 working results，沒有載入 source formal scenario summaries／daily results。

## I. 正式保存 preview lineage

1. 載入 source official X、active annual A，重新演算後產生正式保存 preview。
2. 確認 candidate `derived_from_official_version_id=X`、annual=A；若已明確以 B 延長，annual 應為 B。
3. 確認 `previous_official_version_id` 等於 preview 當下 observed official current，而不是 X 的 source previous。
4. 本驗收若不需驗證 publisher，停在 preview；不得誤寫正式根目錄。

## J. restart／rerun 基本穩定性

1. 在 load、date adjustment、Q fill、capacity confirm 與 calculation 各狀態進行一般 rerun。
2. 確認 active annual 不 silent rebase、authoritative outflow 不被 current demand 覆蓋、override 不反向回復舊 session copy。
3. 切回「建立全新推估」並完成確認，確認建立新 batch ID、清除 derived/source/added/pending context、helper 恢復 current annual B，comparison registry 保留。

## 驗收紀錄

- 執行日期：待填
- 執行人：待填
- synthetic/test root：待填（不得為正式 `U:`）
- 測試 commit：待填
- A～J 結果：待填
- 發現事項／截圖位置：待填
- 結論：待執行；未完成前不得宣稱 2-6E 或 Phase 2-8 acceptance 已通過。
