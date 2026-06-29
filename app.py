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
    動態切分上、中、下旬，並精確計算該旬的「實際日曆天數」作為後續流量轉換的分母。
    
    參數:
    - start_date (datetime.date): 起始日期
    - end_date (datetime.date): 結束日期
    
    回傳:
    - pd.DataFrame: 包含 Date, Year, Month, Day, Period(旬), PeriodTotalDays(該旬總天數) 的 DataFrame
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
        
        # 判斷旬別 (1-10為上旬、11-20為中旬、21至月底為下旬)
        if day <= 10:
            period = "上旬"
            period_total_days = 10
        elif day <= 20:
            period = "中旬"
            period_total_days = 10
        else:
            period = "下旬"
            # 動態取得該月份的總天數 (自動處理大小月與閏年)
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
# 2. Streamlit 網頁初始化與 Session State 宣告
# ==========================================

st.set_page_config(
    page_title="鯉魚潭水庫庫容推估系統 (第一階段)",
    page_icon="💧",
    layout="wide"
)

# 初始化 Session State，確保參數可在後續階段跨頁面/模組調用
if "max_capacity" not in st.session_state:
    st.session_state.max_capacity = 11584.0  # 預設最高庫容 (萬噸)
if "shilin_eco_flow" not in st.session_state:
    st.session_state.shilin_eco_flow = 2.2     # 士林堰生態基流量預設值 (cms)
if "liyutan_eco_flow" not in st.session_state:
    st.session_state.liyutan_eco_flow = 0.3    # 鯉魚潭最低生態放流量預設值 (cms)
if "start_date" not in st.session_state:
    st.session_state.start_date = datetime.date(2026, 2, 1)  # 預設涵蓋 2 月下旬驗證
if "end_date" not in st.session_state:
    st.session_state.end_date = datetime.date(2026, 3, 15)
if "init_capacity" not in st.session_state:
    st.session_state.init_capacity = 8000.0    # 起始日當天庫容 (萬噸)

# ==========================================
# 3. UI 介面設計
# ==========================================

st.title("💧 鯉魚潭水庫庫容推估系統 (第一階段)")
st.markdown("""
本系統旨在提供科學化、高強韌度的日步進水庫調度與庫容推估。
**第一階段任務**：建立標準水文曆法切分基礎與水利核心參數設定 UI。
""")

# 參數設定區塊 (使用 Columns 進行美觀的左右對齊排版)
st.subheader("⚙️ 基礎參數與邊界條件設定")

col1, col2 = st.columns(2)

with col1:
    st.markdown("### 🏛️ 水庫與堰體物理限制")
    
    # 庫容上限設定
    max_cap = st.number_input(
        "水庫上限庫容 (萬噸)",
        min_value=100.0,
        max_value=20000.0,
        value=st.session_state.max_capacity,
        step=100.0,
        help="鲤魚潭水庫之設計最高水位庫容（預設為 11584 萬噸）。後續若超過此值將觸發溢流機制。",
        key="temp_max_capacity"
    )
    st.session_state.max_capacity = max_cap
    
    # 士林堰生態放水設定
    shilin_eco = st.number_input(
        "士林堰生態基流量 (cms)",
        min_value=0.0,
        max_value=10.0,
        value=st.session_state.shilin_eco_flow,
        step=0.1,
        help="士林堰維持生態所需之最小下游放流量，抗旱時期可手動調整下限。",
        key="temp_shilin_eco"
    )
    st.session_state.shilin_eco_flow = shilin_eco

    # 鯉魚潭生態放水設定
    liyutan_eco = st.number_input(
        "鯉魚潭最低生態放流量 (cms)",
        min_value=0.0,
        max_value=5.0,
        value=st.session_state.liyutan_eco_flow,
        step=0.05,
        help="鯉魚潭大壩下游之基本生態維繫放流量（預設為 0.3 cms）。",
        key="temp_liyutan_eco"
    )
    st.session_state.liyutan_eco_flow = liyutan_eco

with col2:
    st.markdown("### 📅 模擬區間與起始狀態")
    
    # 起始日期設定
    s_date = st.date_input(
        "模擬起始日期",
        value=st.session_state.start_date,
        help="預計開始模擬與推估的第一天。",
        key="temp_start_date"
    )
    st.session_state.start_date = s_date
    
    # 結束日期設定
    e_date = st.date_input(
        "預計推估目標日 (結束日期)",
        value=st.session_state.end_date,
        help="模擬與推估的最後一天。",
        key="temp_end_date"
    )
    st.session_state.end_date = e_date
    
    # 防呆檢查：確保起始日不大於結束日
    if st.session_state.start_date > st.session_state.end_date:
        st.error("⚠️ 錯誤：『模擬起始日期』不可晚於『預計推估目標日』。請重新修正日期。")
    
    # 起始庫容設定
    init_cap = st.number_input(
        "起始日當天庫容 (萬噸)",
        min_value=0.0,
        max_value=st.session_state.max_capacity,
        value=st.session_state.init_capacity,
        step=10.0,
        help="模擬起算日前一天的水庫實測庫容。不可高於上限庫容。",
        key="temp_init_capacity"
    )
    st.session_state.init_capacity = init_cap

# 將最終參數狀態呈現在資訊提示盒中，確保 session_state 運作正常
st.info(
    f"ℹ️ **當前工作暫存參數確認：** "
    f"庫容上限 = {st.session_state.max_capacity} 萬噸 | "
    f"士林堰基流 = {st.session_state.shilin_eco_flow} cms | "
    f"鯉魚潭最低放流 = {st.session_state.liyutan_eco_flow} cms | "
    f"模擬區間 = {st.session_state.start_date} 至 {st.session_state.end_date} | "
    f"起始庫容 = {st.session_state.init_capacity} 萬噸"
)

# ==========================================
# 4. 曆法驗證與測試區
# ==========================================
st.markdown("---")
st.subheader("🧪 曆法模塊與天數驗證測試")
st.markdown("""
本區塊用於檢驗時間曆法引擎 (Calendar Engine) 是否能精準辨識大小月、平閏年以及下旬的「日曆實際總天數」。
這在第二階段以後將決定**當旬總配額水庫容積（萬噸）**與**日均流量（cms）**之間的數學分母轉換是否正確（例如，2026年2月下旬僅有8天，分母必須為8，而非固定10或11）。
""")

# 點擊按鈕觸發引擎生成
if st.button("🚀 生成曆法測試", type="primary"):
    if st.session_state.start_date > st.session_state.end_date:
        st.error("無法生成測試資料，請先修正日期範圍錯誤。")
    else:
        try:
            # 呼叫曆法引擎
            df_profile = generate_date_profile(st.session_state.start_date, st.session_state.end_date)
            
            # 計算跨越年份、月份與各旬實際總天數（進行群組彙整）
            # 用於驗證：不論使用者切換何種區間，該旬在日曆上的「實際法定總天數」皆可正確對應。
            df_summary = df_profile.groupby(["年份", "月份", "旬別"]).agg(
                區間內所佔天數=("日期", "count"),
                該旬日曆實際總天數=("該旬實際總天數", "first")
            ).reset_index()
            
            # 使用 Columns 呈現明細與彙整
            col_sum, col_det = st.columns([2, 3])
            
            with col_sum:
                st.markdown("##### 📊 旬度天數彙整表 (分母驗證)")
                st.dataframe(df_summary, use_container_width=True)
                st.success("✅ 彙整說明：下旬實際總天數已依平閏年與大小月動態校正。")
                
            with col_det:
                st.markdown("##### 📅 逐日曆法分析明細 (日步進序列)")
                st.dataframe(df_profile, use_container_width=True, height=350)
                
        except Exception as e:
            st.error(f"執行時發生非預期錯誤: {e}")