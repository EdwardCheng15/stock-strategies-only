"""全市場產業別自動對照、產業週期判定與策略路由器"""

from __future__ import annotations
import requests
import yfinance as yf

TECH_CODES = {"24", "25", "26", "27", "28", "29", "30", "31"}
FINANCE_CODES = {"17"}
CYCLICAL_CODES = {"01", "03", "08", "10", "11", "14", "15", "21", "23"}

SECTOR_BENCHMARKS = {
    "sector_tech": ["0052.TW", "2330.TW"],
    "sector_finance": ["0055.TW"],
    "sector_cyclical": ["2603.TW", "2002.TW"],
    "sector_general": ["0050.TW", "1216.TW"],
}

_INDUSTRY_CACHE: dict[str, tuple[str, str]] = {}
_CYCLE_CACHE: dict[str, dict] = {}


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
    if _INDUSTRY_CACHE:
        return _INDUSTRY_CACHE
    try:
        r1 = requests.get("https://openapi.twse.com.tw/v1/opendata/t187ap03_L", timeout=15)
        for item in r1.json():
            sid = str(item.get("公司代號", "")).strip()
            ind = str(item.get("產業別", "")).strip()
            if sid and ind:
                _INDUSTRY_CACHE[sid] = _classify_code(ind)
    except Exception as e:
        print(f"⚠️ 上市產業對照載入異常: {e}")

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


def get_sector_cycles() -> dict[str, dict]:
    if _CYCLE_CACHE:
        return _CYCLE_CACHE

    all_syms = [s for syms in SECTOR_BENCHMARKS.values() for s in syms]
    try:
        df = yf.download(all_syms, period="100d", progress=False, auto_adjust=True)["Close"]
    except Exception as e:
        print(f"⚠️ 產業週期資料抓取失敗: {e}")
        df = None

    adjust_rules = {
        "sector_tech": (-3.0, 0.0, 6.0),
        "sector_finance": (-2.0, 0.0, 5.0),
        "sector_cyclical": (-4.0, 0.0, 8.0),
        "sector_general": (-2.0, 0.0, 5.0),
    }

    for sector_key, syms in SECTOR_BENCHMARKS.items():
        exp_adj, neu_adj, con_adj = adjust_rules[sector_key]
        if df is None or df.empty:
            _CYCLE_CACHE[sector_key] = {"phase": "中性震盪期", "emoji": "🟡", "threshold_adj": neu_adj}
            continue

        bull_signals = 0
        bear_signals = 0
        valid_cnt = 0

        for sym in syms:
            if sym not in df.columns:
                continue
            s = df[sym].dropna()
            if len(s) < 60:
                continue
            valid_cnt += 1
            close = float(s.iloc[-1])
            ma20 = float(s.tail(20).mean())
            ma60 = float(s.tail(60).mean())

            if close > ma20 and ma20 > ma60:
                bull_signals += 1
            elif close < ma20 and close < ma60:
                bear_signals += 1

        if valid_cnt > 0 and bull_signals == valid_cnt:
            _CYCLE_CACHE[sector_key] = {"phase": "擴張主升期", "emoji": "🟢", "threshold_adj": exp_adj}
        elif valid_cnt > 0 and bear_signals == valid_cnt:
            _CYCLE_CACHE[sector_key] = {"phase": "修正收縮期", "emoji": "🔴", "threshold_adj": con_adj}
        else:
            _CYCLE_CACHE[sector_key] = {"phase": "復甦震盪期", "emoji": "🟡", "threshold_adj": neu_adj}

    return _CYCLE_CACHE


def resolve_strategy_for_stock(stock_id: str, explicit_strategy: str = "") -> tuple[str, str, dict]:
    mapping = load_industry_map()
    strat, label = mapping.get(str(stock_id).strip(), ("sector_general", "綜合產業"))
    chosen_strat = explicit_strategy if explicit_strategy and explicit_strategy not in ("default", "auto", "") else strat
    cycles = get_sector_cycles()
    cycle_info = cycles.get(chosen_strat, {"phase": "復甦震盪期", "emoji": "🟡", "threshold_adj": 0.0})
    return chosen_strat, label, cycle_info
