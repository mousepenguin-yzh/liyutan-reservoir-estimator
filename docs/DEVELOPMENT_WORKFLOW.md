# 開發與驗證流程

本文件是人類維護者與 Astra／Codex 共用的開發、verification、PR 與完成回報規則。業務契約仍以對應 spec 與既有 tests 為準。

## 1. Suggested reasoning effort

這是 human orchestration guidance，不要求 agent 自行切換模型。

| 強度 | 適用工作 |
| --- | --- |
| 輕度 | docs、文案、fixture cleanup、很局部且明確的小修 |
| 中度 | 一般功能、既有架構內的 UI／session integration、多檔修改 |
| 高 | schema、lineage、資料 migration、formal-write contract、跨模組 domain change |
| 極高 | concurrency、lock、recovery、race condition、重大架構問題 |
| Ultra | 只有多次高強度仍無法定位，或非常複雜的全局問題 |

強度依實際風險決定，不依修改行數或 phase 名稱決定。若小改動碰到 formal persistence boundary，仍應提高檢查強度。

## 2. Verification tiers

### Tier 0 — docs / cleanup only

- 不跑 pytest。
- 執行 quick verification 即可。
- Pull Request CI 最後仍會 full verify。
- 適用於不改 Python behavior、schema、workflow 或 test expectation 的純文件與清理。

### Tier 1 — localized low-risk code change

- 開發時只跑 affected focused tests。
- 執行 quick verification。
- 不需要反覆 full pytest。
- push 後由 CI 執行 full verification。

### Tier 2 — normal feature / integration

- 開發中只跑 focused unit／integration／AppTest。
- 功能穩定後，full verification 最多一次。
- push 後 CI 再執行 full verification。
- 若 full verification 失敗，修正後只重跑受影響測試；準備再次 push 前再依風險決定是否重跑 full。

### Tier 3 — high-risk persistence / schema / formal writes / recovery

- 先跑 focused contract tests。
- 加入或執行 failure、fault-injection、conflict 與 recovery tests。
- 完成後執行 final full verification。
- 若存在自動化無法代表的 environment risk，再安排最小必要 manual acceptance。
- 正式 `U:` 不屬於一般開發或自動測試環境。

**不要因每個小修改重跑 entire pytest suite。**

Repository 共用入口：

```bash
python scripts/verify_repo.py --quick
python scripts/verify_repo.py --full
```

`--quick` 只驗證 Git tracked Python files 可編譯，並執行 `git diff --check`；`--full` 先做 quick，再執行 `pytest -q`。focused pytest 仍直接指定 test file 或 test node，不在 entry point 增加 focused mode。

## 3. Automated vs manual acceptance

原則：能可靠自動化，就不要要求使用者再人工逐步重測。

優先順序：

1. domain／unit tests。
2. synthetic filesystem tests。
3. Streamlit AppTest／integration tests。
4. automation 無法代表真實風險時，才做 manual acceptance。

通常不需要人工驗收：

- business rules 與 validators。
- lineage transformations。
- 日期、Q 值、capacity 與 session state logic。
- schema、serialization、checksum 與 deterministic error handling。
- Streamlit button enabled／disabled 與 session transitions，只要 AppTest 可可靠驗證。

仍需要人工／實機驗收：

- 真實 Windows／SMB behavior。
- 兩台實體電腦 concurrency。
- OS file locking 與實際 lock pressure。
- network disconnect／reconnect。
- 真實 ACL、磁碟權限與公司環境設定。
- human visual／usability judgment。
- 高風險 recovery 操作中 synthetic tests 無法代表的環境行為。

人工驗收 checklist 應維持「最小充分」。已被 automated regression tests 可靠覆蓋的項目，不要再要求使用者重複 A～J 全流程。

若 manual acceptance 建立 synthetic／temp environment，開始前記錄位置並確認不是正式 root；結束後明確回報是否已清理、保留，以及原因。

## 4. PR workflow

1. 確認 working tree，從 latest `main` 建立 branch。
2. 保持 small coherent scope，不順手實作下一 phase。
3. 依既有 architecture、validator 與 tests 完成實作。
4. 依 verification tier 執行 focused、quick 或 full verification。
5. 檢查 diff、文件連結、scope 與 working tree。
6. commit、push 並開 Pull Request。
7. 等待 GitHub Actions full verification。
8. 處理 CI 或 review feedback，避免無關修改。
9. 只有 user／reviewer 決定後才 merge；agent 不自行 merge。
10. merge 後再同步本機 `main`。

PR 描述應說明 scope、風險邊界、驗證結果及 manual acceptance 是否必要。不要把未執行的人工驗收描述為通過。

## 5. Completion report

Agent 最後回報保持精簡，包含：

- PR URL 與 head SHA。
- changed files 與 scope。
- focused tests。
- quick／full verification 結果。
- GitHub Actions 狀態。
- manual acceptance 是否需要，以及原因。
- 是否建立 temp／synthetic environment；若有，是否需 cleanup。
- working tree 狀態。

若某項未執行，直接標示 not run／not required，並說明對應 verification tier；不要以模糊文字暗示已驗證。