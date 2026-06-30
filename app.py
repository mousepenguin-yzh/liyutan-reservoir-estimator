import streamlit as st

# ==========================================
# 0. 必須為 Streamlit 第一行指令：強制全螢幕寬版配置 (解決版面集中問題)
# ==========================================
st.set_page_config(
    page_title="鯉魚潭水庫庫容推估系統",
    page_icon="💧",
    layout="wide"
)

import pandas as pd
import datetime
import calendar
import io
import plotly.graph_objects as go

# ==========================================
# 0. 內建第一層標準水文流量資料庫 (Embedded Database)
# ==========================================

RAW_DEFAULT_HYDROLOGY = """工作表\tQ95\tQ90\tQ85\tQ80\tQ75\tQ70\tQ65\tQ60\tQ55\tQ50\tQ45\tQ40\tQ35\tQ30\tQ25\tQ20\tQ15\tQ10\tQ5
1月上旬\t4.03\t4.21\t4.6\t5.1\t5.2\t5.5\t5.87\t5.97\t6.55\t7.3\t7.61\t7.66\t8.2\t10.04\t10.37\t10.7\t11.93\t14.29\t25.02
1月中旬\t3.9\t4.11\t4.36\t4.74\t4.95\t4.96\t5.52\t5.96\t6.34\t6.45\t6.66\t7.13\t7.91\t8.07\t9.41\t13.4\t14.82\t20.29\t24.2
1月下旬\t4.23\t4.43\t4.49\t4.56\t4.91\t5.04\t5.18\t5.86\t6.33\t6.5\t6.57\t6.87\t7\t8.18\t9.86\t11.9\t12.56\t15.74\t46.32
2月上旬\t3.71\t3.86\t4.23\t4.54\t4.71\t4.85\t5.58\t5.98\t6.32\t6.39\t7.42\t8.26\t8.75\t9.13\t10.73\t13.11\t15.82\t17.43\t40.85
2月中旬\t3.54\t4.51\t4.59\t4.65\t4.9\t5.04\t5.81\t6.05\t6.87\t7.81\t8.74\t9.61\t11.26\t12.91\t14.18\t14.62\t19.26\t22.99\t29.53
2月下旬\t3.71\t3.77\t3.9\t4.26\t4.67\t5.24\t6.4\t6.68\t6.87\t7.33\t8.07\t8.59\t9.2\t9.98\t16.76\t21.04\t30.99\t46.2\t61.85
3月上旬\t3.61\t3.99\t4.36\t4.86\t5.61\t6.25\t7.09\t7.95\t9.15\t10.64\t13.2\t14.24\t14.52\t16.5\t20.4\t24.02\t25.53\t27.3\t70.17
3月中旬\t3.28\t4.3\t5.43\t6.09\t6.37\t6.62\t8.44\t8.91\t10.91\t11.79\t12.39\t13.66\t14.13\t15.23\t19.93\t25\t26.61\t45.38\t69.68
3月下旬\t3.55\t4.17\t5.54\t6.88\t8.75\t9.22\t9.29\t9.67\t11.27\t11.52\t12.59\t13.44\t15.37\t15.89\t18.15\t28.5\t36.09\t43.07\t67.21
4月上旬\t4.41\t6.12\t7.44\t8.23\t9.16\t9.9\t10.21\t10.87\t11.96\t12.71\t14.21\t18.14\t22.63\t28.08\t32.16\t37.04\t38.26\t41.82\t47.57
4月中旬\t4.3\t7.11\t8.09\t8.3\t9.47\t9.85\t10.28\t12.86\t14.14\t15.2\t16.94\t18.63\t19.02\t23.79\t24.38\t30.85\t39.99\t47.24\t61.78
4月下旬\t4.99\t7.26\t7.75\t8.35\t8.63\t9.26\t11.95\t13.46\t14.45\t16.73\t17.71\t17.92\t30.62\t32.43\t34.66\t36.81\t42.75\t54.55\t74.69
5月上旬\t4.59\t5.66\t7.77\t10.18\t10.76\t10.94\t12.83\t17.1\t18.48\t19.69\t24.03\t26.62\t29.62\t32.01\t33.65\t38.2\t45.06\t65.16\t75.88
5月中旬\t4.82\t9.53\t9.96\t10.25\t10.98\t12.1\t12.36\t13.79\t17.75\t19.7\t27.22\t34.56\t38.88\t39.56\t57.62\t61.59\t71.5\t91.7\t186.07
5月下旬\t7.82\t10.39\t13.16\t15.91\t21.1\t22.75\t28.76\t29.38\t29.99\t30.15\t31.58\t36.05\t43.49\t56.15\t71.42\t78.51\t87.53\t96.11\t97.85
6月上旬\t8.52\t12.24\t14.3\t15.81\t21.99\t25.02\t33\t34.98\t36.61\t36.79\t40.5\t41.84\t41.91\t42.6\t43.86\t61.29\t78.6\t145.54\t250.92
6月中旬\t10.92\t12.63\t15.83\t21.71\t28.06\t29.21\t34.4\t36.36\t39.23\t40.25\t42.24\t48.94\t53.72\t64.35\t82.14\t105.45\t159.57\t188.66\t233.97
6月下旬\t10.48\t13.02\t13.96\t16.9\t19.55\t23.32\t23.94\t24.73\t25.39\t27.5\t31.44\t34.72\t35.87\t47.1\t49.35\t50.87\t55.75\t70.76\t92.07
7月上旬\t10.71\t11.83\t13.69\t15.37\t17.19\t20.62\t20.93\t21.27\t21.77\t22.21\t27.09\t28.58\t29.26\t30.36\t34.39\t36.53\t37.35\t48.47\t229.44
7月中旬\t10.07\t13.92\t14.43\t15.01\t15.74\t16\t17.79\t21.11\t23.14\t23.75\t25.21\t31.87\t37.26\t39.08\t47.3\t50.93\t63.04\t110.12\t260.34
7月下旬\t9.39\t11.43\t12.78\t14.68\t16.33\t16.77\t19.67\t21.85\t23.54\t27.81\t34.05\t39.18\t43.54\t46.6\t52.28\t54.35\t66.5\t86.47\t104.59
8月上旬\t9.5\t11.39\t12.92\t15.79\t20.31\t20.76\t22.69\t25.3\t28.17\t35.32\t39.14\t44.89\t50.11\t54.22\t64.7\t92.62\t148.46\t188.63\t337.47
8月中旬\t10.39\t11.24\t12.86\t16.59\t18.35\t20.2\t22.33\t23.53\t24.48\t24.74\t27.21\t36.35\t41.55\t46.59\t48.61\t63.23\t78.97\t105.32\t132.93
8月下旬\t9.02\t10.45\t11.3\t12.95\t16.25\t16.93\t17.31\t17.48\t18.65\t27.36\t28.4\t32.47\t37.77\t40.35\t53.68\t63.91\t69.04\t125.21\t377.4
9月上旬\t7.54\t11.49\t13.83\t14.67\t14.96\t15.46\t15.85\t17.72\t19.25\t20.05\t24.41\t28.43\t33.5\t34.61\t39.08\t42.2\t54.76\t83.15\t137.61
9月中旬\t7.54\t10.51\t11.36\t13.57\t15.79\t16.56\t17.58\t19.12\t20.83\t21.54\t24.9\t29.49\t31.96\t38.75\t45.86\t59.82\t67.64\t106.32\t331.22
9月下旬\t8.4\t9.32\t10.74\t11.51\t13.08\t13.68\t15.29\t15.89\t16.35\t17.21\t17.8\t22.04\t24\t26.49\t30.68\t44.92\t61.74\t76.62\t132.3
10月上旬\t8.06\t8.41\t9.58\t10.61\t11\t12.05\t12.56\t13.16\t13.68\t14.49\t14.67\t16.04\t19.08\t23.48\t36.06\t42.2\t47.39\t72.02\t195.92
10月中旬\t5.91\t7.64\t8.83\t9.49\t9.82\t10.04\t10.92\t11.67\t12.17\t12.25\t12.92\t15.72\t17.99\t19.42\t20.85\t22.4\t23.84\t32.84\t63.75
10月下旬\t5.69\t6.35\t7.34\t7.9\t7.99\t9.41\t9.89\t10.42\t10.86\t11\t11.31\t12.09\t12.84\t14.12\t15.11\t15.57\t18.32\t23.98\t31.66
11月上旬\t4.7\t5.7\t6.47\t7.07\t7.67\t7.97\t8.05\t8.6\t9.17\t9.66\t10.1\t11.29\t11.98\t12.85\t13.84\t14.61\t16.53\t19.94\t35.59
11月中旬\t4.5\t5.35\t5.91\t6.65\t6.81\t6.92\t7.35\t7.66\t7.95\t9.11\t9.17\t9.75\t10.63\t11.27\t13.48\t13.87\t14.78\t15.22\t26.19
11月下旬\t3.97\t4.91\t5.56\t6.04\t6.16\t6.26\t6.73\t7.49\t7.77\t8.85\t9.86\t10.49\t10.77\t11.08\t12.17\t13.62\t15.14\t21.16\t57.72
12月上旬\t4.51\t4.87\t5.33\t5.37\t5.72\t6.03\t6.54\t6.97\t7.01\t7.29\t8.01\t8.13\t8.43\t9.09\t10\t10.39\t11.89\t20.25\t43.89
12月中旬\t4.45\t4.69\t4.89\t5.12\t5.23\t5.66\t5.71\t6.23\t6.78\t6.92\t7.23\t7.54\t8.33\t9.11\t9.29\t13.29\t15.18\t18.28\t29.87
12月下旬\t4.08\t4.68\t5.29\t5.48\t5.84\t6.05\t6.2\t6.32\t6.69\t6.97\t7.42\t7.57\t8.06\t8.42\t8.47\t8.82\t11.05\t14.39\t18.75"""

# 定義 36 旬標準順序，用於資料校驗
CANONICAL_PERIODS = [
    "1月上旬", "1月中旬", "1月下旬", "2月上旬", "2月中旬", "2月下旬",
    "3月上旬", "3月中旬", "3月下旬", "4月上旬", "4月中旬", "4月下旬",
    "5月上旬", "5月中旬", "5月下旬", "6月上旬", "6月中旬", "6月下旬",
    "7月上旬", "7月中旬", "7月下旬", "8月上旬", "8月中旬", "8月下旬",
    "9月上旬", "9月中旬", "9月下旬", "10月上旬", "10月中旬", "10月下旬",
    "11月上旬", "11月中旬", "11月下旬", "12月上旬", "12月中旬", "12月下旬"
]

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
# 2. 第二階段核心邏輯：動態旬流量檢索、多重解碼與解析
# ==========================================

def get_dynamic_shilin_flow(month: int, period: str, scenario: str) -> float:
    """
    動態流量檢索引擎：自 st.session_state.hydrology_df 讀取對應旬別與情境之流量 (單位: cms)。
    相容 `Q50 (平水)` 與簡寫 `Q50` 的情境欄位名稱。
    """
    # 提取情境代碼 (例如 "Q50 (平水)" -> "Q50")
    scenario_code = scenario.split(" ")[0].strip()
    row_key = f"{month}月{period}"
    
    df = st.session_state.hydrology_df
    # 比對主索引欄位（去除前後空白）
    match_row = df[df["工作表"].str.strip() == row_key]
    
    if not match_row.empty:
        try:
            return float(match_row.iloc[0][scenario_code])
        except (KeyError, ValueError, TypeError):
            pass
            
    # 若找不到，回退至安全預設流量 1.0 cms
    return 1.0


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


def read_csv_with_fallback(file_obj) -> pd.DataFrame:
    """
    強韌多重解碼回退解碼引擎 (解決 CP950 解碼報錯問題)：
    台灣同仁在 Excel 另存為 CSV 時預設使用 cp950 (ANSI) 編碼，此引擎依序嘗試：
    1. utf-8-sig (有BOM的UTF-8)
    2. cp950 (微軟繁體中文Big5)
    3. utf-8 (標準無BOM的UTF-8)
    4. gb18030 (簡中相容編碼)
    """
    bytes_data = file_obj.getvalue()
    for enc in ["utf-8-sig", "cp950", "utf-8", "gb18030"]:
        try:
            decoded_text = bytes_data.decode(enc)
            return pd.read_csv(io.StringIO(decoded_text))
        except Exception:
            continue
    # 萬一全部失敗，回退至 pandas 預設讀取器
    return pd.read_csv(file_obj)


def validate_uploaded_hydrology(df_input: pd.DataFrame) -> tuple:
    """
    強韌防呆校驗器：校驗上傳的 Excel 或 CSV 水文資料庫結構。
    回傳 (是否成功, 錯誤訊息/成功解析之DataFrame)
    """
    df = df_input.copy()
    # 欄位去空白
    df.columns = [str(c).strip() for c in df.columns]
    
    # 1. 檢查首欄名稱是否正確（相容「工作表」或「旬別」）
    first_col = df.columns[0]
    if first_col not in ["工作表", "旬別"]:
        df.rename(columns={first_col: "工作表"}, inplace=True)
        first_col = "工作表"
        
    # 2. 檢查必要的情境欄位 (Q95 至 Q5 共 19 個欄位)
    required_scenarios = [
        "Q95", "Q90", "Q85", "Q80", "Q75", "Q70", "Q65", "Q60", "Q55", 
        "Q50", "Q45", "Q40", "Q35", "Q30", "Q25", "Q20", "Q15", "Q10", "Q5"
    ]
    missing_cols = [c for c in required_scenarios if c not in df.columns]
    if missing_cols:
        return False, f"上傳檔案缺少必要的情境欄位：{', '.join(missing_cols)}"
        
    # 3. 檢查總筆數是否精確為 36 旬
    if len(df) != 36:
        return False, f"水文年度資料筆數錯誤。預期為 36 旬（列），但實際讀得 {len(df)} 筆。"
        
    # 4. 校驗 36 旬名稱順序，並將其標準化
    df[first_col] = df[first_col].str.strip()
    for idx, expected_name in enumerate(CANONICAL_PERIODS):
        actual_name = df.iloc[idx][first_col]
        if actual_name != expected_name:
            return False, f"第 {idx+1} 列的旬別名稱不符。預期為 '{expected_name}'，但實際為 '{actual_name}'，請確認格式順序。"
            
    # 5. 強制將流量數據轉換成非負浮點數，檢查是否有非法字元
    try:
        for col in required_scenarios:
            df[col] = pd.to_numeric(df[col]).astype(float)
            if (df[col] < 0).any():
                return False, f"欄位 '{col}' 偵測到負值流量，請確認所有流量皆大於等於 0。"
    except Exception:
        return False, "流量資料中含有非數值的文字或非法空白，請重新檢查檔案。"
        
    # 只保留必要欄位
    final_df = df[[first_col] + required_scenarios].copy()
    if first_col != "工作表":
        final_df.rename(columns={first_col: "工作表"}, inplace=True)
        
    return True, final_df

# ==========================================
# 3. 第三階段核心邏輯：標準出流配置與預設值
# ==========================================

def get_default_demands(month: int) -> dict:
    """
    依據歷史常態，提供各月份出流三大標訂的平水期預設需求。
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
# 4. 輔助函數：水利旬末邊界與目標日期對齊 (對齊橫向 Excel 表)
# ==========================================

def get_sim_target_date(milestone_date: datetime.date) -> datetime.date:
    """
    將前台 Excel 旬度標題日期（如 7月1日、7月11日、7月21日、8月1日）
    映射至底層質量守恆模擬日誌對應的「期末結算時間點」：
    - X月1日   -> 實際對應前一月的最後一日
    - X月11日  -> 實際對應本月 10 日 24:00 庫容
    - X月21日  -> 實際對應本月 20 日 24:00 庫容
    """
    if milestone_date.day == 1:
        return milestone_date - datetime.timedelta(days=1)
    elif milestone_date.day == 11:
        return milestone_date.replace(day=10)
    elif milestone_date.day == 21:
        return milestone_date.replace(day=20)
    return milestone_date


def plot_reservoir_capacity_trend(df_sim_results: pd.DataFrame, display_start: datetime.date, start_date: datetime.date, end_date: datetime.date, max_capacity: float) -> go.Figure:
    """
    繪製單一情境之鯉魚潭水庫蓄水量變化趨勢圖（無背景色塊，黑色實際庫容 + 藍色推估庫容）。
    有效庫容字卡標註於繪圖區外部左上角 (x=0.0, y=1.02)，確保滿庫時不遮擋高水位推估曲線。
    """
    fig = go.Figure()
    boundary_day = start_date - datetime.timedelta(days=1)
    
    df_plot = df_sim_results.copy()
    df_plot["日期"] = pd.to_datetime(df_plot["日期"]).dt.date
    
    # 實際庫容 (黑色實線)
    df_history = df_plot[df_plot["日期"] <= boundary_day]
    if not df_history.empty:
        fig.add_trace(go.Scatter(
            x=df_history["日期"],
            y=df_history["本日末庫容 (萬噸)"],
            mode="lines",
            name="實際庫容",
            line=dict(color="black", width=2.5),
            hovertemplate="日期: %{x}<br>實際庫容: %{y:.2f} 萬噸<extra></extra>"
        ))
        
    # 推估庫容 (藍色實線)
    df_projection = df_plot[df_plot["日期"] >= boundary_day]
    if not df_projection.empty:
        fig.add_trace(go.Scatter(
            x=df_projection["日期"],
            y=df_projection["本日末庫容 (萬噸)"],
            mode="lines",
            name="推估庫容",
            line=dict(color="#1f77b4", width=2.5),
            hovertemplate="日期: %{x}<br>推估庫容: %{y:.2f} 萬噸<extra></extra>"
        ))
        
    # 有效庫容字卡：以 Annotation 置於繪圖區外部左上角 (x=0.0, y=1.02)
    formatted_capacity = f"{max_capacity:,.0f}" if max_capacity == 11584.0 else f"{max_capacity:,.1f}"
    fig.add_annotation(
        text=f"有效庫容：{formatted_capacity}萬噸",
        xref="paper", yref="paper",
        x=0.0, y=1.02,
        showarrow=False,
        xanchor="left",
        yanchor="bottom",
        font=dict(color="red", size=13, family="sans-serif", weight="bold"),
        bordercolor="red",
        borderwidth=1,
        borderpad=5,
        bgcolor="white",
        opacity=0.9
    )
    
    # 生成橫軸月首 1 號刻度
    tick_dates = []
    curr_y, curr_m = display_start.year, display_start.month
    end_y, end_m = end_date.year, end_date.month
    
    while (curr_y < end_y) or (curr_y == end_y and curr_m <= end_m):
        d = datetime.date(curr_y, curr_m, 1)
        if display_start <= d <= end_date:
            tick_dates.append(d)
        curr_m += 1
        if curr_m > 12:
            curr_m = 1
            curr_y += 1
            
    tick_text = [f"{d.month}/{d.day}" for d in tick_dates]
    
    fig.update_layout(
        title={
            "text": "📊 鯉魚潭水庫蓄水量變化趨勢圖",
            "y": 0.95,
            "x": 0.5,
            "xanchor": "center",
            "yanchor": "top"
        },
        xaxis_title="日期",
        yaxis_title="水庫蓄水量 (萬噸)",
        hovermode="x unified",
        legend=dict(
            orientation="h",
            yanchor="bottom",
            y=1.02,
            xanchor="right",
            x=1
        ),
        margin=dict(l=50, r=50, t=100, b=50),
        plot_bgcolor="rgba(0,0,0,0)",
        paper_bgcolor="rgba(0,0,0,0)"
    )
    
    fig.update_xaxes(
        tickvals=tick_dates,
        ticktext=tick_text,
        showgrid=True,
        gridwidth=0.5,
        gridcolor="lightgray",
        zeroline=False
    )
    fig.update_yaxes(
        showgrid=True,
        gridwidth=0.5,
        gridcolor="lightgray",
        zeroline=False,
        range=[0, max_capacity * 1.05]
    )
    
    return fig

# ==========================================
# 5. Streamlit 初始化與會話狀態 (狀態持久化以繼承預設值)
# ==========================================

# 基礎參數初始化
if "max_capacity" not in st.session_state:
    st.session_state.max_capacity = 11584.0
if "shilin_eco_flow" not in st.session_state:
    st.session_state.shilin_eco_flow = 2.7
if "liyutan_eco_flow" not in st.session_state:
    st.session_state.liyutan_eco_flow = 0.3

# 時間區間初始化
if "display_start_date" not in st.session_state:
    st.session_state.display_start_date = datetime.date(2026, 5, 1)
if "start_date" not in st.session_state:
    st.session_state.start_date = datetime.date(2026, 6, 21)
if "end_date" not in st.session_state:
    st.session_state.end_date = datetime.date(2026, 9, 1)

# 起始蓄水量與輸入狀態初始化 (保留上次推估預設值)
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
if "mixed_flow_configs" not in st.session_state:
    st.session_state.mixed_flow_configs = {}

# 多情境數據暫存器
if "scenarios" not in st.session_state:
    st.session_state.scenarios = {}

# 初始化標準流量資料庫
if "hydrology_df" not in st.session_state:
    default_io = io.StringIO(RAW_DEFAULT_HYDROLOGY)
    default_df = pd.read_csv(default_io, sep="\t")
    default_df.columns = [c.strip() for c in default_df.columns]
    st.session_state.hydrology_df = default_df
    st.session_state.hydrology_source_status = "系統預設標準流量"

# ==========================================
# 6. 前端 UI 分頁排版 (含第六階段全面整合)
# ==========================================

st.title("💧 鯉魚潭水庫庫容推估系統")

tab_config, tab_inflow, tab_outflow, tab_simulation, tab_products = st.tabs([
    "⚙️ 第一階段：推估需求基礎資料設定", 
    "🌊 第二階段：入流條件與水文維護",
    "🚰 第三階段：出流需求與抗旱調整",
    "🧮 第四階段：庫容推估演算",
    "📊 第五階段：推估成果產品"
])

# -----------------
# TAB 1: 基礎與曆法
# -----------------
with tab_config:
    st.subheader("⚙️ 水庫基本資料與展示區間")
    col1, col2 = st.columns(2)
    with col1:
        st.markdown("##### 🏛️ 水庫基本資料")
        st.session_state.max_capacity = st.number_input("水庫上限庫容 (萬噸)", min_value=100.0, max_value=20000.0, value=st.session_state.max_capacity, step=100.0)
        st.session_state.shilin_eco_flow = st.number_input("士林堰生態基流量 (cms)", min_value=0.0, max_value=10.0, value=st.session_state.shilin_eco_flow, step=0.1)
        st.session_state.liyutan_eco_flow = st.number_input("鯉魚潭最低生態放流量 (cms)", min_value=0.0, max_value=5.0, value=st.session_state.liyutan_eco_flow, step=0.05)
    with col2:
        st.markdown("##### 📅 展示區間設定")
        st.session_state.display_start_date = st.date_input("展示起始日期(若早於推估起始日期，需在下方填入實際蓄水量)", value=st.session_state.display_start_date)
        st.session_state.start_date = st.date_input("推估起始日期 (庫容推估起點)", value=st.session_state.start_date)
        st.session_state.end_date = st.date_input("預計推估結束日期 (此日不計入日計算)", value=st.session_state.end_date)
        
        # 檢驗日期先後關係
        if st.session_state.display_start_date > st.session_state.start_date:
            st.error("⚠️ 錯誤：『展示起始日期』不可晚於『推估起始日期』。")
        if st.session_state.start_date >= st.session_state.end_date:
            st.error("⚠️ 錯誤：『推估起始日期』必須早於『預計推估結束日期』。")
            
        calc_start_day = st.session_state.start_date
        prev_day = calc_start_day - datetime.timedelta(days=1)
        prev_day_label = f"推估起點前一日 ({prev_day.strftime('%m/%d')} 24:00) 庫容 (萬噸)"
        
        st.session_state.init_capacity = st.number_input(prev_day_label, min_value=0.0, max_value=st.session_state.max_capacity, value=st.session_state.init_capacity, step=10.0)

    # 處理展示期（歷史觀測期）的逐旬蓄水量輸入
    if st.session_state.display_start_date < st.session_state.start_date:
        st.markdown("---")
        st.markdown("##### 📈 展示區間實際蓄水量輸入")
        st.caption("請輸入展示期間內，各旬末日前一日 24:00 的實際蓄水量 (萬噸)：")
        
        milestones = get_historical_milestone_dates_v2(st.session_state.display_start_date, st.session_state.start_date)
        
        # 排除最後一個邊界日
        end_boundary = st.session_state.start_date - datetime.timedelta(days=1)
        other_milestones = [m for m in milestones if m != end_boundary]
        
        if other_milestones:
            cols_num = min(4, len(other_milestones))
            m_cols = st.columns(cols_num)
            
            for idx, m_date in enumerate(other_milestones):
                col_idx = idx % cols_num
                m_label = f"{m_date.strftime('%m/%d')} 24:00 蓄水量"
                default_v = st.session_state.hist_capacity.get(m_date.strftime('%Y-%m-%d'), st.session_state.init_capacity)
                st.session_state.hist_capacity[m_date.strftime('%Y-%m-%d')] = m_cols[col_idx].number_input(
                    m_label, 
                    min_value=0.0, 
                    max_value=st.session_state.max_capacity, 
                    value=default_v, 
                    step=50.0, 
                    key=f"active_hist_{m_date}"
                )

    # 生成總時間剖面
    if st.session_state.display_start_date < st.session_state.end_date and st.session_state.start_date < st.session_state.end_date:
        df_cal = generate_date_profile(st.session_state.display_start_date, st.session_state.end_date)
        unique_periods = df_cal.groupby(["年份", "月份", "旬別"]).size().reset_index().drop(columns=[0])
        
        period_order = {"上旬": 1, "中旬": 2, "下旬": 3}
        unique_periods["旬別順序碼"] = unique_periods["旬別"].map(period_order)
        unique_periods = unique_periods.sort_values(by=["年份", "月份", "旬別順序碼"]).drop(columns=["旬別順序碼"]).reset_index(drop=True)
        st.success(f"📅 曆法配置成功：當前展示+推估計算區間（左閉右開）共計 **{len(df_cal)}** 天。")
    else:
        unique_periods = pd.DataFrame()

    # 計算「未來推估期」所專屬跨越的旬別
    if st.session_state.start_date < st.session_state.end_date:
        df_proj_cal = generate_date_profile(st.session_state.start_date, st.session_state.end_date)
        proj_unique_periods = df_proj_cal.groupby(["年份", "月份", "旬別"]).size().reset_index().drop(columns=[0])
        
        period_order = {"上旬": 1, "中旬": 2, "下旬": 3}
        proj_unique_periods["旬別順序碼"] = proj_unique_periods["旬別"].map(period_order)
        proj_unique_periods = proj_unique_periods.sort_values(by=["年份", "月份", "旬別順序碼"]).drop(columns=["旬別順序碼"]).reset_index(drop=True)
    else:
        proj_unique_periods = pd.DataFrame()

# -----------------
# TAB 2: 第二階段入流與水文維護
# -----------------
with tab_inflow:
    st.subheader("🌊 入流條件設定")
    
    if proj_unique_periods.empty:
        st.warning("⚠️ 請先返回第一階段，設定正確的模擬日期區間。")
    else:
        # 第一區塊：主要入流模式選擇
        inflow_options = [
            "內建標準水文情境 (Q5~Q95)", 
            "手動批次匯入（支援 Excel 複製貼上）", 
            "內建與手動混合模式"
        ]
        
        if st.session_state.inflow_source not in inflow_options:
            st.session_state.inflow_source = "內建標準水文情境 (Q5~Q95)"
            
        inflow_index = inflow_options.index(st.session_state.inflow_source)
        inflow_mode = st.radio("請選擇天然流量 (cms) 來源模式：", inflow_options, index=inflow_index, horizontal=True)
        st.session_state.inflow_source = inflow_mode
        period_flow_mapping = []
        
        if inflow_mode == "內建標準水文情境 (Q5~Q95)":
            # 19 旬情境選單
            SCENARIO_OPTIONS = [
                "Q5 (極豐水)", "Q10", "Q15", "Q20 (偏豐水)", "Q25", "Q30", "Q35", "Q40", "Q45", 
                "Q50 (平水)", "Q55", "Q60", "Q65", "Q70", "Q75 (偏枯水)", "Q80", "Q85", "Q90", "Q95 (特枯水)"
            ]
            
            # 確保 session_state 初始值在完整清單內
            if st.session_state.builtin_scenario not in SCENARIO_OPTIONS:
                st.session_state.builtin_scenario = "Q50 (平水)"
                
            selected_scen = st.selectbox(
                "請選擇水文情境：", 
                SCENARIO_OPTIONS, 
                index=SCENARIO_OPTIONS.index(st.session_state.builtin_scenario)
            )
            st.session_state.builtin_scenario = selected_scen
            
            for idx, row in proj_unique_periods.iterrows():
                y, m, p = row["年份"], row["月份"], row["旬別"]
                flow_val = get_dynamic_shilin_flow(m, p, selected_scen)
                period_flow_mapping.append({"年份": y, "月份": m, "旬別": p, f"天然流量(cms) - {selected_scen}": flow_val})
                
        elif inflow_mode == "手動批次匯入（支援 Excel 複製貼上）":
            st.markdown("##### 📥 Excel 數據批次貼上區")
            # 生成動態 Q50 預設列表以供複製參考
            dummy_data_list = [round(get_dynamic_shilin_flow(row["月份"], row["旬別"], "Q50 (平水)"), 1) for _, row in proj_unique_periods.iterrows()]
            dummy_paste_str = "\t".join(map(str, dummy_data_list))
            st.caption(f"💡 測試範例串（共 {len(dummy_data_list)} 個數值）： `{dummy_paste_str}`")
            
            pasted_text = st.text_area("請在此貼上 Excel 數據 (手動輸入時需以空格、Tab或換行分隔)：", placeholder="例如: 12.5  14.2  10.1 ...", height=80, key="inflow_paste")
            parsed_list = parse_pasted_data(pasted_text)
            
            if pasted_text.strip():
                if len(parsed_list) != len(proj_unique_periods):
                    st.error(f"❌ 解析失敗：您貼上的數據個數（{len(parsed_list)} 筆）與當前區間所需（{len(proj_unique_periods)} 筆）不符！")
                    for i, (_, row) in enumerate(proj_unique_periods.iterrows()):
                        period_flow_mapping.append({"年份": row["年份"], "月份": row["月份"], "旬別": row["旬別"], "天然流量(cms)": get_dynamic_shilin_flow(row["月份"], row["旬別"], "Q50 (平水)")})
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
                    period_flow_mapping.append({"年份": row["年份"], "月份": row["月份"], "旬別": row["旬別"], "天然流量(cms)": get_dynamic_shilin_flow(row["月份"], row["旬別"], "Q50 (平水)")})
                    
        else:
            # 內建與手動混合模式
            st.markdown("##### 🎛️ 逐旬內建與手動混合設定")
            st.caption("您可以針對未來推估期間的各個旬別單獨指定水文入流來源（可選擇任意內建標準水文情境，或選擇自訂手動輸入）：")
            
            MIXED_SCENARIO_OPTIONS = [
                "Q5 (極豐水)", "Q10", "Q15", "Q20 (偏豐水)", "Q25", "Q30", "Q35", "Q40", "Q45", 
                "Q50 (平水)", "Q55", "Q60", "Q65", "Q70", "Q75 (偏枯水)", "Q80", "Q85", "Q90", "Q95 (特枯水)",
                "✍️ 手動輸入"
            ]
            
            # 使用自訂 CSS 樣式製作簡潔的手動表格標題欄，減少垂直空間占用
            st.markdown("<div style='font-weight:bold; margin-bottom: 5px; color:#555555; font-size:14px;'>"
                        "<span style='display:inline-block; width:22%;'>📅 旬別時間點</span>"
                        "<span style='display:inline-block; width:38%;'>⚙️ 水文來源模式</span>"
                        "<span style='display:inline-block; width:38%;'>🌊 流量值 (cms)</span>"
                        "</div>", unsafe_allow_html=True)
            
            for idx, row in proj_unique_periods.iterrows():
                y, m, p = row["年份"], row["月份"], row["旬別"]
                key = f"{y}-{m}-{p}"
                
                # 初始化該旬的設定狀態
                if key not in st.session_state.mixed_flow_configs:
                    st.session_state.mixed_flow_configs[key] = {
                        "type": "Q50 (平水)",
                        "manual_val": get_dynamic_shilin_flow(m, p, "Q50 (平水)")
                    }
                    
                config = st.session_state.mixed_flow_configs[key]
                
                # 安全防呆：避免因為修改規格導致儲存的型態不相容
                current_type = config["type"]
                if current_type not in MIXED_SCENARIO_OPTIONS:
                    current_type = "Q50 (平水)"
                default_opt_idx = MIXED_SCENARIO_OPTIONS.index(current_type)
                
                col_p_name, col_p_sel, col_p_val = st.columns([2, 3, 3])
                with col_p_name:
                    st.markdown(f"**{y}年{m}月{p}**")
                with col_p_sel:
                    selected_opt = st.selectbox(
                        "來源模式",
                        MIXED_SCENARIO_OPTIONS,
                        index=default_opt_idx,
                        key=f"mixed_sel_{key}",
                        label_visibility="collapsed"
                    )
                    config["type"] = selected_opt
                with col_p_val:
                    if selected_opt == "✍️ 手動輸入":
                        man_val = st.number_input(
                            "流量 (cms)",
                            min_value=0.0,
                            max_value=500.0,
                            value=float(config["manual_val"]),
                            step=0.1,
                            key=f"mixed_num_{key}",
                            label_visibility="collapsed"
                        )
                        config["manual_val"] = man_val
                        flow_val = man_val
                    else:
                        # 從系統預設/已上傳的水文資料庫自動提取
                        flow_val = get_dynamic_shilin_flow(m, p, selected_opt)
                        st.markdown(f"<div style='padding-top:6px; color:#1f77b4; font-weight:bold;'>系統內建：{flow_val:.2f} cms</div>", unsafe_allow_html=True)
                        
                # 寫入統一資料結構
                period_flow_mapping.append({
                    "年份": y, "月份": m, "旬別": p,
                    "天然流量(cms)": flow_val
                })
            
            st.markdown("<br>", unsafe_allow_html=True)

        df_period_flow = pd.DataFrame(period_flow_mapping)
        st.dataframe(df_period_flow, use_container_width=True)

    # 第二區塊：資料庫維護 (隱藏至底部)
    st.markdown("<br><br>", unsafe_allow_html=True)
    with st.expander("🛠️ 歷史標準水文資料庫 維護與年度更新專區 (年更新)", expanded=False):
        st.markdown("#### ⚙️ 歷史標準水文主資料庫覆寫與還原")
        
        # 顯示狀態字卡
        if st.session_state.hydrology_source_status == "系統預設標準流量":
            st.info(f"📊 當前主資料庫狀態：🟢 **系統內建標準水文流量 (36旬)**")
        else:
            st.success(f"📊 當前主資料庫狀態：🔵 **已成功載入自訂流量檔案** (來源: {st.session_state.hydrology_source_status})")
            
        m_col1, m_col2 = st.columns([2, 1])
        with m_col1:
            st.markdown("##### 📥 檔案上傳更新（支援 Excel .xlsx 與 CSV）")
            uploaded_hydrology_file = st.file_uploader(
                "請選擇欲上傳之水文流量檔案 (需符合36旬格式規格，強烈推薦使用修改後的 .xlsx 檔)：",
                type=["xlsx", "csv"],
                key="hydrology_uploader"
            )
            
            # 處理上傳與多重解碼覆寫邏輯
            if uploaded_hydrology_file is not None:
                file_name = uploaded_hydrology_file.name
                try:
                    if file_name.endswith(".xlsx"):
                        temp_df = pd.read_excel(uploaded_hydrology_file, engine="openpyxl")
                    else:
                        temp_df = read_csv_with_fallback(uploaded_hydrology_file)
                    
                    is_valid, validated_data = validate_uploaded_hydrology(temp_df)
                    if is_valid:
                        st.session_state.hydrology_df = validated_data
                        st.session_state.hydrology_source_status = file_name
                        st.toast("🎉 水文資料庫已成功覆寫更新！", icon="✅")
                        st.rerun()
                    else:
                        st.error(f"❌ 上傳失敗！檔案結構校驗未通過：{validated_data}")
                except Exception as e:
                    st.error(f"❌ 解析檔案時發生系統錯誤：{str(e)}。請確認檔案內容格式正確。")
                    
        with m_col2:
            st.markdown("##### 💾 範本檔案下載與重設")
            st.caption("下載下方範本，編輯後即可重新上傳。")
            
            # Excel 範本下載
            try:
                excel_io = io.BytesIO()
                with pd.ExcelWriter(excel_io, engine="openpyxl") as writer:
                    st.session_state.hydrology_df.to_excel(writer, index=False, sheet_name="標準水文流量")
                excel_template_bytes = excel_io.getvalue()
                
                st.download_button(
                    label="📥 下載標準水文 Excel 範本 (推薦！)",
                    data=excel_template_bytes,
                    file_name="hydrology_standard_template.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    use_container_width=True
                )
            except Exception:
                pass

            # CSV 備用範本下載
            csv_template_bytes = st.session_state.hydrology_df.to_csv(index=False).encode('utf-8-sig')
            st.download_button(
                label="📥 下載標準水文 CSV 範本 (備用)",
                data=csv_template_bytes,
                file_name="hydrology_standard_template.csv",
                mime="text/csv",
                use_container_width=True
            )
                
            # 重設按鈕常駐顯示
            if st.button("🔄 重設回系統預設標準流量", use_container_width=True, type="secondary"):
                default_io = io.StringIO(RAW_DEFAULT_HYDROLOGY)
                default_df = pd.read_csv(default_io, sep="\t")
                default_df.columns = [c.strip() for c in default_df.columns]
                st.session_state.hydrology_df = default_df
                st.session_state.hydrology_source_status = "系統預設標準流量"
                st.toast("🔄 已還原為系統預設標準流量。", icon="🔄")
                st.rerun()

        st.markdown("---")
        st.markdown("##### 📖 歷史標準水文資料庫 年度更新操作指南")
        st.markdown("""
        為了協助非技術人員能輕鬆維護本系統水文流量資料，請參考以下三步標準流程：
        
        * **步驟一**：點擊右側的 **「下載標準水文 Excel 範本 (推薦！)」** 獲取標準的 Excel 試算表檔案。
        * **步驟二**：使用 Excel 直接開啟該檔案。請保持首欄的旬別名稱（如「1月上旬」）完全不動，將取得之各旬最新天然流量 (cms) 填入對應欄位，直接點擊儲存，**不要變更檔案格式，維持 .xlsx 檔案**。
        * **步驟三**：將修改完畢的 Excel (.xlsx) 檔案拖曳上傳至本專區的「檔案上傳更新」區域，系統驗證通過後即完成年度流量更新。
        """)

# -----------------
# TAB 3: 第三階段出流需求
# -----------------
with tab_outflow:
    st.subheader("🚰 出流標的設定與抗旱調整")
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
            st.markdown("##### 📥 Excel 需求數據批次匯入(手動輸入時需以空格、Tab或換行分隔)")
            def_up_list = [get_default_demands(r["月份"])["up_irr"] for _, r in proj_unique_periods.iterrows()]
            def_down_list = [get_default_demands(r["月份"])["down_irr"] for _, r in proj_unique_periods.iterrows()]
            def_pub_list = [get_default_demands(r["月份"])["public"] for _, r in proj_unique_periods.iterrows()]
            
            col_u, col_d, col_p = st.columns(3)
            with col_u:
                st.caption(f"💡 上灌區(cms) 測試串： `{'  '.join(map(str, def_up_list))}`")
                paste_up = st.text_area("1. 貼上【上灌區(cms)】：", height=70, key="paste_up_pasted")
            with col_d:
                st.caption(f"💡 下灌區(cms) 測試串： `{'  '.join(map(str, def_down_list))}`")
                paste_down = st.text_area("2. 貼上【下灌區(cms)】：", height=70, key="paste_down_pasted")
            with col_p:
                st.caption(f"💡 公共給水(萬噸/日) 測試串： `{'  '.join(map(str, def_pub_list))}`")
                paste_pub = st.text_area("3. 貼上【公共給水(萬噸/日)】：", height=70, key="paste_pub_pasted")
                
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
                            st.error(f"⚠️ 錯誤：規則 #{idx+1} 的起日不可晚於迄日. ")
                            
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

        # 這裡生成包含展示期與推估期的完整日曆需求
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
            
            # 展示期間不受出水影響
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
    st.subheader("🧮 鯉魚潭水庫庫容推估結果")
    st.markdown("""
    本模組依據 **「士林堰引水隧道上限33cms，上游灌區優先滿足，下游灌區剩餘分配」** 之調度原則，進行逐日水庫庫容演算。
    """)
    
    if proj_unique_periods.empty:
        st.warning("⚠️ 請先返回第一階段，設定正確的模擬日期區間。")
    elif 'df_period_flow' not in locals() or 'df_daily_outflow' not in locals():
        st.warning("⚠️ 請確保已完成第一至三階段的入流與出流條件設定。")
    else:
        if st.button("▶️ 開始進行庫容推估", type="primary"):
            
            max_capacity = st.session_state.max_capacity
            shilin_eco = st.session_state.shilin_eco_flow
            liyutan_eco = st.session_state.liyutan_eco_flow
            
            # 歷史區間插值
            has_history = st.session_state.display_start_date < st.session_state.start_date
            daily_hist_caps = {}
            if has_history:
                daily_hist_caps = interpolate_historical_capacities_v2(
                    st.session_state.display_start_date, 
                    st.session_state.start_date, 
                    st.session_state.hist_capacity,
                    st.session_state.init_capacity
                )
            
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
            
            df_daily_profile = generate_date_profile(st.session_state.display_start_date, st.session_state.end_date)
            
            flow_lookup = {}
            for _, item in df_period_flow.iterrows():
                scen_col = [c for c in item.index if "天然流量" in c][0]
                key = f"{int(item['年份'])}-{int(item['月份'])}-{item['旬別']}"
                flow_lookup[key] = item[scen_col]
                
            for _, row in df_daily_profile.iterrows():
                current_date = row["日期"]
                key = f"{row['年份']}-{row['月份']}-{row['旬別']}"
                
                is_projection = current_date >= st.session_state.start_date
                
                if not is_projection:
                    # 觀測展示期
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
                    # 未來推估期 (物理演算)
                    I_cms = flow_lookup.get(key, 0.0)
                    
                    out_row_candidates = df_daily_outflow[df_daily_outflow["日期"] == current_date]
                    if out_row_candidates.empty:
                        U_cms, D_cms, P_vol = 0.0, 0.0, 0.0
                    else:
                        out_row = out_row_candidates.iloc[0]
                        U_cms = out_row["上灌區當日流量(cms)"]
                        D_cms = out_row["下灌區當日流量(cms)"]
                        P_vol = out_row["公共供水當日水量(萬噸)"]
                    
                    actual_U_cms = min(U_cms, I_cms)
                    remaining_flow_cms = max(0.0, I_cms - actual_U_cms)
                    actual_D_cms = min(D_cms, remaining_flow_cms)
                    
                    ag_control_triggered = (actual_U_cms < U_cms) or (actual_D_cms < D_cms)
                    reduction_cms = (U_cms + D_cms) - (actual_U_cms + actual_D_cms)
                    total_ag_intercept_volume_10k += (reduction_cms * 8.64)
                    
                    shilin_river_release_cms = min(I_cms, max(shilin_eco, actual_U_cms))
                    available_diversion_cms = max(0.0, I_cms - shilin_river_release_cms)
                    actual_diversion_cms = min(33.0, available_diversion_cms)
                    actual_diversion_vol = round(actual_diversion_cms * 8.64, 2)
                    
                    liyutan_river_release_cms = max(liyutan_eco, actual_D_cms)
                    liyutan_river_release_vol = round(liyutan_river_release_cms * 8.64, 2)
                    actual_outflow_vol = round(P_vol + liyutan_river_release_vol, 2)
                    
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
            st.success("🎉 模擬演算順利結束！您可以前往『推估成果產品』頁籤儲存此情境、進行方案比對。")
            
            # --- 儀表板指標 ---
            st.markdown("### 🏆 當前推估成果指標")
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

            # --- 庫容與推估蓄水量歷線圖 ---
            st.markdown("---")
            st.markdown("### 📈 庫容與推估蓄水量歷線圖")
            fig = plot_reservoir_capacity_trend(
                df_sim_results, 
                st.session_state.display_start_date, 
                st.session_state.start_date, 
                st.session_state.end_date, 
                st.session_state.max_capacity
            )
            st.plotly_chart(fig, use_container_width=True)

            # --- 旬推估資訊彙整表 ---
            st.markdown("---")
            st.markdown("#### 📅 旬推估資訊彙整表")
            
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
            
            period_order = {"上旬": 1, "中旬": 2, "下旬": 3}
            df_grouped_sim["旬別順序碼"] = df_grouped_sim["旬別"].map(period_order)
            df_grouped_sim = df_grouped_sim.sort_values(by=["年份", "月份", "旬別順序碼"]).drop(columns=["旬別順序碼"]).reset_index(drop=True)
            
            st.dataframe(df_grouped_sim, use_container_width=True)
            
            csv_data_period = df_grouped_sim.to_csv(index=False).encode('utf-8-sig')
            st.download_button(
                label="📥 下載 旬推估資訊彙整表 (Excel 貼上專用)",
                data=csv_data_period,
                file_name=f"liyutan_summary_by_period_{datetime.date.today().strftime('%Y%m%d')}.csv",
                mime="text/csv"
            )

            # --- 日推估資訊彙整表 ---
            st.markdown("---")
            st.markdown("#### 📅 日推估資訊彙整表")
            
            df_daily_show = df_sim_results.drop(columns=["年份", "月份", "旬別"])
            st.dataframe(df_daily_show, use_container_width=True)
            
            csv_data_daily = df_daily_show.to_csv(index=False).encode('utf-8-sig')
            st.download_button(
                label="📥 下載 日推估資訊彙整表 (Excel 貼上專用)",
                data=csv_data_daily,
                file_name=f"liyutan_daily_details_{datetime.date.today().strftime('%Y%m%d')}.csv",
                mime="text/csv"
            )
            
        elif "sim_results" in st.session_state:
            st.markdown("### 📈 庫容與推估蓄水量歷線圖")
            fig = plot_reservoir_capacity_trend(
                st.session_state.sim_results, 
                st.session_state.display_start_date, 
                st.session_state.start_date, 
                st.session_state.end_date, 
                st.session_state.max_capacity
            )
            st.plotly_chart(fig, use_container_width=True)

            st.markdown("##### 📅 歷史模擬明細表")
            st.dataframe(st.session_state.sim_results, use_container_width=True)

# -----------------
# TAB 5: 第五階段：推估成果產品 (多情境對比與字卡位置優化)
# -----------------
with tab_products:
    st.subheader("📊 第五階段：推估成果產品")
    
    # 情境暫存機制控制區
    if "sim_results" in st.session_state:
        st.markdown("### 💾 暫存當前推估成果")
        st.caption("您可以將目前運行的這套設定與推估曲線存檔，以便跟其他流量或不同供水折扣條件的情境疊圖比對。")
        
        col_scen_name, col_scen_btn = st.columns([3, 1])
        with col_scen_name:
            new_scen_name = st.text_input(
                "請輸入此情境名稱 (例：氣候區間上限 / Q80 / Q90)：", 
                value="情境A", 
                key="new_scen_name_input"
            )
        with col_scen_btn:
            st.markdown("<br>", unsafe_allow_html=True)
            if st.button("💾 暫存此情境", use_container_width=True, type="secondary"):
                clean_name = new_scen_name.strip()
                if clean_name:
                    st.session_state.scenarios[clean_name] = st.session_state.sim_results.copy()
                    st.success(f"✅ 已成功暫存情境：『{clean_name}』！")
                    st.rerun()
                else:
                    st.error("❌ 請輸入有效的情境名稱！")
                    
    # 已儲存情境管理控制區
    if st.session_state.scenarios:
        st.markdown("---")
        st.markdown("### 🛠️ 暫存情境管理與比對選擇")
        
        all_saved_names = list(st.session_state.scenarios.keys())
        
        col_scen_select, col_scen_reset = st.columns([3, 1])
        with col_scen_select:
            selected_scenarios = st.multiselect(
                "請勾選欲呈現在下方『旬推估表』與『情境對比圖』中的情境方案：",
                options=all_saved_names,
                default=all_saved_names
            )
        with col_scen_reset:
            st.markdown("<br>", unsafe_allow_html=True)
            if st.button("🗑️ 清空所有暫存情境", use_container_width=True):
                st.session_state.scenarios = {}
                st.success("已清空所有情境！")
                st.rerun()
                
        if selected_scenarios:
            st.markdown("---")
            
            # ==========================================
            # 產品一：旬推估表 (Excel 直接貼上橫向格式)
            # ==========================================
            st.markdown("### 📅 產品一：旬推估表")
            st.markdown("""
            **💡 使用說明**：此表格式完全與 Excel 對齊，各行為不同的情境方案，各列為關鍵的旬度時間點。
            您可以**用滑鼠直接全選此網頁表格複製**，並**直接貼上至您的 Excel 試算表**中，格式與千分位標記皆會完美對齊。
            """)
            
            period_milestones = []
            curr_step = st.session_state.display_start_date
            while curr_step <= st.session_state.end_date:
                if curr_step.day in [1, 11, 21]:
                    period_milestones.append(curr_step)
                curr_step += datetime.timedelta(days=1)
            
            table_data = []
            for m_date in period_milestones:
                target_date = get_sim_target_date(m_date)
                row_dict = {"時間點": m_date.strftime("%m月%d日")}
                for name in selected_scenarios:
                    df_scen = st.session_state.scenarios[name]
                    match_rows = df_scen[pd.to_datetime(df_scen["日期"]).dt.date == target_date]
                    if not match_rows.empty:
                        val = match_rows.iloc[0]["本日末庫容 (萬噸)"]
                        row_dict[name] = val
                    else:
                        row_dict[name] = None
                table_data.append(row_dict)
                
            df_milestone_table = pd.DataFrame(table_data)
                        
            # 1. 進行轉置 (Transpose)，讓情境方案變為橫列，旬度時間點變為直欄
            df_transposed = df_milestone_table.set_index("時間點").T.reset_index()
            df_transposed.rename(columns={"index": "情境方案"}, inplace=True)
            
            # 2. 建立格式化呈現表 (套用整數千分位格式，精確對齊水利署 Excel 樣式)
            df_disp = df_transposed.copy()
            for col in df_disp.columns:
                if col != "情境方案":
                    df_disp[col] = df_disp[col].apply(lambda x: f"{x:,.0f}" if pd.notnull(x) else "-")
                    
            st.dataframe(df_disp.set_index("情境方案"), use_container_width=True)
            
            # 3. 提供轉置後的橫向 CSV 對比表一鍵下載 (格式與畫面對齊)
            csv_milestone = df_transposed.to_csv(index=False).encode('utf-8-sig')
            st.download_button(
                label="📥 下載 橫向旬推估對比表 (CSV 格式)",
                data=csv_milestone,
                file_name=f"liyutan_horizontal_scenarios_{datetime.date.today().strftime('%Y%m%d')}.csv",
                mime="text/csv"
            )
            
            # ==========================================
            # 產品二：多情境蓄水量推估對比圖 (Plotly 多線疊圖)
            # ==========================================
            st.markdown("---")
            st.markdown("### 📈 產品二：多情境蓄水量推估對比圖")
            
            fig_multi = go.Figure()
            boundary_day = st.session_state.start_date - datetime.timedelta(days=1)
            
            # 1. 繪製歷史觀測段
            ref_name = selected_scenarios[0]
            df_ref = st.session_state.scenarios[ref_name]
            df_hist = df_ref[pd.to_datetime(df_ref["日期"]).dt.date <= boundary_day]
            
            if not df_hist.empty:
                fig_multi.add_trace(go.Scatter(
                    x=pd.to_datetime(df_hist["日期"]).dt.date,
                    y=df_hist["本日末庫容 (萬噸)"],
                    mode="lines",
                    name="實際觀測庫容",
                    line=dict(color="black", width=2.5),
                    hovertemplate="日期: %{x}<br>實際庫容: %{y:.2f} 萬噸<extra></extra>"
                ))
                
            # 2. 繪製各個被選取情境的推估未來歷線
            for name in selected_scenarios:
                df_scen = st.session_state.scenarios[name]
                df_proj = df_scen[pd.to_datetime(df_scen["開期"] if "開期" in df_scen else df_scen["日期"]).dt.date >= boundary_day]
                # 相容處理日期欄位
                if "日期" in df_proj:
                    proj_x = pd.to_datetime(df_proj["日期"]).dt.date
                else:
                    proj_x = pd.to_datetime(df_proj.index).dt.date
                fig_multi.add_trace(go.Scatter(
                    x=proj_x,
                    y=df_proj["本日末庫容 (萬噸)"],
                    mode="lines",
                    name=f"{name} (推估)",
                    line=dict(width=2.5),
                    hovertemplate=f"情境: {name}<br>日期: %{{x}}<br>推估庫容: %{{y:.2f}} 萬噸<extra></extra>"
                ))
                    
            # 3. 有效庫容字卡配置
            max_capacity = st.session_state.max_capacity
            formatted_capacity = f"{max_capacity:,.0f}" if max_capacity == 11584.0 else f"{max_capacity:,.1f}"
            fig_multi.add_annotation(
                text=f"有效庫容：{formatted_capacity}萬噸",
                xref="paper", yref="paper",
                x=0.0, y=1.02,
                showarrow=False,
                xanchor="left",
                yanchor="bottom",
                font=dict(color="red", size=13, family="sans-serif", weight="bold"),
                bordercolor="red",
                borderwidth=1,
                borderpad=5,
                bgcolor="white",
                opacity=0.9
            )
            
            # 4. 生成圖表 X 軸月首刻度標示
            tick_dates = []
            curr_y, curr_m = st.session_state.display_start_date.year, st.session_state.display_start_date.month
            end_y, end_m = st.session_state.end_date.year, st.session_state.end_date.month
            
            whil