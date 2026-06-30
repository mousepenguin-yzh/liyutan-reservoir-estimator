import streamlit as st
import pandas as pd
import datetime
import calendar

# ==========================================
# 1. 核心物理與曆法引擎 (Calendar Engine)
# ==========================================

def generate_date_profile(start_date: datetime.date, end_date: datetime.date) -> pd.DataFrame:
    """
    根據起始日與結束日，生成逐日的時間剖面資料表。
    採用【左閉右開區間 [start_date, end_date)】：
    僅生成到 end_date - 1 天，結束日當天不進行日計算。
    """
    if start_date >= end_date:
        raise ValueError("起始日期不可大於或等於結束日期，請重新檢查您的時間區間。")
        
    dates = []
    curr = start_date
    # 左閉右開，排除 end_date 當天
    while curr < end_date:
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


def get_period_date_range(year: int, month: int, period: str) -> tuple:
    """
    計算特定年份、月份與旬別的「實際日曆起迄日期」。
    """
    if period == "上旬":
        return datetime.date(year, month, 1), datetime.date(year, month, 10)
    elif period == "中旬":
        return datetime.date(year, month, 11), datetime.date(year, month, 20)
    else:  # 下旬
        _, last_day = calendar.monthrange(year, month)
        return datetime.date(year, month, 21), datetime.date(year, month, last_day)


def is_overlapping(start1: datetime.date, end1: datetime.date, start2: datetime.date, end2: datetime.date) -> bool:
    """
    判斷兩個日期區間是否有重疊。
    """
    return max(start1, start2) <= min(end1, end2)


def get_historical_milestone_dates_v2(disp_start: datetime.date, proj_start: datetime.date) -> list:
    """
    獲取歷史展示區間的「旬末邊界日」列表。
    包含：展示前一日(disp_start - 1)、推估前一日(proj_start - 1)，
    以及展示區間內所有的旬末日（10日、20日、月底日）。
    """
    milestones = set()
    start_bound = disp_start - datetime.timedelta(days=1)
    end_bound = proj_start - datetime.timedelta(days=1)
    
    milestones.add(start_bound)
    milestones.add(end_bound)
    
    # 遍歷展示期內的每一天，尋找標準旬末日
    curr = disp_start
    while curr < proj_start:
        is_end_of_period = False
        if curr.day in [10, 20]:
            is_end_of_period = True
        else:
            _, last_day = calendar.monthrange(curr.year, curr.month)
            if curr.day == last_day:
                is_end_of_period = True
                
        if is_end_of_period:
            milestones.add(curr)
        curr += datetime.timedelta(days=1)
        
    return sorted(list(milestones))


def interpolate_historical_capacities_v2(disp_start: datetime.date, proj_start: datetime.date, cap_dict: dict, init_capacity: float) -> dict:
    """
    在 [disp_start - 1, proj_start - 1] 區間內進行線性插值。
    cap_dict 包含其餘歷史旬末點的數值。
    init_capacity 為 proj_start - 1 當天 24:00 的數值（鎖定為模擬起點）。
    """
    start_bound = disp_start - datetime.timedelta(days=1)
    end_bound = proj_start - datetime.timedelta(days=1)
    
    # 建立完整的 milestone-value 對應表
    full_caps = {}
    for k, v in cap_dict.items():
        try:
            d = datetime.datetime.strptime(k, "%Y-%m-%d").date()
            full_caps[d] = v
        except ValueError:
            continue
    
    # 強制將「推估起始前一日」鎖定為 init_capacity (重要！)
    full_caps[end_bound] = init_capacity
    
    milestones = sorted(list(full_caps.keys()))
    # 確保範圍在 [start_bound, end_bound] 之內
    milestones = [m for m in milestones if start_bound <= m <= end_bound]
    
    daily_caps = {}
    for i in range(len(milestones) - 1):
        d1 = milestones[i]
        d2 = milestones[i+1]
        val1 = full_caps.get(d1, 8000.0)
        val2 = full_caps.get(d2, 8000.0)
        
        days_diff = (d2 - d1).days
        for step in range(days_diff + 1):
            curr_d = d1 + datetime.timedelta(days=step)
            if days_diff == 0:
                daily_caps[curr_d] = val1
            else:
                ratio = step / days_diff
                daily_caps[curr_d] = round(val1 + (val2 - val1) * ratio, 2)
    return daily_caps

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
    """
    irrigation_schedule = {
        1: (0.5, 0.8),   2: (3.0, 4.5),   3: (4.0, 5.5),   4: (3.5, 5.0),
        5: (2.5, 3.5),   6: (3.5, 4.8),   7: (4.0, 5.5),   8: (4.5, 6.0),
        9: (3.5, 5.0),   10: (2.0, 3.0),  11: (1.0, 1.5),  12: (0.5, 0.8)
    }
    up_irr, down_irr = irrigation_schedule.get(month, (1.0, 1.5))
    public_water = 60.0  # 預設台中常態公共給水 60 萬噸/日
    return {"up_irr": up_irr, "down_irr": down_irr, "public": public_water}

# ==========================================
# 4. Streamlit 初始化與會話狀態
# ==========================================

st.set_page_config(
    page_title="鯉魚潭水庫庫容推估系統 (數據對齊調校版)",
    page_icon="💧",
    layout="wide"
)

# 基礎參數與雙時間軸日期初始化
if "max_capacity" not in st.session_state:
    st.session_state.max_capacity = 11584.0
if "shilin_eco_flow" not in st.session_state:
    st.session_state.shilin_eco_flow = 2.2
if "liyutan_eco_flow" not in st.session_state:
    st.session_state.liyutan_eco_flow = 0.3

if "display_start_date" not in st.session_state:
    st.session_state.display_start_date = datetime.date(2026, 5, 1)
if "start_date" not in st.session_state:
    st.session_state.start_date = datetime.date(2026, 6, 21)
if "end_date" not in st.session_state:
    st.session_state.end_date = datetime.date(2026, 9, 1)

if "init_capacity" not in st.session_state:
    st.session_state.init_capacity = 8000.0
if "inflow_source" not in st.session_state:
    st.session_state.inflow_source = "內建標準水文情境 (Q5~Q95)"
if "builtin_scenario" not in st.session_state:
    st.session_state.builtin_scenario = "Q50 (平水)"
if "manual_flow_dict" not in st.session_state:
    st.session_state.manual_flow_dict = {}
if "override_list" not in st.session_state:
    st.session_state.override_list = []
if "hist_capacity" not in st.session_state:
    st.session_state.hist_capacity = {}

# ==========================================
# 5. 前端 UI 分頁排版
# ==========================================

st.title("💧 鯉魚潭水庫庫容推估系統 (數據對齊調校版)")
st.markdown("""
本版本已將曆法計算重構為**「左閉右開區間 $[D_{\\text{start}}, D_{\\text{end}})$」**，結束日當天不計入演算。
您可以直接在此對齊與您 Excel 檔案的各項數據。
""")

tab_config, tab_inflow, tab_outflow, tab_simulation = st.tabs([
    "⚙️ 第一階段：基礎參數與曆法", 
    "🌊 第二階段：入流條件設定",
    "🚰 第三階段：出流需求與抗旱調整",
    "🧮 第四階段：核心庫容守恆演算"
])

# -----------------
# TAB 1: 基礎與曆法
# -----------------
with tab_config:
    st.subheader("⚙️ 基礎參數與雙時間軸對齊")
    col1, col2 = st.columns(2)
    with col1:
        st.markdown("##### 🏛️ 水庫與堰體物理限制")
        st.session_state.max_capacity = st.number_input("水庫上限庫容 (萬噸)", min_value=100.0, max_value=20000.0, value=st.session_state.max_capacity, step=100.0)
        st.session_state.shilin_eco_flow = st.number_input("士林堰生態基流量 (cms)", min_value=0.0, max_value=10.0, value=st.session_state.shilin_eco_flow, step=0.1)
        st.session_state.liyutan_eco_flow = st.number_input("鯉魚潭最低生態放流量 (cms)", min_value=0.0, max_value=5.0, value=st.session_state.liyutan_eco_flow, step=0.05)
    with col2:
        st.markdown("##### 📅 雙時間軸日期設定")
        st.session_state.display_start_date = st.date_input("展示起始日期", value=st.session_state.display_start_date)
        st.session_state.start_date = st.date_input("推估起始日期 (物理模擬起點)", value=st.session_state.start_date)
        st.session_state.end_date = st.date_input("預計推估結束日期 (此日不計入日計算)", value=st.session_state.end_date)
        
        # 檢驗日期先後關係
        if st.session_state.display_start_date > st.session_state.start_date:
            st.error("⚠️ 錯誤：『展示起始日期』不可晚於『推估起始日期』。")
        if st.session_state.start_date >= st.session_state.end_date:
            st.error("⚠️ 錯誤：『推估起始日期』必須早於『預計推估結束日期』。")
            
        # 【修正 1】最下方的輸入框嚴格綁定為：推估起點前一日 24:00 (例如：6/20 24:00)
        calc_start_day = st.session_state.start_date
        prev_day = calc_start_day - datetime.timedelta(days=1)
        prev_day_label = f"推估起點前一日 ({prev_day.strftime('%m/%d')} 24:00) 庫容 (萬噸)"
        
        st.session_state.init_capacity = st.number_input(prev_day_label, min_value=0.0, max_value=st.session_state.max_capacity, value=st.session_state.init_capacity, step=10.0)

    # 處理展示期（歷史觀測期）的逐旬蓄水量輸入
    if st.session_state.display_start_date < st.session_state.start_date:
        st.markdown("---")
        st.markdown("##### 📈 展示區間歷史蓄水量輸入")
        st.caption("請輸入展示期間內，各旬末日前一日 24:00 的實際蓄水量 (萬噸)：")
        
        milestones = get_historical_milestone_dates_v2(st.session_state.display_start_date, st.session_state.start_date)
        
        # 排除最後一個邊界日（因為它已經被最下方的推估起點庫容鎖定了，防止重複輸入衝突）
        end_boundary = st.session_state.start_date - datetime.timedelta(days=1)
        other_milestones = [m for m in milestones if m != end_boundary]
        
        if other_milestones:
            cols_num = min(4, len(other_milestones))
            m_cols = st.columns(cols_num)
            
            for idx, m_date in enumerate(other_milestones):
                col_idx = idx % cols_num
                m_label = f"{m_date.strftime('%m/%d')} 24:00 蓄水量"
                # 給予合理的預設值
                default_v = st.session_state.hist_capacity.get(m_date.strftime('%Y-%m-%d'), st.session_state.init_capacity)
                st.session_state.hist_capacity[m_date.strftime('%Y-%m-%d')] = m_cols[col_idx].number_input(
                    m_label, 
                    min_value=0.0, 
                    max_value=st.session_state.max_capacity, 
                    value=default_v, 
                    step=50.0, 
                    key=f"active_hist_{m_date}"
                )

    # 生成總時間剖面 (左閉右開，用於第一階段統計參考)
    if st.session_state.display_start_date < st.session_state.end_date and st.session_state.start_date < st.session_state.end_date:
        df_cal = generate_date_profile(st.session_state.display_start_date, st.session_state.end_date)
        unique_periods = df_cal.groupby(["年份", "月份", "旬別"]).size().reset_index().drop(columns=[0])
        
        # 中文旬別排序
        period_order = {"上旬": 1, "中旬": 2, "下旬": 3}
        unique_periods["旬別順序碼"] = unique_periods["旬別"].map(period_order)
        unique_periods = unique_periods.sort_values(by=["年份", "月份", "旬別順序碼"]).drop(columns=["旬別順序碼"]).reset_index(drop=True)
        
        st.success(f"📅 曆法配置成功：當前展示+推估計算區間（左閉右開）共計 **{len(df_cal)}** 天。")
    else:
        unique_periods = pd.DataFrame()

    # 計算「未來推估期」所專屬跨越的旬別 (proj_unique_periods)
    if st.session_state.start_date < st.session_state.end_date:
        df_proj_cal = generate_date_profile(st.session_state.start_date, st.session_state.end_date)
        proj_unique_periods = df_proj_cal.groupby(["年份", "月份", "旬別"]).size().reset_index().drop(columns=[0])
        
        period_order = {"上旬": 1, "中旬": 2, "下旬": 3}
        proj_unique_periods["旬別順序碼"] = proj_unique_periods["旬別"].map(period_order)
        proj_unique_periods = proj_unique_periods.sort_values(by=["年份", "月份", "旬別順序碼"]).drop(columns=["旬別順序碼"]).reset_index(drop=True)
    else:
        proj_unique_periods = pd.DataFrame()

# -----------------
# TAB 2: 第二階段入流
# -----------------
with tab_inflow:
    st.subheader("🌊 入流條件與流量解析 (未來推估期專用)")
    if proj_unique_periods.empty:
        st.warning("⚠️ 請先返回第一階段，設定正確的模擬日期區間。")
    else:
        inflow_mode = st.radio("請選擇天然流量 (cms) 來源模式：", ["內建標準水文情境 (Q5~Q95)", "手動批次匯入（支援 Excel 複製貼上）"], horizontal=True)
        st.session_state.inflow_source = inflow_mode
        period_flow_mapping = []
        
        if inflow_mode == "內建標準水文情境 (Q5~Q95)":
            selected_scen = st.selectbox("請選擇水文情境：", ["Q5 (極豐水)", "Q20 (偏豐水)", "Q50 (平水)", "Q75 (偏枯水)", "Q95 (特枯水)"], index=["Q5 (極豐水)", "Q20 (偏豐水)", "Q50 (平水)", "Q75 (偏枯水)", "Q95 (特枯水)"].index(st.session_state.builtin_scenario))
            st.session_state.builtin_scenario = selected_scen
            for idx, row in proj_unique_periods.iterrows():
                y, m, p = row["年份"], row["月份"], row["旬別"]
                flow_val = get_builtin_shilin_flow(m, p, selected_scen)
                period_flow_mapping.append({"年份": y, "月份": m, "旬別": p, "天然流量(cms)": flow_val})
        else:
            st.markdown("##### 📥 Excel 數據批次貼上區")
            dummy_data_list = [round(get_builtin_shilin_flow(row["月份"], row["旬別"], "Q50 (平水)"), 1) for _, row in proj_unique_periods.iterrows()]
            dummy_paste_str = "\t".join(map(str, dummy_data_list))
            st.caption(f"💡 測試範例串（共 {len(dummy_data_list)} 個數值）： `{dummy_paste_str}` (您可直接複製此串進行貼上測試)")
            
            pasted_text = st.text_area("請在此貼上 Excel 數據 (以空格、Tab或換行分隔)：", placeholder="例如: 12.5  14.2  10.1 ...", height=80, key="inflow_paste")
            parsed_list = parse_pasted_data(pasted_text)
            
            if pasted_text.strip():
                if len(parsed_list) != len(proj_unique_periods):
                    st.error(f"❌ 解析失敗：您貼上的數據個數（{len(parsed_list)} 筆）與當前區間所需（{len(proj_unique_periods)} 筆）不符！")
                    for i, (_, row) in enumerate(proj_unique_periods.iterrows()):
                        period_flow_mapping.append({"年份": row["年份"], "月份": row["月份"], "旬別": row["旬別"], "天然流量(cms)": get_builtin_shilin_flow(row["月份"], row["旬別"], "Q50 (平水)")})
                else:
                    st.success("✅ 數據成功解析並對齊！")
                    for i, (_, row) in enumerate(proj_unique_periods.iterrows()):
                        y, m, p = row["年份"], row["月份"], row["旬別"]
                        flow_val = parsed_list[i]
                        st.session_state.manual_flow_dict[f"{y}-{m}-{p}"] = flow_val
                        period_flow_mapping.append({"年份": y, "月份": m, "旬別": p, "天然流量(cms)": flow_val})
            else:
                st.info("⚠️ 尚未貼上數據，下方目前顯示內建 Q50 預設值作為參考占位。")
                for idx, row in proj_unique_periods.iterrows():
                    period_flow_mapping.append({"年份": row["年份"], "月份": row["月份"], "旬別": row["旬別"], "天然流量(cms)": get_builtin_shilin_flow(row["月份"], row["旬別"], "Q50 (平水)")})

        df_period_flow = pd.DataFrame(period_flow_mapping)
        st.dataframe(df_period_flow, use_container_width=True)

# -----------------
# TAB 3: 第三階段出流
# -----------------
with tab_outflow:
    st.subheader("🚰 出流標的需求配置與抗旱調整 (未來推估期專用)")
    if proj_unique_periods.empty:
        st.warning("⚠️ 請先返回第一階段，設定正確的模擬日期區間。")
    else:
        outflow_mode = st.radio("請選擇常態出流配置模式：", ["使用歷史常態預設需求值", "自訂出流需求（支援 Excel 複製貼上）"], horizontal=True, key="outflow_mode_radio")
        
        base_demand_list = []
        if outflow_mode == "使用歷史常態預設需求值":
            for idx, row in proj_unique_periods.iterrows():
                y, m, p = row["年份"], row["月份"], row["旬別"]
                def_demands = get_default_demands(m)
                base_demand_list.append({
                    "年份": y, "月份": m, "旬別": p,
                    "上灌區需求(cms)": def_demands["up_irr"],
                    "下灌區需求(cms)": def_demands["down_irr"],
                    "公共出水(萬噸/日)": def_demands["public"]
                })
            st.info("💡 系統已自動帶入歷史常態平水期供水需求。")
        else:
            st.markdown("##### 📥 Excel 需求數據批次匯入")
            def_up_list = [get_default_demands(r["月份"])["up_irr"] for _, r in proj_unique_periods.iterrows()]
            def_down_list = [get_default_demands(r["月份"])["down_irr"] for _, r in proj_unique_periods.iterrows()]
            def_pub_list = [get_default_demands(r["月份"])["public"] for _, r in proj_unique_periods.iterrows()]
            
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
            
            for i, (_, row) in enumerate(proj_unique_periods.iterrows()):
                y, m, p = row["年份"], row["月份"], row["旬別"]
                def_val = get_default_demands(m)
                u_val = parsed_up[i] if (len(parsed_up) == len(proj_unique_periods)) else def_val["up_irr"]
                d_val = parsed_down[i] if (len(parsed_down) == len(proj_unique_periods)) else def_val["down_irr"]
                p_val = parsed_pub[i] if (len(parsed_pub) == len(proj_unique_periods)) else def_val["public"]
                
                base_demand_list.append({
                    "年份": y, "月份": m, "旬別": p,
                    "上灌區需求(cms)": u_val,
                    "下灌區需求(cms)": d_val,
                    "公共出水(萬噸/日)": p_val
                })
                
            if paste_up.strip() or paste_down.strip() or paste_pub.strip():
                if len(parsed_up) != len(proj_unique_periods) or len(parsed_down) != len(proj_unique_periods) or len(parsed_pub) != len(proj_unique_periods):
                    st.warning("⚠️ 貼入之數據筆數與推估區間需求不符，不符之欄位已自動套用預設值。")
                else:
                    st.success("✅ 三大標的出流需求皆已成功解析並載入！")

        df_base_demands = pd.DataFrame(base_demand_list)
        
        # ⚡ 歷史枯旱期/臨時調度自訂日期覆寫清單 ⚡
        st.markdown("---")
        st.markdown("#### ⚡ 歷史枯旱期/臨時調度自訂日期覆寫清單")
        
        enable_override = st.checkbox("啟用抗旱臨時日期覆寫機制", value=False)
        
        if enable_override:
            if st.button("➕ 新增抗旱覆寫時段"):
                st.session_state.override_list.append({
                    "start": st.session_state.start_date,
                    "end": st.session_state.start_date + datetime.timedelta(days=7),
                    "up_irr": 0.0,
                    "down_irr": 0.0,
                    "public": 45.0,
                    "reason": "抗旱一級減壓"
                })
            
            if st.session_state.override_list:
                to_delete = []
                for idx, ov in enumerate(st.session_state.override_list):
                    st.markdown(f"**🔴 覆寫規則設定 #{idx + 1}**")
                    col_dates, col_vals, col_act = st.columns([3, 4, 1])
                    
                    with col_dates:
                        ov["start"] = st.date_input(f"起日 #{idx+1}", value=ov["start"], key=f"ov_start_{idx}")
                        ov["end"] = st.date_input(f"迄日 #{idx+1}", value=ov["end"], key=f"ov_end_{idx}")
                        if ov["start"] > ov["end"]:
                            st.error(f"⚠️ 錯誤：規則 #{idx+1} 的起日不可晚於迄日。")
                            
                    with col_vals:
                        ov["up_irr"] = st.number_input(f"上灌 (cms) #{idx+1}", value=ov["up_irr"], step=0.1, key=f"ov_up_{idx}")
                        ov["down_irr"] = st.number_input(f"下灌 (cms) #{idx+1}", value=ov["down_irr"], step=0.1, key=f"ov_down_{idx}")
                        ov["public"] = st.number_input(f"公共 (萬噸/日) #{idx+1}", value=ov["public"], step=1.0, key=f"ov_pub_{idx}")
                        ov["reason"] = st.text_input(f"覆寫原因/備註 (必填) #{idx+1}", value=ov["reason"], key=f"ov_reason_{idx}", placeholder="例：配合抗旱打折")
                        
                    with col_act:
                        st.markdown("<br><br>", unsafe_allow_html=True)
                        if st.button("🗑️ 刪除此時段", key=f"ov_del_{idx}"):
                            to_delete.append(idx)
                    st.markdown("<hr style='border:1px dashed #cccccc;'>", unsafe_allow_html=True)
                
                if to_delete:
                    for i in reversed(to_delete):
                        st.session_state.override_list.pop(i)
                    st.rerun()
            else:
                st.info("💡 目前無任何覆寫規則，請點擊上方按鈕新增覆寫。")
        else:
            st.session_state.override_list = []

        # 這裡生成包含展示期與推估期的完整日曆需求，方便在模擬中抓取
        df_daily_outflow = generate_date_profile(st.session_state.display_start_date, st.session_state.end_date)
        
        base_lookup = {}
        for _, item in df_base_demands.iterrows():
            k = f"{int(item['年份'])}-{int(item['月份'])}-{item['旬別']}"
            base_lookup[k] = item
            
        up_irr_cms, down_irr_cms, pub_vol_list, eco_cms_list = [], [], [], []
        up_irr_vol, down_irr_vol, pub_vol, eco_vol, total_out_vol, statuses, notes = [], [], [], [], [], [], []
        
        for _, row in df_daily_outflow.iterrows():
            current_date = row["日期"]
            k = f"{row['年份']}-{row['月份']}-{row['旬別']}"
            base_demand = base_lookup.get(k)
            
            # 展示期間不受出水影響（設為 0），推估期間才對齊
            active_up_cms = base_demand["上灌區需求(cms)"] if base_demand is not None else 0.0
            active_down_cms = base_demand["下灌區需求(cms)"] if base_demand is not None else 0.0
            active_pub_vol = base_demand["公共出水(萬噸/日)"] if base_demand is not None else 0.0
            day_status = "🟢 常態運作"
            day_note = ""
            
            if current_date >= st.session_state.start_date:
                if enable_override and st.session_state.override_list:
                    for ov in st.session_state.override_list:
                        if ov["start"] <= current_date <= ov["end"]:
                            active_up_cms = ov["up_irr"]
                            active_down_cms = ov["down_irr"]
                            active_pub_vol = ov["public"]
                            day_status = "⚡ 抗旱覆寫"
                            day_note = f"[{ov['start'].strftime('%m/%d')}~{ov['end'].strftime('%m/%d')} 覆寫] {ov['reason']}"
            else:
                day_status = "🟢 展示歷史"
                day_note = "展示實際值不參與演算"
            
            up_irr_cms.append(active_up_cms)
            down_irr_cms.append(active_down_cms)
            pub_vol_list.append(active_pub_vol)
            eco_cms_list.append(st.session_state.liyutan_eco_flow)
            
            u_v = round(active_up_cms * 8.64, 2)
            d_v = round(active_down_cms * 8.64, 2)
            e_v = round(st.session_state.liyutan_eco_flow * 8.64, 2)
            p_v = round(active_pub_vol, 2)
            
            up_irr_vol.append(u_v)
            down_irr_vol.append(d_v)
            eco_vol.append(e_v)
            pub_vol.append(p_v)
            total_out_vol.append(round(u_v + d_v + e_v + p_v, 2))
            statuses.append(day_status)
            notes.append(day_note)
                
        df_daily_outflow["上灌區當日流量(cms)"] = up_irr_cms
        df_daily_outflow["下灌區當日流量(cms)"] = down_irr_cms
        df_daily_outflow["公共供水當日水量(萬噸)"] = pub_vol_list
        df_daily_outflow["上灌區日水量(萬噸)"] = up_irr_vol
        df_daily_outflow["下灌區日水量(萬噸)"] = down_irr_vol
        df_daily_outflow["生態放流日水量(萬噸)"] = eco_vol
        df_daily_outflow["公共供水日水量(萬噸)"] = pub_vol
        df_daily_outflow["當日出流總需求(萬噸)"] = total_out_vol
        df_daily_outflow["調度狀態"] = statuses
        df_daily_outflow["今日抗旱備註"] = notes

        # 日轉旬回推彙整
        df_grouped = df_daily_outflow[df_daily_outflow["日期"] >= st.session_state.start_date].groupby(["年份", "月份", "旬別"]).agg(
            up_mean=("上灌區當日流量(cms)", "mean"),
            down_mean=("下灌區當日流量(cms)", "mean"),
            pub_mean=("公共供水當日水量(萬噸)", "mean"),
            override_count=("調度狀態", lambda x: (x == "⚡ 抗旱覆寫").sum())
        ).reset_index()

        final_demand_list = []
        for idx, row in df_grouped.iterrows():
            y, m, p = row["年份"], row["月份"], row["旬別"]
            p_start, p_end = get_period_date_range(int(y), int(m), p)
            
            overlapping_notes = []
            is_overridden_period = row["override_count"] > 0
            
            if enable_override and st.session_state.override_list:
                bullet_num = 1
                for ov in st.session_state.override_list:
                    if is_overlapping(p_start, p_end, ov["start"], ov["end"]):
                        start_str = ov["start"].strftime("%m/%d")
                        end_str = ov["end"].strftime("%m/%d")
                        reason_text = ov["reason"].strip() if ov["reason"].strip() else "⚠️ 未填寫調整原因"
                        overlapping_notes.append(f"{bullet_num}. {start_str}~{end_str}: {reason_text}")
                        bullet_num += 1
            
            final_note = " \n ".join(overlapping_notes) if is_overridden_period else ""
            status_text = "⚡ 部分/全部抗旱覆寫" if is_overridden_period else "🟢 常態運作"
            
            final_demand_list.append({
                "年份": y, "月份": m, "旬別": p,
                "上灌區需求 (cms, 旬加權均值)": round(row["up_mean"], 2),
                "下灌區需求 (cms, 旬加權均值)": round(row["down_mean"], 2),
                "公共出水 (萬噸/日, 旬加權均值)": round(row["pub_mean"], 1),
                "調度狀態": status_text,
                "原因備註 (條列)": final_note
            })
            
        df_final_demands = pd.DataFrame(final_demand_list)
        
        # 中文旬別排序
        period_order = {"上旬": 1, "中旬": 2, "下旬": 3}
        df_final_demands["旬別順序碼"] = df_final_demands["旬別"].map(period_order)
        df_final_demands = df_final_demands.sort_values(by=["年份", "月份", "旬別順序碼"]).drop(columns=["旬別順序碼"])
        
        st.markdown("##### 📌 當前【未來推估期】各旬【常態與抗旱日期權重均值】匯總報表")
        st.dataframe(
            df_final_demands,
            use_container_width=True,
            column_config={
                "原因備註 (條列)": st.column_config.TextColumn("原因備註 (條列)", width="large")
            }
        )

# -----------------
# TAB 4: 第四階段：核心庫容守恆演算
# -----------------
with tab_simulation:
    st.subheader("🧮 鯉魚潭水庫質量守恆與防線調度計算")
    st.markdown("""
    本模組為系統最核心的**物理演算引擎**。點擊下方按鈕將啟動日步進 Loop，特別依據**「上灌區第一優先滿足，下灌區天然流量剩餘分配」**原則演算。
    """)
    
    if proj_unique_periods.empty:
        st.warning("⚠️ 請先返回第一階段，設定正確的模擬日期區間。")
    elif 'df_period_flow' not in locals() or 'df_daily_outflow' not in locals():
        st.warning("⚠️ 請確保已完成第一至三階段的入流與出流條件設定。")
    else:
        if st.button("▶️ 執行庫容守恆模擬", type="primary"):
            
            max_capacity = st.session_state.max_capacity
            shilin_eco = st.session_state.shilin_eco_flow
            liyutan_eco = st.session_state.liyutan_eco_flow
            
            # 【修正 2】歷史區間插值重構：末端自動鎖定為 init_capacity (模擬起點)，徹底消除漂移
            has_history = st.session_state.display_start_date < st.session_state.start_date
            daily_hist_caps = {}
            if has_history:
                daily_hist_caps = interpolate_historical_capacities_v2(
                    st.session_state.display_start_date, 
                    st.session_state.start_date, 
                    st.session_state.hist_capacity,
                    st.session_state.init_capacity
                )
            
            # 初始庫容：鎖定在展示前一日 24:00 的實際庫容 (若有歷史)，否則直接鎖定 init_capacity
            if has_history:
                curr_capacity = daily_hist_caps.get(
                    st.session_state.display_start_date - datetime.timedelta(days=1), 
                    st.session_state.init_capacity
                )
            else:
                curr_capacity = st.session_state.init_capacity
            
            sim_daily_records = []
            total_ag_intercept_volume_10k = 0.0
            total_spillway_overflow_10k = 0.0
            
            # 依據 [展示起始日, 結束日) 生成每日時間表
            df_daily_profile = generate_date_profile(st.session_state.display_start_date, st.session_state.end_date)
            
            flow_lookup = {}
            for _, item in df_period_flow.iterrows():
                key = f"{int(item['年份'])}-{int(item['月份'])}-{item['旬別']}"
                flow_lookup[key] = item["天然流量(cms)"]
                
            for _, row in df_daily_profile.iterrows():
                current_date = row["日期"]
                key = f"{row['年份']}-{row['月份']}-{row['旬別']}"
                
                # 判定當前日期屬於「觀測展示期」還是「未來推估期」
                is_projection = current_date >= st.session_state.start_date
                
                if not is_projection:
                    # ==========================================
                    # 觀測展示期 (無物理演算，庫容直接採用插值實際值)
                    # ==========================================
                    yesterday_capacity = curr_capacity
                    curr_capacity = daily_hist_caps.get(current_date, yesterday_capacity)
                    net_change_vol = round(curr_capacity - yesterday_capacity, 2)
                    
                    sim_daily_records.append({
                        "日期": current_date,
                        "年份": row["年份"],
                        "月份": row["月份"],
                        "旬別": row["旬別"],
                        "運行狀態": "📊 觀測/歷史",
                        "天然流量 (cms)": 0.0,
                        "原上灌需求 (cms)": 0.0,
                        "原下灌需求 (cms)": 0.0,
                        "實際上灌放水 (cms)": 0.0,
                        "實際下灌放水 (cms)": 0.0,
                        "農業削減狀態": "🟢 觀測",
                        "農業削減量 (cms)": 0.0,
                        "士林堰河道保留 (cms)": 0.0,
                        "實際引水流量 (cms)": 0.0,
                        "今日引入量 (萬噸)": 0.0,
                        "大壩河道放流 (cms)": 0.0,
                        "公共給水量 (萬噸)": 0.0,
                        "今日出水總量 (萬噸)": 0.0,
                        "溢流量 (萬噸)": 0.0,
                        "昨日期末庫容 (萬噸)": round(yesterday_capacity, 2),
                        "本日末庫容 (萬噸)": round(curr_capacity, 2),
                        "當日庫容淨變化 (萬噸)": net_change_vol
                    })
                else:
                    # ==========================================
                    # 未來推估期 (啟用質量守恆物理引擎)
                    # ==========================================
                    I_cms = flow_lookup.get(key, 0.0)
                    
                    # 抓取推估日需求
                    out_row_candidates = df_daily_outflow[df_daily_outflow["日期"] == current_date]
                    if out_row_candidates.empty:
                        U_cms, D_cms, P_vol = 0.0, 0.0, 0.0
                    else:
                        out_row = out_row_candidates.iloc[0]
                        U_cms = out_row["上灌區當日流量(cms)"]
                        D_cms = out_row["下灌區當日流量(cms)"]
                        P_vol = out_row["公共供水當日水量(萬噸)"]
                    
                    # 物理防線 1：上灌/下灌 優先權分配 (農業控制律)
                    actual_U_cms = min(U_cms, I_cms)
                    remaining_flow_cms = max(0.0, I_cms - actual_U_cms)
                    actual_D_cms = min(D_cms, remaining_flow_cms)
                    
                    ag_control_triggered = (actual_U_cms < U_cms) or (actual_D_cms < D_cms)
                    reduction_cms = (U_cms + D_cms) - (actual_U_cms + actual_D_cms)
                    total_ag_intercept_volume_10k += (reduction_cms * 8.64)
                    
                    # 物理防線 2：士林堰引水計算 & 生態基流折抵
                    shilin_river_release_cms = min(I_cms, max(shilin_eco, actual_U_cms))
                    available_diversion_cms = max(0.0, I_cms - shilin_river_release_cms)
                    actual_diversion_cms = min(33.0, available_diversion_cms)
                    actual_diversion_vol = round(actual_diversion_cms * 8.64, 2)
                    
                    # 物理防線 3：鯉魚潭出流計算 & 生態最低放流折抵
                    liyutan_river_release_cms = max(liyutan_eco, actual_D_cms)
                    liyutan_river_release_vol = round(liyutan_river_release_cms * 8.64, 2)
                    actual_outflow_vol = round(P_vol + liyutan_river_release_vol, 2)
                    
                    # 物理防線 4：庫容質量守恆演算
                    yesterday_capacity = curr_capacity
                    calculated_capacity = yesterday_capacity + actual_diversion_vol - actual_outflow_vol
                    
                    spillway_overflow_vol = 0.0
                    if calculated_capacity > max_capacity:
                        spillway_overflow_vol = round(calculated_capacity - max_capacity, 2)
                        total_spillway_overflow_10k += spillway_overflow_vol
                        curr_capacity = max_capacity
                    elif calculated_capacity < 0:
                        curr_capacity = 0.0
                    else:
                        curr_capacity = round(calculated_capacity, 2)
                    
                    net_change_vol = round(curr_capacity - yesterday_capacity, 2)
                    
                    sim_daily_records.append({
                        "日期": current_date,
                        "年份": row["年份"],
                        "月份": row["月份"],
                        "旬別": row["旬別"],
                        "運行狀態": "🔮 未來推估",
                        "天然流量 (cms)": I_cms,
                        "原上灌需求 (cms)": U_cms,
                        "原下灌需求 (cms)": D_cms,
                        "實際上灌放水 (cms)": round(actual_U_cms, 2),
                        "實際下灌放水 (cms)": round(actual_D_cms, 2),
                        "農業削減狀態": "🚨 觸發削減" if ag_control_triggered else "🟢 正常",
                        "農業削減量 (cms)": round(reduction_cms, 2),
                        "士林堰河道保留 (cms)": round(shilin_river_release_cms, 2),
                        "實際引水流量 (cms)": round(actual_diversion_cms, 2),
                        "今日引入量 (萬噸)": actual_diversion_vol,
                        "大壩河道放流 (cms)": round(liyutan_river_release_cms, 2),
                        "公共給水量 (萬噸)": round(P_vol, 2),
                        "今日出水總量 (萬噸)": actual_outflow_vol,
                        "溢流量 (萬噸)": spillway_overflow_vol,
                        "昨日期末庫容 (萬噸)": round(yesterday_capacity, 2),
                        "本日末庫容 (萬噸)": round(curr_capacity, 2),
                        "當日庫容淨變化 (萬噸)": net_change_vol
                    })
            
            df_sim_results = pd.DataFrame(sim_daily_records)
            st.session_state.sim_results = df_sim_results
            
            st.markdown("### 🏆 庫容模擬計算完成！調度成果指標如下：")
            m1, m2, m3, m4 = st.columns(4)
            with m1:
                final_volume = df_sim_results.iloc[-1]["本日末庫容 (萬噸)"]
                st.metric("模擬期末庫容", f"{final_volume} 萬噸", delta=f"{round(final_volume - st.session_state.init_capacity, 1)} 萬噸 (較期初)")
            with m2:
                st.metric("累積溢流量 (Spill)", f"{round(total_spillway_overflow_10k, 1)} 萬噸")
            with m3:
                st.metric("農業控制律削減總水量", f"{round(total_ag_intercept_volume_10k, 1)} 萬噸")
            with m4:
                dry_days = sum(1 for item in sim_daily_records if item["本日末庫容 (萬噸)"] <= 0.0 and item["運行狀態"] == "🔮 未來推估")
                st.metric("庫容枯竭空庫天數", f"{dry_days} 天", delta="🚨 警告：空庫枯竭！" if dry_days > 0 else "🟢 安全")

            # -----------------
            # 產品一：水利署標準「旬度加總」彙整報表 (萬噸單位)
            # -----------------
            st.markdown("---")
            st.markdown("#### 📅 產品一：水利署標準「旬度加總/平均」彙整報表")
            st.caption("說明：用於直接複製貼上水利署彙整表。")
            
            df_grouped_sim = df_sim_results.groupby(["年份", "月份", "旬別"], sort=False).agg(
                天然流量_cms=("天然流量 (cms)", "mean"),
                實際引水流量_cms=("實際引水流量 (cms)", "mean"),
                累計引入量_萬噸=("今日引入量 (萬噸)", "sum"),
                上灌區供灌總量_萬噸=("實際上灌放水 (cms)", lambda x: round((x * 8.64).sum(), 2)),
                下灌區供灌總量_萬噸=("實際下灌放水 (cms)", lambda x: round((x * 8.64).sum(), 2)),
                公共用水總量_萬噸=("公共給水量 (萬噸)", "sum"),
                累計出水總量_萬噸=("今日出水總量 (萬噸)", "sum"),
                累計溢流量_萬噸=("溢流量 (萬噸)", "sum"),
                累計庫容淨變化_萬噸=("當日庫容淨變化 (萬噸)", "sum"),
                期末庫容_萬噸=("本日末庫容 (萬噸)", "last")
            ).reset_index()
            
            df_grouped_sim.columns = [
                "年份", "月份", "旬別",
                "天然流量 (cms, 旬均值)",
                "實際引水流量 (cms, 旬均值)",
                "累計引入量 (萬噸)",
                "上灌區供灌總量 (萬噸)",
                "下灌區供灌總量 (萬噸)",
                "公共用水總量 (萬噸)",
                "累計出水總量 (萬噸)",
                "累計溢流量 (萬噸)",
                "累計庫容淨變化 (萬噸)",
                "期末庫容 (萬噸)"
            ]
            
            # 強制排序
            df_grouped_sim["旬別順序碼"] = df_grouped_sim["旬別"].map(period_order)
            df_grouped_sim = df_grouped_sim.sort_values(by=["年份", "月份", "旬別順序碼"]).drop(columns=["旬別順序碼"]).reset_index(drop=True)
            
            st.dataframe(df_grouped_sim, use_container_width=True)
            
            csv_data_period = df_grouped_sim.to_csv(index=False).encode('utf-8-sig')
            st.download_button(
                label="📥 下載 產品一：旬度彙整報表 (Excel 貼上專用)",
                data=csv_data_period,
                file_name=f"liyutan_summary_by_period_{datetime.date.today().strftime('%Y%m%d')}.csv",
                mime="text/csv"
            )

            # -----------------
            # 產品三：隨機抗旱逐日守恆明細表
            # -----------------
            st.markdown("---")
            st.markdown("#### 📅 產品三：隨機抗旱逐日質量守恆明細報表")
            
            df_daily_show = df_sim_results.drop(columns=["年份", "月份", "旬別"])
            st.dataframe(df_daily_show, use_container_width=True)
            
            csv_data_daily = df_daily_show.to_csv(index=False).encode('utf-8-sig')
            st.download_button(
                label="📥 下載 產品三：逐日守恆明細報表 (Excel 貼上專用)",
                data=csv_data_daily,
                file_name=f"liyutan_daily_details_{datetime.date.today().strftime('%Y%m%d')}.csv",
                mime="text/csv"
            )
            
        elif "sim_results" in st.session_state:
            st.markdown("##### 📅 歷史模擬明細表 (保留前次計算結果)")
            st.dataframe(st.session_state.sim_results, use_container_width=True)