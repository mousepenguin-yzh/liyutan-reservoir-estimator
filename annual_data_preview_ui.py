"""Streamlit annual-data preview, immutable publish, and activation workflow."""

from __future__ import annotations

import hashlib

import pandas as pd
import streamlit as st

from annual_data_diagnostics import (
    AnnualDataDiagnostics,
    CurrentAuditStatus,
    CurrentStatus,
    RecoverySeverity,
    VersionStatus,
)
from annual_data_current_repair import (
    AnnualCurrentRepairConflictError,
    AnnualCurrentRepairError,
    AnnualCurrentRepairRecoveryRequiredError,
    CurrentRepairAction,
)
from annual_data_activation import (
    AnnualDataActivationConflictError,
    AnnualDataActivationError,
    AnnualDataActivationRecoveryRequiredError,
    AnnualDataAlreadyCurrentError,
)
from annual_data_excel import (
    PREVIEW_NOTICE,
    AnnualDataCandidate,
    compare_annual_data,
    parse_annual_data_excel,
)
from annual_data_maintenance import (
    AnnualDataMaintenanceService,
    AnnualDataRecoveryCapability,
    AnnualDataWriteCapability,
    annual_data_recovery_capability,
    annual_data_write_capability,
)
from annual_data_recovery import AnnualDataRecoveryConflictError, AnnualDataRecoveryError
from annual_data_version_writer import AnnualDataVersionPublishError
from shared_storage_reader import StorageErrorCode


PENDING_VERSION_KEY = "annual_pending_published_version"
RECOVERY_REQUIRED_KEY = "annual_activation_recovery_required"
ACTIVATION_RESULT_KEY = "annual_activation_result"
AUDIT_RECOVERY_RESULT_KEY = "annual_audit_recovery_result"
REACTIVATION_RESULT_KEY = "annual_existing_version_reactivation_result"
FIRST_CURRENT_RESULT_KEY = "annual_first_current_initialization_result"
CURRENT_REPAIR_RESULT_KEY = "annual_current_repair_result"
CURRENT_REPAIR_PARTIAL_KEY = "annual_current_repair_partial_result"


def render_annual_data_diagnostics(
    diagnostics: AnnualDataDiagnostics | None,
    *,
    shared_mode_enabled: bool,
    result=None,
    recovery_capability: AnnualDataRecoveryCapability | None = None,
    service: AnnualDataMaintenanceService | None = None,
) -> None:
    """Render diagnostics plus the two explicitly gated safe recovery actions."""
    if not shared_mode_enabled:
        return
    with st.expander("⚙️ 系統維護與進階診斷", expanded=False):
        if diagnostics is None:
            st.error("年度 diagnostics 結果不可用；正常建立／啟用功能維持停止。")
            return

        severity = diagnostics.overall_severity
        if severity is RecoverySeverity.HEALTHY:
            st.success(f"年度資料診斷：healthy。{diagnostics.summary}")
        elif severity is RecoverySeverity.ATTENTION:
            st.warning(f"年度資料診斷：attention。{diagnostics.summary}")
        elif severity is RecoverySeverity.INITIALIZATION_REQUIRED:
            st.warning(diagnostics.summary)
            st.info("這是首次正式基準設定，不代表 current 損壞；系統不會自動選擇版本。")
        elif severity is RecoverySeverity.RECOVERY_REQUIRED:
            st.error(
                "正式年度資料需要復原處理；正常建立／啟用功能維持停止。"
            )
            st.warning(diagnostics.summary)
            st.info("只有 evidence 足以唯一判斷的 condition-specific recovery 才會顯示寫入動作。")
        else:
            st.error(f"年度資料診斷：uninspectable。{diagnostics.summary}")

        if result is not None and not result.ok and result.error is not None:
            st.markdown("**共享資料讀取錯誤**")
            st.error(f"{result.error.code.value}：{result.error.message}")
            if result.error.detail:
                st.caption(result.error.detail)

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
            "current evidence source",
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
        elif diagnostics.current_audit_status is CurrentAuditStatus.MATCHED_RECOVERY:
            st.warning(
                "此 current transition 的 audit evidence 是事後補建的 recovery record，"
                "不是原始 activation 操作紀錄。"
            )
        elif diagnostics.current_audit_status is CurrentAuditStatus.MATCHED_REPAIR:
            st.warning("目前 current 的來源為受控 current repair；repair audit evidence 已完整。")
        elif diagnostics.current_audit_status is CurrentAuditStatus.REPAIR_EVIDENCE_INCOMPLETE:
            st.error("current 已健康，但 current-repair audit publication 尚未完成；不可重送 repair。")
        elif diagnostics.current_audit_status is CurrentAuditStatus.REDUNDANT_EVIDENCE:
            st.warning(
                "此 transition 同時存在原始 activation audit 與 recovery evidence；"
                "不需要也不允許再次補建。"
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

        service = service or AnnualDataMaintenanceService()
        recovery_capability = recovery_capability or annual_data_recovery_capability(
            result,
            shared_mode_enabled=shared_mode_enabled,
            diagnostics=diagnostics,
        )
        _render_recovery_actions(
            diagnostics,
            result=result,
            capability=recovery_capability,
            service=service,
        )


def _candidate_from_version_data(data: dict) -> AnnualDataCandidate:
    version = data["version"]
    return AnnualDataCandidate(
        template_version=version["template_version"],
        reservoir_id=version["reservoir_id"],
        reservoir_name=version["reservoir_name"],
        applicable_year=version["applicable_year"],
        actual_data_cutoff_period=version["actual_data_cutoff_period"],
        hydrology_source_period=version["hydrology_source_period"],
        annual_outflow_source=version["annual_outflow_source"],
        overall_note=version["overall_note"],
        hydrology=tuple(data["hydrology"]),
        outflow_demand=tuple(data["outflow_demand"]),
        reservoir_parameters=dict(data["reservoir_parameters"]),
        parameter_metadata=dict(version["parameter_metadata"]),
        source_filename=version["source_excel"]["original_filename"],
        source_sha256=version["source_excel"]["sha256"],
        fingerprint=version["candidate_fingerprint"],
        warnings=(),
    )


def _eligible_reactivation_versions(
    diagnostics: AnnualDataDiagnostics,
):
    """Return only complete non-current historical/orphan immutable versions."""
    return tuple(
        item
        for item in diagnostics.versions
        if item.validation_ok
        and item.data is not None
        and item.status in {VersionStatus.HISTORICAL, VersionStatus.ORPHAN}
        and item.version_id != diagnostics.current_version_id
    )


def _render_software_provenance(service: AnnualDataMaintenanceService):
    provenance = service.software_provenance()
    if not provenance.ok:
        st.error(provenance.error)
        return None
    software = provenance.software
    st.caption(
        f"Software provenance：{software['repository']} @ {software['git_commit']}｜"
        f"app version：{software['app_version']}｜"
        f"source tree dirty：{'是' if software['source_tree_dirty'] else '否'}"
    )
    if software["source_tree_dirty"]:
        st.warning("目前 source tree 有未提交變更；此狀態會如實寫入 audit metadata。")
    return software


def _explicit_target_selectbox(label: str, target_ids: tuple[str, ...], *, key: str):
    return st.selectbox(
        label,
        (None, *target_ids),
        index=0,
        format_func=lambda value: "請明確選擇版本" if value is None else value,
        key=key,
    )


def _render_condition_specific_current_action(
    diagnostics: AnnualDataDiagnostics,
    *,
    capability: AnnualDataRecoveryCapability,
    service: AnnualDataMaintenanceService,
) -> bool:
    """Render one first-current/repair action and suppress unrelated controls."""
    plan = capability.repair_plan
    if plan is None or plan.action is CurrentRepairAction.NONE:
        return False

    partial = st.session_state.get(CURRENT_REPAIR_PARTIAL_KEY)
    if partial:
        st.error(
            f"current 已 repair 為 {partial['current_version_id']} / revision "
            f"{partial['revision']}，但 repair audit 未完整發布；請只使用下方補建動作。"
        )
    repaired = st.session_state.get(CURRENT_REPAIR_RESULT_KEY)
    if repaired:
        st.success(
            f"current repair 已完成：{repaired['target_version_id']} / revision "
            f"{repaired['after_revision']}，來源為 current-repair audit。"
        )
    initialized = st.session_state.get(FIRST_CURRENT_RESULT_KEY)
    if initialized:
        st.success(
            f"第一個正式年度版本已設定：{initialized['target_version_id']} / revision 1。"
        )

    if plan.first_current_initialization_available:
        st.markdown("#### 設定第一個正式年度版本")
        st.info(
            "已建立年度資料版本，但尚未設定第一個啟用版本。"
            "請選擇要作為首次正式基準的完整 orphan version。"
        )
        target_id = _explicit_target_selectbox(
            "選擇首次正式年度版本",
            plan.target_version_ids,
            key=f"annual_first_current_target_{plan.observed_token}",
        )
        software = _render_software_provenance(service)
        operator = st.text_input(
            "首次設定操作人",
            key=f"annual_first_current_operator_{plan.observed_token}",
        )
        st.caption("人工填報身分未經登入驗證。")
        note = st.text_area(
            "首次設定備註",
            key=f"annual_first_current_note_{plan.observed_token}",
        )
        if target_id is not None:
            st.caption(
                f"設定後結果：revision = 1｜current = {target_id}｜previous = None｜"
                "normal activation audit"
            )
        confirmed = st.checkbox(
            "我確認要以所選 immutable version 建立第一個 current；系統不會自動選最新版。",
            key=f"annual_first_current_confirm_{plan.observed_token}",
        )
        enabled = bool(
            capability.first_current_initialization_available
            and target_id is not None
            and software is not None
            and operator.strip()
            and note.strip()
            and confirmed
        )
        if st.button(
            "設定為第一個正式年度版本",
            type="primary",
            disabled=not enabled,
            key=f"annual_first_current_button_{plan.observed_token}",
        ):
            try:
                activated = service.initialize_first_current(
                    root=capability.root,
                    target_version_id=target_id,
                    observed_revision=0,
                    observed_current_version_id=None,
                    operator_display_name=operator,
                    note=note,
                    software=software,
                )
            except AnnualDataActivationConflictError:
                st.error("鎖內首次設定條件已改變；未寫入，請重新執行 diagnostics。")
            except AnnualDataActivationRecoveryRequiredError as exc:
                st.session_state[RECOVERY_REQUIRED_KEY] = {
                    "target_version_id": target_id,
                    "after_revision": exc.current.get("revision"),
                    "current": exc.current,
                    "audit_path": str(exc.audit_path),
                }
                st.error("第一個 current 已可能建立，但 normal activation audit 未確認；請勿重送。")
            except AnnualDataActivationError as exc:
                st.error(f"首次正式年度版本設定失敗（{exc.code}）：{exc}")
            else:
                st.session_state[FIRST_CURRENT_RESULT_KEY] = {
                    "target_version_id": activated.target_version_id,
                    "after_revision": activated.after_revision,
                    "audit_path": str(activated.audit_path),
                }
                st.info("目前工作區未由此動作偷偷改寫；rerun 後沿用 current-changed interlock。")
                st.rerun()
        return True

    if plan.repair_audit_completion_available:
        st.markdown("#### 補完 current-repair audit")
        st.error("前次 current repair 已完成 pointer 寫入，但 audit publication 未完成。")
        st.caption(f"pending evidence：{plan.pending_audit_path}")
        confirmed = st.checkbox(
            "我確認只發布既有 pending repair evidence，不重寫 current、不重送 repair。",
            key=f"annual_repair_completion_confirm_{plan.observed_token}",
        )
        if st.button(
            "補完 current-repair audit",
            type="primary",
            disabled=not confirmed,
            key=f"annual_repair_completion_button_{plan.observed_token}",
        ):
            try:
                completed = service.complete_repair_audit(
                    root=capability.root,
                    observed_plan_token=plan.observed_token,
                )
            except AnnualCurrentRepairConflictError:
                st.error("current 或 pending evidence 已改變；未發布，請重新 diagnostics。")
            except (AnnualCurrentRepairError, AnnualDataActivationError) as exc:
                st.error(f"repair audit 補完失敗（{exc.code}）：{exc}")
            else:
                st.session_state.pop(CURRENT_REPAIR_PARTIAL_KEY, None)
                st.session_state[CURRENT_REPAIR_RESULT_KEY] = {
                    "target_version_id": completed.target_version_id,
                    "after_revision": completed.after_revision,
                    "audit_path": str(completed.audit_path),
                }
                st.rerun()
        return True

    if not capability.current_repair_available:
        return False

    reconstruction = plan.reconstruction_available
    if reconstruction:
        st.markdown("#### 依最後一筆可驗證正式紀錄重建 current")
        target_id = plan.reconstructed_current_version_id
        after_revision = plan.reconstructed_revision
        previous_id = plan.reconstructed_previous_version_id
        st.info("audit transition chain 已完整且唯一；reconstruction 不增加 revision。")
    else:
        st.markdown("#### 改以其他完整年度版本恢復 current")
        if plan.recommended_target_version_id:
            st.info(
                f"建議優先檢視 previous_version_id：{plan.recommended_target_version_id}；"
                "此建議不會自動選定或執行。"
            )
        target_id = _explicit_target_selectbox(
            "選擇完整 historical／orphan recovery target",
            plan.target_version_ids,
            key=f"annual_current_repair_target_{plan.observed_token}",
        )
        after_revision = None if diagnostics.revision is None else diagnostics.revision + 1
        previous_id = diagnostics.current_version_id

    evidence = st.columns(4)
    evidence[0].metric("pre-repair status", diagnostics.current_status.value)
    evidence[1].metric(
        "原 current SHA-256",
        diagnostics.current_evidence.raw_bytes_sha256 or "current 不存在",
    )
    evidence[2].metric("target", target_id or "尚未選擇")
    evidence[3].metric("repair 後 revision", str(after_revision) if after_revision else "待選擇")
    st.caption(
        f"repair 後 previous_version_id：{previous_id or 'None'}｜"
        "immutable target 與 broken bundle 均不修改。"
    )
    software = _render_software_provenance(service)
    operator = st.text_input(
        "current repair 操作人",
        key=f"annual_current_repair_operator_{plan.observed_token}",
    )
    st.caption("人工填報身分未經登入驗證。")
    note = st.text_area(
        "current repair recovery 備註",
        key=f"annual_current_repair_note_{plan.observed_token}",
    )
    confirmed = st.checkbox(
        "我確認 evidence 與 repair 後結果；此動作不修改任何 immutable version，且不會自動重試。",
        key=f"annual_current_repair_confirm_{plan.observed_token}",
    )
    enabled = bool(
        target_id is not None
        and software is not None
        and operator.strip()
        and note.strip()
        and confirmed
    )
    label = (
        "依最後正式紀錄重建 current"
        if reconstruction
        else "改以所選完整年度版本恢復 current"
    )
    if st.button(
        label,
        type="primary",
        disabled=not enabled,
        key=f"annual_current_repair_button_{plan.observed_token}",
    ):
        try:
            repaired_result = service.repair_current(
                root=capability.root,
                observed_plan_token=plan.observed_token,
                repair_kind=plan.action.value,
                target_version_id=target_id,
                recovery_operator_display_name=operator,
                recovery_note=note,
                recovery_software=software,
            )
        except AnnualCurrentRepairConflictError:
            st.error("鎖內 evidence 已改變；未 repair、不 retry，請重新執行 diagnostics。")
        except AnnualCurrentRepairRecoveryRequiredError as exc:
            st.session_state[CURRENT_REPAIR_PARTIAL_KEY] = {
                "current_version_id": exc.current["current_version_id"],
                "revision": exc.current["revision"],
                "pending_audit_path": str(exc.pending_audit_path),
            }
            st.error("current 已 repair，但 audit publication 未完成；不 rollback，也不可重送 repair。")
            st.rerun()
        except (AnnualCurrentRepairError, AnnualDataActivationError) as exc:
            st.error(f"current repair 失敗（{exc.code}）：{exc}")
        except Exception as exc:
            st.error(f"current repair 失敗，未自動重試：{exc}")
        else:
            st.session_state[CURRENT_REPAIR_RESULT_KEY] = {
                "target_version_id": repaired_result.target_version_id,
                "after_revision": repaired_result.after_revision,
                "audit_path": str(repaired_result.audit_path),
            }
            st.info("目前工作區未由 repair action 偷偷改寫；rerun 後沿用 current-changed interlock。")
            st.rerun()
    return True


def _render_recovery_actions(
    diagnostics: AnnualDataDiagnostics,
    *,
    result,
    capability: AnnualDataRecoveryCapability,
    service: AnnualDataMaintenanceService,
) -> None:
    st.divider()
    st.subheader("Recovery Actions")
    st.caption(capability.reason)
    previous_id = (
        diagnostics.current.get("previous_version_id")
        if diagnostics.current is not None
        else None
    )
    facts = st.columns(4)
    facts[0].metric("current version", diagnostics.current_version_id or "無")
    facts[1].metric(
        "current revision",
        str(diagnostics.revision) if diagnostics.revision is not None else "無",
    )
    facts[2].metric("previous version", previous_id or "無")
    facts[3].metric("diagnostics 結論", diagnostics.overall_severity.value)

    recovered = st.session_state.get(AUDIT_RECOVERY_RESULT_KEY)
    if recovered:
        st.success(
            f"Recovery audit 已補建；current {recovered['current_version_id']}／"
            f"revision {recovered['revision']} 均未變，diagnostics = matched_recovery。"
        )
    reactivated = st.session_state.get(REACTIVATION_RESULT_KEY)
    if reactivated:
        st.success(
            f"既有版本 {reactivated['target_version_id']} 已重新啟用；"
            f"新 revision = {reactivated['after_revision']}。"
        )

    if _render_condition_specific_current_action(
        diagnostics,
        capability=capability,
        service=service,
    ):
        return

    if diagnostics.current_audit_status is CurrentAuditStatus.MISSING:
        st.markdown("#### 補建此次 current transition 的 recovery audit")
        st.error("原始 activation audit 缺失。")
        software = _render_software_provenance(service)
        identity = f"{diagnostics.current_version_id}_{diagnostics.revision}"
        operator = st.text_input(
            "recovery 操作人",
            key=f"annual_recovery_operator_{identity}",
        )
        st.caption("人工填報身分未經登入驗證。")
        note = st.text_area(
            "recovery 備註",
            key=f"annual_recovery_note_{identity}",
        )
        confirmed = st.checkbox(
            "我了解這不是還原原始操作紀錄，而是依目前完整 current／version evidence "
            "補建 recovery audit。",
            key=f"annual_recovery_confirm_{identity}",
        )
        can_recover = bool(
            capability.audit_recovery_available
            and software is not None
            and operator.strip()
            and note.strip()
            and confirmed
        )
        if st.button(
            "補建 Recovery Audit",
            type="primary",
            disabled=not can_recover,
            key=f"annual_recovery_button_{identity}",
        ):
            try:
                recovery = service.recover_audit(
                    root=capability.root,
                    observed_revision=capability.observed_revision,
                    observed_current_version_id=capability.observed_current_version_id,
                    observed_previous_version_id=capability.observed_previous_version_id,
                    recovery_operator_display_name=operator,
                    recovery_note=note,
                    recovery_software=software,
                )
            except AnnualDataRecoveryConflictError:
                st.error(
                    "鎖內狀態已改變或其他電腦已補建；未重複寫入，請重新執行 diagnostics。"
                )
            except (AnnualDataRecoveryError, AnnualDataActivationError) as exc:
                st.error(f"Recovery audit 補建失敗（{exc.code}）：{exc}")
            except Exception as exc:
                st.error(f"Recovery audit 補建失敗；current 未變更：{exc}")
            else:
                st.session_state[AUDIT_RECOVERY_RESULT_KEY] = {
                    "current_version_id": recovery.current_version_id,
                    "revision": recovery.revision,
                    "audit_path": str(recovery.audit_path),
                }
                st.rerun()

    if not capability.available:
        if diagnostics.current_status is not CurrentStatus.HEALTHY:
            st.error(
                "系統無法由現有紀錄唯一判斷正確正式版本，已停止自動復原。"
                "請由系統維護人員人工檢查。"
            )
        return

    if not capability.reactivation_available:
        return
    eligible = _eligible_reactivation_versions(diagnostics)
    st.markdown("#### 重新啟用既有 immutable 年度版本")
    if not eligible:
        st.caption("目前沒有可重新啟用的 historical／orphan 合法版本。")
        return
    choices = {item.version_id: item for item in eligible}
    target_id = st.selectbox(
        "選擇 historical／orphan target",
        tuple(choices),
        key=f"annual_reactivation_target_{diagnostics.revision}",
    )
    target = choices[target_id]
    version = target.data["version"]
    details = st.columns(4)
    details[0].metric("target version ID", target_id)
    details[1].metric("target 適用年度", str(version["applicable_year"]))
    details[2].metric("target 建立時間", version["created_at"])
    details[3].metric("inventory status", target.status.value)
    st.caption(
        f"原建立操作人：{version['operator_display_name']}｜原建立備註：{version['note']}"
    )
    st.caption(
        f"current version：{diagnostics.current_version_id}｜"
        f"current revision：{diagnostics.revision}"
    )
    target_candidate = _candidate_from_version_data(target.data)
    baseline = result.annual if result is not None and result.ok else None
    _render_difference(
        compare_annual_data(target_candidate, baseline),
        heading="target 與 current 的完整差異摘要",
        checkbox_key=f"annual_reactivation_difference_{target_id}_{diagnostics.revision}",
    )
    software = _render_software_provenance(service)
    identity = f"{target_id}_{diagnostics.revision}_{diagnostics.current_version_id}"
    operator = st.text_input(
        "重新啟用操作人",
        key=f"annual_reactivation_operator_{identity}",
    )
    st.caption("人工填報身分未經登入驗證。")
    note = st.text_area(
        "新的啟用備註",
        key=f"annual_reactivation_note_{identity}",
    )
    confirmed = st.checkbox(
        f"我確認要將既有 immutable 年度版本 {target_id} 重新設為 current；"
        "此動作會建立新的 revision，不會修改任何歷史版本。",
        key=f"annual_reactivation_confirm_{identity}",
    )
    can_activate = bool(
        software is not None and operator.strip() and note.strip() and confirmed
    )
    if st.button(
        "重新啟用既有版本",
        type="primary",
        disabled=not can_activate,
        key=f"annual_reactivation_button_{identity}",
    ):
        try:
            activation = (
                service.initialize_first_current
                if capability.state == "first_version"
                else service.activate
            )
            activated = activation(
                root=capability.root,
                target_version_id=target_id,
                observed_revision=capability.observed_revision,
                observed_current_version_id=capability.observed_current_version_id,
                operator_display_name=operator,
                note=note,
                software=software,
            )
        except AnnualDataActivationConflictError:
            st.error("current revision 已改變；不自動 retry，請重新執行 diagnostics。")
        except AnnualDataAlreadyCurrentError:
            st.info("target 已是 current；未建立新 revision 或 audit。")
        except AnnualDataActivationRecoveryRequiredError as exc:
            st.session_state[RECOVERY_REQUIRED_KEY] = {
                "target_version_id": target_id,
                "after_revision": exc.current.get("revision"),
                "current": exc.current,
                "audit_path": str(exc.audit_path),
            }
            st.error("current 可能已切換但 audit 未確認；請勿重試，需重新 diagnostics。")
        except AnnualDataActivationError as exc:
            st.error(f"既有版本重新啟用失敗（{exc.code}）：{exc}")
        except Exception as exc:
            st.error(f"既有版本重新啟用失敗，未自動重試：{exc}")
        else:
            st.session_state[REACTIVATION_RESULT_KEY] = {
                "target_version_id": activated.target_version_id,
                "after_revision": activated.after_revision,
                "previous_version_id": activated.before_current_version_id,
            }
            st.info(
                "目前工作區不會背景替換；既有 current-changed interlock 會在 rerun 後處理。"
            )
            st.rerun()


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
            "目前無法確認系統中是否已有舊版；下方只顯示這次上傳的完整內容。",
            "info",
        )
    if result is not None and result.ok:
        return "available", result.annual, None, None
    if result is None or result.error is None:
        return (
            "unverified",
            None,
            "目前無法確認系統中是否已有舊版；下方只顯示這次上傳的完整內容。",
            "warning",
        )
    if result.error.code is StorageErrorCode.ANNUAL_CURRENT_MISSING:
        if diagnostics is not None and diagnostics.is_first_version_state:
            return "confirmed_absent", None, None, None
        return (
            "unverified",
            None,
            "系統中已有年度資料記錄，但目前無法安全判斷使用中的版本。"
            "請由維護人員處理；下方只顯示這次上傳的完整內容。",
            "error",
        )
    if result.error.code is StorageErrorCode.SYSTEM_MISSING:
        return (
            "unverified",
            None,
            "系統資料尚未完成初始化，無法確認是否已有舊版。"
            "下方只顯示這次上傳的完整內容。",
            "warning",
        )
    return (
        "unverified",
        None,
        "系統基準資料目前無法完整讀取，因此不產生新舊差異；"
        "下方只顯示這次上傳的完整內容。請由維護人員查看進階診斷。",
        "error",
    )


def _render_candidate_preview(candidate, *, heading: str) -> None:
    preview = compare_annual_data(candidate, None)
    st.subheader(heading)
    counts = st.columns(4)
    labels = {
        "基本資訊": "基本資料",
        "水文Q值": "水文資料",
        "年度基準出流": "出流資料",
        "水庫參數": "水庫參數",
    }
    for column, section in zip(
        counts,
        ("基本資訊", "水文Q值", "年度基準出流", "水庫參數"),
    ):
        column.metric(labels[section], f"{preview.section_totals[section]} 項")
    for section in ("基本資訊", "水文Q值", "年度基準出流", "水庫參數"):
        with st.expander(f"{labels[section]}候選內容", expanded=False):
            rows = [
                {"資料鍵": row["資料鍵"], "欄位": row["欄位"], "候選值": row["新值"]}
                for row in preview.rows(section, changed_only=False)
            ]
            st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")


def _render_difference(difference, *, heading: str, checkbox_key: str | None = None) -> None:
    st.subheader(f"{heading}：共 {difference.total_changes} 項")
    labels = {
        "基本資訊": "基本資料",
        "水文Q值": "水文資料",
        "年度基準出流": "出流資料",
        "水庫參數": "水庫參數",
    }
    columns = st.columns(4)
    for column, section in zip(
        columns,
        ("基本資訊", "水文Q值", "年度基準出流", "水庫參數"),
    ):
        column.metric(
            labels[section],
            f"{difference.section_changes[section]} 項變更",
        )
    show_all = False
    if checkbox_key is not None:
        show_all = st.checkbox(
            "顯示完整資料（取消勾選時只顯示有變動項目）",
            value=False,
            key=checkbox_key,
        )
    for section in ("基本資訊", "水文Q值", "年度基準出流", "水庫參數"):
        with st.expander(f"{labels[section]}差異明細", expanded=False):
            rows = difference.rows(section, changed_only=not show_all)
            if rows:
                st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
            else:
                st.caption("此區沒有變動。")


def _render_capability(capability: AnnualDataWriteCapability) -> None:
    if capability.available:
        if capability.state == "first_version":
            st.info("目前尚未設定第一份系統基準資料；建立新版後仍需由維護人員明確啟用。")
        if not capability.activation_available:
            st.warning("目前可檢查與建立新版，但暫時無法啟用；請由系統維護人員查看進階診斷。")
    else:
        st.info("目前可以上傳與檢查年度資料，但建立／啟用功能暫不可用。")
        st.caption("如需處理，請由系統維護人員查看進階診斷。")


def _render_publish_workflow(
    candidate,
    parsed,
    source_bytes: bytes,
    source_filename: str,
    difference,
    capability: AnnualDataWriteCapability,
    service: AnnualDataMaintenanceService,
) -> None:
    st.divider()
    st.subheader("3. 建立新版")
    st.warning("建立新版後，尚不會立即套用到系統。")
    if st.session_state.get(RECOVERY_REQUIRED_KEY):
        st.warning("上次年度資料操作需要維護人員確認；處理完成前不能建立或啟用新版。")
        st.button("建立新版", disabled=True, key="annual_create_recovery_blocked")
        return
    if not capability.available:
        st.button("建立新版", disabled=True, key="annual_create_unavailable")
        return

    st.caption(f"上傳檔案：{source_filename}")
    st.caption(f"差異摘要：共 {difference.total_changes} 項；提醒：{len(parsed.warnings)} 項。")
    with st.expander("技術驗證資訊（進階）", expanded=False):
        facts = st.columns(2)
        facts[0].metric("候選 fingerprint", candidate.fingerprint)
        facts[1].metric("來源 SHA-256", parsed.source_sha256)
    if parsed.warnings:
        st.markdown("提醒明細：")
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
    note = st.text_area("建立新版備註", key=f"annual_publish_note_{identity}")
    warnings_confirmed = True
    if parsed.warnings:
        warnings_confirmed = st.checkbox(
            "我已逐項確認上述提醒，仍要建立新版。",
            key=f"annual_publish_warnings_{identity}",
        )
    confirmed = st.checkbox(
        "我已確認上述內容與差異，建立新版；建立後尚不會立即套用。",
        key=f"annual_publish_confirm_{identity}",
    )
    can_publish = bool(operator.strip() and note.strip() and warnings_confirmed and confirmed)
    if st.button(
        "建立新版",
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
            st.error("建立新版失敗，未變更系統目前使用的年度資料。")
            with st.expander("查看技術錯誤", expanded=False):
                st.error(f"{exc.code}：{exc}")
        except Exception as exc:
            st.error("建立新版失敗，未變更系統目前使用的年度資料。")
            with st.expander("查看技術錯誤", expanded=False):
                st.error(str(exc))
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
            st.success("✅ 新版已建立，但尚未套用")
            st.caption(f"適用年度：{candidate.applicable_year}")


def _render_persistent_activation_state() -> None:
    recovery = st.session_state.get(RECOVERY_REQUIRED_KEY)
    if recovery:
        st.error("⚠️ 上次啟用結果需要系統維護人員確認，請勿再次點擊啟用。")
        st.caption("請開啟「⚙️ 系統維護與進階診斷」處理。")
    activated = st.session_state.get(ACTIVATION_RESULT_KEY)
    if activated:
        st.success("✅ 新版已啟用")
        with st.expander("本次啟用技術記錄（進階）", expanded=False):
            st.caption(
                f"version：{activated['target_version_id']}｜revision：{activated['after_revision']}｜"
                f"previous：{activated['previous_version_id'] or '無（第一版）'}"
            )


def _render_activation_workflow(
    result,
    capability: AnnualDataWriteCapability,
    service: AnnualDataMaintenanceService,
    current_candidate,
) -> None:
    pending = st.session_state.get(PENDING_VERSION_KEY)
    if not pending:
        st.subheader("4. 啟用新版")
        st.button("啟用新版", disabled=True, key="annual_activate_no_pending")
        st.caption("尚未建立可啟用的新版。")
        return

    st.divider()
    st.subheader("4. 啟用新版")
    target_id = pending["version_id"]
    st.info(
        "啟用後，之後新開啟的推估會使用這份年度資料。"
        "已經開啟中的其他電腦／工作區不會被強制切換。"
    )
    if current_candidate is not None and (
        current_candidate.fingerprint != pending["candidate_fingerprint"]
        or current_candidate.source_sha256 != pending["source_sha256"]
        or current_candidate.source_filename != pending["source_filename"]
    ):
        st.warning(
            "目前上傳的是另一份檔案；啟用動作仍會使用剛才已建立的新版，不會混用。"
        )

    st.metric("適用年度", str(pending["applicable_year"]))
    current_text = capability.observed_current_version_id or "無（第一版）"

    target_candidate = pending.get("candidate_preview")
    if target_candidate is not None and capability.state in {"healthy_current", "first_version"}:
        baseline = result.annual if result is not None and result.ok else None
        target_difference = compare_annual_data(target_candidate, baseline)
        _render_difference(target_difference, heading="新版與目前資料的差異摘要")

    with st.expander("此次啟用的技術驗證資訊（進階）", expanded=False):
        details = st.columns(2)
        details[0].metric("target version ID", target_id)
        details[1].metric("candidate fingerprint", pending["candidate_fingerprint"])
        st.caption(
            f"observed current：{current_text}｜observed revision："
            f"{capability.observed_revision if capability.observed_revision is not None else '未知'}"
        )
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
        "啟用備註（與建立新版備註是不同動作）",
        key=f"annual_activate_note_{identity}",
    )
    confirmed = st.checkbox(
        "我確認要啟用這份年度資料。",
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
        "啟用新版",
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
            st.error("⚠️ 啟用結果需要系統維護人員確認，請勿再次點擊啟用。")
            st.caption("請開啟「⚙️ 系統維護與進階診斷」處理。")
        except AnnualDataActivationConflictError:
            st.error(
                "另一位使用者已先更新系統基準資料，請重新整理並檢查最新差異後再決定是否啟用。"
            )
        except AnnualDataAlreadyCurrentError:
            st.info("這份年度資料已經在使用中，系統未重複啟用。")
        except AnnualDataActivationError as exc:
            if exc.code == "current_version_invalid":
                st.error(
                    "目前使用中的系統基準資料不完整，已停止啟用；請由系統維護人員處理。"
                )
            else:
                st.error("新版啟用失敗，系統未自動重試。")
                with st.expander("查看技術錯誤", expanded=False):
                    st.error(f"{exc.code}：{exc}")
        except Exception as exc:
            st.error("新版啟用失敗，系統未自動重試。")
            with st.expander("查看技術錯誤", expanded=False):
                st.error(str(exc))
        else:
            st.session_state[ACTIVATION_RESULT_KEY] = {
                "target_version_id": activated.target_version_id,
                "after_revision": activated.after_revision,
                "previous_version_id": activated.before_current_version_id,
                "audit_path": str(activated.audit_path),
            }
            st.session_state.pop(PENDING_VERSION_KEY, None)
            st.success("✅ 新版已啟用")
            with st.expander("本次啟用技術記錄（進階）", expanded=False):
                st.caption(
                    f"version：{activated.target_version_id}｜revision：{activated.after_revision}｜"
                    f"previous：{activated.before_current_version_id or '無（第一版）'}｜"
                    f"audit：{activated.audit_path}"
                )
            st.info(
                "目前開啟的推估不會自動變更。重新整理後，系統會讓您明確選擇是否載入新版。"
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
    with st.expander("🧾 年度資料維護", expanded=False):
        st.subheader("1. 上傳年度資料")
        st.info(
            "這個功能只在初次建立或日後更新系統基準資料時使用；"
            "一般每旬推估不需要重新填寫或上傳年度 Excel。"
        )
        st.markdown(
            "系統基準資料是所有新推估共用的預設基礎；單次推估的自訂入流、出流、"
            "抗旱調度與臨時參數只屬於該次推估。"
        )
        _render_persistent_activation_state()
        _render_capability(capability)

        uploaded = st.file_uploader(
            "上傳已填寫的年度基準資料 Excel",
            type=["xlsx"],
            key="annual_data_excel_preview_upload",
            help="系統不會自動掃描或載入公司共享資料夾中的 Excel。",
        )
        current_candidate = None
        if uploaded is None:
            st.caption("尚未上傳檔案。此區不會改變目前推估，也不會建立新版。")
            st.button("建立新版", disabled=True, key="annual_create_no_upload")
        else:
            source_bytes = uploaded.getvalue()
            parsed = parse_annual_data_excel(source_bytes, filename=uploaded.name)
            st.caption(f"上傳檔案：{uploaded.name}")
            with st.expander("上傳檔案技術驗證資訊（進階）", expanded=False):
                st.metric("原始檔案 SHA-256", parsed.source_sha256 or "無法計算")
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
                st.button("建立新版", disabled=True, key="annual_create_invalid")
            else:
                candidate = parsed.candidate
                current_candidate = candidate
                st.success("✅ 年度資料驗證成功")
                st.warning(PREVIEW_NOTICE)
                summary_columns = st.columns(3)
                summary_columns[0].metric("適用年度", str(candidate.applicable_year))
                summary_columns[1].metric("實績截止旬", candidate.actual_data_cutoff_period)
                summary_columns[2].metric("基本資料", "完整")
                with st.expander("候選資料技術驗證資訊（進階）", expanded=False):
                    technical_columns = st.columns(3)
                    technical_columns[0].metric("水文／出流旬數", "36／36")
                    technical_columns[1].metric("Q欄／參數數", "19／4")
                    technical_columns[2].metric("候選 fingerprint", candidate.fingerprint)
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
                    st.warning(f"驗證完成，但有 {len(parsed.warnings)} 項提醒；建立新版前請逐項確認。")
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
                    st.caption("沒有驗證提醒。")

                st.subheader("2. 檢查差異")
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
