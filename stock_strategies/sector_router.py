"""全市場產業別自動對照與策略路由器"""

from __future__ import annotations
import requests

# 證交所/櫃買官方產業代碼對照表
# 電子科技類：24(半導體), 25(電腦週邊), 26(光電), 27(通信網路), 28(電子零組件), 29(電子通路), 30(資訊服務), 31(其他電子)
TECH_CODES = {"24", "25", "26", "27", "28", "29", "30", "31"}

# 金融保險類：17(金融保險)
FINANCE_CODES = {"17"}

# 景氣循環與原物料類：01(水泥), 03(塑膠), 08(造紙), 10(鋼鐵), 11(橡膠), 14(建材營造), 15(航運), 21(化學), 23(油電燃氣)
CYCLICAL_CODES = {"01", "03", "08", "10", "11", "14", "15", "21", "23"}

_INDUSTRY_CACHE: dict[str, tuple[str, str]] = {}  # code -> (strategy_name, sector_label)


def _classify_code(ind_code: str) -> tuple[str, str]:
    c = ind_code.strip().zfill(2)
    if c in TECH_CODES:
        return "sector_tech", "電子科技"
    if c in FINANCE_CODES:
        return "sector_finance", "金融保險"
    if c in CYCLICAL_CODES:
        return "sector_cyclical", "景氣循環"
    return "sector_general", "傳產生技"


def load_industry_map() -> dict[str, tuple[str, str]]:
    """載入全市場上市櫃股票之產業分類快取（單次執行僅抓一次）"""
    if _INDUSTRY_CACHE:
        return _INDUSTRY_CACHE

    # 1. 上市公司基本資料
    try:
        r1 = requests.get("https://openapi.twse.com.tw/v1/opendata/t187ap03_L", timeout=15)
        for item in r1.json():
            sid = str(item.get("公司代號", "")).strip()
            ind = str(item.get("產業別", "")).strip()
            if sid and ind:
                _INDUSTRY_CACHE[sid] = _classify_code(ind)
    except Exception as e:
        print(f"⚠️ 上市產業對照載入異常: {e}")

    # 2. 上櫃公司基本資料
    try:
        r2 = requests.get("https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap03_O", timeout=15)
        for item in r2.json():
            sid = str(item.get("SecuritiesCompanyCode", "")).strip()
            ind = str(item.get("SecuritiesIndustryCode", "")).strip()
            if sid and ind:
                _INDUSTRY_CACHE[sid] = _classify_code(ind)
    except Exception as e:
        print(f"⚠️ 上櫃產業對照載入異常: {e}")

    return _INDUSTRY_CACHE


def resolve_strategy_for_stock(stock_id: str, explicit_strategy: str = "") -> tuple[str, str]:
    """回傳 (應套用的策略名稱, 產業標籤)"""
    mapping = load_industry_map()
    strat, label = mapping.get(str(stock_id).strip(), ("sector_general", "綜合產業"))
    # 若使用者在 Google Sheet 手動指定了特定策略（非空且非 default），尊重手動設定；否則依產業自動分派
    if explicit_strategy and explicit_strategy not in ("default", "auto", ""):
        return explicit_strategy, label
    return strat, label
