# 本機 Streamlit＋內網共享資料夾永久保存規格

狀態：第二階段第一步，文件規格定稿；功能尚未實作

適用專案：鯉魚潭水庫庫容推估系統

正式共用根目錄：`U:\經管科\水庫庫容推估系統\鯉魚潭`

不適用：德基 repository 與 `U:\經管科\水庫庫容推估系統\德基`

## 1. 決策摘要

第二階段採用以下架構：

- 每台公司電腦在本機執行 Streamlit。
- 程式碼保存在本機 Git repository，透過 GitHub 發布與更新。
- 正式年度資料、正式推估版本及操作紀錄保存在公司內網共享資料夾。
- Google Sheet 不再是第二階段的主要永久儲存方案。
- 一般使用者日常操作由桌面捷徑啟動，不要求使用 PowerShell、Git 或手動輸入指令。

GitHub 只保存程式、測試、文件，以及不含公司正式資料的範本。真正的年度資料與正式推估成果只可放在公司內網共享資料夾。

本文件使用「必須」、「不得」描述驗收時不可省略的要求；使用「建議」描述可在不破壞資料安全與追溯性的前提下調整的實作方式。

## 2. 目標與非目標

### 2.1 目標

- 建立一份所有公司電腦可共同讀取的正式資料來源。
- 讓正式推估與年度資料採新增版本、不覆蓋舊版，並可追溯來源、操作人、程式版本與上一版本。
- 支援重開系統、換電腦或換承辦人後，從最近一次正式推估接續作業。
- 在 Windows 內網共享資料夾上避免同時寫入造成靜默覆蓋、半成品或錯誤啟用。
- 在 `U:` 斷線或資料損壞時採取明確、保守且不會誤存正式資料的行為。
- 把後續實作拆成可獨立測試與人工驗收的小階段。

### 2.2 非目標

- 本規格不改動核心水量平衡公式。
- 本規格不實作檔案存取、鎖定、畫面、桌面捷徑或自動更新。
- 本規格不建立 Excel 公版，也不把 Excel 定義為程式的正式資料庫。
- 第一版不建立帳號登入、管理密碼或可驗證的電子簽章。
- 第一版不提供自動刪除或自動清理。
- 不規劃德基資料夾、德基 repository 或跨水庫共用版本。
- 不以 Google Sheet、個人雲端硬碟、本機快取或本機任意資料夾取代正式共享根目錄。

## 3. 資料分級與權威來源

| 資料類別 | 權威來源 | 是否正式 | 保存原則 |
| --- | --- | --- | --- |
| 程式碼與文件 | 本機 Git repository／GitHub | 是 | 依 Git 版本管理，不放公司正式資料 |
| 年度資料版本 | `U:` 鯉魚潭正式根目錄 | 是 | 新增不覆蓋；人工確認後才啟用 |
| 正式推估版本 | `U:` 鯉魚潭正式根目錄 | 是 | append-only，不得直接修改或刪除 |
| 操作紀錄 | `U:` 鯉魚潭正式根目錄 | 是 | 一事件一檔，長期保存 |
| Excel 公版 | GitHub 或本機 | 否，屬交換格式 | 不得包含正式公司資料 |
| 使用者上傳的 Excel | 本機工作階段；必要時另存來源附件 | 否，除非已驗證並轉成正式 JSON／CSV 版本 | 不可直接當演算權威資料 |
| 臨時試算 | Streamlit 工作階段，或未來明確標示的非正式區 | 否 | 若有落盤，至少保留一年後才可清理 |
| 下載用 Excel／圖表等可重建成果 | 明確標示的衍生輸出區 | 否 | 至少保留一年後才可清理 |

「目前啟用版本」只由共享資料夾中的指標檔決定。資料夾修改時間、檔名排序、本機最近使用紀錄或快取都不得用來猜測目前版本。

## 4. 正式資料夾結構

後續實作應在已存在的鯉魚潭根目錄下建立以下結構；本 PR 不建立或修改任何 `U:` 內容。

```text
U:\經管科\水庫庫容推估系統\鯉魚潭\
├─ system.json
├─ annual-data\
│  ├─ current.json
│  └─ versions\
│     └─ <annual_version_id>\
│        ├─ version.json
│        ├─ hydrology_q.csv
│        ├─ outflow_demand.csv
│        ├─ reservoir_parameters.json
│        ├─ source\                 # 選用；原始交換檔或來源說明
│        └─ COMMITTED.json
├─ official-estimates\
│  ├─ current.json
│  └─ versions\
│     └─ <estimate_version_id>\
│        ├─ manifest.json
│        ├─ inputs.json
│        ├─ scenario_summaries.csv
│        ├─ daily_results.csv
│        └─ COMMITTED.json
├─ audit\
│  └─ events\<YYYY>\<MM>\<timestamp>_<event_id>.json
├─ nonofficial\                # 選用；明確標示的臨時試算
├─ generated-exports\          # 選用；可重新產生的 Excel／圖表等
├─ staging\                    # 同一共享磁碟上的寫入暫存區
├─ quarantine\                 # 中斷或驗證失敗內容；不可當正式版本讀取
└─ locks\
   ├─ annual-current.lock
   └─ official-current.lock
```

### 4.1 命名原則

- 版本 ID 必須全域唯一且不含操作人姓名，建議格式為 UTC 時間加 UUID，例如 `20260814T021530Z_550e8400-e29b-41d4-a716-446655440000`。
- 內部關聯一律使用 ID，不使用可重複的批次名稱、情境名稱或檔名作主鍵。
- 所有時間以 ISO 8601 保存並包含時區；建議正式檔保存 UTC，介面另顯示台北時間。
- 檔名及欄位名稱固定，不因操作人或年度任意改名。
- 所有正式 JSON 使用 UTF-8；CSV 使用 UTF-8 with BOM 或另一個全系統一致且有測試的 UTF-8 規則。

### 4.2 `system.json`

用途是辨識資料根目錄與整體相容性，不保存目前版本：

```json
{
  "schema": "liyutan-reservoir-estimator/shared-root",
  "schema_version": 1,
  "reservoir_id": "liyutan",
  "display_name": "鯉魚潭水庫"
}
```

程式必須同時核對 schema、版本及 `reservoir_id`，避免把鯉魚潭程式連到德基或其他目錄。

## 5. 年度資料版本

### 5.1 版本內容

一個年度資料版本是同時啟用的一組資料，必須包含：

- Q5～Q95 的 36 旬流量資料。
- 36 旬年度出流需求。
- 滿庫容量。
- 士林堰生態流量。
- 鯉魚潭最低生態放流量。
- 士林堰引水上限；現行演算使用的 `33 cms` 必須成為正式參數，不得只寫死在程式中。
- 資料來源、適用年度或期間、建立時間、人工填報操作人及備註。
- 資料 schema 版本及每個正式資料檔的 SHA-256 checksum。

引水上限參數化是後續程式實作項目；本規格不改動現行公式。

### 5.2 `version.json`

至少包含：

```json
{
  "schema": "liyutan-reservoir-estimator/annual-data-version",
  "schema_version": 1,
  "version_id": "<annual_version_id>",
  "applicable_year": 2027,
  "created_at": "2026-12-15T02:30:00Z",
  "operator_display_name": "人工填報名稱",
  "note": "年度資料更新原因與範圍",
  "source_references": ["來源文件名稱或可交接的內部索引"],
  "files": {
    "hydrology_q.csv": {"sha256": "..."},
    "outflow_demand.csv": {"sha256": "..."},
    "reservoir_parameters.json": {"sha256": "..."}
  }
}
```

`operator_display_name` 是使用者人工填報的文字，不代表已登入或已驗證身分。來源欄位不得放帳密、權杖或不應散布的敏感內容。

### 5.3 `hydrology_q.csv`

固定 36 列，每列一旬，基本欄位如下：

```text
period_key,month,period,q05_cms,q10_cms,...,q90_cms,q95_cms
```

- `period_key` 採固定年度內旬鍵，例如 `01-上旬`；不得依列號推算。
- `month` 為 1～12；`period` 只能是 `上旬`、`中旬`、`下旬`。
- Q5～Q95 每 5 一級，必須全部存在且為非負有限數值，單位固定為 cms。
- 36 旬必須恰好各出現一次，不可重複或缺漏。

### 5.4 `outflow_demand.csv`

固定 36 列，基本欄位如下：

```text
period_key,month,period,upstream_irrigation_cms,downstream_irrigation_cms,public_water_10k_ton_per_day
```

三項數值必須為非負有限數值，單位不得由使用者猜測。旬鍵與完整性規則同 `hydrology_q.csv`。

### 5.5 `reservoir_parameters.json`

至少包含：

```json
{
  "schema": "liyutan-reservoir-estimator/reservoir-parameters",
  "schema_version": 1,
  "max_capacity_10k_ton": 11584.0,
  "shilin_ecological_flow_cms": 2.7,
  "liyutan_ecological_release_cms": 0.3,
  "shilin_diversion_limit_cms": 33.0
}
```

數值示例只對應現行程式預設值，不取代業務來源確認。各欄位必須有固定單位、非負有限值驗證及合理範圍檢查。

### 5.6 `annual-data/current.json`

這是可變動的啟用指標，不是年度資料本體：

```json
{
  "schema": "liyutan-reservoir-estimator/annual-data-current",
  "schema_version": 1,
  "revision": 7,
  "current_version_id": "<annual_version_id>",
  "previous_version_id": "<previous_annual_version_id>",
  "updated_at": "2026-12-15T02:35:00Z",
  "operator_display_name": "人工填報名稱"
}
```

`revision` 每次切換啟用版本必須加一，供同時操作的衝突檢查使用。切回舊年度版本是更新指標並留下操作紀錄，不得修改舊版本內容。

正式模式啟用前必須先由初始化程序建立並確認至少一個年度資料版本；沒有有效的年度 `current.json` 時，只能使用內建資料進行非正式試算。

## 6. 正式推估版本

### 6.1 必存內容

正式保存前，使用者必須明確確認本批次「要正式保存的情境集合」。集合中的每一個情境都必須在同一份目前設定下成功完成演算，且演算結果所綁定的設定指紋必須與保存當下的設定指紋一致。只要任一要正式保存的情境缺少結果、演算失敗或設定指紋已失效，就必須阻止整個正式保存；不得建立部分成功的正式版本，也不得切換目前正式推估指標。

每次正式推估必須保存一份自足且可稽核的快照：

- 完整輸入設定。
- 各正式保存情境的設定、排序、旬入流值、原始單位及來源。
- 起始庫容、歷史庫容、共用出流、逐日出流及抗旱調整。
- 水庫操作參數，包含士林堰引水上限。
- 所有要正式保存情境的成功摘要。
- 所有要正式保存情境的完整逐日演算結果。
- 保存當下的設定指紋，以及正式保存情境 ID 清單。
- 使用的年度資料版本 ID。
- 程式 Git commit、應用程式版本、批次 schema 版本與永久資料 schema 版本。
- 建立時間、人工填報操作人、必填備註。
- 儲存當下所觀察到的上一個正式推估版本 ID。
- 每個正式檔案的 SHA-256 checksum。

失敗情境的狀態與原因只能保留在目前 Streamlit 工作階段、明確標示的非正式試算，或記錄「正式保存遭阻止」的操作紀錄中；不得寫入正式推估版本內容，也不得建立為目前正式推估版本。

正式推估是新增版本，不是更新既有版本。已提交版本目錄內的任何檔案都不得由應用程式直接修改或刪除。

### 6.2 `manifest.json`

至少包含：

```json
{
  "schema": "liyutan-reservoir-estimator/official-estimate-version",
  "schema_version": 1,
  "version_id": "<estimate_version_id>",
  "batch_id": "<batch_id>",
  "batch_name": "例行推估",
  "previous_official_version_id": "<previous_estimate_version_id>",
  "annual_data_version_id": "<annual_version_id>",
  "settings_fingerprint": "<sha256 fingerprint>",
  "official_scenario_ids": ["<scenario_id>"],
  "created_at": "2026-08-14T02:15:30Z",
  "operator_display_name": "人工填報名稱",
  "note": "本次調整與正式保存原因",
  "software": {
    "repository": "liyutan-reservoir-estimator",
    "git_commit": "<40-character commit>",
    "app_version": "<release identifier>",
    "source_tree_dirty": false
  },
  "batch_schema_version": 1,
  "files": {
    "inputs.json": {"sha256": "..."},
    "scenario_summaries.csv": {"sha256": "..."},
    "daily_results.csv": {"sha256": "..."}
  }
}
```

第一版不把 `operator_display_name` 當成登入證明。畫面必須清楚標示「人工填報，未經登入驗證」。

### 6.3 `inputs.json`

`inputs.json` 以目前 V2 批次 JSON 的語意為基礎，正式 schema 定稿時至少保留：

2-2 固定永久保存外層 schema 為 `liyutan-reservoir-estimator/official-inputs` version 1，欄位包含 `annual_data_version_id`、`batch_id`、`official_scenario_ids`、含 33 cms 欄位的 `reservoir_parameters` 正式快照，以及不含清單外情境的現有 V2 `batch`。正式設定指紋以整份驗證後的外層資料進行 deterministic fingerprint。

- 批次與日期欄位。
- 起始庫容與歷史庫容。
- 完整水庫參數快照。
- 旬別清單、共用旬數及共用入流。
- manifest 的 `official_scenario_ids` 所列全部情境及其每旬入流來源；不得夾帶清單外的失敗或非正式情境。
- 旬出流與權威逐日出流。
- 日期／抗旱覆寫及啟用狀態。
- 年度資料版本 ID。

現有可攜 JSON 不保存舊演算結果；正式版本延續此界線，由獨立 CSV 保存演算結果。載入正式設定後仍必須依失效規則重新演算，不能把舊結果當成目前工作區的有效結果。

### 6.4 `scenario_summaries.csv`

每個要正式保存且成功完成演算的情境一列，至少包含：

```text
version_id,batch_id,scenario_id,scenario_name,scenario_order,
calculation_status,settings_fingerprint,
final_capacity_10k_ton,minimum_capacity_10k_ton,
spill_volume_10k_ton,agricultural_reduction_volume_10k_ton,dry_days
```

`calculation_status` 在正式版本中只能是 `success`，且每列 `settings_fingerprint` 必須與 manifest 相同。摘要數值必須能由 `daily_results.csv` 或同版演算規則核對。若有任何要正式保存的情境失敗、缺漏或指紋不符，整份 `scenario_summaries.csv` 不得發布為正式版本。

### 6.5 `daily_results.csv`

使用長表格式，每列代表一個情境的一日結果。至少包含 `version_id`、`batch_id`、`scenario_id`、`settings_fingerprint`、`date`，以及目前逐日結果產品的完整欄位，包括天然流量、需求、實際放水、削減、士林堰河道保留、實際引水、引入量、大壩河道放流、公共給水、總出水、溢流、昨日庫容、本日庫容及淨變化。

檔案必須完整涵蓋 manifest 中每一個 `official_scenario_ids` 的全部推估日期，且不得包含清單外情境。欄位名稱、順序、型別與單位必須由資料 schema 固定；不得只保存畫面格式化後的字串。

### 6.6 `official-estimates/current.json`

格式與年度資料的 `current.json` 同樣採 revision：

```json
{
  "schema": "liyutan-reservoir-estimator/official-estimate-current",
  "schema_version": 1,
  "revision": 18,
  "current_version_id": "<estimate_version_id>",
  "previous_version_id": "<previous_estimate_version_id>",
  "updated_at": "2026-08-14T02:15:35Z",
  "operator_display_name": "人工填報名稱"
}
```

尚無任何正式推估時，`official-estimates/current.json` 可不存在；保存流程將它視為邏輯上的 `revision = 0`、`current_version_id = null`，並在取得鎖及再次確認仍不存在後，以原子方式建立第一份指標。

## 7. `COMMITTED.json` 與完整版本判定

每個版本的 `COMMITTED.json` 必須最後建立，至少記錄：

```json
{
  "schema": "liyutan-reservoir-estimator/committed",
  "schema_version": 1,
  "version_id": "<version_id>",
  "committed_at": "2026-08-14T02:15:34Z",
  "manifest_file": "manifest.json",
  "manifest_sha256": "..."
}
```

年度資料可將 `manifest_file` 指向 `version.json`。系統只有在下列條件全部成立時，才可把目錄視為完整版本：

1. 目錄位於對應的 `versions` 下，不在 `staging` 或 `quarantine`。
2. `COMMITTED.json` 存在且可解析。
3. `version_id` 與目錄名、manifest 一致。
4. manifest schema 受目前程式支援。
5. manifest checksum 正確。
6. manifest 所列必要檔案全部存在，且每個 SHA-256 checksum 正確。
7. 結構、欄位、型別、單位、日期與 36 旬完整性驗證全部通過。

任何一項失敗都必須顯示資料損壞或不相容，不得略過錯誤後當成正式版本使用。

## 8. 正式推估版本生命週期

1. **載入基準**：記錄目前 `official-estimates/current.json` 的 `revision` 與 `current_version_id`。
2. **完成演算與硬性檢查**：先固定要正式保存的情境 ID 清單，再確認清單中的每一個情境都有成功結果，且全部結果綁定的設定指紋都與保存當下的設定指紋一致。任一情境失敗、缺少結果或指紋失效時，立即阻止整批正式保存；不得建立部分成功版本、不得進入暫存發布，也不得切換 current。失敗資訊只可留在工作階段、非正式試算或「保存遭阻止」audit event。
3. **填寫與確認**：操作人及備註必填；畫面顯示年度資料版本、程式版本、上一正式版本、要正式保存的完整情境清單與成功摘要、設定指紋，以及「人工填報身分未驗證」提示，使用者再次確認。確認後若情境集合或任何設定改變，必須回到步驟 2 重新驗證與演算。
4. **建立暫存**：在同一共享根目錄的 `staging` 建立唯一目錄，寫入完整內容。
5. **驗證暫存**：重新讀回、驗證 schema、筆數、日期、參照關係及 checksum。
6. **取得短時間鎖**：取得 `official-current.lock`，只包住最後重讀、發布與指標切換。
7. **衝突檢查**：重新讀取 current；若 revision 或上一版本與步驟 1 不同，停止保存並提示另一位使用者已先儲存。不得自動覆蓋或自動改接新上一版本。
8. **發布版本**：在同一共享磁碟內把已驗證暫存目錄改名至最終 `versions\<version_id>`；`COMMITTED.json` 必須已最後寫入。
9. **切換目前版本**：以暫存檔＋原子取代方式寫入 `current.json`，revision 加一，並讀回確認。
10. **操作紀錄**：新增一個 audit event，記錄版本 ID、前版 ID、revision、操作人、備註、程式版本與結果。
11. **釋放鎖並回報**：顯示成功版本 ID。若 current 切換失敗，必須顯示「完整版本已建立但尚未設為目前版本」，留待復原，不得謊報成功。

正式版本不得進入「編輯」狀態。更正任何輸入、備註或結果都要建立新版本，並以 `previous_official_version_id` 串接。

## 9. 年度資料版本生命週期

1. 使用者以 Excel 或固定欄位資料上傳至本機工作階段。
2. 程式解析成正式 JSON／CSV 候選資料，不把 Excel 本身當權威資料。
3. 執行欄名、單位、數值、36 旬、Q5～Q95、適用年度、來源、操作人及備註驗證。
4. 顯示候選版本與目前啟用版本的差異。
5. 使用者人工確認後，依暫存、checksum、`COMMITTED.json` 與原子發布流程新增年度版本。
6. 新增版本不代表自動啟用；啟用前再次顯示版本 ID、差異、操作人及備註。
7. 取得 `annual-current.lock`，重新核對 `annual-data/current.json` revision；有衝突就停止並要求重新載入。
8. 以 revision 加一的方式切換 `annual-data/current.json`，並新增 audit event。
9. 切換後的新工作區使用新版本；已開啟的工作區必須顯示資料版本已變更，要求使用者選擇重新載入。不得在背景靜默換掉其演算基準。

切回舊版本使用相同的啟用流程。舊版本內容不改名、不搬移、不覆蓋。

## 10. 初始化與跨裝置接續

### 10.1 啟動初始化

桌面捷徑啟動本機 Streamlit 後，程式依序：

1. 確認正式根目錄可讀，並讀取 `system.json`。
2. 驗證這是鯉魚潭資料根目錄與受支援的 schema。
3. 讀取並驗證目前啟用年度資料版本。
4. 讀取並驗證最近一次正式推估版本；沒有正式版本時顯示「尚無正式推估」。
5. 在畫面固定顯示資料來源狀態、年度版本 ID、最近正式推估版本 ID 與讀取時間。
6. 提供「沿用最近正式推估」與「建立全新批次」兩個明確選項，不自動覆蓋使用者已開始的工作區。

### 10.2 沿用最近正式推估

- 以實際 `年＋月＋旬別` 鍵比對，不依列號平移。
- 日期延長後，重疊旬沿用最近正式推估中的既有入流與出流資料。
- 新增旬的出流使用啟動時已確認的目前啟用年度資料。
- 新增旬的入流原則上保持「待填」，不得用 0、Q50 或其他值靜默補入。
- 只有情境資料中有明確機器可讀標記，例如 `extension_default.type = annual_quantile` 且 `quantile = Q90`，新增旬才自動套用當前年度資料版本的 Q90。
- 不得只因情境名稱含「專業評估」或「Q90」就推測預設規則。
- 推估起始日改變時，起始庫容必須重新確認；未確認前不得正式演算或保存。
- 日期、年度資料版本、入流、出流、歷史庫容、起始庫容、參數或覆寫改變後，舊演算結果立即失效並須重新演算。
- 沿用動作建立新的工作批次 ID；不得把最近正式版本直接變成可修改的原版本。

### 10.3 建立全新批次

- 使用目前啟用年度資料初始化年度出流與水庫參數。
- 入流依使用者選擇的範本建立；未明確套用的旬維持待填。
- 正式保存前仍須完成全部驗證、重新演算、填寫操作人及備註並再次確認。

## 11. 權限與身分說明

- 能存取公司內網共享資料夾的人都可進行非正式試算。
- 是否能建立正式版本，第一版依 Windows／共享資料夾 ACL 決定；應用程式不另設管理密碼。
- 儲存正式推估時，操作人與備註必填並須再次確認。
- 建立或啟用年度資料版本時，同樣必須填寫操作人、來源及備註並人工確認。
- 操作人是人工宣告身分，不是經登入驗證的帳號。介面、manifest 與操作文件不得宣稱它能證明實際執行者。
- 未來若需要可驗證身分、角色分工或電子簽核，必須另立安全規格，不得把第一版人工欄位包裝成驗證機制。

## 12. Windows 共享資料夾寫入安全

### 12.1 短時間寫入鎖

後續實作應使用 Windows／SMB 可辨識的作業系統層排他檔案鎖，持有開啟的檔案 handle 並禁止其他程序共享寫入；不得只用「看到 `.lock` 檔就算鎖住」的存在判斷。

- 年度啟用指標與正式推估指標使用不同鎖，避免互不相關的寫入互相阻塞。
- 鎖只涵蓋重新讀取 revision、發布已驗證版本、取代 current 與必要的 audit 建立；資料準備與大檔寫入在取得鎖前完成。
- 建議每 200～500 ms 加入隨機抖動重試，總等待 15 秒；逾時後顯示目前有人正在寫入並提供重試，不得改成本機保存。
- 鎖內容可記錄隨機 token、電腦名稱、程序 ID、開始時間及操作類別供診斷，但這些資訊不作身分驗證。
- 程序中斷或網路斷線時，由作業系統關閉 handle 並釋放排他鎖。持久的 lock 檔內容本身不代表仍被鎖定，也不得僅依用戶端時鐘直接刪除所謂過期鎖。
- 實作前必須在公司實際檔案伺服器驗證 SMB 排他鎖、同目錄 rename 與 replace 行為；若不符合假設，必須採用由資訊單位核准的集中鎖服務或等效機制後再開放正式寫入。

### 12.2 暫存、驗證與原子發布

- 暫存目錄必須位於同一個鯉魚潭共享根目錄，避免跨磁碟移動失去原子性。
- 每個檔案寫完後必須 flush、關閉、重新讀取並計算 checksum。
- `COMMITTED.json` 最後寫入；沒有它的目錄永遠不是完整版本。
- 暫存目錄驗證通過後，才可在同一共享磁碟內 rename 成最終版本目錄。
- `current.json` 必須先寫成同目錄唯一暫存檔，flush 並驗證後再以原子 replace 取代。
- 最終版本目錄若已存在，視為 ID 衝突並停止；不得合併或覆蓋內容。

### 12.3 Revision 衝突

使用者開始保存時記住 `observed_revision`。取得鎖後必須重讀 current：

```text
若 current.revision != observed_revision：
    停止，不發布為目前版本
    顯示對方已建立的新版本與時間
    要求重新載入、比較後再另存新版本
否則：
    發布新版本
    current.revision = observed_revision + 1
```

不能採最後寫入者勝出，也不能靜默把 `previous_version_id` 改成剛出現的版本後繼續保存。

## 13. 中斷與復原

| 中斷位置 | 系統狀態 | 復原行為 |
| --- | --- | --- |
| 暫存尚未寫完 | `staging` 中沒有有效 `COMMITTED.json` | 不讀取為正式資料；下次啟動列為待清理／診斷 |
| 暫存驗證失敗 | 不完整或 checksum 錯誤 | 移至 `quarantine` 或保留原位並標記；禁止發布 |
| 已發布版本、current 尚未切換 | 完整但未被 current 指向的版本 | 顯示待復原版本；不得自動啟用，由使用者核對後透過正式啟用流程處理 |
| current 暫存檔寫完、replace 前中斷 | 舊 current 仍有效 | 忽略未完成暫存檔並重試 |
| current replace 後、audit 前中斷 | 新 current 有效，操作紀錄可能缺漏 | 啟動檢查依 current 與版本 manifest 補建「復原事件」，不得回寫版本內容 |
| current 指向不存在或驗證失敗版本 | 正式來源不一致 | 阻止正式作業，顯示資料損壞；由指定維護人員依操作程序修復，不自動猜測其他版本 |
| 網路於任何正式寫入途中中斷 | 結果未知 | 回報「未確認成功」；重新連線後先讀 current 與版本目錄確認，不可直接重送相同操作並假設安全 |

復原工具只能建立新 audit event、切換 current 指標或隔離不完整內容，不得改寫已提交版本。`staging`、`quarantine` 與未被指向的完整版本都應有可檢視的診斷清單。

## 14. 操作紀錄

為避免多台電腦同時 append 同一檔案，操作紀錄採「一事件一個 JSON 檔」，檔名包含 UTC 時間與 UUID。至少記錄：

- event schema 與版本。
- event ID、事件類型及時間。
- 相關年度資料或正式推估版本 ID。
- current 切換前後的 revision 與 version ID。
- 人工填報操作人與備註。
- 程式版本與電腦識別資訊；電腦識別只供診斷。
- 成功、衝突、復原或錯誤結果。

事件檔也必須用暫存＋原子 rename 建立；既有事件不得覆蓋或刪除。無法寫入必要 audit event 時，正式操作不得回報完整成功；若 current 已切換，依前節進入復原流程。

## 15. `U:` 讀取或驗證失敗

若 `U:` 無法讀取、根目錄不存在、權限不足、schema 不支援、current 無法解析、版本缺檔或 checksum 錯誤：

- 畫面必須明確顯示錯誤原因與「正式資料來源不可用」狀態。
- 顯示目前使用的是內建資料、其版本標示，以及「僅供非正式試算」。
- 使用者可明確選擇使用內建資料進行非正式試算。
- 禁止儲存正式推估。
- 禁止建立或啟用年度資料版本。
- 不得無提示地改存本機、其他磁碟、Google Sheet 或舊快取。
- 不得把上次成功讀取的記憶體／本機快取標示成目前正式資料。
- 已開啟的工作區若之後失去 `U:` 連線，可以繼續非正式查看或試算，但正式保存按鈕必須停用；重新連線後須重新讀取 current、核對 revision 與版本，不能沿用斷線前的保存前提。

若 `U:` 可讀但不可寫，可使用正式資料進行試算，但保存正式推估與更新年度資料仍須停用並清楚標示唯讀狀態。

## 16. 保存期限

- 正式推估版本：長期保留，不由應用程式刪除。
- 年度資料版本及其正式來源附件：長期保留，不由應用程式刪除。
- 操作紀錄：長期保留，不由應用程式刪除。
- 臨時試算、重複備份、可重新產生的 Excel／圖表／匯出檔及中斷暫存：自建立日起至少保留一年後，才具備人工清理資格。
- 「一年後可清理」不代表必須刪除；清理前仍須確認不是正式版本、不是唯一來源、沒有被 current 或 manifest 參照，並留下清理操作紀錄。
- 本階段只定義規則，不實作排程、自動刪除或批次清理。

組織層級的檔案伺服器備份、快照與災難復原政策由資訊單位另行確認；它們不取代本規格的 append-only 與 checksum。

## 17. Excel 公版角色

- Excel 是人工上傳、檢查及下載的交換格式，不是程式正式永久保存格式。
- JSON、CSV、manifest、版本指標及 checksum 才是程式正式保存格式。
- 不含正式公司資料的空白 Excel 範本可放 GitHub。
- 真正的年度資料、來源附件及正式推估成果只放公司內網共享資料夾，不提交 GitHub。
- 未來公版 Excel 必須有範本版本欄位、固定工作表／欄名、明確單位、36 旬完整性驗證、重複旬與缺漏提示、非數字／負值／未知欄位錯誤提示。
- 上傳後必須先顯示解析與差異預覽，再由使用者確認轉成新的正式 JSON／CSV 版本；不得直接覆蓋啟用版本。
- 下載的 Excel 是正式 JSON／CSV 資料或演算結果的衍生產品，必須標示來源版本 ID，但不得反過來成為權威資料。
- 本 PR 不建立任何 Excel 檔。

## 18. 後續開發階段與獨立驗收條件

### 2-2：永久資料 schema 與純邏輯驗證

狀態：已完成（2026-09-02）。實作位於 `shared_storage_schema.py`，合成 fixture 與自動化測試位於 `tests/test_shared_storage_schema.py`。本階段只驗證記憶體中的 JSON／CSV bytes 與版本包，不讀寫或連接公司內網共享資料夾；共享資料唯讀啟動仍屬 2-3。

範圍：新增不依賴 Streamlit 的 JSON／CSV schema、序列化、checksum、版本完整性與 36 旬驗證函式；把 33 cms 納入參數資料結構，但不先改公式。

驗收：

- 合法年度與正式推估 fixture 可 round-trip。
- 缺欄、重複旬、錯誤單位、NaN、負值、checksum 錯誤、未知 schema 都會被拒絕。
- 現有水量平衡測試結果不變。
- 不連線或寫入正式 `U:`。

### 2-3：共享資料唯讀啟動與失敗降級

範圍：讀取 `system.json`、current 與完整版本；顯示資料來源狀態；提供明確的內建資料非正式模式。

驗收：

- 可從測試共享目錄讀取啟用年度資料與最近正式推估。
- `U:` 不存在、唯讀、schema 錯誤、缺檔及 checksum 錯誤均有明確訊息。
- 失敗時正式保存與年度更新不可用，且不會讀取舊快取或偷偷改存本機。

### 2-4：年度資料新增版本與啟用

範圍：Excel 解析預覽、正式 JSON／CSV 產生、版本發布、年度 current revision、鎖定、衝突與 audit。

驗收：

- 新版本不覆蓋舊版；未人工確認不會啟用。
- 兩個程序同時啟用時只有一方成功，另一方收到 revision 衝突。
- 中斷注入測試不會產生被誤認為完整版本的資料。
- 33 cms 可由年度參數版本讀取；改參數後的公式相容性另以既有案例驗證。

### 2-5：正式推估保存

範圍：保存 inputs、所有正式情境的成功摘要與完整逐日結果、設定指紋、manifest、checksum、上一正式版本、程式版本、人工操作人與備註。

驗收：

- 必填欄位、二次確認與未驗證身分提示完整。
- 所有要正式保存的情境都成功且設定指紋有效時才可保存；任一正式情境失敗、缺結果或指紋失效，都會阻止整批保存且不產生部分正式版本。
- 失敗資訊只留在工作階段、非正式試算或操作紀錄，不會出現在目前正式推估版本內容。
- 正式目錄 append-only，舊版本沒有被修改。
- 同時保存不會靜默覆蓋；revision 衝突可重現。
- 斷線與各寫入步驟中斷後，current 仍指向完整版本或明確進入可診斷復原狀態。

### 2-6：跨裝置載入與接續

範圍：載入最近正式推估、沿用或全新批次、日期延長、Q90 明確預設規則、結果失效。

驗收：

- 不同電腦讀到相同 current 與年度版本。
- 重疊旬依年月旬鍵保留；新增出流取自啟用年度資料。
- 新增入流預設待填，只有明確 Q90 規則才自動補值。
- 起始日改變會要求重新確認起始庫容。
- 任何日期或資料變更後舊結果不可保存為正式版本，重新演算後才恢復。

### 2-7：桌面捷徑與一般使用者啟動

範圍：提供受控啟動器／桌面捷徑、版本顯示與 GitHub 發布更新流程；一般使用者不接觸 PowerShell 或 Git。

現階段本機 repository 與 GitHub 不會自動雙向同步。本機分支必須執行 push，commit 才會出現在 GitHub；GitHub Pull Request 合併後，本機 `main` 仍必須執行 fetch／pull 才會更新。供一般使用者使用的一鍵更新屬本 2-7 階段後續功能，尚未實作。

驗收：

- 新公司電腦依操作文件安裝後，可由桌面捷徑啟動本機 Streamlit。
- 畫面可辨識程式版本與 Git commit。
- 更新失敗不破壞目前可用版本，且不影響 `U:` 正式資料。
- repository 與正式資料仍分離。
- 一鍵更新能清楚顯示本機版本、遠端可用版本、更新結果及需要人工處理的錯誤，不把尚未實作的 Git 同步描述成現有能力。

### 2-8：多人、中斷、復原與營運驗收

範圍：在公司實際 SMB 環境進行雙機競爭、斷線、權限、備份還原與維運操作驗收；建立人工清理與復原操作手冊，不實作自動刪除。

驗收：

- Windows／SMB 排他鎖、同目錄 rename、replace 與逾時行為經雙機測試。
- 任何中斷都不會讓半成品成為 current。
- 可辨識並處理未指向完整版本、失敗暫存與 audit 補建。
- 資訊單位確認 ACL、備份、容量監控與災難復原責任。
- 業務人員完成跨裝置接續、年度更新與正式保存人工驗收。

## 19. 本文件 PR 的驗收界線

- 只新增或修改 Markdown 文件。
- 不修改 `app.py`、`v2_workflow.py`、`tests/`、requirements 或核心水量平衡公式。
- 不建立 Excel。
- 不建立、修改或刪除任何 `U:` 檔案或資料夾。
- README 與既有 V2 文件保留第一階段歷史脈絡，但現行第二階段方向均指向本規格，不再把 Google Sheet 描述為目前採用方案。

## 20. 實作前仍需人工確認

下列事項不影響本規格架構，但須在對應開發階段前由業務或資訊單位確認：

- 年度資料各欄位的正式資料來源、統計期間、來源文件索引與合理值範圍。
- 滿庫容量、生態流量及 33 cms 引水上限的業務依據與適用期間。
- 哪些水利署或內部情境允許帶有機器可讀的「延長時自動套用 Q90」標記。
- 人工填報操作人的統一格式，以及是否日後導入可驗證的公司帳號身分。
- 公司共享資料夾 ACL：誰可讀、誰可正式寫入、誰可執行復原與人工清理。
- 公司檔案伺服器的 SMB 鎖、rename／replace 相容性，以及 15 秒鎖逾時是否需依實測調整。
- 正式發布版本的命名／標記方式，以及是否禁止 dirty working tree 的程式保存正式推估。
- 檔案伺服器備份、快照、離線備援、容量監控、復原演練與事件通報責任。
- 一年期非正式資料清理的核准人、清理頻率與稽核紀錄格式。
