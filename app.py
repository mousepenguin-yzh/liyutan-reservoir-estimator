import streamlit as st
import pandas as pd
import datetime
import calendar

# ==========================================
# 1. 核心物理與曆法引擎 (Calendar Engine)
# ==========================================

def generate_date_profile(start_date: datetime.date, end_date: datetime.date) -> pd.DataFrame:
    """
    根據起始日與目標日，生成逐日的時間剖面資料表。
    動態切分上、中、下旬，並精確計算該旬的「實際日曆天數」。
    """
    if start_date > end_date:
        raise ValueError("起始日期不可大於結束日期，請重新檢查您的時間區間。")
        
    dates = []
    curr = start_date
    while curr <= end_date:
        dates.append(curr)
        curr += datetime.timedelta(days=1)
        
    data = []
    for d in dates:
        year = d.year
        month = d.month
        day = d.day
        
        # 判斷旬別
        if day <= 10:
            period = "上旬"
            period_total_days = 10
        elif day <= 20:
            period = "中旬"
            period_total_days = 10
        else:
            period = "下旬"
            _, total_days_in_month = calendar.monthrange(year, month)
            period_total_days = total_days_in_month - 20
            
        data.append({
            "日期": d,
            "年份": year,
            "月份": month,
            "日": day,
            "旬別": period,
            "該旬實際總天數": period_total_days
        })
        
    return pd.DataFrame(data)

# ==========================================
# 2. 第二階段核心邏輯：標準旬流量資料庫與解析
# ==========================================

def get_builtin_shilin_flow(month: int, period: str, scenario: str) -> float:
    """
    後台內建大安溪士林堰歷史標準旬流量資料庫 (單位: cms)。
    """
    base_flows = {
        1: 3.5,   2: 3.2,   3: 4.0,   4: 6.5,   5: 12.0,  6: 22.0,
        7: 35.0,  8: 38.0,  9: 25.0,  10: 10.0, 11: 5.0,  12: 4.0
    }
    period_multipliers = {"上旬": 0.95, "中旬": 1.00, "下旬": 1.05}
    scenario_multipliers = {
        "Q5 (極豐水)": 2.50, "Q20 (偏豐水)": 1.50, "Q50 (平水)": 1.00,
        "Q75 (偏枯水)": 0.60, "Q95 (特枯水)": 0.35
    }
    base = base_flows.get(month, 5.0)
    p_mult = period_multipliers.get(period, 1.0)
    s_mult = scenario_multipliers.get(scenario, 1.0)
    return round(base * p_mult * s_mult, 2)


def parse_pasted_data(paste_str: str) -> list:
    """
    高容錯解析器：解析從 Excel 複製貼上的橫向或縱向數據。
    """
    if not paste_str.strip():
        return []
    raw_tokens = paste_str.replace(",", "").split()
    parsed_values = []
    for token in raw_tokens:
        try:
            parsed_values.append(float(token))
        except ValueError:
            continue
    return parsed_values

# ==========================================
# 3. 第三階段核心邏輯：標準出流配置與預設值
# ==========================================

def get_default_demands(month: int) -> dict:
    """
    依據歷史常態，提供各月份出流三大標的的平水期預設需求。
    - 上灌區 (cms): 農業用水 (第一期稻作 2-6月需求較高，第二期 7-11月，枯水期無)
    - 下灌區 (cms): 農業用水 (同上，通常下灌區範圍較大，流量需求略高)
    - 公共出水 (萬噸/日): 台中地區常態民生/工業供水，通常維持在 55 ~ 65 萬噸/日
    """
    # 灌區一期稻作 2-6 月需求高，二期稻作 7-11 月，其餘為休耕或低需求
    irrigation_schedule = {
        1: (0.5, 0.8),   2: (3.0, 4.5),   3: (4.0, 5.5),   4: (3.5, 5.0),
        5: (2.5, 3.5),   6: (3.5, 4.8),   7: (4.0, 5.5),   8: (4.5, 6.0),
        9: (3.5, 5.0),   10: (2.0, 3.0),  11: (1.0, 1.5),  12: (0.5, 0.8)
    }
    
    up_irr, down_irr = irrigation_schedule.get(month, (1.0, 1.5))
    public_water = 60.0  # 預設台中常態公共給水 60 萬噸/日
    
    return {
        "up_irr": up_irr,
        "down_irr": down_irr,
        "public": public_water
    }

# ==========================================
# 4. Streamlit 初始化與會話狀態
# ==========================================

st.set_page_config(
    page_title="鯉魚潭水庫庫容推估系統 (第三階段)",
    page_icon="💧",
    layout="wide"
)

# 基礎參數初始化 (第一、二階段)
if "max_capacity" not in st.session_state:
    st.session_state.max_capacity = 11584.0
if "shilin_eco_flow" not in st.session_state:
    st.session_state.shilin_eco_flow = 2.2
if "liyutan_eco_flow" not in st.session_state:
    st.session_state.liyutan_eco_flow = 0.3
if "start_date" not in st.session_state:
    st.session_state.start_date = datetime.date(2026, 2, 1)
if "end_date" not in st.session_state:
    st.session_state.end_date = datetime.date(2026, 3, 15)
if "init_capacity" not in st.session_state:
    st.session_state.init_capacity = 8000.0
if "inflow_source" not in st.session_state:
    st.session_state.inflow_source = "內建標準水文情境 (Q5~Q95)"
if "builtin_scenario" not in st.session_state:
    st.session_state.builtin_scenario = "Q50 (平水)"
if "manual_flow_dict" not in st.session_state:
    st.session_state.manual_flow_dict = {}

# 第三階段新增狀態：出流臨時調整覆寫字典
# 格式: { "2026-02-上旬": {"up_irr": 1.5, "down_irr": 2.0, "public": 50.0, "reason": "抗旱一級減壓"} }
if "override_dict" not in st.session_state:
    st.session_state.override_dict = {}

# ==========================================
# 5. 前端 UI 分頁排版
# ==========================================

st.title("💧 鯉魚潭水庫庫容推估系統 (第三階段)")
st.markdown("""
本階段已成功整合 **時間曆法模塊**、**入流設定**，並全新加入 **出流需求配置與抗旱臨時調整機制**。
""")

tab_config, tab_inflow, tab_outflow = st.tabs([
    "⚙️ 第一階段：基礎參數與曆法", 
    "🌊 第二階段：入流條件設定",
    "🚰 第三階段：出流需求與抗旱調整"
])

# -----------------
# TAB 1: 基礎與曆法
# -----------------
with tab_config:
    st.subheader("⚙️ 基礎參數與邊界條件設定")
    col1, col2 = st.columns(2)
    with col1:
        st.markdown("##### 🏛️ 水庫與堰體物理限制")
        st.session_state.max_capacity = st.number_input("水庫上限庫容 (萬噸)", min_value=100.0, max_value=20000.0, value=st.session_state.max_capacity, step=100.0)
        st.session_state.shilin_eco_flow = st.number_input("士林堰生態基流量 (cms)", min_value=0.0, max_value=10.0, value=st.session_state.shilin_eco_flow, step=0.1)
        st.session_state.liyutan_eco_flow = st.number_input("鯉魚潭最低生態放流量 (cms)", min_value=0.0, max_value=5.0, value=st.session_state.liyutan_eco_flow, step=0.05)
    with col2:
        st.markdown("##### 📅 模擬區間與起始狀態")
        st.session_state.start_date = st.date_input("模擬起始日期", value=st.session_state.start_date)
        st.session_state.end_date = st.date_input("預計推估目標日 (結束日期)", value=st.session_state.end_date)
        if st.session_state.start_date > st.session_state.end_date:
            st.error("⚠️ 錯誤：『模擬起始日期』不可晚於『預計推估目標日』。")
        st.session_state.init_capacity = st.number_input("起始日當天庫容 (萬噸)", min_value=0.0, max_value=st.session_state.max_capacity, value=st.session_state.init_capacity, step=10.0)

    if st.session_state.start_date <= st.session_state.end_date:
        df_cal = generate_date_profile(st.session_state.start_date, st.session_state.end_date)
        unique_periods = df_cal.groupby(["年份", "月份", "旬別"]).size().reset_index().drop(columns=[0])
        st.success(f"📅 曆法配置成功：當前模擬區間跨越了 **{len(unique_periods)}** 個完整的旬別。")
    else:
        unique_periods = pd.DataFrame()

# -----------------
# TAB 2: 第二階段入流
# -----------------
with tab_inflow:
    st.subheader("🌊 入流條件與流量解析")
    if unique_periods.empty:
        st.warning("⚠️ 請先返回第一階段，設定正確的模擬日期區間。")
    else:
        inflow_mode = st.radio("請選擇天然流量 (cms) 來源模式：", ["內建標準水文情境 (Q5~Q95)", "手動批次匯入（支援 Excel 複製貼上）"], horizontal=True)
        st.session_state.inflow_source = inflow_mode
        period_flow_mapping = []
        
        if inflow_mode == "內建標準水文情境 (Q5~Q95)":
            selected_scen = st.selectbox("請選擇水文情境：", ["Q5 (極豐水)", "Q20 (偏豐水)", "Q50 (平水)", "Q75 (偏枯水)", "Q95 (特枯水)"], index=["Q5 (極豐水)", "Q20 (偏豐水)", "Q50 (平水)", "Q75 (偏枯水)", "Q95 (特枯水)"].index(st.session_state.builtin_scenario))
            st.session_state.builtin_scenario = selected_scen
            for idx, row in unique_periods.iterrows():
                y, m, p = row["年份"], row["月份"], row["旬別"]
                flow_val = get_builtin_shilin_flow(m, p, selected_scen)
                period_flow_mapping.append({"年份": y, "月份": m, "旬別": p, "天然流量(cms)": flow_val})
        else:
            st.markdown("##### 📥 Excel 數據批次貼上區")
            dummy_data_list = [round(get_builtin_shilin_flow(row["月份"], row["旬別"], "Q50 (平水)"), 1) for _, row in unique_periods.iterrows()]
            dummy_paste_str = "\t".join(map(str, dummy_data_list))
            st.caption(f"💡 測試範例串（共 {len(dummy_data_list)} 個數值）： `{dummy_paste_str}` (您可直接複製此串進行貼上測試)")
            
            pasted_text = st.text_area("請在此貼上 Excel 數據 (以空格、Tab或換行分隔)：", placeholder="例如: 12.5  14.2  10.1 ...", height=80, key="inflow_paste")
            parsed_list = parse_pasted_data(pasted_text)
            
            if pasted_text.strip():
                if len(parsed_list) != len(unique_periods):
                    st.error(f"❌ 解析失敗：您貼上的數據個數（{len(parsed_list)} 筆）與當前區間所需（{len(unique_periods)} 筆）不符！")
                    for i, (_, row) in enumerate(unique_periods.iterrows()):
                        period_flow_mapping.append({"年份": row["年份"], "月份": row["月份"], "旬別": row["旬別"], "天然流量(cms)": get_builtin_shilin_flow(row["月份"], row["旬別"], "Q50 (平水)")})
                else:
                    st.success("✅ 數據成功解析並對齊！")
                    for i, (_, row) in enumerate(unique_periods.iterrows()):
                        y, m, p = row["年份"], row["月份"], row["旬別"]
                        flow_val = parsed_list[i]
                        st.session_state.manual_flow_dict[f"{y}-{m}-{p}"] = flow_val
                        period_flow_mapping.append({"年份": y, "月份": m, "旬別": p, "天然流量(cms)": flow_val})
            else:
                st.info("⚠️ 尚未貼上數據，下方目前顯示內建 Q50 預設值作為參考占位。")
                for idx, row in unique_periods.iterrows():
                    period_flow_mapping.append({"年份": row["年份"], "月份": row["月份"], "旬別": row["旬別"], "天然流量(cms)": get_builtin_shilin_flow(row["月份"], row["旬別"], "Q50 (平水)")})

        df_period_flow = pd.DataFrame(period_flow_mapping)
        st.dataframe(df_period_flow, use_container_width=True)

# -----------------
# TAB 3: 第三階段出流
# -----------------
with tab_outflow:
    st.subheader("🚰 出流標的需求配置與抗旱覆寫機制")
    if unique_periods.empty:
        st.warning("⚠️ 請先返回第一階段，設定正確的模擬日期區間。")
    else:
        # 1. 基礎出水配置模式 (提供手動複製 Excel 的彈性)
        outflow_mode = st.radio(
            "請選擇出流配置模式：",
            ["使用歷史常態預設需求值", "自訂出流需求（支援 Excel 複製貼上）"],
            horizontal=True,
            key="outflow_mode_radio"
        )
        
        # 建立一個基礎需求對照列表
        base_demand_list = []
        
        if outflow_mode == "使用歷史常態預設需求值":
            for idx, row in unique_periods.iterrows():
                y, m, p = row["年份"], row["月份"], row["旬別"]
                def_demands = get_default_demands(m)
                base_demand_list.append({
                    "年份": y, "月份": m, "旬別": p,
                    "上灌區需求(cms)": def_demands["up_irr"],
                    "下灌區需求(cms)": def_demands["down_irr"],
                    "公共出水(萬噸/日)": def_demands["public"]
                })
            st.info("💡 系統已自動帶入歷史常態平水期供水需求（各月份灌區用水不同，民生供水常態為 60 萬噸/日）。")
            
        else:
            st.markdown("##### 📥 Excel 需求數據批次匯入")
            st.markdown(f"請依序分別貼入這 {len(unique_periods)} 個旬的需求數據（以空格或 Tab 鍵分隔）：")
            
            # 生動產生範例貼上值
            def_up_list = [get_default_demands(r["月份"])["up_irr"] for _, r in unique_periods.iterrows()]
            def_down_list = [get_default_demands(r["月份"])["down_irr"] for _, r in unique_periods.iterrows()]
            def_pub_list = [get_default_demands(r["月份"])["public"] for _, r in unique_periods.iterrows()]
            
            col_u, col_d, col_p = st.columns(3)
            with col_u:
                st.caption(f"💡 上灌區(cms) 測試串： `{'  '.join(map(str, def_up_list))}`")
                paste_up = st.text_area("1. 貼上【上灌區(cms)】：", height=70)
            with col_d:
                st.caption(f"💡 下灌區(cms) 測試串： `{'  '.join(map(str, def_down_list))}`")
                paste_down = st.text_area("2. 貼上【下灌區(cms)】：", height=70)
            with col_p:
                st.caption(f"💡 公共給水(萬噸/日) 測試串： `{'  '.join(map(str, def_pub_list))}`")
                paste_pub = st.text_area("3. 貼上【公共給水(萬噸/日)】：", height=70)
                
            parsed_up = parse_pasted_data(paste_up)
            parsed_down = parse_pasted_data(paste_down)
            parsed_pub = parse_pasted_data(paste_pub)
            
            # 容錯組裝
            for i, (_, row) in enumerate(unique_periods.iterrows()):
                y, m, p = row["年份"], row["月份"], row["旬別"]
                def_val = get_default_demands(m)
                
                u_val = parsed_up[i] if (len(parsed_up) == len(unique_periods)) else def_val["up_irr"]
                d_val = parsed_down[i] if (len(parsed_down) == len(unique_periods)) else def_val["down_irr"]
                p_val = parsed_pub[i] if (len(parsed_pub) == len(unique_periods)) else def_val["public"]
                
                base_demand_list.append({
                    "年份": y, "月份": m, "旬別": p,
                    "上灌區需求(cms)": u_val,
                    "下灌區需求(cms)": d_val,
                    "公共出水(萬噸/日)": p_val
                })
                
            if paste_up.strip() or paste_down.strip() or paste_pub.strip():
                if len(parsed_up) != len(unique_periods) or len(parsed_down) != len(unique_periods) or len(parsed_pub) != len(unique_periods):
                    st.warning("⚠️ 貼入之數據筆數與區間需求不符，不符之欄位已自動套用預設值暫代。")
                else:
                    st.success("✅ 三大標的出流需求皆已成功解析並載入！")

        df_base_demands = pd.DataFrame(base_demand_list)
        
        # 2. ⚡ 抗旱臨時調整覆寫機制 ⚡
        st.markdown("---")
        st.markdown("#### ⚡ 歷史枯旱期/臨時調度覆寫機制")
        st.markdown("當遭遇極端乾旱時，水利署或水資源分署會實施農業打折停灌或公共給水減壓。**請啟用下方機制並選擇旬別進行覆寫：**")
        
        enable_override = st.checkbox("啟用抗旱臨時覆寫機制", value=False)
        
        if enable_override:
            # 讓使用者多選需要調整的旬別
            period_options = [f"{r['年份']}-{r['月份']}-{r['旬別']}" for _, r in unique_periods.iterrows()]
            selected_override_periods = st.multiselect("請選擇欲調整/覆寫的旬別（可多選）：", period_options)
            
            if selected_override_periods:
                st.markdown("##### ✏️ 請輸入調整後的目標數值與「強制備註」：")
                
                for popt in selected_override_periods:
                    y_str, m_str, p_str = popt.split("-")
                    # 抓取該旬原本的值作為預設值
                    orig_row = df_base_demands[
                        (df_base_demands["年份"] == int(y_str)) & 
                        (df_base_demands["月份"] == int(m_str)) & 
                        (df_base_demands["旬別"] == p_str)
                    ].iloc[0]
                    
                    st.markdown(f"**📍 調整區間：{popt}**")
                    col_u_ov, col_d_ov, col_p_ov, col_note_ov = st.columns([1.5, 1.5, 1.5, 3.5])
                    
                    with col_u_ov:
                        ov_up = st.number_input(f"上灌需求 (cms)", min_value=0.0, max_value=20.0, value=float(orig_row["上灌區需求(cms)"]), key=f"ov_up_{popt}", step=0.1)
                    with col_d_ov:
                        ov_down = st.number_input(f"下灌需求 (cms)", min_value=0.0, max_value=20.0, value=float(orig_row["下灌區需求(cms)"]), key=f"ov_down_{popt}", step=0.1)
                    with col_p_ov:
                        ov_pub = st.number_input(f"公共給水 (萬噸/日)", min_value=0.0, max_value=150.0, value=float(orig_row["公共出水(萬噸/日)"]), key=f"ov_pub_{popt}", step=1.0)
                    with col_note_ov:
                        ov_reason = st.text_input(f"※ 覆寫原因/抗旱備註 (必填)", value="", placeholder="例如：農業打 5 折、民生減量 10%", key=f"ov_reason_{popt}")
                    
                    # 存入 Session State 中
                    st.session_state.override_dict[popt] = {
                        "up_irr": ov_up,
                        "down_irr": ov_down,
                        "public": ov_pub,
                        "reason": ov_reason
                    }
            else:
                # 若取消選擇，清空覆寫字典
                st.session_state.override_dict = {}
        else:
            st.session_state.override_dict = {}

        # 3. 整合基礎需求與抗旱覆寫的最終出流對照表
        final_demand_list = []
        for idx, row in df_base_demands.iterrows():
            key = f"{int(row['年份'])}-{int(row['月份'])}-{row['旬別']}"
            
            # 檢查是否有抗旱覆寫
            if enable_override and key in st.session_state.override_dict:
                ov = st.session_state.override_dict[key]
                final_demand_list.append({
                    "年份": row["年份"], "月份": row["月份"], "旬別": row["旬別"],
                    "上灌區需求(cms)": ov["up_irr"],
                    "下灌區需求(cms)": ov["down_irr"],
                    "公共出水(萬噸/日)": ov["public"],
                    "調度狀態": "⚡ 抗旱覆寫",
                    "原因備註": ov["reason"] if ov["reason"].strip() else "⚠️ 未填寫調整原因"
                })
            else:
                final_demand_list.append({
                    "年份": row["年份"], "月份": row["月份"], "旬別": row["旬別"],
                    "上灌區需求(cms)": row["上灌區需求(cms)"],
                    "下灌區需求(cms)": row["下灌區需求(cms)"],
                    "公共出水(萬噸/日)": row["公共出水(萬噸/日)"],
                    "調度狀態": "🟢 常態運作",
                    "原因備註": ""
                })
                
        df_final_demands = pd.DataFrame(final_demand_list)
        st.markdown("##### 📌 當前模擬區間各旬【出流需求】最終對照表")
        
        # 醒目強調：如果有人未填寫必填原因，給予紅底字警告
        empty_reason_count = sum(1 for item in final_demand_list if item["調度狀態"] == "⚡ 抗旱覆寫" and item["原因備註"] == "⚠️ 未填寫調整原因")
        if empty_reason_count > 0:
            st.error(f"⚠️ 警報：您有 **{empty_reason_count}** 處抗旱覆寫未填寫「覆寫原因備註」，請於上方確實填寫以符合防呆審核！")
            
        st.dataframe(df_final_demands, use_container_width=True)

# ==========================================
# 6. 旬度出流「展開至逐日」數據整合
# ==========================================
st.markdown("---")
st.subheader("📊 模擬期間「逐日出流總需求」展開明細")
st.markdown("""
本區塊會將上述設定好（包含抗旱覆寫）的各旬出流需求，完全展開至模擬區間的每一天，並依物理流量轉換為 **每日萬噸**。
轉換公式說明：
* **每日上/下灌區放水量（萬噸）** = $\\text{農業需求流量 (cms)} \\times 8.64$
* **每日生態基本放水量（萬噸）** = $\\text{鯉魚潭最低生態流量 (cms, 預設 0.3)} \\times 8.64$
* **每日公共出水量（萬噸）** = 直接帶入日需求值（萬噸/日）
* **當日出流總需求（萬噸）** = 上灌日水量 + 下灌日水量 + 生態日水量 + 公共日給水
""")

if not unique_periods.empty and 'df_final_demands' in locals():
    # 建立日曆時間剖面
    df_daily_outflow = generate_date_profile(st.session_state.start_date, st.session_state.end_date)
    
    # 建立 Lookup 字典
    out_lookup = {}
    for _, item in df_final_demands.iterrows():
        k = f"{int(item['年份'])}-{int(item['月份'])}-{item['旬別']}"
        out_lookup[k] = item
        
    # 逐日展開並計算
    up_irr_vol, down_irr_vol, pub_vol, eco_vol, total_out_vol, statuses, notes = [], [], [], [], [], [], []
    
    for _, row in df_daily_outflow.iterrows():
        k = f"{row['年份']}-{row['月份']}-{row['旬別']}"
        demand = out_lookup.get(k)
        
        if demand is not None:
            # 農業與生態流量轉日水量 (cms * 8.64)
            u_v = round(demand["上灌區需求(cms)"] * 8.64, 2)
            d_v = round(demand["下灌區需求(cms)"] * 8.64, 2)
            e_v = round(st.session_state.liyutan_eco_flow * 8.64, 2)  # 來自第一階段參數
            p_v = round(demand["公共出水(萬噸/日)"], 2)
            
            up_irr_vol.append(u_v)
            down_irr_vol.append(d_v)
            eco_vol.append(e_v)
            pub_vol.append(p_v)
            total_out_vol.append(round(u_v + d_v + e_v + p_v, 2))
            statuses.append(demand["調度狀態"])
            notes.append(demand["原因備註"])
        else:
            up_irr_vol.append(0.0)
            down_irr_vol.append(0.0)
            eco_vol.append(0.0)
            pub_vol.append(0.0)
            total_out_vol.append(0.0)
            statuses.append("未知")
            notes.append("")
            
    df_daily_outflow["上灌區日水量(萬噸)"] = up_irr_vol
    df_daily_outflow["下灌區日水量(萬噸)"] = down_irr_vol
    df_daily_outflow["生態放流日水量(萬噸)"] = eco_vol
    df_daily_outflow["公共供水日水量(萬噸)"] = pub_vol
    df_daily_outflow["當日出流總需求(萬噸)"] = total_out_vol
    df_daily_outflow["調度狀態"] = statuses
    df_daily_outflow["調整原因備註"] = notes
    
    # 計算加總數據
    tot_days = len(df_daily_outflow)
    tot_irr = round(df_daily_outflow["上灌區日水量(萬噸)"].sum() + df_daily_outflow["下灌區日水量(萬噸)"].sum(), 2)
    tot_pub = round(df_daily_outflow["公共供水日水量(萬噸)"].sum(), 2)
    tot_out_volume = round(df_daily_outflow["當日出流總需求(萬噸)"].sum(), 2)
    
    # 呈現關鍵指標 (Metrics)
    mo1, mo2, mo3 = st.columns(3)
    with mo1:
        st.metric("模擬總天數", f"{tot_days} 天")
    with mo2:
        st.metric("農業供水總需求", f"{tot_irr} 萬噸")
    with mo3:
        st.metric("公共與民生總給水需求", f"{tot_pub} 萬噸")
        
    st.markdown(f"💡 **出流總容積需求**：此模擬區間內，大台中與灌區合計共向鯉魚潭水庫/士林堰提出 **{tot_out_volume}** 萬噸的需求水量。")
    st.dataframe(df_daily_outflow, use_container_width=True, height=400)
    st.success("✅ 出流需求日剖面配置完成！至此，入流（天然水量）與出流（需求水量）皆已完整對齊。")