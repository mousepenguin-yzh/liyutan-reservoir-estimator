# Repository Agent Guide

本文件是 coding agent 的短入口與導航。
它不是完整規格百科，也不保存 current milestone 或完整 phase 歷史。

## Repository purpose

- 本 repository 是鯉魚潭水庫庫容情境推估系統。
- 應用程式以 Streamlit 提供多情境入流、共用出流、逐日演算、成果比較與正式資料流程。
- 系統是人工條件下的決策輔助工具，不是即時水文預報或正式調度決策本身。
- 程式碼與正式業務資料必須分離。

## Safety boundaries

- 正式 shared root 的定義以 `docs/LOCAL_SHARED_STORAGE_SPEC.md` 為準。
- 開發、診斷與自動測試不得存取正式 shared root，包括讀取、列舉、建立、修改、重新命名或刪除其內容。
- 不要把正式 `U:` 設為測試或開發用的 `LIYUTAN_SHARED_ROOT`。
- 自動測試只能使用 pytest `tmp_path`、synthetic data、injected dependencies 或 fake dependencies。
- Windows／SMB 行為的測試也應先以 injected/fake lock、clock、filesystem failure 或 fault injection 驗證。
- 只有使用者明確要求的人工 acceptance，才可使用其指定的 test root。
- 使用 test root 前，先再次確認它不是正式 shared root。
- acceptance 若建立 synthetic 或 temp environment，結束後必須確認是否需要清理。
- 若清理，應先核對目標與正式資料完全分離；回報清理結果。
- 不提交真實業務資料、使用者 Excel、正式 shared-storage artifacts、憑證或 secrets。
- 不自行 merge Pull Request。
- 未經明確要求，不執行正式寫入、recovery、repair、orphan 處理或共享資料清理。

## Source-of-truth routing

- 現在做到哪、下一個 milestone 與尚未完成項目：`docs/PROJECT_STATUS.md`。
- Phase 2 shared-storage 與 formal data contract：`docs/LOCAL_SHARED_STORAGE_SPEC.md`。
- V2 multi-scenario business rules：`docs/V2_MULTI_SCENARIO_SPEC.md`。
- 已完成 acceptance evidence：`docs/PHASE_*_ACCEPTANCE.md`。
- 開發、測試、PR 與 completion report 規則：`docs/DEVELOPMENT_WORKFLOW.md`。
- 一般使用者入口與啟動方式：`README.md`。
- 不要在 README、AGENTS 或新文件複製完整 current-status list。
- 不要建立第二份 schema、lineage、verification 或 milestone source of truth。

## Code map

- `app.py`：Streamlit orchestration 與 UI；檔案很大，不要預設整份閱讀。
- 修改功能前，先用 `rg` 找到對應 UI 區塊、session key、helper 與測試。
- `v2_workflow.py`：V2 batch、scenario、validation、fingerprint 與批次演算 domain logic。
- `official_estimate_candidate.py`：正式推估 candidate 與 bundle 建立。
- `official_estimate_workflow.py`：正式保存資格與 workflow helpers。
- `official_estimate_publisher.py`：正式推估安全發布與 current/audit 寫入。
- `official_estimate_loader.py`：正式版本唯讀載入與 publication history。
- `official_estimate_continuation.py`：正式 snapshot 轉成新的 working batch。
- `official_estimate_date_adjustment.py`：接續工作日期、旬資料與 Q80/Q90 domain rules。
- `official_estimate_continuation_ui.py`：接續工作 view model 與 session adapter。
- `annual_data_excel.py`：年度 Excel parsing、validation 與 candidate。
- `annual_data_version_writer.py`：年度不可變版本建立。
- `annual_data_activation.py`：年度 current 安全啟用。
- `annual_data_diagnostics.py`：年度資料唯讀 inventory 與診斷。
- `annual_data_recovery.py`、`annual_data_current_repair.py`：受控 recovery／repair。
- `annual_data_maintenance.py`、`annual_data_preview_ui.py`：年度維護支援與 UI。
- `shared_storage_schema.py`：正式 JSON／CSV schema、serialization、checksum 與 validators。
- `shared_storage_reader.py`：shared root 與年度資料的安全唯讀入口。
- `ten_day_period.py`：日期、年度旬鍵與 working 旬鍵的 mapping source of truth。
- `software_provenance.py`：Git commit 與 source-tree provenance。
- `tests/`：依 module 分組的 unit、filesystem、fault-injection 與 Streamlit AppTest。
- 測試時先找與修改 module 對應的 `tests/test_<module>.py`。
- `tests/test_app_shared_storage_integration.py` 與 `tests/test_app_official_continuation.py` 是主要 AppTest/integration 入口。

## Working principles

- 先找既有 pattern、validator、serializer、error model 與 test，再新增 abstraction。
- domain rule 優先放在可獨立測試的 domain module。
- 不把新的 business、schema、lineage 或 persistence logic 直接堆入 `app.py`。
- `app.py` 主要負責 orchestration、顯示、使用者確認與 session adapter 呼叫。
- 正式 schema 驗證應重用既有 validator，不在 loader、publisher 或 UI 複製規則。
- 日期與旬別 mapping 重用 `ten_day_period.py`，不要用列號或自行猜測月份旬別。
- `derived_from` 是工作來源 lineage；publication `previous` 是正式發布順序，兩者不可混用。
- historical annual 不得因 current 已更新而 silent rebase。
- formal shared data 與 session-only/nonofficial data 必須保持清楚分離。
- 正式版本 append-only；不要改寫、合併或刪除既有 version contents。
- current pointer、revision、audit 與 immutable bundle 必須維持既有契約。
- 避免以檔名、mtime、顯示名稱或 list order 推測正式 identity 或 history。
- 任何可能失敗的 transformation，先驗證完整輸入，再原子更新 session 或 filesystem state。
- 小任務維持小而連貫的 scope，不順手實作下一 phase。
- 不因重構方便而改變 business behavior、schema 或正式保存語意。
- 修改文件時，連到既有 source of truth，不複製會快速過期的內容。
- 發現文件與程式不一致時，先判斷哪個是權威契約並清楚回報。
- 工作樹可能有使用者變更；保留無關修改，不使用破壞性 reset 或 checkout。

## Verification

- verification tiers、manual acceptance 邊界與 PR 流程見 `docs/DEVELOPMENT_WORKFLOW.md`。
- quick repository verification：`python scripts/verify_repo.py --quick`。
- full repository verification：`python scripts/verify_repo.py --full`。
- focused pytest 直接執行對應 test file 或 test node。
- 不要因每個小修改重跑 entire pytest suite。
- full verification 應依變更風險與 workflow tier 執行，不以直覺無限重複。
- 若測試會建立 temp/synthetic environment，記錄位置與 cleanup 狀態。
- 不在本文件重複長篇 testing SOP。

## PR and completion boundaries

- 從最新 `main` 建立小而連貫的 branch。
- 實作與測試應對應同一 scope；避免把未要求的 phase 混入同一 PR。
- push 並開 PR 後等待 CI；若失敗，先讀取實際 failure 再修正。
- review 與 merge 由使用者／reviewer 決定；agent 不自行 merge。
- 最後依 `docs/DEVELOPMENT_WORKFLOW.md` 回報 PR、SHA、scope、verification、CI、manual acceptance、cleanup 與 working tree。
