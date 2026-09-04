"""Streamlit presentation for stage 2-4B annual-data validation previews."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from annual_data_excel import PREVIEW_NOTICE, compare_annual_data, parse_annual_data_excel
from shared_storage_reader import StorageErrorCode


def render_annual_data_maintenance(result, *, shared_mode_enabled: bool) -> None:
    """Render the isolated 2-4B preview without mutating estimation workspace data."""
    with st.expander("🧾 系統基準資料維護－Excel驗證與差異預覽", expanded=False):
        st.subheader("系統基準資料維護－Excel驗證與差異預覽")
        st.info(
            "這個功能只在初次建立或日後更新系統基準資料時使用；"
            "一般每旬推估不需要重新填寫或上傳年度 Excel。"
        )
        st.markdown(
            "系統基準資料是所有新推估共用的預設基礎；單次推估的自訂入流、出流、"
            "抗旱調度與臨時參數只屬於該次推估。"
        )
        st.caption("系統基準資料＋本次推估調整＋計算結果＝正式推估版本")
        uploaded = st.file_uploader(
            "手動上傳已填寫的 2-4A.1 年度基準資料 Excel",
            type=["xlsx"],
            key="annual_data_excel_preview_upload",
            help="系統不會自動掃描或載入公司共享資料夾中的 Excel。",
        )
        if uploaded is None:
            st.caption("尚未上傳檔案。此區不會改變目前推估工作區，也不會建立正式版本。")
            st.button("建立版本（2-4C 尚未實作）", disabled=True, key="annual_create_disabled")
            st.button("啟用版本（2-4C 尚未實作）", disabled=True, key="annual_activate_disabled")
            return

        parsed = parse_annual_data_excel(uploaded.getvalue(), filename=uploaded.name)
        file_columns = st.columns(2)
        file_columns[0].metric("上傳檔名", uploaded.name)
        file_columns[1].metric("原始檔案 SHA-256", parsed.source_sha256 or "無法計算")
        if parsed.errors:
            st.error("Excel 驗證失敗；未建立候選資料，請依下列位置人工修正原檔。")
            st.dataframe(
                pd.DataFrame(
                    [
                        {
                            "嚴重度": issue.severity.value,
                            "代碼": issue.code,
                            "位置": issue.location,
                            "說明": issue.message,
                        }
                        for issue in parsed.issues
                    ]
                ),
                hide_index=True,
                width="stretch",
            )
            st.warning(PREVIEW_NOTICE)
            st.button("建立版本（驗證未通過）", disabled=True, key="annual_create_invalid")
            st.button("啟用版本（2-4C 尚未實作）", disabled=True, key="annual_activate_invalid")
            return

        candidate = parsed.candidate
        st.success("Excel 結構與完整內容驗證成功，已建立記憶體中的標準候選資料。")
        st.warning(PREVIEW_NOTICE)
        summary_columns = st.columns(4)
        summary_columns[0].metric("適用年度", str(candidate.applicable_year))
        summary_columns[1].metric("實績截止旬", candidate.actual_data_cutoff_period)
        summary_columns[2].metric("水文／出流旬數", "36／36")
        summary_columns[3].metric("Q欄／參數數", "19／4")
        st.caption(f"候選內容 fingerprint：{candidate.fingerprint}")
        st.markdown(
            f"年度基準出流來源分界：**{candidate.actual_data_cutoff_period} 以前（含該旬）**"
            "使用本年度實際資料；其後使用前一年度相同旬別資料。"
        )
        st.markdown(
            f"- 水文Q值資料來源／統計期間：{candidate.hydrology_source_period}\n"
            f"- 年度基準出流資料來源：{candidate.annual_outflow_source}\n"
            f"- 整體備註：{candidate.overall_note or '未填寫'}"
        )
        if parsed.warnings:
            st.warning(f"驗證完成，但有 {len(parsed.warnings)} 項警告；請於正式發布前確認。")
            st.dataframe(
                pd.DataFrame(
                    [
                        {"代碼": issue.code, "位置": issue.location, "說明": issue.message}
                        for issue in parsed.warnings
                    ]
                ),
                hide_index=True,
                width="stretch",
            )
        else:
            st.caption("warnings：0 項")

        baseline = None
        baseline_damaged = False
        if shared_mode_enabled and result is not None:
            if result.ok:
                baseline = result.annual
            elif result.error.code not in {
                StorageErrorCode.ROOT_NOT_FOUND,
                StorageErrorCode.SYSTEM_MISSING,
                StorageErrorCode.ANNUAL_CURRENT_MISSING,
            }:
                baseline_damaged = True
                st.error(
                    "目前啟用的年度基準資料讀取失敗，可能為資料損壞或版本不一致；"
                    f"無法產生可靠差異（{result.error.code.value}）：{result.error.message}"
                )
        elif not shared_mode_enabled:
            st.info(
                "共享模式未啟用，因此本區未讀取任何共享路徑；"
                "下方以第一個候選版本方式完整預覽，但不代表已確認正式環境沒有舊版。"
            )

        if not baseline_damaged:
            difference = compare_annual_data(candidate, baseline)
            if difference.is_first_version:
                st.info("這是第一個候選系統基準版本，目前沒有舊版可比較。")
            else:
                st.subheader(f"與目前啟用年度版本的差異：共 {difference.total_changes} 項")
            difference_columns = st.columns(4)
            for column, section in zip(
                difference_columns,
                ("基本資訊", "水文Q值", "年度基準出流", "水庫參數"),
            ):
                column.metric(
                    section,
                    f"{difference.section_changes[section]} / {difference.section_totals[section]} 變更",
                )
            show_all = st.checkbox(
                "顯示完整資料（取消勾選時只顯示有變動項目）",
                value=difference.is_first_version,
                key="annual_preview_show_all",
            )
            for section in ("基本資訊", "水文Q值", "年度基準出流", "水庫參數"):
                with st.expander(f"{section}明細", expanded=section == "基本資訊"):
                    rows = difference.rows(section, changed_only=not show_all)
                    if rows:
                        st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
                    else:
                        st.caption("此區沒有變動。")
            with st.expander("水庫參數補充 metadata（適用起日、來源與備註）"):
                st.dataframe(
                    pd.DataFrame(
                        [
                            {"參數代碼": code, **metadata}
                            for code, metadata in candidate.parameter_metadata.items()
                        ]
                    ),
                    hide_index=True,
                    width="stretch",
                )

        st.button("建立版本（2-4C 尚未實作）", disabled=True, key="annual_create_valid")
        st.button("啟用版本（2-4C 尚未實作）", disabled=True, key="annual_activate_valid")
