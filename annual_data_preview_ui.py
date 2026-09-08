"""Streamlit annual-data preview, immutable publish, and activation workflow."""

from __future__ import annotations

import hashlib

import pandas as pd
import streamlit as st

from annual_data_diagnostics import (
    AnnualDataDiagnostics,
    CurrentAuditStatus,
    RecoverySeverity,
    VersionStatus,
)
from annual_data_activation import (
    AnnualDataActivationConflictError,
    AnnualDataActivationError,
    AnnualDataActivationRecoveryRequiredError,
    AnnualDataAlreadyCurrentError,
)
from annual_data_excel import PREVIEW_NOTICE, compare_annual_data, parse_annual_data_excel
from annual_data_maintenance import (
    AnnualDataMaintenanceService,
    AnnualDataWriteCapability,
    annual_data_write_capability,
)
from annual_data_version_writer import AnnualDataVersionPublishError
from shared_storage_reader import StorageErrorCode


PENDING_VERSION_KEY = "annual_pending_published_version"
RECOVERY_REQUIRED_KEY = "annual_activation_recovery_required"
ACTIVATION_RESULT_KEY = "annual_activation_result"


def render_annual_data_diagnostics(
    diagnostics: AnnualDataDiagnostics | None,
    *,
    shared_mode_enabled: bool,
) -> None:
    """Render read-only filesystem evidence without offering recovery actions."""
    if not shared_mode_enabled:
        return
    with st.expander("🩺 年度資料診斷與復原狀態", expanded=False):
        if diagnostics is None:
            st.error("年度 diagnostics 結果不可用；正常建立／啟用功能維持停止。")
            return

        severity = diagnostics.overall_severity
        if severity is RecoverySeverity.HEALTHY:
            st.success(f"年度資料診斷：healthy。{diagnostics.summary}")
        elif severity is RecoverySeverity.ATTENTION:
            st.warning(f"年度資料診斷：attention。{diagnostics.summary}")
        elif severity is RecoverySeverity.RECOVERY_REQUIRED:
            st.error(
                "正式年度資料需要復原處理；正常建立／啟用功能維持停止。"
            )
            st.warning(diagnostics.summary)
            st.info(
                "Recovery actions 尚未實作，將於 2-4C2b2b 提供人工確認流程。"
            )
        else:
            st.error(f"年度資料診斷：uninspectable。{diagnostics.summary}")

        current_columns = st.columns(4)
        current_columns[0].metric("current 狀態", diagnostics.current_status.value)
        current_columns[1].metric(
            "current version",
            diagnostics.current_version_id or "無",
        )
        current_columns[2].metric(
            "current revision",
            str(diagnostics.revision) if diagnostics.revision is not None else "無",
        )
        current_columns[3].metric(
            "current activation audit",
            diagnostics.current_audit_status.value,
        )
        if diagnostics.current_audit_status is CurrentAuditStatus.MISSING:
            st.error(
                "current 本身與版本完整，但找不到對應此次 current transition 的正式 "
                "audit event，需要 recovery 補建。"
            )
        elif diagnostics.current_audit_status is CurrentAuditStatus.AMBIGUOUS:
            st.error(
                "找到多個對應同一 revision transition 的 audit event，需要人工檢查。"
            )
        if diagnostics.current_failure_reason:
            st.caption(f"current diagnostics：{diagnostics.current_failure_reason}")

        counts = {
            status: sum(item.status is status for item in diagnostics.versions)
            for status in VersionStatus
        }
        version_columns = st.columns(4)
        version_columns[0].metric("current versions", counts[VersionStatus.CURRENT])
        version_columns[1].metric("historical versions", counts[VersionStatus.HISTORICAL])
        version_columns[2].metric("orphan versions", counts[VersionStatus.ORPHAN])
        version_columns[3].metric("invalid versions", counts[VersionStatus.INVALID])
        evidence_columns = st.columns(3)
        evidence_columns[0].metric("staging", len(diagnostics.staging))
        evidence_columns[1].metric("quarantine", len(diagnostics.quarantine))
        evidence_columns[2].metric("temp artifacts", len(diagnostics.temp_artifacts))
        st.caption(f"overall recovery severity：{severity.value}")

        if diagnostics.versions:
            st.markdown("**Annual versions inventory**")
            st.dataframe(
                pd.DataFrame(
                    [
                        {
                            "entry": item.entry_name,
                            "status": item.status.value,
                            "validation": "valid" if item.validation_ok else "invalid",
                            "modified_at": item.modified_at,
                            "failure_reason": item.failure_reason,
                        }
                        for item in diagnostics.versions
                    ]
                ),
                hide_index=True,
                width="stretch",
            )
        if diagnostics.staging:
            st.markdown("**Annual writer staging inventory（非正式版本）**")
            st.dataframe(
                pd.DataFrame(
                    [
                        {
                            "entry": item.entry_name,
                            "status": item.status.value,
                            "directory": item.is_directory,
                            "COMMITTED.json": item.has_committed,
                            "looks_complete": item.looks_complete,
                            "validation": "valid" if item.validation_ok else "invalid",
                            "modified_at": item.modified_at,
                            "failure_reason": item.failure_reason,
                        }
                        for item in diagnostics.staging
                    ]
                ),
                hide_index=True,
                width="stretch",
            )
        if diagnostics.quarantine:
            st.markdown("**Quarantine inventory（只讀 evidence）**")
            st.dataframe(
                pd.DataFrame(
                    [
                        {
                            "entry": item.entry_name,
                            "annual_data_evidence": item.is_annual_data_evidence,
                            "validation": item.validation_ok,
                            "modified_at": item.modified_at,
                            "failure_reason": item.failure_reason,
                        }
                        for item in diagnostics.quarantine
                    ]
                ),
                hide_index=True,
                width="stretch",
            )
        if diagnostics.audits:
            st.markdown("**Audit inventory**")
            st.dataframe(
                pd.DataFrame(
                    [
                        {
                            "path": str(item.path.relative_to(diagnostics.root)),
                            "status": item.status.value,
                            "modified_at": item.modified_at,
                            "failure_reason": item.failure_reason,
                        }
                        for item in diagnostics.audits
                    ]
                ),
                hide_index=True,
                width="stretch",
            )
        if diagnostics.temp_artifacts:
            st.markdown("**Residual temp artifacts（diagnostics evidence only）**")
            st.dataframe(
                pd.DataFrame(
                    [
                        {
                            "path": str(item.path.relative_to(diagnostics.root)),
                            "type": item.artifact_type,
                            "modified_at": item.modified_at,
                        }
                        for item in diagnostics.temp_artifacts
                    ]
                ),
                hide_index=True,
                width="stretch",
            )
        if diagnostics.inspection_errors:
            st.error("；".join(diagnostics.inspection_errors))
        if diagnostics.lock_metadata and diagnostics.lock_metadata.exists:
            st.caption(diagnostics.lock_metadata.note)


def _baseline_context(
    result,
    *,
    shared_mode_enabled: bool,
    diagnostics: AnnualDataDiagnostics | None = None,
):
    if not shared_mode_enabled:
        return (
            "unverified",
            None,
            "共享模式未啟用，本次未讀取任何共享路徑；"
            "無法確認正式環境是否存在舊版。下方只顯示候選內容完整預覽。",
            "info",
        )
    if result is not None and result.ok:
        return "available", result.annual, None, None
    if result is None or result.error is None:
        return (
            "unverified",
            None,
            "正式資料讀取結果不可用，無法確認正式環境是否存在舊版。"
            "下方只顯示候選內容完整預覽。",
            "warning",
        )
    if result.error.code is StorageErrorCode.ANNUAL_CURRENT_MISSING:
        if diagnostics is not None and diagnostics.is_first_version_state:
            return "confirmed_absent", None, None, None
        return (
            "unverified",
            None,
            "current 缺失，但 versions 中已存在正式資料 evidence，需要 recovery 判斷，"
            "不能視為第一版。下方只顯示候選內容完整預覽。",
            "error",
        )
    if result.error.code is StorageErrorCode.SYSTEM_MISSING:
        return (
            "unverified",
            None,
            "設定的測試／共享資料根目錄尚未初始化（system.json 不存在）；"
            "無法確認正式環境是否存在舊版。下方只顯示候選內容完整預覽。",
            "warning",
        )
    return (
        "unverified",
        None,
        f"正式資料來源無法完整讀取（{result.error.code.value}）："
        f"{result.error.message} 無法確認正式環境是否存在舊版，"
        "因此不產生新舊差異；下方只顯示候選內容完整預覽。",
        "error",
    )


def _render_candidate_preview(candidate, *, heading: str) -> None:
    preview = compare_annual_data(candidate, None)
    st.subheader(heading)
    counts = st.columns(4)
    for column, section in zip(
        counts,
        ("基本資訊", "水文Q值", "年度基準出流", "水庫參數"),
    ):
        column.metric(section, f"{preview.section_totals[section]} 項")
    for section in ("基本資訊", "水文Q值", "年度基準出流", "水庫參數"):
        with st.expander(f"{section}候選內容", expanded=section == "基本資訊"):
            rows = [
                {"資料鍵": row["資料鍵"], "欄位": row["欄位"], "候選值": row["新值"]}
                for row in preview.rows(section, changed_only=False)
            ]
            st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")


def _render_difference(difference, *, heading: str, checkbox_key: str | None = None) -> None:
    st.subheader(f"{heading}：共 {difference.total_changes} 項")
    columns = st.columns(4)
    for column, section in zip(
        columns,
        ("基本資訊", "水文Q值", "年度基準出流", "水庫參數"),
    ):
        column.metric(
            section,
            f"{difference.section_changes[section]} / {difference.section_totals[section]} 變更",
        )
    show_all = False
    if checkbox_key is not None:
        show_all = st.checkbox(
            "顯示完整資料（取消勾選時只顯示有變動項目）",
            value=False,
            key=checkbox_key,
        )
    for section in ("基本資訊", "水文Q值", "年度基準出流", "水庫參數"):
        with st.expander(f"{section}明細", expanded=section == "基本資訊"):
            rows = difference.rows(section, changed_only=not show_all)
            if rows:
                st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
            else:
                st.caption("此區沒有變動。")


def _render_capability(capability: AnnualDataWriteCapability) -> None:
    if capability.available:
        if capability.state == "first_version":
            st.success("年度正式寫入已受控開放：已確認 first-version observed state = (0, None)。")
        else:
            st.success("年度正式寫入已受控開放：共享 current 與完整年度版本驗證成功。")
        if not capability.activation_available:
            st.warning(capability.reason)
    else:
        st.info(f"建立／啟用正式年度版本目前不可用：{capability.reason}")


def _render_publish_workflow(
    candidate,
    parsed,
    source_bytes: bytes,
    source_filename: str,
    difference,
    capability: AnnualDataWriteCapability,
    service: AnnualDataMaintenanceService,
) -> None:
    if st.session_state.get(RECOVERY_REQUIRED_KEY):
        st.warning("目前有 recovery-required 狀態；完成診斷與 recovery 前不可再建立或啟用版本。")
        st.button("建立版本", disabled=True, key="annual_create_recovery_blocked")
        return
    if not capability.available:
        st.button("建立版本", disabled=True, key="annual_create_unavailable")
        return

    st.divider()
    st.subheader("建立新的正式年度版本")
    st.warning("建立版本只會發布不可變資料；建立成功後尚不會自動啟用。")
    facts = st.columns(3)
    facts[0].metric("候選 fingerprint", candidate.fingerprint)
    facts[1].metric("原始 Excel", source_filename)
    facts[2].metric("來源 SHA-256", parsed.source_sha256)
    st.caption(f"差異摘要：共 {difference.total_changes} 項；warnings：{len(parsed.warnings)} 項。")
    if parsed.warnings:
        st.markdown("warnings 明細：")
        st.dataframe(
            pd.DataFrame(
                [
                    {"代碼": item.code, "位置": item.location, "說明": item.message}
                    for item in parsed.warnings
                ]
            ),
            hide_index=True,
            width="stretch",
        )

    identity = hashlib.sha256(
        f"{candidate.fingerprint}\0{candidate.source_sha256}\0{source_filename}".encode("utf-8")
    ).hexdigest()
    operator = st.text_input("人工填報操作人", key=f"annual_publish_operator_{identity}")
    st.caption("人工填報身分未經登入驗證。")
    note = st.text_area("建立版本備註", key=f"annual_publish_note_{identity}")
    warnings_confirmed = True
    if parsed.warnings:
        warnings_confirmed = st.checkbox(
            "我已逐項確認上述 warnings，仍要建立此版本。",
            key=f"annual_publish_warnings_{identity}",
        )
    confirmed = st.checkbox(
        "我已確認上述內容與差異，建立新的正式年度版本；建立後尚不會自動啟用。",
        key=f"annual_publish_confirm_{identity}",
    )
    can_publish = bool(operator.strip() and note.strip() and warnings_confirmed and confirmed)
    if st.button(
        "建立版本",
        type="primary",
        disabled=not can_publish,
        key=f"annual_publish_button_{identity}",
    ):
        try:
            published = service.publish(
                root=capability.root,
                candidate=candidate,
                source_excel_bytes=source_bytes,
                source_filename=source_filename,
                operator_display_name=operator,
                note=note,
                confirmed_candidate_fingerprint=candidate.fingerprint,
                warnings_confirmed=warnings_confirmed,
            )
        except AnnualDataVersionPublishError as exc:
            st.error(f"年度版本建立失敗（{exc.code}）：{exc}")
        except Exception as exc:
            st.error(f"年度版本建立失敗；current 未變更：{exc}")
        else:
            st.session_state[PENDING_VERSION_KEY] = {
                "version_id": published.version_id,
                "candidate_fingerprint": candidate.fingerprint,
                "source_filename": source_filename,
                "source_sha256": candidate.source_sha256,
                "applicable_year": candidate.applicable_year,
                "published_metadata": published.version,
                "publish_operator": operator.strip(),
                # Display-only snapshot. Activation revalidates the immutable target itself.
                "candidate_preview": candidate,
            }
            st.session_state.pop(ACTIVATION_RESULT_KEY, None)
            st.success(
                f"年度版本 {published.version_id} 已建立完成，但尚未設為目前啟用版本。"
            )


def _render_persistent_activation_state() -> None:
    recovery = st.session_state.get(RECOVERY_REQUIRED_KEY)
    if recovery:
        st.error(
            "🚨 current 可能已經成功切換，但 audit 尚未完整確認。請勿再次點擊啟用；"
            "必須重新讀取共享狀態並進行 recovery。"
        )
        st.caption(
            f"Recovery target：{recovery['target_version_id']}｜"
            f"可能的新 revision：{recovery.get('after_revision', '未知')}"
        )
    activated = st.session_state.get(ACTIVATION_RESULT_KEY)
    if activated:
        st.success(
            f"年度版本 {activated['target_version_id']} 已完成啟用；"
            f"新 revision = {activated['after_revision']}，audit event 已建立。"
        )
        st.caption(f"previous version：{activated['previous_version_id'] or '無（第一版）'}")


def _render_activation_workflow(
    result,
    capability: AnnualDataWriteCapability,
    service: AnnualDataMaintenanceService,
    current_candidate,
) -> None:
    pending = st.session_state.get(PENDING_VERSION_KEY)
    if not pending:
        st.button("啟用此版本", disabled=True, key="annual_activate_no_pending")
        st.caption("尚無本工作階段已建立、待啟用的 immutable 年度版本。")
        return

    st.divider()
    st.subheader("啟用已建立的 immutable 年度版本")
    target_id = pending["version_id"]
    st.warning(f"本區只處理待啟用版本：{target_id}")
    if current_candidate is not None and (
        current_candidate.fingerprint != pending["candidate_fingerprint"]
        or current_candidate.source_sha256 != pending["source_sha256"]
        or current_candidate.source_filename != pending["source_filename"]
    ):
        st.warning(
            "目前上傳的是另一個候選檔案；下方啟用區仍明確指向先前已建立的 immutable 版本，"
            "不會把兩者混用。"
        )

    details = st.columns(3)
    details[0].metric("target version ID", target_id)
    details[1].metric("candidate fingerprint", pending["candidate_fingerprint"])
    details[2].metric("適用年度", str(pending["applicable_year"]))
    current_text = capability.observed_current_version_id or "無（第一版）"
    st.markdown(
        f"目前確認畫面觀察到的 current：**{current_text}**  "
        f"／ revision：**{capability.observed_revision if capability.observed_revision is not None else '未知'}**"
    )

    target_candidate = pending.get("candidate_preview")
    if target_candidate is not None and capability.state in {"healthy_current", "first_version"}:
        baseline = result.annual if result is not None and result.ok else None
        target_difference = compare_annual_data(target_candidate, baseline)
        _render_difference(target_difference, heading="target 與目前 current 的差異摘要")

    provenance = service.software_provenance()
    if provenance.ok:
        software = provenance.software
        st.caption(
            f"Software：{software['repository']} @ {software['git_commit']}｜"
            f"app version：{software['app_version']}｜"
            f"source tree dirty：{'是' if software['source_tree_dirty'] else '否'}"
        )
        if software["source_tree_dirty"]:
            st.warning("目前 source tree 有未提交變更；此狀態會如實寫入 audit metadata。")
    else:
        software = None
        st.error(provenance.error)

    identity = f"{target_id}_{capability.observed_revision}_{current_text}"
    operator = st.text_input(
        "啟用操作人",
        value=pending.get("publish_operator", ""),
        key=f"annual_activate_operator_{identity}",
    )
    st.caption("人工填報身分未經登入驗證。")
    note = st.text_area(
        "啟用備註（與建立版本備註是不同動作）",
        key=f"annual_activate_note_{identity}",
    )
    confirmed = st.checkbox(
        f"我確認要將 immutable 年度版本 {target_id} 設為 current。",
        key=f"annual_activate_confirm_{identity}",
    )
    recovery_blocked = bool(st.session_state.get(RECOVERY_REQUIRED_KEY))
    can_activate = bool(
        capability.available
        and capability.activation_available
        and software is not None
        and operator.strip()
        and note.strip()
        and confirmed
        and not recovery_blocked
    )
    if st.button(
        "啟用此版本",
        type="primary",
        disabled=not can_activate,
        key=f"annual_activate_button_{identity}",
    ):
        try:
            activated = service.activate(
                root=capability.root,
                target_version_id=target_id,
                observed_revision=capability.observed_revision,
                observed_current_version_id=capability.observed_current_version_id,
                operator_display_name=operator,
                note=note,
                software=software,
            )
        except AnnualDataActivationRecoveryRequiredError as exc:
            st.session_state[RECOVERY_REQUIRED_KEY] = {
                "target_version_id": target_id,
                "after_revision": exc.current.get("revision"),
                "current": exc.current,
                "audit_path": str(exc.audit_path),
            }
            st.session_state.pop(ACTIVATION_RESULT_KEY, None)
            st.error(
                "🚨 current 可能已經成功切換，但 audit 尚未完整確認。請勿再次點擊啟用；"
                "必須重新讀取共享狀態並進行 recovery。"
            )
        except AnnualDataActivationConflictError:
            st.error(
                "另一位使用者已先更新年度基準資料，請重新載入最新 current、"
                "重新比較後再決定是否啟用。"
            )
        except AnnualDataAlreadyCurrentError:
            st.info("指定年度版本已是目前 current；未重寫 current，也未新增 audit。")
        except AnnualDataActivationError as exc:
            if exc.code == "current_version_invalid":
                st.error(
                    "目前 current 所指年度版本已不完整／損壞，正常啟用已停止，需要 recovery。"
                )
            else:
                st.error(f"年度版本啟用失敗（{exc.code}）：{exc}")
        except Exception as exc:
            st.error(f"年度版本啟用失敗，未自動重試：{exc}")
        else:
            st.session_state[ACTIVATION_RESULT_KEY] = {
                "target_version_id": activated.target_version_id,
                "after_revision": activated.after_revision,
                "previous_version_id": activated.before_current_version_id,
                "audit_path": str(activated.audit_path),
            }
            st.session_state.pop(PENDING_VERSION_KEY, None)
            st.success(
                f"年度版本 {activated.target_version_id} 已啟用；新 current revision = "
                f"{activated.after_revision}，audit event 已建立。"
            )
            st.caption(
                f"previous version：{activated.before_current_version_id or '無（第一版）'}｜"
                f"audit：{activated.audit_path}"
            )
            st.info(
                "目前工作區不會在這次動作中被背景改寫。下次重新讀取共享狀態時，"
                "系統會要求明確選擇是否重新載入新版基準。"
            )


def render_annual_data_maintenance(
    result,
    *,
    shared_mode_enabled: bool,
    diagnostics: AnnualDataDiagnostics | None = None,
    capability: AnnualDataWriteCapability | None = None,
    service: AnnualDataMaintenanceService | None = None,
) -> None:
    """Render preview plus two independent, explicitly confirmed write actions."""
    capability = capability or annual_data_write_capability(
        result,
        shared_mode_enabled=shared_mode_enabled,
        diagnostics=diagnostics,
    )
    service = service or AnnualDataMaintenanceService()
    with st.expander("🧾 系統基準資料維護－驗證、建立與啟用", expanded=False):
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
        _render_persistent_activation_state()
        _render_capability(capability)

        uploaded = st.file_uploader(
            "手動上傳已填寫的 2-4A.1 年度基準資料 Excel",
            type=["xlsx"],
            key="annual_data_excel_preview_upload",
            help="系統不會自動掃描或載入公司共享資料夾中的 Excel。",
        )
        current_candidate = None
        if uploaded is None:
            st.caption("尚未上傳檔案。此區不會改變目前推估工作區，也不會建立正式版本。")
            st.button("建立版本", disabled=True, key="annual_create_no_upload")
        else:
            source_bytes = uploaded.getvalue()
            parsed = parse_annual_data_excel(source_bytes, filename=uploaded.name)
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
                st.button("建立版本", disabled=True, key="annual_create_invalid")
            else:
                candidate = parsed.candidate
                current_candidate = candidate
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

                baseline_state, baseline, message, message_kind = _baseline_context(
                    result,
                    shared_mode_enabled=shared_mode_enabled,
                    diagnostics=diagnostics,
                )
                if message:
                    getattr(st, message_kind)(message)
                if baseline_state == "available":
                    difference = compare_annual_data(candidate, baseline)
                    _render_difference(
                        difference,
                        heading="與目前啟用年度版本的差異",
                        checkbox_key=f"annual_preview_show_all_{candidate.fingerprint}",
                    )
                elif baseline_state == "confirmed_absent":
                    difference = compare_annual_data(candidate, None)
                    st.info("這是第一個候選系統基準版本，目前沒有舊版可比較。")
                    _render_candidate_preview(candidate, heading="第一版候選內容完整預覽")
                else:
                    difference = compare_annual_data(candidate, None)
                    _render_candidate_preview(candidate, heading="候選內容完整預覽（未與舊版比較）")

                _render_publish_workflow(
                    candidate,
                    parsed,
                    source_bytes,
                    uploaded.name,
                    difference,
                    capability,
                    service,
                )

        _render_activation_workflow(result, capability, service, current_candidate)
