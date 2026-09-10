# 鯉魚潭水庫庫容推估系統

以 Streamlit 建置的鯉魚潭水庫逐日庫容情境推估工具，目的是把原本沿用於 Excel 的例行工作流程、必要輸入及調度邏輯整理成可操作、可檢查、可交接的網頁。

本系統主要供水庫營運與水源調配承辦人進行每週例行推估及情境比較。它是依人工設定條件進行演算的決策輔助工具，不是即時水文預報模型，也不取代正式調度決策。

## 目前狀態

- 核心推估、圖表、旬表、日表及 CSV 匯出已可支援例行作業。
- V2 第一階段支援 1～N 個可新增、複製、改名、刪除及排序的入流情境；標準 A／B／C 僅是快速範本。
- 推估批次可設定 0～全部共用旬（預設 2 旬），所有情境共用同一套逐日出流及抗旱覆蓋。
- 第四階段可批次演算、摘要比較並一次查看一個情境詳情；單一情境仍沿用相同水量平衡公式與成果產品。
- 第五階段可整批或個別加入成功結果，並以批次、情境及設定指紋避免重複，保留跨批次比較。
- 完整設定可下載／載入具版本的 JSON；載入先驗證、預覽及確認，且不攜帶舊演算結果。
- 可測的 V2 資料結構、驗證、設定交換及批次演算邏輯已拆至 `v2_workflow.py`，介面仍位於 `app.py`。
- 第二階段 2-2 已完成 `shared_storage_schema.py` 純邏輯：正式 JSON／CSV schema、穩定序列化、SHA-256、設定指紋、年度資料與正式推估版本包驗證；合成測試位於 `tests/test_shared_storage_schema.py`。
- 第二階段 2-3 已完成 `shared_storage_reader.py` 唯讀載入：明確設定 `LIYUTAN_ENABLE_SHARED_STORAGE=1` 後，透過 `LIYUTAN_SHARED_ROOT` 讀取並完整驗證目前年度版本及最近正式推估；不建立、修改、重新命名或刪除共享檔案。
- 共享功能尚未啟用時，既有線上 Streamlit 網站維持內建年度資料的相容模式；共享功能啟用後若讀取失敗，則只有使用者明確選擇「內建備援資料」後才能進行非正式試算。
- 第二階段 2-4 年度資料功能實作已完成，對一般維護者可概括為三件事：
  1. **年度 Excel 驗證與版本建立**：產生空白公版、完整驗證人工上傳內容、預覽差異，並安全發布不可變年度版本；建立後不會自動啟用。
  2. **年度版本安全啟用與版本切換**：人工再次確認後，透過 Windows/SMB 排他鎖、observed-state conflict check、atomic current publication 與 append-only audit 啟用或切換完整版本。
  3. **診斷與受控復原**：唯讀盤點 current、versions、audit 及殘留 evidence；支援 healthy-current audit 補建、既有版本重新啟用、首次 current 初始化，以及只有在 evidence 足以唯一判斷時的 missing／invalid current reconstruction 或 broken-target switch。
- 2-4 功能實作完成，但公司 SMB 實機多人、鎖定與中斷 acceptance 仍留在 2-8；generic `formal_write_available`／`formal_operations_available` 仍維持 `False`。
- 2-4 年度資料 UI 已完成收斂：一般畫面只呈現可用狀態、使用年度、實績截止旬與更新日期；「年度資料維護」依上傳、檢查差異、建立新版、啟用新版四個任務呈現；current、revision、audit、fingerprint、SHA-256 及 recovery 技術資訊保留在預設收合的進階區。
- 2-5 尚未開始，正式推估保存仍不可用；本次 UI 收斂沒有開啟 generic `formal_write_available` 或 `formal_operations_available`。
- 既有水文或出流工作階段上傳只會套用於當次 Streamlit 工作階段，並持續標示為非正式資料，不會永久更新共享正式資料。
- 暫存情境也只存在當次工作階段，關閉或重啟工作階段後可能消失。
- JSON 設定檔可由使用者手動下載、帶到另一台電腦再載入，但不會自動同步或自動恢復。
- staging、quarantine 與一般 temp evidence 的隔離／清理仍不自動執行，屬 2-8 人工維運與實機 acceptance 範圍。


## V2 多情境工作流程（第一階段已完成）

V2 第一階段已於 2026-08-14 完成實作、測試及人工驗收，合併紀錄為 [PR #3](https://github.com/mousepenguin-yzh/liyutan-reservoir-estimator/pull/3)。完整規格與驗收範圍請見：

- [V2 多情境推估工作流程規格](docs/V2_MULTI_SCENARIO_SPEC.md)

本階段完成 1～N 個入流情境、共用 0 旬至全部推估旬、共用出流、批次演算、步驟四單一情境詳情，以及步驟五跨批次比較相容；核心水量平衡公式未改動。

第二階段改採「每台公司電腦本機執行 Streamlit＋公司內網共享資料夾保存正式資料」。2-4 年度 Excel 驗證與不可變版本建立、安全啟用／切換、唯讀 diagnostics 與受控 recovery 功能均已完成；公司 SMB 實機 acceptance 留在 2-8，正式推估保存、跨電腦接續及桌面捷徑仍尚未實作。完整方向請見 [本機 Streamlit＋內網共享資料夾永久保存規格](docs/LOCAL_SHARED_STORAGE_SPEC.md)。

## 技術與單位

- Python
- Streamlit
- pandas
- Plotly
- openpyxl
- 流量單位：cms（立方公尺／秒）
- 水量及庫容單位：萬噸
- 換算：1 cms = 8.64 萬噸／日

## 操作流程

系統將例行推估整理為五個階段：

1. **基礎資料設定**
   - 設定展示起始日、推估起始日及推估結束日。
   - 設定推估起點前一日 24:00 的實際庫容。
   - 必要時輸入展示期間各旬末實際庫容，供歷線圖呈現。

2. **入流條件與水文維護**
   - 可選用內建 Q5～Q95 旬流量情境。
   - 可由 Excel 複製貼上自訂旬流量。
   - 可逐旬混合使用內建情境及手動數值。
   - 支援上傳36旬標準水文資料。

3. **出流需求與抗旱調整**
   - 可使用前一年度常態需求、自訂需求或逐旬混合。
   - 標的包含上灌區、下灌區及公共給水。
   - 可針對特定日期覆寫需求，模擬抗旱或臨時調度。

4. **逐日庫容演算**
   - 依旬資料展開為逐日條件。
   - 依士林堰引水、灌溉優先、生態基流及水庫出水進行水量平衡。
   - 計算期末庫容、溢流、農業削減及空庫天數。

5. **成果產品**
   - 庫容歷線圖。
   - 橫向旬推估表。
   - 直向旬推估表。
   - 逐日明細表。
   - CSV 匯出。
   - 當次工作階段內的多情境暫存與比較。

## 核心演算概念

目前主要參數包括：

- 鯉魚潭水庫上限庫容，預設 11,584 萬噸。
- 士林堰生態基流量，預設 2.7 cms。
- 鯉魚潭最低生態放流量，預設 0.3 cms。
- 士林堰引水上限 33 cms。

概念上的逐日水量平衡為：

```text
本日末庫容
= 昨日期末庫容
+ 士林堰實際引入量
- 公共給水量
- 鯉魚潭河道放流量
```

士林堰天然流量先滿足上灌區；剩餘流量再決定下灌區可供水量及可引入水庫的流量。超過上限庫容的部分記為溢流。

本模型為例行操作的簡化模型，目前未明確納入蒸發、滲漏、河道旅行時間等項目。各參數、優先順序及簡化假設仍應逐步補上業務依據與適用條件。

## 重要限制：上傳資料目前不會永久保存

目前上傳後的資料只寫入 `st.session_state`。這代表：

- 上傳者當次操作可以使用新版資料。
- 另一位使用者不會自動取得該新版。
- 工作階段結束、應用程式重啟或重新部署後，系統會重新讀取 `app.py` 內的預設資料。
- 上傳功能目前應理解為「本次推估暫時套用」，不是「更新正式年度資料庫」。

在正式寫入機制完成前，每次試算仍應確認畫面標示的資料來源與年度版本，避免把工作階段上傳或內建備援資料誤認為共享正式資料。

## 目前的保存與跨裝置能力

| 機制 | 目前可做到 | 不能做到 |
| --- | --- | --- |
| Streamlit `session_state` | 同一次工作階段內保留輸入、情境及演算結果 | 關閉／逾時／重啟後仍保留；跨瀏覽器或跨電腦同步 |
| JSON 設定檔 | 手動下載後，可在同一台或另一台電腦載入並重新演算 | 自動保存、自動載入、多人共用最新版 |
| 公司內網共享資料夾年度資料 | 讀取目前年度版本與最近正式推估摘要；建立／啟用 immutable 年度版本；顯示 diagnostics；執行具唯一 evidence 的首次初始化、audit 補建、current reconstruction 或 broken-target switch | 尚無 destructive evidence cleanup、正式推估保存或跨裝置載入正式推估工作區；公司 SMB acceptance 留在 2-8 |

因此，目前若要換電腦接續工作，必須先下載 JSON，再於另一台電腦手動載入。網站不會自動記得上一次推估條件，也不會辨識使用者或裝置。

## 第二階段永久保存方向

已確定採用：

> 每台公司電腦在本機執行 Streamlit；程式碼由本機 Git repository 與 GitHub 更新；正式資料保存在公司內網共享資料夾。

鯉魚潭正式共用根目錄規劃為 `U:\經管科\水庫庫容推估系統\鯉魚潭`。Google Sheet 是早期評估過的方向，已不再是第二階段主要永久儲存方案。

正式推估將保存完整輸入、摘要、完整逐日結果、年度資料版本、程式與 schema 版本、操作人、備註、上一版本及 checksum；年度資料將涵蓋 Q5～Q95、出流需求、滿庫容量、生態流量及士林堰 33 cms 引水上限。兩者都採新增版本、不覆蓋舊版。

資料 schema、共享資料唯讀啟動，以及 2-4 年度 Excel 驗證／版本建立、安全啟用／切換、diagnostics 與受控 recovery 功能均已完成。2-4 功能實作完成；公司實際 SMB 多人、鎖定與中斷驗收留在 2-8。

- [本機 Streamlit＋內網共享資料夾永久保存規格](docs/LOCAL_SHARED_STORAGE_SPEC.md)

## 資料管理原則

- 「系統基準資料」或「年度基準資料」是所有新推估共用的預設基礎，包括 Q 值、年度基準出流及水庫參數；一般每旬推估不需要重新填寫年度 Excel。
- 「正式推估版本」是某一次推估的完整條件與結果；單次自訂入流、出流、抗旱調度或臨時參數只屬該次推估，不會反向修改系統基準。關係可表為：`系統基準資料＋本次推估調整＋計算結果＝正式推估版本`。
- 正式年度資料採新增版本，不直接覆蓋。
- 正式年度 JSON／CSV 與必要來源附件應保存於公司內網共享資料夾。
- 程式碼、資料內容與憑證分開管理。
- 每次正式推估應能追溯使用的資料版本與調度假設。
- 備援資料必須標示版本，不可被誤認為最新資料。
- 若資料涉及機關內部資訊，公開方式及存取權限應依機關規定處理。

## 本機執行

```bash
pip install -r requirements.txt
streamlit run app.py
```

### 共享資料來源設定

共享資料功能必須以 `LIYUTAN_ENABLE_SHARED_STORAGE=1` 明確啟用，並同時設定 `LIYUTAN_SHARED_ROOT`。Windows PowerShell 範例：

```powershell
$env:LIYUTAN_ENABLE_SHARED_STORAGE = '1'
$env:LIYUTAN_SHARED_ROOT = 'U:\經管科\水庫庫容推估系統\鯉魚潭'
streamlit run app.py
```

未將 `LIYUTAN_ENABLE_SHARED_STORAGE` 設為精確字串 `1` 時，程式完全不讀取共享路徑，並維持 PR #8 合併前既有網站的內建年度資料操作方式。這是現有線上 Streamlit 網站在正式本機系統切換前的明確相容模式，不代表共享讀取失敗後的自動備援。未來桌面啟動器會同時設定功能開關與正式共享路徑；桌面啟動器本身不屬於 2-3 範圍。

共享模式啟用後，程式不會猜測其他路徑。未設定共享根目錄、磁碟或路徑不存在、權限不足、schema 錯誤、缺檔、manifest 或 checksum 驗證失敗時，畫面會顯示失敗類型及一般使用者可理解的處理訊息，不會自動使用舊快取或內建資料。

年度資料正式建立／啟用另有第二層、預設關閉的功能開關：

```powershell
$env:LIYUTAN_ENABLE_ANNUAL_DATA_WRITES = '1'
```

只有值精確為 `1`，且共享模式已啟用、root 與 `system.json`（含 `reservoir_id=liyutan`）已驗證，reader 與 diagnostics 均確認健康 current／完整 bundle／唯一匹配的 current transition audit，或 diagnostics 確認 current 缺失且 versions inventory 完全為空的合法第一版狀態時，`annual_data_write_available=True`。current audit missing／ambiguous、current／target 損壞、current 缺失但已有任何 valid 或 invalid version entry，以及無法可靠盤點的狀態一律停止 create／activate。單純 historical、orphan、staging、quarantine 或 temp evidence 在 current 與 audit 健康時只標示 attention，不一定阻止寫入。其他 fallback、compatibility、權限或讀取錯誤仍不開放。正式 activation 另限 Windows/SMB；非 Windows 可 import/test，但 production 啟用按鈕停用。這個年度專用能力不會打開 generic `formal_write_available` 或 `formal_operations_available`。

Recovery Actions 另有第三層、預設關閉的高風險開關：

```powershell
$env:LIYUTAN_ENABLE_ANNUAL_DATA_RECOVERY = '1'
```

只有共享模式、年度寫入與 recovery 三個開關都為精確字串 `1`，執行環境為 Windows，且 diagnostics 明確符合唯一 safe case 時，`annual_recovery_available=True`。可用 action 依狀態限定為 healthy-current audit 補建、人工重新啟用完整 historical/orphan、first-current initialization、依唯一 audit chain reconstruction，或 broken-target 的人工 target switch；不顯示萬用 repair。ambiguous、audit chain 衝突、unreadable evidence、無完整 target 或 uninspectable 一律停用。一般 `annual_data_write_available` 不會為了 recovery 被強行打開；完成並 rerun 為 `matched_recovery` 或 `matched_repair` 後才恢復正常年度寫入。

資料來源分為：

| 資料來源 | 性質 | 行為 |
| --- | --- | --- |
| 相容模式內建年度資料 | 過渡用途 | 僅在共享功能開關未啟用時沿用既有網站行為；不讀取共享路徑，也不具正式寫入資格 |
| 共享正式年度資料 | 正式 | `system.json`、current、版本目錄、完整 Q5～Q95、出流需求、水庫參數、manifest 與 checksum 全部通過後才整組使用 |
| 工作階段上傳資料 | 非正式 | 只影響目前 Streamlit 工作階段；畫面持續警示，不能冒充共享正式版本 |
| 內建備援資料 | 非正式 | 已啟用共享模式但讀取失敗後，只有使用者明確點選備援按鈕才會啟用 |

共享 reader 完整驗證成功時 `shared_storage_readable=True`。`annual_data_write_available` 與 `annual_recovery_available` 是互相獨立的年度專用能力；generic `formal_write_available=False` 與 `formal_operations_available=False` 仍固定不變，避免被尚未完成的 2-5 正式推估保存誤用。正式共享資料內容不得加入 Git；開發及自動化測試一律使用 pytest 暫存資料夾與合成資料。

### 產生 2-4A 空白年度資料 Excel 公版

產生器必須由使用者明確指定輸出檔案，不會猜測或預設任何共享路徑；若檔案已存在，除非明確加上 `--overwrite`，否則會停止：

```powershell
python scripts/create_annual_data_template.py --output "C:\明確指定位置\鯉魚潭年度資料匯入範本.xlsx"
```

公版包含 `版本資訊`、`水文Q值`、`年度基準出流`、`水庫參數` 四張工作表。所有年度、Q 值、出流與參數業務數值均保持空白，只預填技術範本版本、水庫識別、水庫名稱、固定欄位、固定36旬、單位、填寫說明與資料驗證規則。Excel 是人工填寫與交換格式，不能直接當作正式資料來源；正式共享根目錄仍未開放寫入。

### 2-4B Excel 驗證與差異預覽

Streamlit 頁面上方提供獨立的「系統基準資料維護－Excel驗證與差異預覽」。使用者必須手動上傳 `.xlsx`；系統不會掃描或自動載入公司資料夾。解析器拒絕未知範本版本、巨集、外部連結、公式、缺少或額外工作表、修改固定機器代碼或旬鍵、未知資料列／欄位，以及不完整、非有限、負值或語意順序錯誤的業務資料。

驗證成功後只在記憶體中建立候選資料，先顯示適用年度、實績截止旬、完整性與分區差異摘要；檔案 SHA-256、候選 fingerprint 等技術資訊保留在進階收合區，warnings 仍逐項顯示位置與修正資訊。水庫參數的數值、適用起日、來源及備註均納入主要差異筆數與明細；舊版未保存這些 metadata 時會標示「舊版未記錄」，不會誤報完全相同。只有在 `system.json` 已成功驗證、`annual-data/current.json` 確實不存在，且 diagnostics 確認 versions inventory 完全為空時，介面才顯示可確認的第一版完整預覽；current 缺失但已有任何 valid 或 invalid version entry 時明確要求 recovery 判斷。相容模式、未設定或無法存取根目錄、`system.json` 尚未初始化，以及權限、損壞或版本不一致等讀取失敗，仍可顯示候選內容，但會明確標示無法確認正式環境是否存在舊版，且不產生看似可靠的新舊差異。所有畫面均標示「僅供驗證與差異預覽，尚未建立或啟用正式系統基準版本。」`formal_write_available` 與 `formal_operations_available` 仍為 `False`。

供後續 2-4C 使用的候選資料位置約定為 `AnnualDataCandidate.parameter_metadata[parameter_code]`，每項包含 `effective_start_date`、`source_reference` 與 `note`。若未來正式年度版本保存這些欄位，比較器接受同層的 `parameter_metadata` 映射；本階段不更動正式 schema，也不寫入任何版本。

本階段不建立版本目錄，不寫入正式 JSON／CSV、`system.json`、`annual-data/current.json` 或 `COMMITTED.json`，也不實作建立／啟用按鈕。實際公司 Excel、正式業務數值與驗證輸出不得提交 GitHub；正式共享根目錄仍未開放寫入。

### 2-4C1 年度不可變版本安全建立

`annual_data_version_writer.py` 接受 2-4B 已驗證且由使用者確認 fingerprint 的 `AnnualDataCandidate`、完全相同的原始 Excel bytes／原始檔名、人工宣告的操作人與建立備註。建立前會重新解析 Excel，核對來源 SHA-256、candidate fingerprint、候選完整內容及 warnings；有 warnings 時必須由呼叫端明確確認，確認後的完整 warning 紀錄會保存於 `version.json`。操作人名稱只是人工宣告，不代表系統已驗證真實身分。

完整版本固定包含三個核心資料檔、`source/original.xlsx`、`version.json` 及最後寫入的 `COMMITTED.json`。原始 Excel bytes 不經另存，原始檔名只作 metadata，所有正式內容均列入 checksum。writer 只接受已存在且 `system.json` 通過 schema 與 `reservoir_id=liyutan` 驗證的指定根目錄；它在同一根目錄 staging 寫入、逐檔 flush／關閉／重讀核對、完整 schema 與 36 旬驗證後才 rename 到 `annual-data/versions/<version_id>`，並在發布後再次讀回驗證。驗證失敗資料移至 quarantine；中斷證據保留；既有版本永不覆蓋或合併。

「建立版本」與「啟用版本」是兩個不同動作。2-4C1 成功只表示不可變版本已發布且仍未啟用；本模組不建立、不修改、不切換 `annual-data/current.json`，也不寫 audit。啟用的後端責任由獨立的 2-4C2a 模組承擔，Streamlit 建立／啟用按鈕由 2-4C2b1 接線。

### 2-4C2a 年度資料版本啟用安全核心

`annual_data_activation.py` 只接受 `annual-data/versions/<target_version_id>` 下已存在的版本。它先驗證共享根目錄與 `reservoir_id=liyutan` 的 `system.json`，完整重讀 target bundle 並執行既有 checksum、schema、`COMMITTED.json` 與 36 旬驗證；取得鎖後會再次驗證完全相同的 target bytes。staging、quarantine、路徑跳脫、symbolic link、缺檔、checksum 或 schema 錯誤均不能啟用，且 target 內任何檔案都不會修改。

正式鎖固定為 `locks/annual-current.lock`。Windows implementation 以 `CreateFileW` 開啟並持續持有不允許任何 share mode 的 file handle，遇分享衝突時以 200～500 ms jitter 重試，預設最長 15 秒；非 Windows 可安全 import，但核心測試必須注入 fake lock。鎖內才重讀 current，並同時比對 `observed_revision` 與 `observed_current_version_id`；任一不同都回報 `revision_conflict`，不採 last-write-wins。conflict check 通過後，若 current 已存在，還必須以同一套 immutable bundle 規則完整驗證 current 所指版本；缺目錄、缺 `COMMITTED.json`、checksum/schema 錯誤或其他損壞一律回報 `current_version_invalid`，停止正常切換並留待 recovery，不得以合法 target 掩蓋異常。缺少 current 視為 revision 0／current null，target 已是 current 則回報 `already_current`，不增加 revision。

新 current 先在 `annual-data` 同目錄唯一 temp file 完整寫入、flush/fsync、關閉、重讀及驗證，再以 atomic replace 發布並再次重讀確認。audit event 由純 validator 驗證，再以唯一檔名、temp 與 no-overwrite atomic publication 寫入。若 current 已切換而 audit 後續失敗，系統不 rollback current；2-4C2b2b1 只在 current 仍健康且 audit zero-match 時允許補建 recovery evidence，broken current 仍留待 2-4C2b2b2。

### 2-4C2b1 Streamlit 年度建立／啟用與工作區保護

2-4C2b1 將既有 2-4B preview 接到 2-4C1 publisher；操作人、建立備註、差異與 warnings 維持顯示及獨立確認，candidate fingerprint、原始檔名／SHA-256 等技術資訊移入進階收合區。一般維護流程使用「建立新版」與「啟用新版」；建立成功明示「尚未套用」，不會自動呼叫 activation。啟用區以業務語言說明新開啟推估與既有工作區的影響，observed current/revision、target 與 software provenance 保留在進階收合區；送往 2-4C2a 的 exact observed state 與 revision conflict 不重試規則完全不變。

啟用的 software provenance 由 `software_provenance.py` 以唯讀 `git rev-parse HEAD` 與 `git status --porcelain --untracked-files=normal` 取得；repository 固定為本 repository，`app_version` 自動衍生為 `git-<12字元commit>`。無可靠 40 字元 SHA 時停用啟用；dirty 狀態會警示並如實寫入 audit，但本階段不另立禁止政策。

已開啟工作區記住其 loaded annual version。其他電腦啟用新版後，rerun 只顯示「系統基準資料已有新版」，不會改 hydrology、demand、水庫參數或使用者輸入。使用者可選擇「暫時使用目前資料」（通知持續），或明確按「重新載入新版」；後者完整套用新版、清除水文／出流年度 session overrides、使舊演算結果失效並更新 workspace version。activation 成功本身不直接改工作區。

`current_switched_audit_incomplete` 會留下 session recovery-required banner、停止再次建立／啟用且不 rollback。diagnostics 另會在每次 rerun 由 filesystem 尋找與 current 的 `(N-1, A) → (N, B)` 完全匹配的合法 activation/recovery/repair evidence；零份、矛盾或多個可能狀態即使 session key 已消失仍會重新判定。後續受控 action 由 2-4C2b2b1/b2b2 處理；evidence cleanup 不自動執行。公司實際 SMB acceptance 留在 2-8，generic `formal_write_available` 與 `formal_operations_available` 仍為 `False`。

### 2-4C2b2a 年度資料唯讀 diagnostics／inventory

`annual_data_diagnostics.py` 只接受明確 root 或 `SharedStorageResult.root`，先驗證 root directory、`system.json` schema 與 `reservoir_id=liyutan`；system 不可信時停止，不猜測正式資料。它只掃描 `annual-data/versions` 的直接子項目、writer 命名的 `staging/annual-data-*`、`quarantine`、固定 `audit/events/YYYY/MM/*.json` 與已知 current／audit temp pattern。完整版本依可靠證據分成 current、historical（current previous reference 或合法 activation audit before／after reference）及 orphan；驗證失敗則保留可理解原因並列為 invalid。orphan 代表完整但沒有 activation history 證據，不等同損壞。

overall severity 包含 `healthy`、`attention`、`initialization_required`、`recovery_required` 與 `uninspectable`。合法 recovery event 且 transition、`current.json` checksum 與 current manifest checksum 全部符合目前 current 時為 `matched_recovery`；完整 repair event 則為 `matched_repair`，current 已寫入但 repair audit 未發布時為 `repair_evidence_incomplete`。多個可能 transition 或不可信 evidence 停止自動復原。diagnostics 模組本身維持唯讀。

### 2-4C2b2b1 年度資料 safe recovery

`annual_data_recovery.py` 只補 healthy current 的 audit evidence。事件類型固定為 `annual-data-activation-recovery`，保存 recovered transition、補建操作人／備註／software provenance、執行 diagnostics 的 hostname/PID、current 與 immutable manifest checksum，以及補建前的 diagnostics state；`result=recovered_audit_evidence`，並明確聲明這是事後 recovery record，不是原始 activation event，也不宣稱知道原始 note、software、hostname、process 或真正操作人。

寫入前先取得既有 `annual-current.lock`，鎖內重跑 diagnostics，重新核對 revision、current、previous、完整 bundle、原始 audit 仍為 zero match、recovery audit 仍不存在。事件以唯一 temp file、flush/fsync、重讀 schema validation、no-overwrite atomic publish 與發布後重讀驗證建立；動作不修改 `current.json` 或 immutable version。若鎖內狀態改變或別台電腦已補建就停止並要求重新 diagnostics，不自動 retry。

健康 current 且 audit evidence 已可接受時，inventory 中 `validation_ok=True` 的 historical／orphan（排除 current 與 invalid）可由人工選擇重新啟用。UI 顯示 target metadata 與完整差異、要求新操作人／備註／software provenance／checkbox，然後直接呼叫既有 `activate_annual_data_version()`；成功使 revision +1、previous 指向原 current，不修改 target，也不背景替換已開啟工作區。ambiguous/uninspectable 自動處理、staging/quarantine/temp cleanup 與 checksum repair 均不在本階段。

### 2-4C2b2b2 first-current initialization 與 broken-current repair

`current.json` 缺失、沒有任何 valid activation/recovery/repair transition history、audit inventory 可完整檢查且至少有一個完整 immutable orphan 時，diagnostics 顯示 `initialization_required`，而非把正常首次建置稱為系統損壞。使用者必須明確選 target；即使有多個版本也不依檔名或修改時間自動選最新版。動作直接重用 `activate_annual_data_version(..., observed_revision=0, observed_current_version_id=None, first_current_initialization=True)`，並在既有 Windows/SMB lock 內再次驗證首次條件，因此結果仍是 revision 1、previous null 與正常 `annual-data-activation` audit。

真正 repair 由 `annual_data_current_repair.py` 規劃與執行。missing／invalid current 只有在合法 transition audit 能從 revision 1 連續建鏈、每個 revision 只有一種 before/after 狀態、最後 target bundle 完整時才可 reconstruction；revision/current/previous 全由程式推導，reconstruction 不增加 revision。合法 current 指向 missing／invalid target 時，使用者可從完整 historical/orphan 中明確選擇 recovery target；`previous_version_id` 只作建議、不自動選定，成功後 revision 加一，previous 記錄原 broken current version ID。

所有 repair 都沿用 `locks/annual-current.lock`，鎖內重跑 diagnostics、核對畫面 evidence token、current bytes 與 target bundle；狀態改變即 conflict，不 retry。current publication 共用 activation 的同目錄 unique temp、flush/fsync、重讀 validation、`os.replace` 與 replace 後重驗。repair 不修改或刪除任何 version、staging、quarantine 或一般 temp evidence，也不修 checksum。

repair audit 使用獨立 `annual-data-current-repair` event type，明確記錄 repair kind、operator/note/software、hostname/PID、pre-repair diagnostics、target manifest checksum、before/after revision 與 resulting current。existing current 的原始 bytes 在覆寫前以 SHA-256 與 base64 完整保存；missing current 則明記不存在。若 current 已成功而 audit publication 失敗，不 rollback current、不回報成功，完整 repair audit temp 保留為 pending evidence；diagnostics 顯示 `repair_evidence_incomplete`，後續只能在鎖內補完該既有 audit，不得重送 current repair。成功後 current 來源顯示 `matched_repair`。2-4 功能實作至此完成，公司 SMB 實機 acceptance 留在 2-8。

## 自動化測試

```bash
python -m compileall -q app.py v2_workflow.py shared_storage_schema.py shared_storage_reader.py annual_data_excel.py annual_data_preview_ui.py annual_data_version_writer.py annual_data_activation.py annual_data_maintenance.py annual_data_diagnostics.py annual_data_recovery.py annual_data_current_repair.py software_provenance.py scripts tests
python -m pytest -q
```

測試另涵蓋 Excel 公版重新載入、2-4B 四表解析、固定欄位與36旬完整性、Q5～Q95 映射與順序、出流與參數驗證、公式／巨集／未知結構拒絕、fingerprint、參數 metadata、可確認第一版與既有版本差異，以及 2-4C1 原始 Excel 重驗、warnings 確認、完整 bundle、逐檔 checksum、staging／quarantine／rename、中斷注入、不可覆蓋及未啟用保證。2-4C2a 測試涵蓋首次／再次／切回啟用、雙欄位 observed conflict、already-current、損壞 target/current、fake lock 互斥與 timeout、current 原子切換中斷點、audit schema／唯一檔名／不可覆寫，以及 current 已切換但 audit 未完成的 recovery-required 狀態。共享資料測試另涵蓋完整載入、未初始化／不可讀狀態、無正式推估、路徑與權限錯誤、manifest、checksum、current 競爭變更、Streamlit 工作區隔離與必須明確選擇的非正式備援模式。自動化測試只使用 pytest `tmp_path` 及合成資料，不存取 `U:`。

Repository 亦包含 `.github/workflows/tests.yml`，每次 push 與 Pull Request 都會使用 Python 3.12 執行 compileall 與完整 pytest 測試。

## 後續文件待補

- Q5～Q95 的統計期間、測站、計算方法及資料來源。
- 前一年度出流需求的資料年度與更新程序。
- 上、下灌區供水優先順序的操作依據。
- 士林堰及鯉魚潭生態流量參數來源。
- 33 cms 引水上限及滿庫資訊來源。
- 目前模型刻意忽略項目的適用性說明。
